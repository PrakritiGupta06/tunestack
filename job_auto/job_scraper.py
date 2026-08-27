"""Small, transparent job-source adapters.

The pipeline deliberately consumes public RSS feeds or files you provide.  It
never logs in, bypasses an application site's controls, or submits an
application; the output is a review queue for the job seeker.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


class SourceError(RuntimeError):
    """Raised when a configured source cannot be read or normalized."""


@dataclass(frozen=True)
class Job:
    """Normalized job posting used by matching and persistence layers."""

    id: str
    title: str
    company: str
    url: str
    location: str
    description: str
    source: str
    posted_at: str = ""
    salary: str = ""
    employment_type: str = ""
    category: str = ""
    required_skills: tuple[str, ...] = field(default_factory=tuple)
    preferred_skills: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEMO_RESUME = """
Alex Morgan
Platform / Backend Engineer
alex@example.com | Remote

Python engineer with 5 years of experience building REST APIs and cloud-native
services. I use FastAPI, SQL, Docker, Kubernetes, Terraform, GCP, GitHub
Actions, Prometheus, Grafana, Linux, and Git. I have operated CI/CD pipelines
and production observability platforms.
""".strip()

# Built in examples make the project testable immediately. They are intentionally
# ordinary JSON-shaped postings so a user can replace this with their own file.
DEMO_JOBS: list[dict[str, Any]] = [
    {
        "id": "demo-platform-engineer",
        "title": "Platform Engineer",
        "company": "Northstar Systems",
        "location": "Remote — United States",
        "url": "https://example.invalid/jobs/platform-engineer",
        "posted_at": "2026-08-27",
        "required_skills": ["Python", "Docker", "Kubernetes", "Terraform", "GCP"],
        "preferred_skills": ["FastAPI", "Prometheus", "GitHub Actions"],
        "description": (
            "Build reliable internal platforms and APIs. Work with Python, Docker, "
            "Kubernetes, Terraform, and Google Cloud Platform in a remote team."
        ),
    },
    {
        "id": "demo-backend-engineer",
        "title": "Backend Engineer, Developer Experience",
        "company": "Cedar Labs",
        "location": "Remote",
        "url": "https://example.invalid/jobs/backend-engineer",
        "posted_at": "2026-08-25",
        "required_skills": ["Python", "SQL", "REST API", "Docker", "Git"],
        "preferred_skills": ["FastAPI", "CI/CD", "AWS"],
        "description": (
            "Create developer-facing RESTful services, automate delivery, and improve "
            "the tooling used by engineering teams."
        ),
    },
    {
        "id": "demo-data-analyst",
        "title": "Data Analyst",
        "company": "Bright Metrics",
        "location": "New York, NY",
        "url": "https://example.invalid/jobs/data-analyst",
        "posted_at": "2026-08-24",
        "required_skills": ["SQL", "Pandas", "Machine Learning"],
        "preferred_skills": ["Python"],
        "description": "Analyze product data and communicate experiments to stakeholders.",
    },
    {
        "id": "demo-frontend-engineer",
        "title": "Frontend Engineer",
        "company": "Canvas Co.",
        "location": "Hybrid — Boston, MA",
        "url": "https://example.invalid/jobs/frontend-engineer",
        "posted_at": "2026-08-22",
        "required_skills": ["JavaScript", "TypeScript", "React"],
        "preferred_skills": ["REST API"],
        "description": "Build accessible web interfaces with React and TypeScript.",
    },
]


def _clean_text(value: Any) -> str:
    """Convert source text, including simple HTML, to compact plain text."""

    if value is None:
        return ""
    text = unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        # A comma-separated list is convenient in hand-authored JSON.
        values = value.split(",")
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        values = value
    else:
        return ()
    cleaned = [_clean_text(item) for item in values]
    return tuple(item for item in cleaned if item)


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return ""


def _stable_id(record: Mapping[str, Any], source: str, title: str, company: str, url: str) -> str:
    supplied = _first(record, "id", "job_id", "uuid", "guid")
    if supplied:
        return f"{source}:{_clean_text(supplied)}"
    fingerprint = "\x1f".join((source, title.lower(), company.lower(), url.lower()))
    return f"{source}:{sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}"


def normalize_job(record: Mapping[str, Any], source: str) -> Job | None:
    """Normalize common JSON/RSS field names into one predictable schema.

    A posting without a title is skipped because it cannot be meaningfully
    ranked or presented to a person for review.
    """

    title = _clean_text(_first(record, "title", "job_title", "position", "name"))
    if not title:
        return None
    company = _clean_text(_first(record, "company", "company_name", "organization", "employer"))
    location = _clean_text(
        _first(record, "location", "job_location", "candidate_required_location", "city")
    )
    url = _clean_text(_first(record, "url", "apply_url", "link", "job_url", "absolute_url", "hostedUrl"))
    description = _clean_text(
        _first(record, "description", "content", "contentPlain", "descriptionPlain", "summary", "body")
    )
    posted_at = _clean_text(
        _first(record, "posted_at", "publication_date", "updated_at", "date", "pubDate", "published")
    )
    salary = _clean_text(_first(record, "salary", "salary_range", "compensation"))
    employment_type = _clean_text(_first(record, "employment_type", "job_type", "commitment"))
    category = _clean_text(_first(record, "category", "team", "department"))
    required = _string_list(_first(record, "required_skills", "requirements", "must_have"))
    preferred = _string_list(_first(record, "preferred_skills", "nice_to_have", "preferred"))

    return Job(
        id=_stable_id(record, source, title, company, url),
        title=title,
        company=company or "Unknown company",
        url=url,
        location=location or "Unspecified",
        description=description,
        source=source,
        posted_at=posted_at,
        salary=salary,
        employment_type=employment_type,
        category=category,
        required_skills=required,
        preferred_skills=preferred,
    )


def _records_from_json_text(text: str, label: str) -> list[Mapping[str, Any]]:
    """Parse either a JSON array/object or newline-delimited JSON records."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records: list[Mapping[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SourceError(f"{label}: invalid JSON on line {line_number}: {exc.msg}") from exc
            if not isinstance(item, Mapping):
                raise SourceError(f"{label}: JSONL line {line_number} is not an object")
            records.append(item)
        if not records:
            raise SourceError(f"{label}: no JSON job records found")
        return records

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, Mapping):
        nested_records = next(
            (
                payload[key]
                for key in ("jobs", "results", "data")
                if key in payload
            ),
            None,
        )
        # A one-line JSONL job is also valid standalone JSON. Treat an object
        # with a recognizable title field as one record instead of dropping it.
        if nested_records is None and any(key in payload for key in ("title", "job_title", "position", "name")):
            records = [payload]
        else:
            records = nested_records if nested_records is not None else []
    else:
        raise SourceError(f"{label}: expected a JSON array or object")
    if not isinstance(records, list):
        raise SourceError(f"{label}: expected 'jobs', 'results', or 'data' to be a list")
    if not all(isinstance(item, Mapping) for item in records):
        raise SourceError(f"{label}: every job record must be a JSON object")
    return list(records)


def _fetch_url(url: str, timeout: int = 20) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "job-auto-review-queue/1.0 (+local job matching; no automated applications)",
            "Accept": "application/json, application/rss+xml, application/xml, text/xml, text/plain; q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs are user-configured inputs.
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise SourceError(f"Could not fetch {url}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise SourceError(f"Could not fetch {url}: {exc.reason}") from exc


def _cached_or_fetched_text(url: str, base_dir: Path, cache_hours: float, timeout: int) -> str:
    """Read a short-lived local cache before requesting a public jobs API.

    This keeps routine runs respectful of public API rate limits. Cache files
    are created under the project's ignored ``data/source_cache`` directory.
    """

    cache_path: Path | None = None
    if cache_hours > 0:
        cache_key = sha256(url.encode("utf-8")).hexdigest()[:20]
        cache_path = base_dir / "data" / "source_cache" / f"{cache_key}.json"
        try:
            age_seconds = time.time() - cache_path.stat().st_mtime
            if age_seconds < cache_hours * 3600:
                return cache_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        except OSError:
            # A cache is an optimization, not a reason to block live discovery.
            pass

    text = _fetch_url(url, timeout)
    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    return text


def _load_json_source(source: Mapping[str, Any], base_dir: Path, source_name: str) -> list[Job]:
    path = source.get("path")
    url = source.get("url")
    if bool(path) == bool(url):
        raise SourceError(f"JSON source '{source_name}' needs exactly one of 'path' or 'url'")

    if path:
        source_path = Path(str(path)).expanduser()
        if not source_path.is_absolute():
            source_path = (base_dir / source_path).resolve()
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SourceError(f"Could not read JSON source {source_path}: {exc}") from exc
        label = str(source_path)
    else:
        label = str(url)
        text = _cached_or_fetched_text(
            label,
            base_dir,
            float(source.get("cache_hours", 0)),
            int(source.get("timeout", 20)),
        )

    return [job for record in _records_from_json_text(text, label) if (job := normalize_job(record, source_name))]


def _local_name(tag: str) -> str:
    """Return an XML tag name without a namespace prefix."""

    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in element:
        if _local_name(child.tag) in wanted:
            return "".join(child.itertext())
    return ""


def _load_rss_source(source: Mapping[str, Any], base_dir: Path, source_name: str) -> list[Job]:
    url = source.get("url")
    if not isinstance(url, str) or not url.strip():
        raise SourceError(f"RSS source '{source_name}' needs a non-empty 'url'")
    xml_text = _cached_or_fetched_text(
        url,
        base_dir,
        float(source.get("cache_hours", 0)),
        int(source.get("timeout", 20)),
    )
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"Could not parse RSS source {url}: {exc}") from exc

    records: list[Mapping[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) not in {"item", "entry"}:
            continue
        link = _element_text(element, "link")
        # Atom commonly puts the URL in href rather than inside <link>.
        if not link:
            for child in element:
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"]
                    break
        records.append(
            {
                "id": _element_text(element, "guid", "id"),
                "title": _element_text(element, "title"),
                "company": source.get("company", "Unknown company"),
                "location": source.get("location", "Unspecified"),
                "url": link,
                "description": _element_text(element, "description", "summary", "content"),
                "posted_at": _element_text(element, "pubdate", "published", "updated"),
            }
        )
    if not records:
        raise SourceError(f"RSS source '{source_name}' did not contain any item or entry elements")
    return [job for record in records if (job := normalize_job(record, source_name))]


def _source_number(
    source: Mapping[str, Any], key: str, default: int | float, minimum: int | float, maximum: int | float
) -> int | float:
    try:
        value = type(default)(source.get(key, default))
    except (TypeError, ValueError) as exc:
        raise SourceError(f"Source option '{key}' must be numeric") from exc
    if not minimum <= value <= maximum:
        raise SourceError(f"Source option '{key}' must be between {minimum} and {maximum}")
    return value


def _api_records(text: str, label: str) -> list[Mapping[str, Any]]:
    """Parse a public JSON API response through the same schema validator."""

    return _records_from_json_text(text, label)


def _load_remotive_source(source: Mapping[str, Any], base_dir: Path, source_name: str) -> list[Job]:
    """Load active remote listings from Remotive's documented public API.

    The output retains the Remotive job URL and source name for attribution.
    One source produces one cached request, rather than crawling job-board pages.
    """

    endpoint = str(source.get("endpoint", "https://remotive.com/api/remote-jobs")).strip()
    if not endpoint.startswith(("https://", "http://")):
        raise SourceError(f"Remotive endpoint for '{source_name}' must be an HTTP(S) URL")
    limit = int(_source_number(source, "limit", 100, 1, 100))
    params: dict[str, str | int] = {"limit": limit}
    for key in ("search", "category", "company_name"):
        value = source.get(key)
        if value is not None and str(value).strip():
            params[key] = str(value).strip()
    separator = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{separator}{urlencode(params)}"
    text = _cached_or_fetched_text(
        url,
        base_dir,
        float(_source_number(source, "cache_hours", 6.0, 0.0, 168.0)),
        int(_source_number(source, "timeout", 20, 1, 60)),
    )
    records = _api_records(text, url)
    return [job for record in records if (job := normalize_job(record, source_name))]


def _load_greenhouse_source(source: Mapping[str, Any], base_dir: Path, source_name: str) -> list[Job]:
    """Load a company's public Greenhouse board through its documented API."""

    board = source.get("board")
    if not isinstance(board, str) or not board.strip():
        raise SourceError(f"Greenhouse source '{source_name}' needs a non-empty 'board' token")
    endpoint = str(source.get("endpoint", "https://boards-api.greenhouse.io/v1/boards")).rstrip("/")
    url = f"{endpoint}/{quote(board.strip(), safe='-_')}/jobs?content=true"
    text = _cached_or_fetched_text(
        url,
        base_dir,
        float(_source_number(source, "cache_hours", 6.0, 0.0, 168.0)),
        int(_source_number(source, "timeout", 20, 1, 60)),
    )
    records = _api_records(text, url)
    company = _clean_text(source.get("company", ""))
    normalized_records: list[Mapping[str, Any]] = []
    for record in records:
        location = record.get("location", "")
        if isinstance(location, Mapping):
            location = location.get("name", "")
        departments = record.get("departments", [])
        category = ", ".join(
            _clean_text(item.get("name", "")) for item in departments if isinstance(item, Mapping)
        )
        normalized_records.append(
            {
                "id": record.get("id", ""),
                "title": record.get("title", ""),
                "company": company,
                "location": location,
                "url": record.get("absolute_url", ""),
                "description": record.get("content", ""),
                "posted_at": record.get("updated_at", ""),
                "category": category,
            }
        )
    return [job for record in normalized_records if (job := normalize_job(record, source_name))]


def _load_lever_source(source: Mapping[str, Any], base_dir: Path, source_name: str) -> list[Job]:
    """Load a company's public Lever board through its documented postings API."""

    site = source.get("site")
    if not isinstance(site, str) or not site.strip():
        raise SourceError(f"Lever source '{source_name}' needs a non-empty 'site' token")
    endpoint = str(source.get("endpoint", "https://api.lever.co/v0/postings")).rstrip("/")
    url = f"{endpoint}/{quote(site.strip(), safe='-_')}?mode=json"
    text = _cached_or_fetched_text(
        url,
        base_dir,
        float(_source_number(source, "cache_hours", 6.0, 0.0, 168.0)),
        int(_source_number(source, "timeout", 20, 1, 60)),
    )
    records = _api_records(text, url)
    company = _clean_text(source.get("company", ""))
    normalized_records: list[Mapping[str, Any]] = []
    for record in records:
        categories = record.get("categories", {})
        if not isinstance(categories, Mapping):
            categories = {}
        normalized_records.append(
            {
                "id": record.get("id", ""),
                "title": record.get("text", record.get("title", "")),
                "company": company,
                "location": categories.get("location", ""),
                "url": record.get("hostedUrl", record.get("applyUrl", "")),
                "description": record.get("descriptionPlain", record.get("description", "")),
                "posted_at": record.get("createdAt", ""),
                "category": categories.get("team", categories.get("department", "")),
                "employment_type": categories.get("commitment", ""),
            }
        )
    return [job for record in normalized_records if (job := normalize_job(record, source_name))]


def load_jobs(sources: Iterable[Mapping[str, Any]], base_dir: Path) -> list[Job]:
    """Load, normalize, and de-duplicate postings from configured sources."""

    jobs: list[Job] = []
    for index, source in enumerate(sources):
        source_type = str(source.get("type", "")).strip().lower()
        source_name = str(source.get("name") or source_type or f"source-{index + 1}").strip()
        if source_type == "demo":
            loaded = [
                job
                for record in DEMO_JOBS
                if (job := normalize_job(record, source_name))
            ]
        elif source_type == "json":
            loaded = _load_json_source(source, base_dir, source_name)
        elif source_type == "rss":
            loaded = _load_rss_source(source, base_dir, source_name)
        elif source_type == "remotive":
            loaded = _load_remotive_source(source, base_dir, source_name)
        elif source_type == "greenhouse":
            loaded = _load_greenhouse_source(source, base_dir, source_name)
        elif source_type == "lever":
            loaded = _load_lever_source(source, base_dir, source_name)
        else:
            raise SourceError(
                f"Unsupported source type '{source_type}' for '{source_name}'. "
                "Use demo, json, rss, remotive, greenhouse, or lever."
            )
        jobs.extend(loaded)

    # A URL is generally the best cross-source identifier. Fall back to the
    # normalized id when the source does not provide a link.
    unique: dict[str, Job] = {}
    for job in jobs:
        dedupe_key = job.url.lower() if job.url else job.id
        unique.setdefault(dedupe_key, job)
    return list(unique.values())
