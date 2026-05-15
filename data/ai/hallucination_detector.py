"""
data/ai/hallucination_detector.py

Enterprise-grade hallucination detection engine for AI/LLM outputs.

This module provides a production-oriented framework to detect, score, explain,
and audit possible hallucinations in generated text using multiple complementary
signals:

- Claim extraction
- Evidence retrieval hooks
- Semantic similarity checks
- Numeric consistency checks
- Entity consistency checks
- Citation/source coverage checks
- Contradiction detection hooks
- Rule-based risk analysis
- Confidence calibration
- Structured audit events

The implementation is dependency-light by default and designed to integrate with
enterprise services such as vector databases, model gateways, audit pipelines,
observability stacks, and governance engines.

Recommended package position:
    data/ai/hallucination_detector.py

Python:
    3.10+
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class HallucinationDetectorError(Exception):
    """Base exception for hallucination detector errors."""


class InvalidDetectorInputError(HallucinationDetectorError):
    """Raised when detector input is invalid."""


class EvidenceRetrievalError(HallucinationDetectorError):
    """Raised when evidence retrieval fails unexpectedly."""


class DetectorConfigurationError(HallucinationDetectorError):
    """Raised when detector configuration is invalid."""


# =============================================================================
# Enums
# =============================================================================


class RiskLevel(str, Enum):
    """Risk severity classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClaimType(str, Enum):
    """Detected claim category."""

    FACTUAL = "factual"
    NUMERIC = "numeric"
    TEMPORAL = "temporal"
    ENTITY = "entity"
    CAUSAL = "causal"
    COMPARATIVE = "comparative"
    LEGAL = "legal"
    MEDICAL = "medical"
    FINANCIAL = "financial"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    """Relationship between a claim and its evidence."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_CHECKED = "not_checked"


class DetectionSignal(str, Enum):
    """Signal identifiers used in scoring and explanation."""

    SOURCE_COVERAGE = "source_coverage"
    SEMANTIC_SUPPORT = "semantic_support"
    CONTRADICTION = "contradiction"
    NUMERIC_MISMATCH = "numeric_mismatch"
    ENTITY_MISMATCH = "entity_mismatch"
    TEMPORAL_UNCERTAINTY = "temporal_uncertainty"
    HIGH_STAKES_DOMAIN = "high_stakes_domain"
    OVERCONFIDENT_LANGUAGE = "overconfident_language"
    CITATION_MISSING = "citation_missing"
    CLAIM_DENSITY = "claim_density"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class DetectorConfig:
    """Configuration for hallucination detection."""

    min_claim_length: int = 24
    max_claims: int = 80
    semantic_support_threshold: float = 0.68
    partial_support_threshold: float = 0.50
    contradiction_threshold: float = 0.72
    numeric_tolerance_ratio: float = 0.02
    require_citations_for_factual_claims: bool = True
    enable_claim_extraction: bool = True
    enable_evidence_retrieval: bool = True
    enable_semantic_similarity: bool = True
    enable_numeric_consistency: bool = True
    enable_entity_consistency: bool = True
    enable_rule_based_risk: bool = True
    fail_open_on_retrieval_error: bool = True
    include_raw_evidence: bool = False
    audit_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.min_claim_length < 1:
            raise DetectorConfigurationError("min_claim_length must be >= 1")
        if self.max_claims < 1:
            raise DetectorConfigurationError("max_claims must be >= 1")
        for name in (
            "semantic_support_threshold",
            "partial_support_threshold",
            "contradiction_threshold",
            "numeric_tolerance_ratio",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise DetectorConfigurationError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class DetectionContext:
    """Context supplied by caller for better detection and auditability."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    model_name: Optional[str] = None
    prompt_hash: Optional[str] = None
    locale: Optional[str] = None
    domain: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceDocument:
    """Evidence document returned by a retriever."""

    id: str
    text: str
    source: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    score: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """A detected claim from model output."""

    id: str
    text: str
    claim_type: ClaimType = ClaimType.UNKNOWN
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    requires_citation: bool = True
    high_stakes: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalScore:
    """A score for a specific detection signal."""

    signal: DetectionSignal
    score: float
    weight: float
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimAssessment:
    """Assessment for one claim."""

    claim: Claim
    evidence_status: EvidenceStatus
    hallucination_probability: float
    risk_level: RiskLevel
    signals: Sequence[SignalScore]
    evidence: Sequence[EvidenceDocument] = field(default_factory=list)
    explanation: str = ""


@dataclass(frozen=True)
class HallucinationReport:
    """Final structured report for one generated response."""

    request_id: str
    detector_version: str
    created_at: str
    output_hash: str
    overall_hallucination_probability: float
    risk_level: RiskLevel
    claims_checked: int
    supported_claims: int
    partially_supported_claims: int
    unsupported_claims: int
    contradicted_claims: int
    assessments: Sequence[ClaimAssessment]
    summary: str
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# =============================================================================
# Protocols / Interfaces
# =============================================================================


class EvidenceRetriever(Protocol):
    """Protocol for external evidence retrievers."""

    def retrieve(self, claim: Claim, context: DetectionContext, limit: int = 5) -> Sequence[EvidenceDocument]:
        """Return evidence documents relevant to the claim."""


class EmbeddingModel(Protocol):
    """Protocol for embedding models."""

    def embed(self, text: str) -> Sequence[float]:
        """Return a vector embedding for a text."""


class ContradictionModel(Protocol):
    """Protocol for NLI/contradiction models."""

    def contradiction_score(self, claim: str, evidence: str) -> float:
        """Return probability-like score that evidence contradicts the claim."""


class AuditSink(Protocol):
    """Protocol for audit sinks."""

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def safe_mean(values: Iterable[float], default: float = 0.0) -> float:
    values = list(values)
    if not values:
        return default
    return float(statistics.mean(values))


def tokenize_words(text: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower(), flags=re.UNICODE)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return clamp(dot / (norm_a * norm_b), -1.0, 1.0)


def jaccard_similarity(a: str, b: str) -> float:
    set_a = set(tokenize_words(a))
    set_b = set(tokenize_words(b))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def extract_numbers(text: str) -> List[float]:
    values: List[float] = []
    for match in re.findall(r"(?<!\w)[+-]?(?:\d+[\.,]?\d*|\d*[\.,]\d+)(?:%|\b)", text):
        normalized = match.replace("%", "").replace(",", ".")
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return values


def relative_numeric_difference(a: float, b: float) -> float:
    denominator = max(abs(a), abs(b), 1.0)
    return abs(a - b) / denominator


# =============================================================================
# Claim Extraction
# =============================================================================


class ClaimExtractor:
    """Heuristic claim extractor.

    In enterprise deployments this class can be replaced by a dedicated LLM/NLP
    claim extraction service. This default implementation intentionally avoids
    heavy dependencies and focuses on deterministic behavior.
    """

    SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

    HIGH_STAKES_KEYWORDS = {
        ClaimType.MEDICAL: (
            "diagnóstico",
            "tratamento",
            "medicamento",
            "doença",
            "sintoma",
            "cirurgia",
            "dose",
            "medical",
            "treatment",
            "diagnosis",
            "drug",
        ),
        ClaimType.LEGAL: (
            "lei",
            "legal",
            "contrato",
            "processo",
            "tribunal",
            "direito",
            "regulamento",
            "compliance",
            "law",
            "court",
        ),
        ClaimType.FINANCIAL: (
            "investimento",
            "ação",
            "juros",
            "lucro",
            "prejuízo",
            "receita",
            "valuation",
            "stock",
            "investment",
            "revenue",
        ),
    }

    TEMPORAL_KEYWORDS = (
        "hoje",
        "ontem",
        "amanhã",
        "atualmente",
        "recentemente",
        "latest",
        "current",
        "currently",
        "recent",
        "2024",
        "2025",
        "2026",
    )

    CAUSAL_MARKERS = ("porque", "devido", "causa", "resulta", "therefore", "because", "caused by", "leads to")
    COMPARATIVE_MARKERS = ("maior", "menor", "melhor", "pior", "mais que", "less than", "greater than", "best", "worst")

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config

    def extract(self, text: str) -> List[Claim]:
        if not text or not text.strip():
            return []

        claims: List[Claim] = []
        for sentence, start, end in self._iter_sentences(text):
            sentence = sentence.strip()
            if len(sentence) < self.config.min_claim_length:
                continue
            if not self._looks_like_claim(sentence):
                continue

            claim_type = self._classify(sentence)
            high_stakes = claim_type in {ClaimType.MEDICAL, ClaimType.LEGAL, ClaimType.FINANCIAL}
            requires_citation = claim_type in {
                ClaimType.FACTUAL,
                ClaimType.NUMERIC,
                ClaimType.TEMPORAL,
                ClaimType.ENTITY,
                ClaimType.CAUSAL,
                ClaimType.COMPARATIVE,
                ClaimType.LEGAL,
                ClaimType.MEDICAL,
                ClaimType.FINANCIAL,
            }

            claims.append(
                Claim(
                    id=f"claim_{len(claims) + 1:04d}",
                    text=sentence,
                    claim_type=claim_type,
                    start_char=start,
                    end_char=end,
                    requires_citation=requires_citation,
                    high_stakes=high_stakes,
                )
            )
            if len(claims) >= self.config.max_claims:
                break

        return claims

    def _iter_sentences(self, text: str) -> Iterable[Tuple[str, int, int]]:
        cursor = 0
        for part in self.SENTENCE_SPLIT_RE.split(text):
            stripped = part.strip()
            if not stripped:
                cursor += len(part)
                continue
            start = text.find(stripped, cursor)
            end = start + len(stripped)
            cursor = end
            yield stripped, start, end

    def _looks_like_claim(self, sentence: str) -> bool:
        lower = sentence.lower()
        if sentence.endswith("?"):
            return False
        if re.search(r"\b(é|são|foi|foram|tem|possui|causa|resulta|is|are|was|were|has|causes|contains)\b", lower):
            return True
        if extract_numbers(sentence):
            return True
        return False

    def _classify(self, sentence: str) -> ClaimType:
        lower = sentence.lower()
        for claim_type, keywords in self.HIGH_STAKES_KEYWORDS.items():
            if any(keyword in lower for keyword in keywords):
                return claim_type
        if extract_numbers(sentence):
            return ClaimType.NUMERIC
        if any(keyword in lower for keyword in self.TEMPORAL_KEYWORDS):
            return ClaimType.TEMPORAL
        if any(marker in lower for marker in self.CAUSAL_MARKERS):
            return ClaimType.CAUSAL
        if any(marker in lower for marker in self.COMPARATIVE_MARKERS):
            return ClaimType.COMPARATIVE
        if re.search(r"\b[A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)+\b", sentence):
            return ClaimType.ENTITY
        return ClaimType.FACTUAL


# =============================================================================
# Default Adapters
# =============================================================================


class InMemoryEvidenceRetriever:
    """Simple retriever for local evidence lists.

    Useful for tests, offline pipelines, or when evidence is already supplied by
    an upstream RAG process.
    """

    def __init__(self, documents: Sequence[EvidenceDocument]) -> None:
        self.documents = list(documents)

    def retrieve(self, claim: Claim, context: DetectionContext, limit: int = 5) -> Sequence[EvidenceDocument]:
        scored: List[EvidenceDocument] = []
        for doc in self.documents:
            score = jaccard_similarity(claim.text, doc.text)
            scored.append(
                EvidenceDocument(
                    id=doc.id,
                    text=doc.text,
                    source=doc.source,
                    title=doc.title,
                    url=doc.url,
                    score=score,
                    metadata=doc.metadata,
                )
            )
        return sorted(scored, key=lambda item: item.score or 0.0, reverse=True)[:limit]


class HashingEmbeddingModel:
    """Small deterministic embedding fallback based on feature hashing.

    This is not a replacement for a semantic embedding model. It exists so the
    detector can run without external dependencies. For production, inject a
    proper embedding provider.
    """

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 16:
            raise ValueError("dimensions must be >= 16")
        self.dimensions = dimensions

    def embed(self, text: str) -> Sequence[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize_words(text):
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            idx = int(digest[:8], 16) % self.dimensions
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]


class KeywordContradictionModel:
    """Lightweight contradiction heuristic.

    Production systems should replace this with a calibrated NLI model.
    """

    NEGATION_MARKERS = (
        "não",
        "nunca",
        "jamais",
        "sem",
        "not",
        "never",
        "no longer",
        "false",
        "incorrect",
    )

    def contradiction_score(self, claim: str, evidence: str) -> float:
        claim_tokens = set(tokenize_words(claim))
        evidence_tokens = set(tokenize_words(evidence))
        overlap = len(claim_tokens & evidence_tokens) / max(len(claim_tokens), 1)
        claim_negated = any(marker in claim.lower() for marker in self.NEGATION_MARKERS)
        evidence_negated = any(marker in evidence.lower() for marker in self.NEGATION_MARKERS)
        if overlap > 0.45 and claim_negated != evidence_negated:
            return 0.74
        return 0.0


class LoggingAuditSink:
    """Audit sink that writes structured events to Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("audit_event=%s payload=%s", event_name, json.dumps(payload, ensure_ascii=False, default=str))


# =============================================================================
# Detection Engine
# =============================================================================


class HallucinationDetector:
    """Enterprise hallucination detection engine."""

    OVERCONFIDENT_PATTERNS = re.compile(
        r"\b(garantidamente|com certeza|sem dúvida|sempre|nunca|definitivamente|obviamente|"
        r"guaranteed|certainly|undoubtedly|always|never|definitely|obviously)\b",
        flags=re.IGNORECASE,
    )

    CITATION_PATTERNS = re.compile(
        r"(https?://\S+|\[[0-9]+\]|\([^)]*\d{4}[^)]*\)|fonte:|source:|doi:)" ,
        flags=re.IGNORECASE,
    )

    def __init__(
        self,
        config: Optional[DetectorConfig] = None,
        evidence_retriever: Optional[EvidenceRetriever] = None,
        embedding_model: Optional[EmbeddingModel] = None,
        contradiction_model: Optional[ContradictionModel] = None,
        audit_sink: Optional[AuditSink] = None,
        claim_extractor: Optional[ClaimExtractor] = None,
    ) -> None:
        self.config = config or DetectorConfig()
        self.config.validate()
        self.evidence_retriever = evidence_retriever
        self.embedding_model = embedding_model or HashingEmbeddingModel()
        self.contradiction_model = contradiction_model or KeywordContradictionModel()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.claim_extractor = claim_extractor or ClaimExtractor(self.config)

    def detect(
        self,
        output_text: str,
        *,
        context: Optional[DetectionContext] = None,
        evidence_documents: Optional[Sequence[EvidenceDocument]] = None,
        claims: Optional[Sequence[Claim]] = None,
    ) -> HallucinationReport:
        """Detect hallucination risk in generated output.

        Args:
            output_text: Text produced by an AI system.
            context: Optional request context.
            evidence_documents: Optional local evidence documents. If supplied,
                they are used through an in-memory retriever unless a custom
                retriever is already configured.
            claims: Optional pre-extracted claims.

        Returns:
            HallucinationReport with risk scores, per-claim assessments and
            recommendations.
        """

        if output_text is None or not isinstance(output_text, str):
            raise InvalidDetectorInputError("output_text must be a string")

        context = context or DetectionContext()
        started = time.perf_counter()
        output_hash = stable_hash(output_text)

        active_retriever = self.evidence_retriever
        if evidence_documents is not None and active_retriever is None:
            active_retriever = InMemoryEvidenceRetriever(evidence_documents)

        extracted_claims = list(claims) if claims is not None else self._extract_claims(output_text)
        assessments = [
            self._assess_claim(claim, context, active_retriever)
            for claim in extracted_claims
        ]

        report = self._build_report(
            context=context,
            output_hash=output_hash,
            assessments=assessments,
            duration_ms=(time.perf_counter() - started) * 1000,
        )

        self._audit("hallucination_detection_completed", {
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "application": context.application,
            "model_name": context.model_name,
            "claims_checked": report.claims_checked,
            "overall_hallucination_probability": report.overall_hallucination_probability,
            "risk_level": report.risk_level.value,
            "duration_ms": report.metadata.get("duration_ms"),
        })

        return report

    def _extract_claims(self, output_text: str) -> List[Claim]:
        if not self.config.enable_claim_extraction:
            return []
        return self.claim_extractor.extract(output_text)

    def _assess_claim(
        self,
        claim: Claim,
        context: DetectionContext,
        retriever: Optional[EvidenceRetriever],
    ) -> ClaimAssessment:
        evidence: Sequence[EvidenceDocument] = []
        retrieval_failed = False

        if self.config.enable_evidence_retrieval and retriever is not None:
            try:
                evidence = retriever.retrieve(claim, context, limit=5)
            except Exception as exc:  # noqa: BLE001 - enterprise detector should isolate failures
                retrieval_failed = True
                logger.exception("Evidence retrieval failed for claim_id=%s", claim.id)
                if not self.config.fail_open_on_retrieval_error:
                    raise EvidenceRetrievalError(str(exc)) from exc

        signals: List[SignalScore] = []

        source_coverage = self._score_source_coverage(claim, evidence, retrieval_failed)
        signals.append(source_coverage)

        if self.config.enable_semantic_similarity:
            signals.append(self._score_semantic_support(claim, evidence))

        if self.config.enable_numeric_consistency:
            numeric_signal = self._score_numeric_consistency(claim, evidence)
            if numeric_signal:
                signals.append(numeric_signal)

        if self.config.enable_entity_consistency:
            entity_signal = self._score_entity_consistency(claim, evidence)
            if entity_signal:
                signals.append(entity_signal)

        contradiction_signal = self._score_contradiction(claim, evidence)
        if contradiction_signal:
            signals.append(contradiction_signal)

        if self.config.enable_rule_based_risk:
            signals.extend(self._score_rule_based_risk(claim))

        evidence_status = self._infer_evidence_status(signals)
        probability = self._aggregate_probability(signals)
        risk_level = self._risk_level(probability, claim.high_stakes)
        explanation = self._explain(claim, evidence_status, probability, signals)

        visible_evidence = evidence if self.config.include_raw_evidence else tuple(
            EvidenceDocument(
                id=doc.id,
                text="",
                source=doc.source,
                title=doc.title,
                url=doc.url,
                score=doc.score,
                metadata=doc.metadata,
            )
            for doc in evidence
        )

        return ClaimAssessment(
            claim=claim,
            evidence_status=evidence_status,
            hallucination_probability=probability,
            risk_level=risk_level,
            signals=tuple(signals),
            evidence=tuple(visible_evidence),
            explanation=explanation,
        )

    def _score_source_coverage(
        self,
        claim: Claim,
        evidence: Sequence[EvidenceDocument],
        retrieval_failed: bool,
    ) -> SignalScore:
        if retrieval_failed:
            return SignalScore(
                signal=DetectionSignal.SOURCE_COVERAGE,
                score=0.45,
                weight=0.22,
                reason="Evidence retrieval failed; risk could not be fully evaluated.",
            )
        if not evidence:
            return SignalScore(
                signal=DetectionSignal.SOURCE_COVERAGE,
                score=0.80 if claim.requires_citation else 0.30,
                weight=0.25,
                reason="No supporting evidence was retrieved for the claim.",
            )
        best = max((doc.score or 0.0) for doc in evidence)
        risk = 1.0 - clamp(best)
        return SignalScore(
            signal=DetectionSignal.SOURCE_COVERAGE,
            score=risk,
            weight=0.18,
            reason=f"Best retrieved evidence lexical relevance score is {best:.2f}.",
            metadata={"best_evidence_score": best, "evidence_count": len(evidence)},
        )

    def _score_semantic_support(self, claim: Claim, evidence: Sequence[EvidenceDocument]) -> SignalScore:
        if not evidence:
            return SignalScore(
                signal=DetectionSignal.SEMANTIC_SUPPORT,
                score=0.75,
                weight=0.26,
                reason="Semantic support could not be established because evidence is missing.",
            )

        claim_vec = self.embedding_model.embed(claim.text)
        similarities = [cosine_similarity(claim_vec, self.embedding_model.embed(doc.text)) for doc in evidence]
        best_similarity = max(similarities, default=0.0)

        if best_similarity >= self.config.semantic_support_threshold:
            risk = 0.10
        elif best_similarity >= self.config.partial_support_threshold:
            risk = 0.40
        else:
            risk = 0.72

        return SignalScore(
            signal=DetectionSignal.SEMANTIC_SUPPORT,
            score=risk,
            weight=0.30,
            reason=f"Best semantic support score is {best_similarity:.2f}.",
            metadata={"best_similarity": best_similarity},
        )

    def _score_numeric_consistency(
        self,
        claim: Claim,
        evidence: Sequence[EvidenceDocument],
    ) -> Optional[SignalScore]:
        claim_numbers = extract_numbers(claim.text)
        if not claim_numbers:
            return None
        if not evidence:
            return SignalScore(
                signal=DetectionSignal.NUMERIC_MISMATCH,
                score=0.65,
                weight=0.16,
                reason="Claim contains numeric values but no evidence was available for comparison.",
                metadata={"claim_numbers": claim_numbers},
            )

        evidence_numbers: List[float] = []
        for doc in evidence:
            evidence_numbers.extend(extract_numbers(doc.text))

        if not evidence_numbers:
            return SignalScore(
                signal=DetectionSignal.NUMERIC_MISMATCH,
                score=0.58,
                weight=0.14,
                reason="Claim contains numeric values but retrieved evidence contains no comparable numbers.",
                metadata={"claim_numbers": claim_numbers},
            )

        mismatches = 0
        comparisons = 0
        for claim_num in claim_numbers:
            nearest_diff = min(relative_numeric_difference(claim_num, ev_num) for ev_num in evidence_numbers)
            comparisons += 1
            if nearest_diff > self.config.numeric_tolerance_ratio:
                mismatches += 1

        mismatch_ratio = mismatches / max(comparisons, 1)
        return SignalScore(
            signal=DetectionSignal.NUMERIC_MISMATCH,
            score=clamp(mismatch_ratio),
            weight=0.18,
            reason=f"Numeric mismatch ratio is {mismatch_ratio:.2f}.",
            metadata={"claim_numbers": claim_numbers, "evidence_numbers": evidence_numbers[:25]},
        )

    def _score_entity_consistency(
        self,
        claim: Claim,
        evidence: Sequence[EvidenceDocument],
    ) -> Optional[SignalScore]:
        entities = self._extract_simple_entities(claim.text)
        if not entities:
            return None
        if not evidence:
            return SignalScore(
                signal=DetectionSignal.ENTITY_MISMATCH,
                score=0.55,
                weight=0.12,
                reason="Claim contains named entities but no evidence was available.",
                metadata={"entities": sorted(entities)},
            )

        evidence_text = "\n".join(doc.text for doc in evidence).lower()
        missing = [entity for entity in entities if entity.lower() not in evidence_text]
        missing_ratio = len(missing) / max(len(entities), 1)
        return SignalScore(
            signal=DetectionSignal.ENTITY_MISMATCH,
            score=clamp(missing_ratio),
            weight=0.12,
            reason=f"Entity mismatch ratio is {missing_ratio:.2f}.",
            metadata={"entities": sorted(entities), "missing_entities": sorted(missing)},
        )

    def _score_contradiction(
        self,
        claim: Claim,
        evidence: Sequence[EvidenceDocument],
    ) -> Optional[SignalScore]:
        if not evidence:
            return None
        scores = [self.contradiction_model.contradiction_score(claim.text, doc.text) for doc in evidence]
        best = max(scores, default=0.0)
        if best < 0.35:
            return None
        return SignalScore(
            signal=DetectionSignal.CONTRADICTION,
            score=clamp(best),
            weight=0.35,
            reason=f"Potential contradiction score is {best:.2f}.",
            metadata={"best_contradiction_score": best},
        )

    def _score_rule_based_risk(self, claim: Claim) -> List[SignalScore]:
        signals: List[SignalScore] = []

        if claim.high_stakes:
            signals.append(
                SignalScore(
                    signal=DetectionSignal.HIGH_STAKES_DOMAIN,
                    score=0.65,
                    weight=0.12,
                    reason=f"Claim belongs to a high-stakes domain: {claim.claim_type.value}.",
                )
            )

        if self.OVERCONFIDENT_PATTERNS.search(claim.text):
            signals.append(
                SignalScore(
                    signal=DetectionSignal.OVERCONFIDENT_LANGUAGE,
                    score=0.42,
                    weight=0.08,
                    reason="Claim uses overconfident language.",
                )
            )

        if (
            self.config.require_citations_for_factual_claims
            and claim.requires_citation
            and not self.CITATION_PATTERNS.search(claim.text)
        ):
            signals.append(
                SignalScore(
                    signal=DetectionSignal.CITATION_MISSING,
                    score=0.40,
                    weight=0.10,
                    reason="Claim appears factual but has no explicit citation marker.",
                )
            )

        if claim.claim_type == ClaimType.TEMPORAL:
            signals.append(
                SignalScore(
                    signal=DetectionSignal.TEMPORAL_UNCERTAINTY,
                    score=0.36,
                    weight=0.08,
                    reason="Temporal/currentness claim may require fresh verification.",
                )
            )

        return signals

    def _infer_evidence_status(self, signals: Sequence[SignalScore]) -> EvidenceStatus:
        contradiction = max(
            (s.score for s in signals if s.signal == DetectionSignal.CONTRADICTION),
            default=0.0,
        )
        if contradiction >= self.config.contradiction_threshold:
            return EvidenceStatus.CONTRADICTED

        semantic = next((s for s in signals if s.signal == DetectionSignal.SEMANTIC_SUPPORT), None)
        source = next((s for s in signals if s.signal == DetectionSignal.SOURCE_COVERAGE), None)

        semantic_risk = semantic.score if semantic else 0.5
        source_risk = source.score if source else 0.5

        if semantic_risk <= 0.20 and source_risk <= 0.35:
            return EvidenceStatus.SUPPORTED
        if semantic_risk <= 0.45 or source_risk <= 0.50:
            return EvidenceStatus.PARTIALLY_SUPPORTED
        return EvidenceStatus.UNSUPPORTED

    def _aggregate_probability(self, signals: Sequence[SignalScore]) -> float:
        if not signals:
            return 0.0
        weighted_sum = sum(signal.score * signal.weight for signal in signals)
        total_weight = sum(signal.weight for signal in signals)
        if total_weight <= 0:
            return safe_mean(signal.score for signal in signals)
        return clamp(weighted_sum / total_weight)

    def _risk_level(self, probability: float, high_stakes: bool = False) -> RiskLevel:
        adjusted = probability + (0.08 if high_stakes else 0.0)
        if adjusted >= 0.82:
            return RiskLevel.CRITICAL
        if adjusted >= 0.62:
            return RiskLevel.HIGH
        if adjusted >= 0.36:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _build_report(
        self,
        *,
        context: DetectionContext,
        output_hash: str,
        assessments: Sequence[ClaimAssessment],
        duration_ms: float,
    ) -> HallucinationReport:
        claims_checked = len(assessments)
        supported = sum(1 for a in assessments if a.evidence_status == EvidenceStatus.SUPPORTED)
        partial = sum(1 for a in assessments if a.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED)
        unsupported = sum(1 for a in assessments if a.evidence_status == EvidenceStatus.UNSUPPORTED)
        contradicted = sum(1 for a in assessments if a.evidence_status == EvidenceStatus.CONTRADICTED)

        if assessments:
            overall = safe_mean(a.hallucination_probability for a in assessments)
            max_risk = max(a.hallucination_probability for a in assessments)
            overall = clamp((overall * 0.75) + (max_risk * 0.25))
            high_stakes = any(a.claim.high_stakes for a in assessments)
        else:
            overall = 0.0
            high_stakes = False

        risk_level = self._risk_level(overall, high_stakes)
        summary = self._build_summary(claims_checked, supported, partial, unsupported, contradicted, overall, risk_level)
        recommendations = self._build_recommendations(assessments, risk_level)

        return HallucinationReport(
            request_id=context.request_id,
            detector_version=self.config.version,
            created_at=utc_now_iso(),
            output_hash=output_hash,
            overall_hallucination_probability=overall,
            risk_level=risk_level,
            claims_checked=claims_checked,
            supported_claims=supported,
            partially_supported_claims=partial,
            unsupported_claims=unsupported,
            contradicted_claims=contradicted,
            assessments=tuple(assessments),
            summary=summary,
            recommendations=tuple(recommendations),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "model_name": context.model_name,
                "domain": context.domain,
                "duration_ms": round(duration_ms, 3),
            },
        )

    def _build_summary(
        self,
        claims_checked: int,
        supported: int,
        partial: int,
        unsupported: int,
        contradicted: int,
        overall: float,
        risk_level: RiskLevel,
    ) -> str:
        if claims_checked == 0:
            return "No checkable factual claims were detected in the output."
        return (
            f"Detected {claims_checked} checkable claim(s). "
            f"Supported: {supported}; partially supported: {partial}; "
            f"unsupported: {unsupported}; contradicted: {contradicted}. "
            f"Overall hallucination probability is {overall:.2f}, classified as {risk_level.value}."
        )

    def _build_recommendations(
        self,
        assessments: Sequence[ClaimAssessment],
        risk_level: RiskLevel,
    ) -> List[str]:
        recommendations: List[str] = []

        if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            recommendations.append("Block automatic publication and require human review before use.")
        elif risk_level == RiskLevel.MEDIUM:
            recommendations.append("Add citations or retrieve stronger evidence before publishing.")
        else:
            recommendations.append("Risk appears low, but keep standard audit logging enabled.")

        if any(a.evidence_status == EvidenceStatus.CONTRADICTED for a in assessments):
            recommendations.append("Rewrite or remove contradicted claims and re-run verification.")
        if any(a.evidence_status == EvidenceStatus.UNSUPPORTED for a in assessments):
            recommendations.append("Attach reliable sources for unsupported claims or mark them as uncertain.")
        if any(a.claim.high_stakes for a in assessments):
            recommendations.append("High-stakes claims require domain expert validation.")
        if any(any(s.signal == DetectionSignal.NUMERIC_MISMATCH and s.score > 0.4 for s in a.signals) for a in assessments):
            recommendations.append("Review numeric values against authoritative records.")

        return recommendations

    def _explain(
        self,
        claim: Claim,
        status: EvidenceStatus,
        probability: float,
        signals: Sequence[SignalScore],
    ) -> str:
        top = sorted(signals, key=lambda s: s.score * s.weight, reverse=True)[:3]
        reasons = "; ".join(signal.reason for signal in top)
        return (
            f"Claim '{claim.id}' is classified as {status.value} with hallucination "
            f"probability {probability:.2f}. Main factors: {reasons}"
        )

    def _extract_simple_entities(self, text: str) -> set[str]:
        candidates = re.findall(r"\b[A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)*\b", text)
        stop_entities = {"O", "A", "Os", "As", "The", "This", "That", "Para", "Em"}
        return {candidate.strip() for candidate in candidates if candidate.strip() not in stop_entities}

    def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled or self.audit_sink is None:
            return
        try:
            self.audit_sink.emit(event_name, payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit audit event: %s", event_name)


# =============================================================================
# Batch / Pipeline API
# =============================================================================


@dataclass(frozen=True)
class BatchDetectionItem:
    """One item for batch hallucination detection."""

    output_text: str
    context: DetectionContext = field(default_factory=DetectionContext)
    evidence_documents: Sequence[EvidenceDocument] = field(default_factory=tuple)


@dataclass(frozen=True)
class BatchDetectionResult:
    """Batch detection output."""

    batch_id: str
    created_at: str
    total_items: int
    reports: Sequence[HallucinationReport]
    failed_items: Sequence[Mapping[str, Any]] = field(default_factory=tuple)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent)


class BatchHallucinationDetector:
    """Batch wrapper around HallucinationDetector."""

    def __init__(self, detector: HallucinationDetector) -> None:
        self.detector = detector

    def detect_many(self, items: Sequence[BatchDetectionItem], *, continue_on_error: bool = True) -> BatchDetectionResult:
        batch_id = str(uuid.uuid4())
        reports: List[HallucinationReport] = []
        failed: List[Mapping[str, Any]] = []

        for index, item in enumerate(items):
            try:
                reports.append(
                    self.detector.detect(
                        item.output_text,
                        context=item.context,
                        evidence_documents=item.evidence_documents,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Batch hallucination detection failed for index=%s", index)
                failed.append({
                    "index": index,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                })
                if not continue_on_error:
                    raise

        return BatchDetectionResult(
            batch_id=batch_id,
            created_at=utc_now_iso(),
            total_items=len(items),
            reports=tuple(reports),
            failed_items=tuple(failed),
        )


# =============================================================================
# Policy Gate
# =============================================================================


@dataclass(frozen=True)
class HallucinationGateDecision:
    """Decision for automated publishing or downstream processing."""

    allowed: bool
    decision: str
    reason: str
    risk_level: RiskLevel
    hallucination_probability: float
    required_actions: Sequence[str] = field(default_factory=tuple)


class HallucinationPolicyGate:
    """Enterprise policy gate for hallucination reports."""

    def __init__(
        self,
        *,
        block_threshold: float = 0.72,
        review_threshold: float = 0.42,
        block_on_contradiction: bool = True,
        block_high_stakes_without_support: bool = True,
    ) -> None:
        self.block_threshold = block_threshold
        self.review_threshold = review_threshold
        self.block_on_contradiction = block_on_contradiction
        self.block_high_stakes_without_support = block_high_stakes_without_support

    def decide(self, report: HallucinationReport) -> HallucinationGateDecision:
        actions: List[str] = []

        has_contradiction = report.contradicted_claims > 0
        has_high_stakes_unsupported = any(
            assessment.claim.high_stakes
            and assessment.evidence_status in {EvidenceStatus.UNSUPPORTED, EvidenceStatus.CONTRADICTED}
            for assessment in report.assessments
        )

        if self.block_on_contradiction and has_contradiction:
            actions.append("Remove or correct contradicted claims.")
            return HallucinationGateDecision(
                allowed=False,
                decision="blocked",
                reason="One or more claims are contradicted by evidence.",
                risk_level=report.risk_level,
                hallucination_probability=report.overall_hallucination_probability,
                required_actions=tuple(actions),
            )

        if self.block_high_stakes_without_support and has_high_stakes_unsupported:
            actions.append("Obtain expert review for high-stakes unsupported claims.")
            return HallucinationGateDecision(
                allowed=False,
                decision="blocked",
                reason="High-stakes claim lacks sufficient support.",
                risk_level=report.risk_level,
                hallucination_probability=report.overall_hallucination_probability,
                required_actions=tuple(actions),
            )

        if report.overall_hallucination_probability >= self.block_threshold:
            actions.append("Run retrieval augmentation and regenerate answer.")
            return HallucinationGateDecision(
                allowed=False,
                decision="blocked",
                reason="Overall hallucination probability exceeded block threshold.",
                risk_level=report.risk_level,
                hallucination_probability=report.overall_hallucination_probability,
                required_actions=tuple(actions),
            )

        if report.overall_hallucination_probability >= self.review_threshold:
            actions.append("Human review recommended before publication.")
            return HallucinationGateDecision(
                allowed=True,
                decision="review_required",
                reason="Hallucination probability exceeded review threshold.",
                risk_level=report.risk_level,
                hallucination_probability=report.overall_hallucination_probability,
                required_actions=tuple(actions),
            )

        return HallucinationGateDecision(
            allowed=True,
            decision="approved",
            reason="Hallucination risk is within acceptable threshold.",
            risk_level=report.risk_level,
            hallucination_probability=report.overall_hallucination_probability,
            required_actions=tuple(actions),
        )


# =============================================================================
# Factory
# =============================================================================


def build_default_hallucination_detector(
    *,
    evidence_documents: Optional[Sequence[EvidenceDocument]] = None,
    config_overrides: Optional[Mapping[str, Any]] = None,
) -> HallucinationDetector:
    """Build a default detector with optional local evidence."""

    config_data = asdict(DetectorConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = DetectorConfig(**config_data)

    retriever = InMemoryEvidenceRetriever(evidence_documents or []) if evidence_documents is not None else None
    return HallucinationDetector(config=config, evidence_retriever=retriever)


# =============================================================================
# CLI Example / Manual Test
# =============================================================================


def _demo() -> None:
    logging.basicConfig(level=logging.INFO)

    evidence = [
        EvidenceDocument(
            id="doc_001",
            title="Enterprise AI Policy",
            source="internal_policy",
            text="All financial recommendations generated by AI must be reviewed by a qualified analyst before publication.",
            score=1.0,
        ),
        EvidenceDocument(
            id="doc_002",
            title="Revenue Report",
            source="finance_dw",
            text="The company reported revenue of 10.2 million in Q4 2025.",
            score=1.0,
        ),
    ]

    output = (
        "The company reported revenue of 12.5 million in Q4 2025. "
        "This investment is guaranteed to be profitable. "
        "All financial recommendations generated by AI must be reviewed by a qualified analyst before publication."
    )

    detector = build_default_hallucination_detector(evidence_documents=evidence)
    report = detector.detect(
        output,
        context=DetectionContext(
            tenant_id="demo",
            application="ai-platform",
            model_name="example-model",
            domain="finance",
        ),
    )

    gate = HallucinationPolicyGate()
    decision = gate.decide(report)

    print(report.to_json(indent=2))
    print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _demo()
