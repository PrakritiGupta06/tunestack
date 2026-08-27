"""Local SQLite review queue and CSV export for job matches.

This module tracks discovered postings only. It intentionally has no browser
automation, credential handling, or application-submission behavior.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from job_matcher import JobMatch


class TrackerError(RuntimeError):
    """Raised when local match storage cannot be written."""


class ApplicationTracker:
    """Persist ranked postings so repeat runs update rather than duplicate them."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path)
        except sqlite3.Error as exc:
            raise TrackerError(f"Could not open local database {self.database_path}: {exc}") from exc
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS job_matches (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    score REAL NOT NULL,
                    matched_skills TEXT NOT NULL,
                    missing_skills TEXT NOT NULL,
                    reasons TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'review',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    raw_job_json TEXT NOT NULL
                )
                """
            )

    def record_matches(self, matches: Iterable[JobMatch]) -> int:
        """Insert or refresh matches, preserving a reviewer-chosen status."""

        rows = list(matches)
        if not rows:
            return 0
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        self.initialize()
        try:
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO job_matches (
                        job_id, title, company, location, url, source, posted_at,
                        score, matched_skills, missing_skills, reasons,
                        first_seen_at, last_seen_at, raw_job_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        title = excluded.title,
                        company = excluded.company,
                        location = excluded.location,
                        url = excluded.url,
                        source = excluded.source,
                        posted_at = excluded.posted_at,
                        score = excluded.score,
                        matched_skills = excluded.matched_skills,
                        missing_skills = excluded.missing_skills,
                        reasons = excluded.reasons,
                        last_seen_at = excluded.last_seen_at,
                        raw_job_json = excluded.raw_job_json
                    """,
                    [
                        (
                            match.job.id,
                            match.job.title,
                            match.job.company,
                            match.job.location,
                            match.job.url,
                            match.job.source,
                            match.job.posted_at,
                            match.score,
                            ", ".join(match.matched_skills),
                            ", ".join(match.missing_skills),
                            " | ".join(match.reasons),
                            now,
                            now,
                            json.dumps(match.job.to_dict(), ensure_ascii=False, sort_keys=True),
                        )
                        for match in rows
                    ],
                )
        except sqlite3.Error as exc:
            raise TrackerError(f"Could not save matches in {self.database_path}: {exc}") from exc
        return len(rows)

    def export_csv(self, path: str | Path, matches: Iterable[JobMatch]) -> Path:
        """Write this run's review queue as a spreadsheet-friendly CSV file."""

        output_path = Path(path).expanduser().resolve()
        rows = list(matches)
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "job_id",
                        "score",
                        "keyword_score",
                        "semantic_score",
                        "title",
                        "company",
                        "location",
                        "url",
                        "source",
                        "posted_at",
                        "employment_type",
                        "salary",
                        "category",
                        "required_skills",
                        "preferred_skills",
                        "matched_skills",
                        "missing_skills",
                        "description",
                        "reasons",
                        "review_status",
                    ],
                )
                writer.writeheader()
                for match in rows:
                    writer.writerow(
                        {
                            "job_id": match.job.id,
                            "score": f"{match.score:.1f}",
                            "keyword_score": f"{match.keyword_score:.1f}",
                            "semantic_score": (
                                f"{match.semantic_score:.1f}"
                                if match.semantic_score is not None
                                else ""
                            ),
                            "title": match.job.title,
                            "company": match.job.company,
                            "location": match.job.location,
                            "url": match.job.url,
                            "source": match.job.source,
                            "posted_at": match.job.posted_at,
                            "employment_type": match.job.employment_type,
                            "salary": match.job.salary,
                            "category": match.job.category,
                            "required_skills": ", ".join(match.job.required_skills),
                            "preferred_skills": ", ".join(match.job.preferred_skills),
                            "matched_skills": ", ".join(match.matched_skills),
                            "missing_skills": ", ".join(match.missing_skills),
                            "description": match.job.description,
                            "reasons": " | ".join(match.reasons),
                            "review_status": "review",
                        }
                    )
        except OSError as exc:
            raise TrackerError(f"Could not write CSV report {output_path}: {exc}") from exc
        return output_path

    def export_json(
        self,
        path: str | Path,
        matches: Iterable[JobMatch],
        model_status: dict[str, object] | None = None,
    ) -> Path:
        """Write complete job details plus scoring evidence as readable JSON."""

        output_path = Path(path).expanduser().resolve()
        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "model": model_status or {},
            "matches": [match.to_dict() for match in matches],
        }
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise TrackerError(f"Could not write JSON details report {output_path}: {exc}") from exc
        return output_path

    def summary(self) -> dict[str, int]:
        """Return lightweight queue counts for callers that need a status display."""

        self.initialize()
        try:
            with self._connect() as connection:
                total = connection.execute("SELECT COUNT(*) FROM job_matches").fetchone()[0]
                review = connection.execute(
                    "SELECT COUNT(*) FROM job_matches WHERE status = 'review'"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            raise TrackerError(f"Could not read local database {self.database_path}: {exc}") from exc
        return {"total": total, "review": review}
