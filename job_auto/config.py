"""Configuration loading and validation for the local job-matching pipeline.

Option A intentionally keeps NLP dependency-free: the parser uses deterministic
regular-expression matching rather than a spaCy model.  Keeping the flag in one
place makes a later upgrade explicit and easy to audit.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

# Runtime spaCy capability is detected by nlp_model.py. The YAML configuration
# selects whether to use the hybrid model or the deterministic regex fallback.


class ConfigError(ValueError):
    """Raised when a configuration file cannot be used safely."""


DEFAULT_CONFIG: dict[str, Any] = {
    "nlp": {
        # "hybrid" uses local TF-IDF relevance scoring and spaCy phrase
        # matching when installed. "regex" remains available for lightweight,
        # dependency-free runs.
        "engine": "hybrid",
        "spacy_model": "en_core_web_sm",
    },
    "profile": {
        "name": "Your Name",
        "resume": None,
        # Starter example used by --live until a local resume path is supplied.
        # Replace it with confirmed facts and keep it free of contact details;
        # it is relevance input, not an application.
        "summary": (
            "Site Reliability and Platform Operations engineer with 3+ years of "
            "experience supporting distributed enterprise applications. Experience with GCP "
            "Anthos/GKE, Kubernetes, Terraform, Docker, GitHub Actions, Linux, "
            "Python, Bash, Java/Spring Boot, Pub/Sub, Apache Airflow, Splunk, "
            "Prometheus, Grafana, incident response, SLI/SLO monitoring, and SQL."
        ),
        # Defaults are tuned for an early-to-mid-career SRE/DevOps search.
        "target_roles": [
            "site reliability engineer",
            "sre",
            "platform engineer",
            "devops engineer",
            "cloud engineer",
            "cloud operations engineer",
            "production support engineer",
        ],
        "locations": [
            "delhi",
            "new delhi",
            "noida",
            "gurugram",
            "gurgaon",
            "ghaziabad",
            "faridabad",
            "india",
            "bengaluru",
            "bangalore",
            "hyderabad",
            "pune",
            "mumbai",
            "chennai",
            "remote",
        ],
        # Add skills here when they are not written in the resume text.
        "skills": [
            "gcp",
            "anthos",
            "kubernetes",
            "terraform",
            "docker",
            "helm",
            "github actions",
            "ci/cd",
            "git",
            "rest api",
            "python",
            "bash",
            "java",
            "spring boot",
            "pub/sub",
            "apache airflow",
            "splunk",
            "prometheus",
            "grafana",
            "linux",
            "sql",
            "incident response",
            "sli/slo",
        ],
    },
    "skills": {
        # Keys are the canonical values written to reports. Values are phrases
        # the phrase/regex matcher should treat as equivalent.
        "catalog": {
            "python": ["python", "python3"],
            "bash": ["bash", "shell scripting", "shell"],
            "java": ["java"],
            "spring boot": ["spring boot", "springboot"],
            "sql": ["sql", "postgresql", "mysql", "sqlite", "db2"],
            "fastapi": ["fastapi", "fast api"],
            "docker": ["docker", "containers"],
            "kubernetes": ["kubernetes", "k8s", "gke", "google kubernetes engine"],
            "anthos": ["anthos"],
            "terraform": ["terraform"],
            "helm": ["helm"],
            "gcp": ["gcp", "google cloud", "google cloud platform"],
            "aws": ["aws", "amazon web services"],
            "azure": ["azure", "microsoft azure"],
            "git": ["git", "github", "gitlab"],
            "github actions": ["github actions"],
            "ci/cd": ["ci/cd", "continuous integration", "continuous deployment"],
            "prometheus": ["prometheus"],
            "grafana": ["grafana"],
            "splunk": ["splunk"],
            "linux": ["linux", "unix"],
            "apache airflow": ["apache airflow", "airflow"],
            "pub/sub": ["pub/sub", "google pub/sub", "gcp pub/sub"],
            "rabbitmq": ["rabbitmq", "rabbit mq"],
            "incident response": ["incident response", "incident management", "major incident"],
            "sli/slo": ["sli/slo", "sli", "slo", "service level objective"],
            "pagerduty": ["pagerduty", "pager duty"],
            "servicenow": ["servicenow", "service now"],
            "rest api": ["rest api", "rest apis", "restful api", "restful services"],
            "pandas": ["pandas"],
            "machine learning": ["machine learning", "ml"],
            "javascript": ["javascript", "ecmascript"],
            "typescript": ["typescript"],
            "react": ["react", "react.js", "reactjs"],
        }
    },
    "matching": {
        "minimum_score": 45,
        # Keep up to 100 discovered roles in the daily review queue. A human
        # still selects any role before a tailored draft is prepared.
        "max_results": 100,
        "weights": {
            "required_skills": 55,
            "preferred_skills": 12,
            "title": 8,
            "location": 5,
            "semantic": 20,
        },
        # Jobs whose title or description contains one of these phrases are
        # omitted before scoring.  Leave empty to consider every job.
        "exclude_terms": [],
    },
    "sources": [
        # This makes a fresh clone demonstrable without credentials or network
        # access. Replace it with a local JSON/JSONL file or an RSS source.
        {"type": "demo"},
    ],
    # --live uses this public, attribution-preserving source. It is intentionally
    # separate from the offline demo so a fresh clone is always testable.
    "live_sources": [
        {
            "type": "remotive",
            "name": "Remotive — SRE / DevOps",
            "search": "site reliability engineer",
            "limit": 100,
            "cache_hours": 24,
        },
    ],
    "daily_search": {
        # The GitHub Actions workflow uses 02:30 UTC, which is 08:00 in
        # Asia/Kolkata. GitHub may delay scheduled workflows during high load.
        "timezone": "Asia/Kolkata",
        "morning_time": "08:00",
        "max_results": 100,
        # Search full-time roles across the arrangements requested by the user.
        # Unknown values are retained and labelled for human review instead of
        # silently dropping an otherwise relevant official listing.
        "workplace_types": ["remote", "hybrid", "on-site"],
        "employment_types": ["full-time"],
        "include_unknown_workplace_type": True,
        "include_unknown_employment_type": True,
        # The daily runner adds a transparent regional ordering: Delhi NCR,
        # then India / India-remote, then other remote opportunities.
        "title_keywords": [
            "site reliability",
            "sre",
            "devops",
            "platform",
            "cloud engineer",
            "cloud operations",
            "reliability engineer",
            "production support",
        ],
        "exclude_title_terms": ["intern", "manager", "director", "principal", "staff", "architect"],
        "output_dir": "output/daily",
    },
    "storage": {
        "database": "data/job_auto.sqlite3",
        "report": "output/job_matches.csv",
        "details_report": "output/job_matches.json",
    },
}


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursive merge without mutating either input mapping."""

    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _as_string_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"'{name}' must be a string or a list of strings")
    return value


def _validate(config: dict[str, Any]) -> None:
    for section in ("nlp", "profile", "skills", "matching", "daily_search", "storage"):
        if not isinstance(config.get(section), Mapping):
            raise ConfigError(f"'{section}' must be a mapping")

    engine = config["nlp"].get("engine", "regex")
    if not isinstance(engine, str) or engine.lower() not in {"regex", "hybrid", "spacy"}:
        raise ConfigError("nlp.engine must be one of: regex, hybrid, spacy")
    config["nlp"]["engine"] = engine.lower()
    model_name = config["nlp"].get("spacy_model", "en_core_web_sm")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ConfigError("nlp.spacy_model must be a non-empty model name")
    config["nlp"]["spacy_model"] = model_name.strip()

    profile = config["profile"]
    profile["target_roles"] = _as_string_list(profile.get("target_roles"), "profile.target_roles")
    profile["locations"] = _as_string_list(profile.get("locations"), "profile.locations")
    profile["skills"] = _as_string_list(profile.get("skills"), "profile.skills")
    if profile.get("resume") is not None and not isinstance(profile["resume"], str):
        raise ConfigError("'profile.resume' must be a path string or null")
    if profile.get("summary") is not None and not isinstance(profile["summary"], str):
        raise ConfigError("'profile.summary' must be a string or null")

    catalog = config["skills"].get("catalog")
    if not isinstance(catalog, Mapping):
        raise ConfigError("'skills.catalog' must be a mapping of skill names to aliases")
    if not catalog:
        raise ConfigError("'skills.catalog' cannot be empty")
    for canonical, aliases in catalog.items():
        if not isinstance(canonical, str) or not canonical.strip():
            raise ConfigError("skill catalog keys must be non-empty strings")
        _as_string_list(aliases, f"skills.catalog.{canonical}")

    matching = config["matching"]
    try:
        minimum_score = float(matching.get("minimum_score", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("'matching.minimum_score' must be numeric") from exc
    if not 0 <= minimum_score <= 100:
        raise ConfigError("'matching.minimum_score' must be between 0 and 100")
    matching["minimum_score"] = minimum_score

    try:
        max_results = int(matching.get("max_results", 20))
    except (TypeError, ValueError) as exc:
        raise ConfigError("'matching.max_results' must be an integer") from exc
    if max_results < 1:
        raise ConfigError("'matching.max_results' must be at least 1")
    matching["max_results"] = max_results

    weights = matching.get("weights")
    if not isinstance(weights, Mapping):
        raise ConfigError("'matching.weights' must be a mapping")
    required_weight_names = {"required_skills", "preferred_skills", "title", "location", "semantic"}
    missing_weights = required_weight_names - set(weights)
    if missing_weights:
        raise ConfigError(f"matching.weights is missing: {', '.join(sorted(missing_weights))}")
    for name in required_weight_names:
        try:
            weights[name] = float(weights[name])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"matching.weights.{name} must be numeric") from exc
        if weights[name] < 0:
            raise ConfigError(f"matching.weights.{name} cannot be negative")
    if sum(weights.values()) <= 0:
        raise ConfigError("at least one matching weight must be positive")
    matching["exclude_terms"] = _as_string_list(matching.get("exclude_terms"), "matching.exclude_terms")

    daily_search = config["daily_search"]
    try:
        daily_limit = int(daily_search.get("max_results", 100))
    except (TypeError, ValueError) as exc:
        raise ConfigError("'daily_search.max_results' must be an integer") from exc
    if not 1 <= daily_limit <= 100:
        raise ConfigError("'daily_search.max_results' must be between 1 and 100")
    daily_search["max_results"] = daily_limit
    daily_search["workplace_types"] = _as_string_list(
        daily_search.get("workplace_types"), "daily_search.workplace_types"
    )
    daily_search["employment_types"] = _as_string_list(
        daily_search.get("employment_types"), "daily_search.employment_types"
    )
    daily_search["title_keywords"] = _as_string_list(
        daily_search.get("title_keywords"), "daily_search.title_keywords"
    )
    daily_search["exclude_title_terms"] = _as_string_list(
        daily_search.get("exclude_title_terms"), "daily_search.exclude_title_terms"
    )
    for key in ("include_unknown_workplace_type", "include_unknown_employment_type"):
        if not isinstance(daily_search.get(key), bool):
            raise ConfigError(f"daily_search.{key} must be true or false")
    for key in ("timezone", "morning_time", "output_dir"):
        if not isinstance(daily_search.get(key), str) or not daily_search[key].strip():
            raise ConfigError(f"daily_search.{key} must be a non-empty string")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily_search["morning_time"]):
        raise ConfigError("daily_search.morning_time must use 24-hour HH:MM format")

    for source_group in ("sources", "live_sources"):
        if not isinstance(config.get(source_group), list):
            raise ConfigError(f"'{source_group}' must be a list")
        for index, source in enumerate(config[source_group]):
            if not isinstance(source, Mapping) or not isinstance(source.get("type"), str):
                raise ConfigError(
                    f"{source_group}[{index}] must be a mapping with a string 'type'"
                )

    storage = config["storage"]
    for key in ("database", "report", "details_report"):
        if not isinstance(storage.get(key), str) or not storage[key].strip():
            raise ConfigError(f"storage.{key} must be a non-empty path string")


def load_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    """Load YAML configuration and return it with the config file's directory.

    Relative paths in a config file are resolved against the returned directory,
    rather than the shell's current working directory.
    """

    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, Mapping):
        raise ConfigError("The configuration root must be a mapping")

    config = _deep_merge(DEFAULT_CONFIG, loaded)
    _validate(config)
    return config, config_path.parent


def resolve_path(path_value: str | Path, base_dir: Path) -> Path:
    """Resolve a user-configured filesystem path relative to ``base_dir``."""

    candidate = Path(path_value).expanduser()
    return candidate if candidate.is_absolute() else (base_dir / candidate).resolve()


def skill_catalog(config: Mapping[str, Any]) -> dict[str, list[str]]:
    """Return a normalized, de-duplicated catalog for regex matching."""

    catalog: dict[str, list[str]] = {}
    for raw_name, raw_aliases in config["skills"]["catalog"].items():
        name = raw_name.strip().lower()
        alias_values = [raw_aliases] if isinstance(raw_aliases, str) else raw_aliases
        aliases = [name, *[alias.strip().lower() for alias in alias_values if alias.strip()]]
        # dict.fromkeys preserves order while removing duplicates.
        catalog[name] = list(dict.fromkeys(aliases))
    return catalog
