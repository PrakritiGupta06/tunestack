"""Local hybrid relevance model for job-search prioritization.

This is an explainable information-retrieval model, not a hiring or eligibility
model. It compares a candidate's supplied profile with job descriptions so the
candidate can decide which links to review. Exact skill matching remains in
place as a transparent fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping, Sequence, TYPE_CHECKING

from resume_parser import extract_skills as regex_extract_skills

if TYPE_CHECKING:
    from job_scraper import Job

try:  # spaCy is optional at import time so the regex-only fallback still runs.
    import spacy
    from spacy.matcher import PhraseMatcher
except ImportError:  # pragma: no cover - depends on the caller's environment.
    spacy = None  # type: ignore[assignment]
    PhraseMatcher = None  # type: ignore[assignment,misc]
    SPACY_AVAILABLE = False
else:
    SPACY_AVAILABLE = True

try:  # scikit-learn supplies the locally fitted TF-IDF relevance model.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:  # pragma: no cover - depends on the caller's environment.
    TfidfVectorizer = None  # type: ignore[assignment,misc]
    cosine_similarity = None  # type: ignore[assignment,misc]
    SKLEARN_AVAILABLE = False
else:
    SKLEARN_AVAILABLE = True


@dataclass(frozen=True)
class ModelStatus:
    """Runtime capabilities exposed in CLI output and machine-readable reports."""

    requested_engine: str
    active_engine: str
    spacy_available: bool
    spacy_model_loaded: bool
    spacy_model_name: str
    semantic_available: bool

    @property
    def description(self) -> str:
        if self.active_engine == "regex":
            return "Regex skill fallback"
        components = ["hybrid TF-IDF relevance model"]
        if self.spacy_model_loaded:
            components.append(f"spaCy model: {self.spacy_model_name}")
        elif self.spacy_available:
            components.append("spaCy tokenizer/PhraseMatcher (no downloaded language model)")
        else:
            components.append("regex skill fallback")
        return " + ".join(components)

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "requested_engine": self.requested_engine,
            "active_engine": self.active_engine,
            "spacy_available": self.spacy_available,
            "spacy_model_loaded": self.spacy_model_loaded,
            "spacy_model_name": self.spacy_model_name,
            "semantic_available": self.semantic_available,
            "description": self.description,
        }


class HybridRelevanceModel:
    """Use spaCy phrase matching plus a local TF-IDF/cosine relevance model.

    The TF-IDF vectorizer is fitted afresh to the supplied candidate profile and
    fetched jobs. Nothing is sent to an external AI service. If either optional
    dependency is absent, callers still receive deterministic regex matching.
    """

    def __init__(self, catalog: Mapping[str, Iterable[str]], nlp_config: Mapping[str, object]) -> None:
        self.catalog = {key.lower(): tuple(str(alias).lower() for alias in aliases) for key, aliases in catalog.items()}
        self.requested_engine = str(nlp_config.get("engine", "regex")).strip().lower()
        self.model_name = str(nlp_config.get("spacy_model", "en_core_web_sm")).strip()
        self._nlp = None
        self._matcher = None
        self._spacy_model_loaded = False

        if self.requested_engine in {"hybrid", "spacy"} and SPACY_AVAILABLE:
            self._setup_spacy()

        semantic_active = self.requested_engine in {"hybrid", "spacy"} and SKLEARN_AVAILABLE
        if self.requested_engine == "regex":
            active_engine = "regex"
        elif semantic_active:
            active_engine = "hybrid"
        else:
            active_engine = "regex"
        self.status = ModelStatus(
            requested_engine=self.requested_engine,
            active_engine=active_engine,
            spacy_available=SPACY_AVAILABLE,
            spacy_model_loaded=self._spacy_model_loaded,
            spacy_model_name=self.model_name,
            semantic_available=semantic_active,
        )

    def _setup_spacy(self) -> None:
        """Load a configured model when present, otherwise use a blank tokenizer.

        A blank English pipeline still provides robust tokenization and
        PhraseMatcher matching, while the project reports honestly that no
        statistical language model was installed.
        """

        assert spacy is not None and PhraseMatcher is not None
        try:
            self._nlp = spacy.load(self.model_name, disable=["parser", "ner", "textcat"])
            self._spacy_model_loaded = True
        except (OSError, IOError):
            self._nlp = spacy.blank("en")

        matcher = PhraseMatcher(self._nlp.vocab, attr="LOWER")
        for canonical, aliases in self.catalog.items():
            phrases = tuple(dict.fromkeys((canonical, *aliases)))
            patterns = [self._nlp.make_doc(phrase) for phrase in phrases if phrase.strip()]
            if patterns:
                matcher.add(canonical, patterns)
        self._matcher = matcher

    def extract_skills(self, text: str, catalog: Mapping[str, Iterable[str]] | None = None) -> tuple[str, ...]:
        """Extract catalog skills using spaCy phrases when available, else regex."""

        active_catalog = catalog or self.catalog
        if self._nlp is None or self._matcher is None:
            return regex_extract_skills(text, active_catalog)

        doc = self._nlp(text)
        matched_labels = {self._nlp.vocab.strings[match_id].lower() for match_id, _, _ in self._matcher(doc)}
        # Return catalog order for stable CSV output and deterministic tests.
        return tuple(skill.lower() for skill in active_catalog if skill.lower() in matched_labels)

    def _normalize_for_model(self, text: str) -> str:
        """Create a compact, technical-token-preserving text representation."""

        if self._nlp is not None:
            doc = self._nlp(text)
            terms: list[str] = []
            for token in doc:
                if token.is_space or token.is_punct or token.is_stop:
                    continue
                lemma = (token.lemma_ or token.lower_).strip().lower()
                if lemma and any(character.isalnum() for character in lemma):
                    terms.append(lemma)
            return " ".join(terms)
        return " ".join(re.findall(r"[a-zA-Z][a-zA-Z0-9+#./-]*", text.lower()))

    def semantic_scores(
        self,
        profile_text: str,
        profile_skills: Iterable[str],
        jobs: Sequence["Job"],
    ) -> dict[str, float]:
        """Fit TF-IDF over this review batch and return 0--100 cosine scores.

        Each job keeps its title and explicit skills in the model text so brief
        descriptions can still be meaningfully compared. The score is relevance
        evidence only; exact required-skill coverage remains separately visible.
        """

        if not self.status.semantic_available or not jobs:
            return {}
        assert TfidfVectorizer is not None and cosine_similarity is not None

        profile_document = " ".join(
            (
                profile_text,
                " ".join(profile_skills),
                " ".join(profile_skills),  # modestly emphasize declared skills
            )
        )
        job_documents = [
            " ".join(
                (
                    job.title,
                    job.title,  # job title is usually the most informative field
                    job.description,
                    " ".join(job.required_skills),
                    " ".join(job.preferred_skills),
                    job.category,
                )
            )
            for job in jobs
        ]
        normalized_documents = [self._normalize_for_model(profile_document)] + [
            self._normalize_for_model(document) for document in job_documents
        ]
        try:
            vectorizer = TfidfVectorizer(
                lowercase=True,
                stop_words="english",
                ngram_range=(1, 2),
                sublinear_tf=True,
                max_features=12_000,
            )
            matrix = vectorizer.fit_transform(normalized_documents)
        except ValueError:
            # For example, an all-stop-word or empty input; exact skill matching
            # remains available and gives a useful explanation to the user.
            return {job.id: 0.0 for job in jobs}

        similarities = cosine_similarity(matrix[0:1], matrix[1:]).ravel()
        return {
            job.id: round(max(0.0, min(1.0, float(score))) * 100, 1)
            for job, score in zip(jobs, similarities)
        }
