"""Configuration loading and validation for the local job-matching pipeline.

Option A intentionally keeps NLP dependency-free: the parser uses deterministic
regular-expression matching rather than a spaCy model.  Keeping the flag in one
place makes a later upgrade explicit and easy to audit.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

# Option A: do not import, download, or require a spaCy language model.
SPACY_AVAILABLE = False


class ConfigError(ValueError):
    """Raised when a configuration file cannot be used safely."""


DEFAULT_CONFIG: dict[str, Any] = {
    "nlp": {
        "engine": "regex",
        "spacy_available": False,
    },
    "profile": {
        "name": "Your Name",
        "resume": None,
        "target_roles": ["platform engineer", "backend engineer"],
        "locations": ["remote"],
        # Add skills here when they are not written in the resume text.
        "skills": [],
    },
    "skills": {
        # Keys are the canonical values written to reports.  Values are phrases
        # the regex matcher should treat as equivalent.
        "catalog": {
            "python": ["python", "python3"],
            "sql": ["sql", "postgresql", "mysql", "sqlite"],
            "fastapi": ["fastapi", "fast api"],
            "docker": ["docker", "containers"],
            "kubernetes": ["kubernetes", "k8s"],
            "terraform": ["terraform"],
            "gcp": ["gcp", "google cloud", "google cloud platform"],
            "aws": ["aws", "amazon web services"],
            "azure": ["azure", "microsoft azure"],
            "git": ["git", "github", "gitlab"],
            "github actions": ["github actions"],
            "ci/cd": ["ci/cd", "continuous integration", "continuous deployment"],
            "prometheus": ["prometheus"],
            "grafana": ["grafana"],
            "linux": ["linux"],
            "rest api": ["rest api", "restful api", "restful services"],
            "pandas": ["pandas"],
            "machine learning": ["machine learning", "ml"],
            "javascript": ["javascript", "ecmascript"],
            "typescript": ["typescript"],
            "react": ["react", "react.js", "reactjs"],
        }
    },
    "matching": {
        "minimum_score": 45,
        "max_results": 20,
        "weights": {
            "required_skills": 70,
            "preferred_skills": 15,
            "title": 10,
            "location": 5,
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
    "storage": {
        "database": "data/job_auto.sqlite3",
        "report": "output/job_matches.csv",
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
    for section in ("nlp", "profile", "skills", "matching", "storage"):
        if not isinstance(config.get(section), Mapping):
            raise ConfigError(f"'{section}' must be a mapping")

    # This project is deliberately shipped in regex-only mode. A true value
    # would falsely imply that a model is installed and being used.
    if config["nlp"].get("spacy_available") is not False:
        raise ConfigError(
            "Option A requires nlp.spacy_available: false; install and wire a "
            "spaCy model before changing this setting."
        )
    if config["nlp"].get("engine", "regex").lower() != "regex":
        raise ConfigError("Option A supports only nlp.engine: regex")

    profile = config["profile"]
    profile["target_roles"] = _as_string_list(profile.get("target_roles"), "profile.target_roles")
    profile["locations"] = _as_string_list(profile.get("locations"), "profile.locations")
    profile["skills"] = _as_string_list(profile.get("skills"), "profile.skills")
    if profile.get("resume") is not None and not isinstance(profile["resume"], str):
        raise ConfigError("'profile.resume' must be a path string or null")

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
    required_weight_names = {"required_skills", "preferred_skills", "title", "location"}
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

    if not isinstance(config.get("sources"), list):
        raise ConfigError("'sources' must be a list")
    for index, source in enumerate(config["sources"]):
        if not isinstance(source, Mapping) or not isinstance(source.get("type"), str):
            raise ConfigError(f"sources[{index}] must be a mapping with a string 'type'")

    storage = config["storage"]
    for key in ("database", "report"):
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
