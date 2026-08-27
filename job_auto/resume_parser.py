"""Resume text extraction and deterministic, regex-based skill detection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Iterable, Mapping

from config import SPACY_AVAILABLE as CONFIG_SPACY_AVAILABLE

# Keep the Option A contract visible in the NLP module as well as config.py.
# No spaCy import is attempted while this is False.
SPACY_AVAILABLE = False
if CONFIG_SPACY_AVAILABLE is not SPACY_AVAILABLE:  # pragma: no cover - defensive configuration guard.
    raise RuntimeError("The regex fallback flag must be consistent across job_auto modules")


class ResumeError(ValueError):
    """Raised when a resume cannot be read or parsed."""


@dataclass(frozen=True)
class ResumeProfile:
    """Information safely inferred from supplied resume text."""

    text: str
    skills: tuple[str, ...]
    email: str | None
    phone: str | None
    years_experience: int | None


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compile a case-insensitive whole-phrase matcher.

    ``\b`` is not reliable for skills such as ``ci/cd`` and ``c++``.  Using
    explicit word-character lookarounds avoids matching ``sql`` inside
    ``PostgreSQL`` while still allowing punctuation in a skill phrase.
    """

    escaped = re.escape(phrase.strip())
    # Let a human-written multi-word alias match any whitespace between words.
    escaped = escaped.replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", re.IGNORECASE)


def extract_skills(text: str, catalog: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Return canonical skills detected by the regex fallback.

    The output follows catalog order, which keeps reports stable and avoids the
    non-determinism that can make automated job reviews hard to compare.
    """

    if not isinstance(text, str):
        raise ResumeError("Text supplied for skill extraction must be a string")
    found: list[str] = []
    for canonical, aliases in catalog.items():
        phrases = [canonical, *aliases]
        if any(_phrase_pattern(str(phrase)).search(text) for phrase in phrases if str(phrase).strip()):
            found.append(canonical.strip().lower())
    return tuple(found)


def canonicalize_skills(values: Iterable[str], catalog: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Map user-entered skill labels and aliases to canonical catalog keys."""

    aliases_to_canonical: dict[str, str] = {}
    for canonical, aliases in catalog.items():
        normalized_canonical = canonical.strip().lower()
        aliases_to_canonical[normalized_canonical] = normalized_canonical
        for alias in aliases:
            aliases_to_canonical[str(alias).strip().lower()] = normalized_canonical

    normalized: list[str] = []
    for value in values:
        key = str(value).strip().lower()
        if not key:
            continue
        # Preserve an unknown value in the missing-skills report rather than
        # quietly treating it as a known capability.
        normalized.append(aliases_to_canonical.get(key, key))
    return tuple(dict.fromkeys(normalized))


def _extract_contact(text: str) -> tuple[str | None, str | None]:
    email_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.IGNORECASE)
    # This is intentionally conservative: it identifies common international
    # phone formatting but does not attempt to validate a real phone number.
    phone_match = re.search(r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)", text)
    return (
        email_match.group(0) if email_match else None,
        phone_match.group(0).strip() if phone_match else None,
    )


def _extract_years_experience(text: str) -> int | None:
    matches = re.findall(
        r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience\b",
        text,
        re.IGNORECASE,
    )
    return max((int(years) for years in matches), default=None)


def parse_resume_text(text: str, catalog: Mapping[str, Iterable[str]]) -> ResumeProfile:
    """Parse plain text with regex-only extraction (the Option A fallback)."""

    cleaned = text.replace("\x00", " ").strip()
    if not cleaned:
        raise ResumeError("Resume text is empty")
    email, phone = _extract_contact(cleaned)
    return ResumeProfile(
        text=cleaned,
        skills=extract_skills(cleaned, catalog),
        email=email,
        phone=phone,
        years_experience=_extract_years_experience(cleaned),
    )


def read_resume(path: str | Path) -> str:
    """Read a UTF-8 text/Markdown resume, or extract text from a PDF.

    PDF support is optional at import time; it only needs ``pypdf`` when a PDF
    is actually supplied.  Text files therefore keep the demo and basic setup
    lightweight.
    """

    resume_path = Path(path).expanduser().resolve()
    if not resume_path.is_file():
        raise ResumeError(f"Resume file not found: {resume_path}")

    suffix = resume_path.suffix.lower()
    if suffix in {".txt", ".md", ".rst"}:
        try:
            return resume_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ResumeError(f"Resume text file is not UTF-8: {resume_path}") from exc
        except OSError as exc:
            raise ResumeError(f"Could not read resume file {resume_path}: {exc}") from exc

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ResumeError(
                "PDF input needs pypdf. Install the project requirements with "
                "'python -m pip install -r requirements.txt'."
            ) from exc
        try:
            reader = PdfReader(str(resume_path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:  # pypdf can expose several parser-specific errors.
            raise ResumeError(f"Could not extract text from PDF resume {resume_path}: {exc}") from exc
        if not text.strip():
            raise ResumeError(
                "The PDF did not contain selectable text. OCR it first or provide a .txt resume."
            )
        return text

    raise ResumeError("Supported resume formats are .txt, .md, .rst, and text-based .pdf")


def parse_resume_file(path: str | Path, catalog: Mapping[str, Iterable[str]]) -> ResumeProfile:
    return parse_resume_text(read_resume(path), catalog)


def add_profile_skills(
    profile: ResumeProfile,
    extra_skills: Iterable[str],
    catalog: Mapping[str, Iterable[str]],
) -> ResumeProfile:
    """Add explicitly configured skills without mutating the parsed profile."""

    merged = tuple(dict.fromkeys([*profile.skills, *canonicalize_skills(extra_skills, catalog)]))
    return replace(profile, skills=merged)
