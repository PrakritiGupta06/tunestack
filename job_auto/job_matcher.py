"""Explainable scoring between a resume profile and normalized job postings."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from job_scraper import Job
from resume_parser import ResumeProfile, canonicalize_skills, extract_skills


@dataclass(frozen=True)
class JobMatch:
    """A ranked, human-reviewable job match; this never represents an application."""

    job: Job
    score: float
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    matched_required_skills: tuple[str, ...]
    matched_preferred_skills: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job.to_dict(),
            "score": self.score,
            "matched_skills": list(self.matched_skills),
            "missing_skills": list(self.missing_skills),
            "matched_required_skills": list(self.matched_required_skills),
            "matched_preferred_skills": list(self.matched_preferred_skills),
            "reasons": list(self.reasons),
        }


def _phrase_in_text(phrase: str, text: str) -> bool:
    words = re.escape(phrase.strip()).replace(r"\ ", r"\s+")
    if not words:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9_]){words}(?![A-Za-z0-9_])", text, re.IGNORECASE))


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _job_skill_sets(job: Job, catalog: Mapping[str, Iterable[str]]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Get explicit job skills, or infer known phrases from a posting's text."""

    required = canonicalize_skills(job.required_skills, catalog)
    preferred = canonicalize_skills(job.preferred_skills, catalog)
    if required or preferred:
        # Avoid double-counting a skill that a feed listed in both categories.
        preferred = tuple(skill for skill in preferred if skill not in required)
        return required, preferred

    detected = extract_skills(f"{job.title}\n{job.description}", catalog)
    return detected, ()


def _title_alignment(title: str, target_roles: Iterable[str]) -> tuple[float, str | None]:
    normalized_title = title.lower()
    role_terms = [role.strip().lower() for role in target_roles if role.strip()]
    if not role_terms:
        return 1.0, None

    for role in role_terms:
        if _phrase_in_text(role, normalized_title):
            return 1.0, f"Title aligns with target role '{role}'"

    title_words = set(re.findall(r"[a-z0-9]+", normalized_title))
    best_overlap = 0.0
    best_role: str | None = None
    for role in role_terms:
        role_words = set(re.findall(r"[a-z0-9]+", role))
        if not role_words:
            continue
        overlap = _ratio(len(title_words & role_words), len(role_words))
        if overlap > best_overlap:
            best_overlap = overlap
            best_role = role
    # One shared word such as "engineer" alone is deliberately weak evidence.
    if best_overlap >= 0.5 and best_role:
        return best_overlap, f"Title partially aligns with target role '{best_role}'"
    return 0.0, None


def _location_alignment(location: str, preferred_locations: Iterable[str]) -> tuple[float, str | None]:
    normalized_location = location.lower()
    preferences = [place.strip().lower() for place in preferred_locations if place.strip()]
    if not preferences:
        return 1.0, None

    for preference in preferences:
        if preference == "remote" and "remote" in normalized_location:
            return 1.0, "Matches location preference 'remote'"
        if preference in normalized_location:
            return 1.0, f"Matches location preference '{preference}'"
    return 0.0, None


def _is_excluded(job: Job, terms: Iterable[str]) -> bool:
    haystack = f"{job.title}\n{job.description}".lower()
    return any(term.strip() and _phrase_in_text(term, haystack) for term in terms)


def score_job(
    job: Job,
    profile: ResumeProfile,
    catalog: Mapping[str, Iterable[str]],
    matching: Mapping[str, Any],
    profile_preferences: Mapping[str, Any],
) -> JobMatch:
    """Score one job with transparent coverage-based rules.

    Scores are normalized by the configured weight total, so custom weights do
    not accidentally turn a score into a value outside the familiar 0--100
    range. A missing required skill lowers the score but does not hide a job;
    the person can decide whether the gap is acceptable.
    """

    required, preferred = _job_skill_sets(job, catalog)
    candidate_skills = set(profile.skills)
    matched_required = tuple(skill for skill in required if skill in candidate_skills)
    matched_preferred = tuple(skill for skill in preferred if skill in candidate_skills)
    matched_skills = tuple(dict.fromkeys([*matched_required, *matched_preferred]))
    missing = tuple(skill for skill in required if skill not in candidate_skills)

    weights = matching["weights"]
    raw_weight_total = sum(float(value) for value in weights.values())
    required_coverage = _ratio(len(matched_required), len(required))
    preferred_coverage = _ratio(len(matched_preferred), len(preferred))
    title_coverage, title_reason = _title_alignment(job.title, profile_preferences.get("target_roles", []))
    location_coverage, location_reason = _location_alignment(
        job.location, profile_preferences.get("locations", [])
    )

    # If a source only supplied free text, all recognized job skills are treated
    # as requirements. The preferred-skill weight is folded into that evidence
    # instead of silently making inferred postings impossible to score highly.
    skill_weight = float(weights["required_skills"])
    if required and not preferred and not job.required_skills:
        skill_weight += float(weights["preferred_skills"])

    raw_score = (
        skill_weight * required_coverage
        + float(weights["preferred_skills"]) * preferred_coverage
        + float(weights["title"]) * title_coverage
        + float(weights["location"]) * location_coverage
    )
    score = round((raw_score / raw_weight_total) * 100, 1) if raw_weight_total else 0.0

    reasons: list[str] = []
    if required:
        if matched_required:
            reasons.append(
                f"Matches {len(matched_required)}/{len(required)} required skills: "
                f"{', '.join(matched_required)}"
            )
        else:
            reasons.append(f"No listed required skills matched ({', '.join(required)})")
    elif preferred:
        reasons.append("Source supplied only preferred skills")
    else:
        reasons.append("No catalog skills were recognized in this posting")
    if matched_preferred:
        reasons.append(f"Matches preferred skills: {', '.join(matched_preferred)}")
    if title_reason:
        reasons.append(title_reason)
    if location_reason:
        reasons.append(location_reason)
    if missing:
        reasons.append(f"Missing required skills: {', '.join(missing)}")

    return JobMatch(
        job=job,
        score=score,
        matched_skills=matched_skills,
        missing_skills=missing,
        matched_required_skills=matched_required,
        matched_preferred_skills=matched_preferred,
        reasons=tuple(reasons),
    )


def rank_jobs(
    jobs: Iterable[Job],
    profile: ResumeProfile,
    catalog: Mapping[str, Iterable[str]],
    matching: Mapping[str, Any],
    profile_preferences: Mapping[str, Any],
) -> list[JobMatch]:
    """Filter excluded postings then return deterministic highest-score-first matches."""

    excluded_terms = matching.get("exclude_terms", [])
    matches = [
        score_job(job, profile, catalog, matching, profile_preferences)
        for job in jobs
        if not _is_excluded(job, excluded_terms)
    ]
    return sorted(
        matches,
        key=lambda match: (-match.score, match.job.company.lower(), match.job.title.lower(), match.job.id),
    )
