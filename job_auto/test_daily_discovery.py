"""Deterministic coverage for the review-first daily discovery runner.

These tests use only a temporary JSON fixture; they never request job-board
pages or attempt an application.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

# job_auto is a standalone command-line directory rather than an installed
# package. This also lets the tests run from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from daily_discovery import daily_location_priority, job_matches_daily_filters, run  # noqa: E402
from job_scraper import (  # noqa: E402
    _load_greenhouse_source,
    _load_lever_source,
    load_jobs_best_effort,
    normalize_job,
)


class DailyDiscoveryTests(unittest.TestCase):
    """Exercise filtering, reports, state, and fault isolation without a network."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.jobs_path = self.root / "fixture_jobs.json"
        self.jobs_path.write_text(
            json.dumps(
                [
                    {
                        "id": "ncr-low-skill",
                        "title": "Site Reliability Engineer",
                        "company": "NCR Example",
                        "location": "Noida, Uttar Pradesh, India",
                        "url": "https://careers.example.test/ncr-sre",
                        "employment_type": "Full-time",
                        "workplace_type": "Hybrid",
                        "required_skills": ["A non-catalog skill"],
                        "description": "Run a reliable production service.",
                    },
                    {
                        "id": "india-cloud",
                        "title": "Cloud Engineer",
                        "company": "India Example",
                        "location": "Bengaluru",
                        "url": "https://careers.example.test/india-cloud",
                        "employment_type": "full time",
                        "workplace_type": "remote",
                        "required_skills": ["GCP", "Linux", "Terraform"],
                        "description": "Full-time cloud operations work.",
                    },
                    {
                        "id": "global-high-skill",
                        "title": "Platform Engineer",
                        "company": "Global Example",
                        "location": "Remote — United States",
                        "url": "https://careers.example.test/global-platform",
                        "employment_type": "full-time",
                        "workplace_type": "remote",
                        "required_skills": ["GCP", "Linux", "Terraform", "Kubernetes"],
                        "description": "Remote platform engineering work.",
                    },
                    {
                        "id": "contract-cloud",
                        "title": "Cloud Engineer",
                        "company": "Contract Example",
                        "location": "Delhi, India",
                        "url": "https://careers.example.test/contract-cloud",
                        "employment_type": "contract",
                        "workplace_type": "on-site",
                        "description": "Terraform contract role.",
                    },
                    {
                        "id": "intern-devops",
                        "title": "DevOps Intern",
                        "company": "Intern Example",
                        "location": "Delhi, India",
                        "url": "https://careers.example.test/intern-devops",
                        "employment_type": "internship",
                        "workplace_type": "on-site",
                        "description": "Kubernetes internship.",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.config_path = self._write_config()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_config(self, live_sources: list[dict[str, object]] | None = None) -> Path:
        base_config_path = Path(__file__).with_name("config.yaml")
        config = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
        config["nlp"]["engine"] = "regex"
        config["matching"]["minimum_score"] = 0
        config["daily_search"]["max_results"] = 100
        config["daily_search"]["output_dir"] = str(self.root / "reports")
        config["storage"]["database"] = str(self.root / "review_queue.sqlite3")
        if live_sources is not None:
            config["live_sources"] = live_sources
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return path

    def _args(self, *, no_store: bool = False, use_live_sources: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            config=str(self.config_path),
            resume=None,
            jobs=None if use_live_sources else str(self.jobs_path),
            limit=None,
            minimum_score=None,
            output_dir=None,
            no_store=no_store,
            show_profile=False,
        )

    def test_daily_run_filters_and_marks_only_first_run_as_new(self) -> None:
        self.assertEqual(run(self._args()), 0)

        reports = self.root / "reports"
        digest = json.loads((reports / "daily_job_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(digest["loaded_count"], 5)
        self.assertEqual(digest["filtered_count"], 3)
        self.assertEqual(digest["retained_count"], 3)
        self.assertEqual(digest["profile"]["source"], "configured starter profile")
        self.assertEqual(len(digest["new_job_ids"]), 3)
        self.assertTrue(all(match["new_since_previous_run"] for match in digest["matches"]))

        # Regional preference is deliberate: Delhi NCR, then India, then other
        # remote roles. The stronger global skills match remains in the queue.
        titles = [match["job"]["title"] for match in digest["matches"]]
        self.assertEqual(titles, ["Site Reliability Engineer", "Cloud Engineer", "Platform Engineer"])
        priorities = [match["daily_location_priority"] for match in digest["matches"]]
        self.assertEqual(priorities, ["Delhi NCR priority", "India priority", "Other remote"])

        with (reports / "job_matches.csv").open(encoding="utf-8", newline="") as handle:
            self.assertIn("workplace_type", csv.DictReader(handle).fieldnames or [])
        markdown = (reports / "daily_job_digest.md").read_text(encoding="utf-8")
        self.assertIn("not an application bot", markdown)
        self.assertIn("Matching input: configured starter profile", markdown)
        self.assertIn("Delhi NCR priority", markdown)

        self.assertEqual(run(self._args()), 0)
        second_digest = json.loads((reports / "daily_job_digest.json").read_text(encoding="utf-8"))
        self.assertEqual(second_digest["new_job_ids"], [])
        self.assertFalse(any(match["new_since_previous_run"] for match in second_digest["matches"]))

    def test_best_effort_loader_keeps_healthy_fixture_when_a_source_is_invalid(self) -> None:
        jobs, failures = load_jobs_best_effort(
            [
                {"type": "json", "name": "fixture", "path": str(self.jobs_path)},
                {"type": "unsupported", "name": "broken board"},
            ],
            self.root,
        )
        self.assertEqual(len(jobs), 5)
        self.assertEqual(len(failures), 1)
        self.assertIn("broken board", failures[0])
        self.assertIn("Unsupported source type", failures[0])

    def test_daily_report_keeps_healthy_jobs_and_exposes_source_warning(self) -> None:
        self.config_path = self._write_config(
            live_sources=[
                {"type": "json", "name": "fixture", "path": str(self.jobs_path)},
                {"type": "unsupported", "name": "broken board"},
            ]
        )
        self.assertEqual(run(self._args(use_live_sources=True)), 0)
        digest = json.loads(
            (self.root / "reports" / "daily_job_digest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(digest["loaded_count"], 5)
        self.assertEqual(len(digest["source_failures"]), 1)
        markdown = (self.root / "reports" / "daily_job_digest.md").read_text(encoding="utf-8")
        self.assertIn("## Source warnings", markdown)
        self.assertIn("broken board", markdown)

    def test_all_failed_dry_run_returns_nonzero_for_schedule_visibility(self) -> None:
        self.config_path = self._write_config(
            live_sources=[{"type": "unsupported", "name": "broken board"}]
        )
        self.assertEqual(run(self._args(no_store=True, use_live_sources=True)), 1)

    def test_remotive_style_underscores_and_tags_are_normalized(self) -> None:
        job = normalize_job(
            {
                "id": 123,
                "title": "DevOps Engineer",
                "company_name": "Example",
                "candidate_required_location": "India (Remote)",
                "job_type": "full_time",
                "tags": ["Kubernetes", "Terraform"],
                "url": "https://remotive.example.test/123",
            },
            "Remotive",
        )
        self.assertIsNotNone(job)
        assert job is not None  # Narrow Optional[Job] for type checkers.
        self.assertEqual(job.employment_type, "full-time")
        self.assertEqual(job.workplace_type, "remote")
        self.assertEqual(job.required_skills, ("Kubernetes", "Terraform"))

    def test_daily_filter_excludes_posting_without_a_review_link(self) -> None:
        job = normalize_job(
            {
                "id": "no-link",
                "title": "DevOps Engineer",
                "location": "Remote — India",
                "employment_type": "full-time",
                "workplace_type": "remote",
            },
            "fixture",
        )
        self.assertIsNotNone(job)
        assert job is not None
        filters = {
            "title_keywords": ["devops"],
            "exclude_title_terms": [],
            "workplace_types": ["remote", "hybrid", "on-site"],
            "employment_types": ["full-time"],
            "include_unknown_workplace_type": True,
            "include_unknown_employment_type": True,
        }
        self.assertFalse(job_matches_daily_filters(job, filters))

    def test_greenhouse_metadata_preserves_workplace_and_employment_labels(self) -> None:
        response = {
            "jobs": [
                {
                    "id": 456,
                    "title": "Site Reliability Engineer",
                    "location": {"name": "India"},
                    "absolute_url": "https://boards.example.test/jobs/456",
                    "content": "Operate production systems.",
                    "metadata": [
                        {"name": "Location Type", "value": "Remote"},
                        {"name": "Employment Type", "value": "Full Time"},
                    ],
                }
            ]
        }
        with patch("job_scraper._cached_or_fetched_text", return_value=json.dumps(response)):
            jobs = _load_greenhouse_source(
                {"type": "greenhouse", "board": "example", "company": "Example"},
                self.root,
                "Example careers",
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].workplace_type, "remote")
        self.assertEqual(jobs[0].employment_type, "full-time")

    def test_lever_country_metadata_preserves_india_for_bare_city_locations(self) -> None:
        response = [
            {
                "id": "lever-123",
                "text": "DevOps Engineer",
                "hostedUrl": "https://jobs.example.test/lever-123",
                "country": "IN",
                "categories": {
                    "location": "Chennai, Tamil Nadu",
                    "commitment": "Full-time Employment",
                    "team": "Infrastructure",
                },
                "workplaceType": "onsite",
                "descriptionPlain": "Manage cloud infrastructure.",
            }
        ]
        with patch("job_scraper._cached_or_fetched_text", return_value=json.dumps(response)):
            jobs = _load_lever_source(
                {"type": "lever", "site": "example", "company": "Example"},
                self.root,
                "Example careers",
            )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].location, "Chennai, Tamil Nadu, India")
        self.assertEqual(jobs[0].employment_type, "full-time")
        self.assertEqual(jobs[0].workplace_type, "on-site")
        self.assertEqual(daily_location_priority(jobs[0]), (2, "India priority"))

    def test_location_priority_uses_board_location_not_description_text(self) -> None:
        jobs, failures = load_jobs_best_effort(
            [{"type": "json", "name": "fixture", "path": str(self.jobs_path)}], self.root
        )
        self.assertEqual(failures, [])
        self.assertEqual(daily_location_priority(jobs[0]), (3, "Delhi NCR priority"))
        self.assertEqual(daily_location_priority(jobs[1]), (2, "India priority"))
        self.assertEqual(daily_location_priority(jobs[2]), (1, "Other remote"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
