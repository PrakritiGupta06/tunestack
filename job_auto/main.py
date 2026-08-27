#!/usr/bin/env python3
"""Run the regex-only job discovery and matching pipeline.

The command builds a local review queue. It does not submit applications,
interact with login-protected sites, or store credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from application_tracker import ApplicationTracker, TrackerError
from config import ConfigError, SPACY_AVAILABLE, load_config, resolve_path, skill_catalog
from job_matcher import JobMatch, rank_jobs
from job_scraper import DEMO_RESUME, SourceError, load_jobs
from resume_parser import ResumeError, add_profile_skills, parse_resume_file, parse_resume_text


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an explainable local job-review queue using regex skill matching.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="YAML configuration file",
    )
    parser.add_argument(
        "--resume",
        help="Path to a .txt, .md, .rst, or text-based .pdf resume; overrides profile.resume",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--jobs",
        metavar="PATH_OR_URL",
        help="JSON or JSONL job file/URL; overrides configured sources for this run",
    )
    source_group.add_argument(
        "--rss",
        metavar="URL",
        help="Public RSS/Atom feed URL; overrides configured sources for this run",
    )
    source_group.add_argument(
        "--demo",
        action="store_true",
        help="Use bundled sample resume and postings; no network access is made",
    )
    parser.add_argument(
        "--minimum-score",
        type=float,
        help="Override matching.minimum_score for this run (0 through 100)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of matches to keep after scoring",
    )
    parser.add_argument(
        "--report",
        help="CSV report path; overrides storage.report",
    )
    parser.add_argument(
        "--no-store",
        action="store_true",
        help="Print results without writing SQLite or CSV output",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write matching results as JSON to stdout instead of a table",
    )
    parser.add_argument(
        "--show-profile",
        action="store_true",
        help="Print detected profile data before matching",
    )
    return parser


def _has_demo_source(sources: Iterable[Mapping[str, Any]]) -> bool:
    return any(str(source.get("type", "")).lower() == "demo" for source in sources)


def _run_sources(
    args: argparse.Namespace,
    configured_sources: list[Mapping[str, Any]],
    config_dir: Path,
) -> tuple[list[Mapping[str, Any]], Path]:
    """Construct this run's sources and the base directory for relative paths."""

    if args.demo:
        return [{"type": "demo", "name": "demo"}], Path.cwd()
    if args.jobs:
        if args.jobs.startswith(("http://", "https://")):
            return [{"type": "json", "url": args.jobs, "name": "command-line-json"}], Path.cwd()
        return [{"type": "json", "path": args.jobs, "name": "command-line-json"}], Path.cwd()
    if args.rss:
        return [{"type": "rss", "url": args.rss, "name": "command-line-rss"}], Path.cwd()
    # YAML paths are documented and resolved relative to config.yaml, not the
    # shell's current directory.
    return configured_sources, config_dir


def _profile_for_run(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    config_dir: Path,
    catalog: Mapping[str, Iterable[str]],
    sources: Iterable[Mapping[str, Any]],
):
    if args.demo and not args.resume:
        profile = parse_resume_text(DEMO_RESUME, catalog)
    else:
        resume_value = args.resume or config["profile"].get("resume")
        if resume_value:
            # A command-line path is interpreted from the user's current shell;
            # a YAML path is interpreted relative to config.yaml.
            base_dir = Path.cwd() if args.resume else config_dir
            resume_path = resolve_path(resume_value, base_dir)
            profile = parse_resume_file(resume_path, catalog)
        elif _has_demo_source(sources):
            profile = parse_resume_text(DEMO_RESUME, catalog)
        else:
            raise ResumeError(
                "No resume was supplied. Set profile.resume in config.yaml or pass --resume PATH."
            )
    return add_profile_skills(profile, config["profile"].get("skills", []), catalog)


def _display_profile(profile: Any) -> None:
    print("Candidate profile")
    print(f"  Skills: {', '.join(profile.skills) if profile.skills else 'none detected'}")
    print(f"  Email: {profile.email or 'not detected'}")
    print(f"  Phone: {profile.phone or 'not detected'}")
    experience = f"{profile.years_experience} years" if profile.years_experience is not None else "not detected"
    print(f"  Experience: {experience}")
    print()


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)].rstrip() + "…"


def _print_table(matches: list[JobMatch]) -> None:
    if not matches:
        print("No postings met the minimum score. Lower matching.minimum_score or review the skill catalog.")
        return

    columns = (("SCORE", 7), ("TITLE", 32), ("COMPANY", 23), ("LOCATION", 21), ("MATCHED SKILLS", 36))
    header = "  ".join(f"{name:<{width}}" for name, width in columns)
    print(header)
    print("  ".join("-" * width for _, width in columns))
    for match in matches:
        cells = (
            f"{match.score:>5.1f}%",
            _truncate(match.job.title, 32),
            _truncate(match.job.company, 23),
            _truncate(match.job.location, 21),
            _truncate(", ".join(match.matched_skills) or "—", 36),
        )
        print("  ".join(f"{cell:<{width}}" for cell, (_, width) in zip(cells, columns)))
        if match.missing_skills:
            print(f"         Missing: {', '.join(match.missing_skills)}")
        if match.job.url:
            print(f"         Review:  {match.job.url}")


def _print_json(matches: list[JobMatch]) -> None:
    print(json.dumps([match.to_dict() for match in matches], indent=2, ensure_ascii=False))


def run(args: argparse.Namespace) -> int:
    config, config_dir = load_config(args.config)
    if SPACY_AVAILABLE is not False:  # Defensive: Option A must never claim to use spaCy.
        raise ConfigError("This project is configured for regex fallback only")

    if args.minimum_score is not None:
        if not 0 <= args.minimum_score <= 100:
            raise ConfigError("--minimum-score must be between 0 and 100")
        config["matching"]["minimum_score"] = args.minimum_score
    if args.limit is not None:
        if args.limit < 1:
            raise ConfigError("--limit must be at least 1")
        config["matching"]["max_results"] = args.limit

    sources, source_base_dir = _run_sources(args, config["sources"], config_dir)
    catalog = skill_catalog(config)
    profile = _profile_for_run(args, config, config_dir, catalog, sources)
    if args.show_profile and not args.json:
        _display_profile(profile)

    jobs = load_jobs(sources, source_base_dir)
    matches = rank_jobs(jobs, profile, catalog, config["matching"], config["profile"])
    minimum_score = float(config["matching"]["minimum_score"])
    limit = int(config["matching"]["max_results"])
    selected = [match for match in matches if match.score >= minimum_score][:limit]

    if args.json:
        _print_json(selected)
    else:
        print(
            f"Regex fallback active (SPACY_AVAILABLE={SPACY_AVAILABLE}). "
            f"Scored {len(jobs)} posting(s); {len(selected)} met {minimum_score:g}% minimum.\n"
        )
        _print_table(selected)

    if not args.no_store:
        database_path = resolve_path(config["storage"]["database"], config_dir)
        report_value = args.report or config["storage"]["report"]
        report_base_dir = Path.cwd() if args.report else config_dir
        report_path = resolve_path(report_value, report_base_dir)
        tracker = ApplicationTracker(database_path)
        saved = tracker.record_matches(selected)
        report_path = tracker.export_csv(report_path, selected)
        if not args.json:
            queue = tracker.summary()
            print(
                f"\nSaved {saved} match(es) to {database_path}\n"
                f"Wrote CSV review queue to {report_path}\n"
                f"Local queue: {queue['review']} review item(s), {queue['total']} total."
            )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (ConfigError, ResumeError, SourceError, TrackerError) as exc:
        parser.error(str(exc))
    return 2  # pragma: no cover - argparse.error exits before reaching this line.


if __name__ == "__main__":
    sys.exit(main())
