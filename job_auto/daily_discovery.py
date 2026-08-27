#!/usr/bin/env python3
"""Build a daily, review-first job-discovery digest.

The runner reads only configured public job APIs and public employer career
boards, ranks up to 100 full-time SRE/DevOps/cloud/platform roles against a
local resume or factual profile, and writes a local review queue. It does not
log in to job sites, answer screening questions, upload a resume, or submit an
application.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application_tracker import ApplicationTracker, TrackerError
from config import ConfigError, load_config, resolve_path, skill_catalog
from job_matcher import JobMatch, rank_jobs
from job_scraper import Job, SourceError, load_jobs_best_effort
from nlp_model import HybridRelevanceModel
from resume_parser import ResumeError, add_profile_skills, parse_resume_file, parse_resume_text


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")


class DailyDiscoveryError(RuntimeError):
    """Raised for an invalid unattended daily discovery invocation."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a daily, human-reviewable SRE/DevOps job digest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="YAML configuration file")
    parser.add_argument(
        "--resume",
        help="Local .txt, .md, .rst, .tex, or text-based .pdf resume; overrides profile.resume",
    )
    parser.add_argument(
        "--jobs",
        metavar="PATH_OR_URL",
        help="JSON/JSONL source for a test or one-off daily run; overrides configured live_sources",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of ranked roles to retain (1–100)")
    parser.add_argument("--minimum-score", type=float, help="Override the configured score threshold")
    parser.add_argument(
        "--output-dir",
        help="Directory for daily_job_digest.md/json and job_matches.csv; defaults to daily_search.output_dir",
    )
    parser.add_argument("--no-store", action="store_true", help="Do not write the review database or reports")
    parser.add_argument("--show-profile", action="store_true", help="Print detected skills and experience")
    return parser


def _profile_for_run(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    config_dir: Path,
    catalog: Mapping[str, Iterable[str]],
    model: HybridRelevanceModel,
):
    resume_value = args.resume or config["profile"].get("resume")
    if resume_value:
        # Command-line paths are relative to the current working directory;
        # YAML paths are relative to config.yaml.
        base_dir = Path.cwd() if args.resume else config_dir
        profile = parse_resume_file(resolve_path(resume_value, base_dir), catalog, model.extract_skills)
    elif config["profile"].get("summary"):
        profile = parse_resume_text(config["profile"]["summary"], catalog, model.extract_skills)
    else:
        raise ResumeError(
            "No resume or factual profile.summary is configured. Supply --resume PATH or profile.summary."
        )
    return add_profile_skills(profile, config["profile"].get("skills", []), catalog)


def _phrase_in_text(phrase: str, text: str) -> bool:
    escaped = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    return bool(escaped and re.search(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", text, re.IGNORECASE))


def _allowed(value: str, accepted: Iterable[str], include_unknown: bool) -> bool:
    normalized = value.strip().lower()
    accepted_values = {item.strip().lower() for item in accepted if item.strip()}
    if not normalized:
        return include_unknown
    return not accepted_values or normalized in accepted_values


def job_matches_daily_filters(job: Job, daily_search: Mapping[str, Any]) -> bool:
    """Apply explicit job-kind filters before relevance ranking.

    The title test deliberately uses role-specific phrases instead of the word
    "engineer" alone. Arrangement and employment type tests retain a source's
    missing metadata when configured, allowing a person to inspect it rather
    than losing a viable employer listing.
    """

    # A review queue must keep a public listing link so the person can verify
    # the role and decide whether to apply themselves.
    if not job.url.strip():
        return False
    title = job.title.lower()
    excluded_terms = daily_search.get("exclude_title_terms", [])
    if any(_phrase_in_text(str(term), title) for term in excluded_terms if str(term).strip()):
        return False
    wanted_terms = daily_search.get("title_keywords", [])
    if wanted_terms and not any(_phrase_in_text(str(term), title) for term in wanted_terms if str(term).strip()):
        return False
    if not _allowed(
        job.workplace_type,
        daily_search.get("workplace_types", []),
        bool(daily_search.get("include_unknown_workplace_type", True)),
    ):
        return False
    return _allowed(
        job.employment_type,
        daily_search.get("employment_types", []),
        bool(daily_search.get("include_unknown_employment_type", True)),
    )


def _now_in_timezone(timezone_name: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        # Config validation catches basic mistakes such as blank values. This
        # fallback still leaves a clear UTC timestamp in a one-off report.
        return datetime.now(UTC)


def _line(value: str) -> str:
    """Keep external source text on one Markdown line."""

    return re.sub(r"\s+", " ", value).strip()


_DELHI_NCR_TERMS = (
    "delhi",
    "new delhi",
    "noida",
    "greater noida",
    "gurugram",
    "gurgaon",
    "ghaziabad",
    "faridabad",
    "ncr",
)
_INDIA_LOCATION_TERMS = (
    "india",
    "bengaluru",
    "bangalore",
    "mumbai",
    "pune",
    "hyderabad",
    "chennai",
    "kolkata",
    "ahmedabad",
    "gandhinagar",
    "coimbatore",
    "kochi",
    "thiruvananthapuram",
    "trivandrum",
    "indore",
    "jaipur",
    "chandigarh",
    "bhubaneswar",
)


def daily_location_priority(job: Job) -> tuple[int, str]:
    """Return the explicit regional tier used for this candidate's daily queue.

    Daily discovery keeps broader opportunities instead of treating geography as
    a hard exclusion. It nonetheless places Delhi NCR first, then India or
    India-remote roles, then other remote roles; relevance decides the ordering
    within each tier. Only a public board's location and normalized work mode
    are considered, so a stray country name in a job description cannot falsely
    change the tier.
    """

    location = job.location.lower()
    if any(_phrase_in_text(term, location) for term in _DELHI_NCR_TERMS):
        return 3, "Delhi NCR priority"
    if any(_phrase_in_text(term, location) for term in _INDIA_LOCATION_TERMS):
        return 2, "India priority"
    if job.workplace_type == "remote" or _phrase_in_text("remote", location):
        return 1, "Other remote"
    return 0, "Other location"


def _match_markdown(rank: int, match: JobMatch, is_new: bool) -> str:
    job = match.job
    _, location_label = daily_location_priority(job)
    labels = ["NEW" if is_new else "Previously seen", location_label]
    if job.workplace_type:
        labels.append(job.workplace_type)
    else:
        labels.append("workplace unknown")
    if job.employment_type:
        labels.append(job.employment_type)
    else:
        labels.append("employment type unknown")
    missing = f" Missing listed skills: {', '.join(match.missing_skills)}." if match.missing_skills else ""
    return (
        f"{rank}. **{_line(job.title)}** — {_line(job.company)}\n"
        f"   - {' · '.join(labels)} · {_line(job.location)}\n"
        f"   - Source: {_line(job.source)}\n"
        f"   - Match score: **{match.score:.1f}%** (keyword {match.keyword_score:.1f}%"
        f"{f'; local semantic {match.semantic_score:.1f}%' if match.semantic_score is not None else ''})\n"
        f"   - Matched: {', '.join(match.matched_skills) or 'no catalog skills detected'}.{missing}\n"
        f"   - [Review official listing / apply yourself]({_line(job.url)})\n"
    )


def _write_daily_digest(
    output_dir: Path,
    matches: list[JobMatch],
    new_job_ids: set[str],
    loaded_count: int,
    filtered_count: int,
    failures: list[str],
    model_status: Mapping[str, object],
    profile: Any,
    profile_source: str,
    timezone_name: str,
) -> tuple[Path, Path]:
    """Write readable and structured daily summaries; no application is made."""

    output_dir.mkdir(parents=True, exist_ok=True)
    now = _now_in_timezone(timezone_name)
    new_matches = [match for match in matches if match.job.id in new_job_ids]
    source_health = (
        "All configured public sources returned successfully."
        if not failures
        else f"{len(failures)} source(s) were unavailable; results from healthy sources are retained."
    )
    lines = [
        "# Daily SRE / DevOps job review digest",
        "",
        f"Generated: {now.isoformat(timespec='seconds')}",
        f"Matching input: {profile_source}",
        f"Profile experience detected: {profile.years_experience if profile.years_experience is not None else 'not detected'} year(s)",
        f"Profile skills used: {', '.join(profile.skills) or 'none detected'}",
        f"Model: {model_status.get('description', 'unknown')}",
        "",
        "## Run summary",
        "",
        f"- Public postings loaded: {loaded_count}",
        f"- Roles passing title, full-time, and arrangement filters: {filtered_count}",
        f"- Roles retained after relevance ranking: {len(matches)} (maximum 100)",
        "- Location order: Delhi NCR, then India / India-remote, then other remote, then other locations; relevance decides within each tier.",
        f"- New since the previous saved run: {len(new_matches)}",
        f"- Source health: {source_health}",
        "",
        "> This is a ranked review queue, not an application bot. Open each official listing, verify eligibility and facts, then decide whether to apply.",
        "",
        "## New roles first",
        "",
    ]
    if new_matches:
        lines.extend(_match_markdown(index, match, True) for index, match in enumerate(new_matches, start=1))
    else:
        lines.append("No newly detected roles in this run; review the current ranked queue below.")
    lines.extend(["", "## Current ranked queue", ""])
    lines.extend(
        _match_markdown(index, match, match.job.id in new_job_ids)
        for index, match in enumerate(matches, start=1)
    )
    if failures:
        lines.extend(["", "## Source warnings", ""])
        lines.extend(f"- {_line(failure)}" for failure in failures)
    lines.extend(
        [
            "",
            "## What this runner does not do",
            "",
            "- It does not log in to job boards, bypass CAPTCHAs, upload a resume, answer eligibility questions, or submit applications.",
            "- It does not claim that every job platform or every open role is accessible; login-protected and unsupported sources are intentionally excluded.",
        ]
    )
    markdown_path = output_dir / "daily_job_digest.md"
    json_path = output_dir / "daily_job_digest.json"
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "generated_at": now.isoformat(timespec="seconds"),
        "timezone": timezone_name,
        "loaded_count": loaded_count,
        "filtered_count": filtered_count,
        "retained_count": len(matches),
        "new_job_ids": sorted(new_job_ids),
        "source_failures": failures,
        "model": dict(model_status),
        "profile": {
            "skills": list(profile.skills),
            "years_experience": profile.years_experience,
            "source": profile_source,
        },
        "matches": [
            {
                **match.to_dict(),
                "new_since_previous_run": match.job.id in new_job_ids,
                "daily_location_priority": daily_location_priority(match.job)[1],
            }
            for match in matches
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return markdown_path, json_path


def run(args: argparse.Namespace) -> int:
    config, config_dir = load_config(args.config)
    if args.limit is not None:
        if not 1 <= args.limit <= 100:
            raise DailyDiscoveryError("--limit must be between 1 and 100")
        config["daily_search"]["max_results"] = args.limit
    if args.minimum_score is not None:
        if not 0 <= args.minimum_score <= 100:
            raise DailyDiscoveryError("--minimum-score must be between 0 and 100")
        config["matching"]["minimum_score"] = args.minimum_score

    catalog = skill_catalog(config)
    model = HybridRelevanceModel(catalog, config["nlp"])
    profile = _profile_for_run(args, config, config_dir, catalog, model)
    profile_source = (
        "resume" if args.resume or config["profile"].get("resume") else "configured starter profile"
    )
    if args.show_profile:
        experience = f"{profile.years_experience} years" if profile.years_experience is not None else "not detected"
        print(f"Candidate profile: {experience}; skills: {', '.join(profile.skills) or 'none'}")

    if args.jobs:
        if args.jobs.startswith(("http://", "https://")):
            sources = [{"type": "json", "url": args.jobs, "name": "command-line-json"}]
        else:
            sources = [{"type": "json", "path": args.jobs, "name": "command-line-json"}]
        source_base_dir = Path.cwd()
    else:
        sources = config["live_sources"]
        source_base_dir = config_dir

    jobs, failures = load_jobs_best_effort(sources, source_base_dir)
    filtered_jobs = [job for job in jobs if job_matches_daily_filters(job, config["daily_search"])]
    semantic_scores = model.semantic_scores(profile.text, profile.skills, filtered_jobs)
    ranked = rank_jobs(
        filtered_jobs,
        profile,
        catalog,
        config["matching"],
        config["profile"],
        semantic_scores=semantic_scores if model.status.semantic_available else None,
        skill_extractor=model.extract_skills,
    )
    eligible_matches = [
        match for match in ranked if match.score >= float(config["matching"]["minimum_score"])
    ]
    # Geographic preferences are an explicit daily ordering, not a hard filter:
    # a high-quality broader opportunity remains reviewable after local roles.
    selected = sorted(
        eligible_matches,
        key=lambda match: (
            -daily_location_priority(match.job)[0],
            -match.score,
            match.job.company.lower(),
            match.job.title.lower(),
            match.job.id,
        ),
    )[: int(config["daily_search"]["max_results"])]

    output_value = args.output_dir or config["daily_search"]["output_dir"]
    output_base_dir = Path.cwd() if args.output_dir else config_dir
    output_dir = resolve_path(output_value, output_base_dir)
    if args.no_store:
        print(
            f"Daily dry run: loaded {len(jobs)} public postings; filtered to {len(filtered_jobs)}; "
            f"retained {len(selected)} ranked roles; source warnings: {len(failures)}."
        )
        return 1 if not jobs and failures else 0

    tracker = ApplicationTracker(resolve_path(config["storage"]["database"], config_dir))
    known_ids = tracker.known_job_ids()
    new_job_ids = {match.job.id for match in selected if match.job.id not in known_ids}
    tracker.record_matches(selected)
    tracker.export_csv(output_dir / "job_matches.csv", selected)
    markdown_path, json_path = _write_daily_digest(
        output_dir,
        selected,
        new_job_ids,
        len(jobs),
        len(filtered_jobs),
        failures,
        model.status.to_dict(),
        profile,
        profile_source,
        str(config["daily_search"]["timezone"]),
    )
    print(
        f"Daily review queue complete: loaded {len(jobs)}, filtered {len(filtered_jobs)}, "
        f"retained {len(selected)} of {config['daily_search']['max_results']} maximum.\n"
        f"New roles: {len(new_job_ids)}. Source warnings: {len(failures)}.\n"
        f"Digest: {markdown_path}\nJSON: {json_path}\nCSV: {output_dir / 'job_matches.csv'}"
    )
    # A no-result report can be legitimate when all postings are poor matches.
    # Return non-zero only when every source failed, so a scheduled workflow
    # exposes an actual discovery outage.
    return 1 if not jobs and failures else 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (ConfigError, ResumeError, SourceError, TrackerError, DailyDiscoveryError) as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover - argparse.error exits first.


if __name__ == "__main__":
    sys.exit(main())
