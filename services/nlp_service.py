"""
kwanza-ai-core/services/nlp_service.py

Enterprise-grade NLP service layer.

Purpose
-------
Centralize natural-language processing capabilities for the Kwanza AI Core:
classification, entity extraction, embeddings, semantic search preparation,
sentiment analysis, summarization, language detection, PII masking and document
routing.

Design goals
------------
- Async-first, framework-agnostic service API.
- Provider/model adapter abstraction.
- Deterministic local fallback for development and resilience.
- Tenant-aware request metadata.
- Privacy-first text handling with optional PII masking.
- Batch processing support.
- Cache, timeout, retry, audit and metrics hooks.
- Explainable outputs with reason/evidence metadata.

This module is self-contained and can be wired into FastAPI endpoints, workers,
Kafka consumers, batch jobs, orchestration pipelines or internal services.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import statistics
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class NLPServiceError(RuntimeError):
    """Base exception for NLP service failures."""


class NLPValidationError(NLPServiceError):
    """Raised when an NLP request is invalid."""


class NLPProviderError(NLPServiceError):
    """Raised when an NLP provider fails."""


class NLPTimeoutError(NLPProviderError):
    """Raised when an NLP provider times out."""


# =============================================================================
# Enums and data models
# =============================================================================


class NLPTask(str, Enum):
    NORMALIZE = "normalize"
    LANGUAGE_DETECTION = "language_detection"
    CLASSIFICATION = "classification"
    SENTIMENT = "sentiment"
    ENTITY_EXTRACTION = "entity_extraction"
    KEYWORD_EXTRACTION = "keyword_extraction"
    SUMMARIZATION = "summarization"
    EMBEDDING = "embedding"
    DOCUMENT_ROUTING = "document_routing"
    PII_DETECTION = "pii_detection"
    PII_MASKING = "pii_masking"
    INTENT_DETECTION = "intent_detection"
    TOPIC_MODELING = "topic_modeling"
    GENERIC = "generic"


class SentimentLabel(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    MIXED = "mixed"


class EntityType(str, Enum):
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    MONEY = "money"
    DATE = "date"
    ID_NUMBER = "id_number"
    DOCUMENT = "document"
    PRODUCT = "product"
    ACCOUNT = "account"
    UNKNOWN = "unknown"


class PIIStrategy(str, Enum):
    NONE = "none"
    DETECT = "detect"
    MASK = "mask"
    HASH = "hash"
    REDACT = "redact"


class TextNormalizationMode(str, Enum):
    LIGHT = "light"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class TextDocument:
    text: str
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    language_hint: Optional[str] = None
    source: Optional[str] = None
    title: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NLPRequest:
    text: str
    task: NLPTask = NLPTask.GENERIC
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    language_hint: Optional[str] = None
    labels: Sequence[str] = field(default_factory=list)
    pii_strategy: PIIStrategy = PIIStrategy.MASK
    normalization_mode: TextNormalizationMode = TextNormalizationMode.STANDARD
    max_summary_sentences: int = 3
    max_keywords: int = 12
    embedding_dim: int = 256
    explain: bool = True
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchNLPRequest:
    documents: Sequence[TextDocument]
    task: NLPTask = NLPTask.GENERIC
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    labels: Sequence[str] = field(default_factory=list)
    pii_strategy: PIIStrategy = PIIStrategy.MASK
    normalization_mode: TextNormalizationMode = TextNormalizationMode.STANDARD
    max_summary_sentences: int = 3
    max_keywords: int = 12
    embedding_dim: int = 256
    explain: bool = True
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NLPEntity:
    text: str
    type: EntityType
    start: int
    end: int
    confidence: float
    normalized: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NLPClassification:
    label: str
    confidence: float
    probabilities: Mapping[str, float] = field(default_factory=dict)
    rationale: Optional[str] = None


@dataclass(frozen=True)
class NLPSentiment:
    label: SentimentLabel
    score: float
    confidence: float
    evidence: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class NLPExplanation:
    method: str
    evidence: Sequence[str] = field(default_factory=list)
    features: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class NLPResult:
    request_id: str
    task: NLPTask
    tenant_id: Optional[str]
    original_text_hash: str
    normalized_text: Optional[str]
    language: Optional[str]
    classification: Optional[NLPClassification]
    sentiment: Optional[NLPSentiment]
    entities: Sequence[NLPEntity]
    keywords: Sequence[str]
    summary: Optional[str]
    embedding: Optional[Sequence[float]]
    masked_text: Optional[str]
    explanation: Optional[NLPExplanation]
    cached: bool
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["task"] = self.task.value
        payload["created_at"] = self.created_at.isoformat()
        if payload.get("sentiment") and payload["sentiment"].get("label"):
            payload["sentiment"]["label"] = self.sentiment.label.value if self.sentiment else None
        for entity in payload.get("entities", []):
            if hasattr(entity.get("type"), "value"):
                entity["type"] = entity["type"].value
        return payload


@dataclass(frozen=True)
class BatchNLPResult:
    request_id: str
    task: NLPTask
    tenant_id: Optional[str]
    results: Sequence[NLPResult]
    total: int
    succeeded: int
    failed: int
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "task": self.task.value,
            "tenant_id": self.tenant_id,
            "results": [result.to_dict() for result in self.results],
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class NLPServiceConfig:
    default_timeout_ms: int = 2_500
    retries: int = 2
    retry_base_delay_ms: int = 80
    retry_jitter_ms: int = 40
    max_text_chars: int = 100_000
    max_batch_size: int = 512
    cache_ttl_seconds: int = 300
    cache_max_size: int = 50_000
    audit_enabled: bool = True
    fail_open: bool = True
    privacy_hash_salt: str = "change-me-in-production"
    default_language: str = "pt"
    min_keyword_length: int = 3
    stopwords: Mapping[str, Sequence[str]] = field(
        default_factory=lambda: {
            "pt": (
                "a", "à", "agora", "ainda", "ao", "aos", "as", "às", "com", "como", "da", "das", "de", "do",
                "dos", "e", "é", "em", "entre", "era", "essa", "esse", "esta", "este", "eu", "foi", "há",
                "isso", "já", "mas", "me", "mesmo", "minha", "muito", "na", "não", "nas", "no", "nos", "o", "os",
                "ou", "para", "pela", "pelo", "por", "que", "se", "sem", "ser", "sua", "são", "também", "tem",
                "uma", "um", "você", "vocês",
            ),
            "en": (
                "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "he", "in", "is", "it",
                "its", "of", "on", "or", "that", "the", "to", "was", "were", "will", "with", "you", "your",
            ),
        }
    )

    def validate(self) -> None:
        if self.default_timeout_ms <= 0:
            raise NLPValidationError("default_timeout_ms must be positive.")
        if self.retries < 0:
            raise NLPValidationError("retries cannot be negative.")
        if self.max_text_chars <= 0:
            raise NLPValidationError("max_text_chars must be positive.")
        if self.max_batch_size <= 0:
            raise NLPValidationError("max_batch_size must be positive.")


# =============================================================================
# Protocols / dependency contracts
# =============================================================================


class NLPProvider(Protocol):
    async def process(self, request: NLPRequest) -> NLPResult: ...

    async def process_batch(self, request: BatchNLPRequest) -> Sequence[NLPResult]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# No-op dependencies
# =============================================================================


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


# =============================================================================
# Cache
# =============================================================================


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int = 300, max_size: int = 50_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        if self.ttl_seconds <= 0:
            return None
        now = time.monotonic()
        async with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            return
        async with self._lock:
            if len(self._items) >= self.max_size:
                self._items.pop(next(iter(self._items)), None)
            self._items[key] = (time.monotonic() + ttl, value)


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=False)


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _hash_text(text: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{text}".encode("utf-8")).hexdigest()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _sentence_split(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ']+", text.lower(), flags=re.UNICODE)


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# =============================================================================
# Text normalization and PII utilities
# =============================================================================


class TextNormalizer:
    def normalize(self, text: str, mode: TextNormalizationMode = TextNormalizationMode.STANDARD) -> str:
        value = text.replace("\u00a0", " ")
        value = unicodedata.normalize("NFKC", value)
        value = re.sub(r"\s+", " ", value).strip()

        if mode in {TextNormalizationMode.STANDARD, TextNormalizationMode.AGGRESSIVE}:
            value = self._normalize_quotes(value)
            value = re.sub(r"\s+([,.;:!?])", r"\1", value)

        if mode == TextNormalizationMode.AGGRESSIVE:
            value = value.lower()
            value = _strip_accents(value)
            value = re.sub(r"[^a-z0-9@._:/\-\s]", " ", value)
            value = re.sub(r"\s+", " ", value).strip()

        return value

    def _normalize_quotes(self, text: str) -> str:
        replacements = {
            "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'", "´": "'", "`": "'",
            "–": "-", "—": "-",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text


class PIIDetector:
    EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    URL_RE = re.compile(r"\bhttps?://[^\s]+|\bwww\.[^\s]+", re.IGNORECASE)
    PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,5}[\s.-]?\d{3,5}(?!\d)")
    MONEY_RE = re.compile(r"(?i)(?:AOA|KZ|USD|EUR|R\$|€|\$)\s?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?\s?(?:AOA|KZ|USD|EUR)")
    ID_RE = re.compile(r"\b(?:BI|CPF|CNPJ|NIF|ID|DOC)[:\s-]*[A-Z0-9.-]{5,}\b", re.IGNORECASE)
    DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")

    def detect(self, text: str) -> List[NLPEntity]:
        entities: List[NLPEntity] = []
        patterns = [
            (EntityType.EMAIL, self.EMAIL_RE, 0.99),
            (EntityType.URL, self.URL_RE, 0.95),
            (EntityType.PHONE, self.PHONE_RE, 0.82),
            (EntityType.MONEY, self.MONEY_RE, 0.88),
            (EntityType.ID_NUMBER, self.ID_RE, 0.86),
            (EntityType.DATE, self.DATE_RE, 0.82),
        ]
        for entity_type, pattern, confidence in patterns:
            for match in pattern.finditer(text):
                entities.append(
                    NLPEntity(
                        text=match.group(0),
                        type=entity_type,
                        start=match.start(),
                        end=match.end(),
                        confidence=confidence,
                        normalized=match.group(0).strip(),
                    )
                )
        return self._dedupe_entities(entities)

    def mask(self, text: str, entities: Sequence[NLPEntity], strategy: PIIStrategy, salt: str) -> str:
        if strategy in {PIIStrategy.NONE, PIIStrategy.DETECT}:
            return text

        output = text
        for entity in sorted(entities, key=lambda e: e.start, reverse=True):
            if strategy == PIIStrategy.REDACT:
                replacement = "[REDACTED]"
            elif strategy == PIIStrategy.HASH:
                replacement = f"[{entity.type.value.upper()}:{_hash_text(entity.text, salt)[:12]}]"
            else:
                replacement = f"[{entity.type.value.upper()}]"
            output = output[: entity.start] + replacement + output[entity.end :]
        return output

    def _dedupe_entities(self, entities: Sequence[NLPEntity]) -> List[NLPEntity]:
        entities_sorted = sorted(entities, key=lambda e: (e.start, -(e.end - e.start)))
        selected: List[NLPEntity] = []
        occupied: List[Tuple[int, int]] = []
        for entity in entities_sorted:
            overlaps = any(not (entity.end <= start or entity.start >= end) for start, end in occupied)
            if overlaps:
                continue
            selected.append(entity)
            occupied.append((entity.start, entity.end))
        return selected


# =============================================================================
# Local deterministic provider
# =============================================================================


class LocalNLPProvider:
    """
    Deterministic local NLP provider for development, tests and resilient fallback.

    It intentionally avoids heavyweight dependencies. Production deployments can
    replace this with adapters for transformer models, OpenAI-compatible APIs,
    spaCy, HuggingFace, ONNX Runtime, internal ML services or vector platforms.
    """

    POSITIVE_TERMS = {
        "bom", "boa", "excelente", "ótimo", "otimo", "feliz", "satisfeito", "aprovado", "recomendo",
        "rápido", "rapido", "seguro", "sucesso", "positivo", "great", "good", "excellent", "happy",
    }
    NEGATIVE_TERMS = {
        "ruim", "péssimo", "pessimo", "lento", "erro", "falha", "fraude", "fraud", "bloqueado",
        "reclamação", "reclamacao", "problema", "cancelar", "negativo", "bad", "failed", "angry",
    }

    LANGUAGE_HINTS = {
        "pt": {"de", "que", "não", "nao", "para", "com", "uma", "cliente", "pagamento", "valor"},
        "en": {"the", "and", "for", "with", "payment", "customer", "value", "transaction"},
        "es": {"de", "que", "para", "con", "cliente", "pago", "valor", "transacción", "transaccion"},
    }

    def __init__(self, config: NLPServiceConfig) -> None:
        self.config = config
        self.normalizer = TextNormalizer()
        self.pii_detector = PIIDetector()

    async def process(self, request: NLPRequest) -> NLPResult:
        started = time.perf_counter()
        original_hash = _hash_text(request.text, self.config.privacy_hash_salt)
        normalized = self.normalizer.normalize(request.text, request.normalization_mode)
        language = request.language_hint or self.detect_language(normalized)
        pii_entities = self.pii_detector.detect(normalized)
        masked_text = self.pii_detector.mask(normalized, pii_entities, request.pii_strategy, self.config.privacy_hash_salt)

        entities: List[NLPEntity] = []
        classification: Optional[NLPClassification] = None
        sentiment: Optional[NLPSentiment] = None
        keywords: List[str] = []
        summary: Optional[str] = None
        embedding: Optional[List[float]] = None
        explanation: Optional[NLPExplanation] = None

        if request.task in {NLPTask.GENERIC, NLPTask.PII_DETECTION, NLPTask.PII_MASKING, NLPTask.ENTITY_EXTRACTION}:
            entities.extend(pii_entities)
            entities.extend(self.extract_named_entities(normalized))
            entities = self._dedupe_entities(entities)

        if request.task in {NLPTask.GENERIC, NLPTask.KEYWORD_EXTRACTION, NLPTask.TOPIC_MODELING}:
            keywords = self.extract_keywords(normalized, language, request.max_keywords)

        if request.task in {NLPTask.GENERIC, NLPTask.SENTIMENT}:
            sentiment = self.analyze_sentiment(normalized)

        if request.task in {NLPTask.GENERIC, NLPTask.CLASSIFICATION, NLPTask.INTENT_DETECTION, NLPTask.DOCUMENT_ROUTING}:
            classification = self.classify(normalized, request.labels, keywords, sentiment)

        if request.task in {NLPTask.GENERIC, NLPTask.SUMMARIZATION}:
            summary = self.summarize(normalized, language, request.max_summary_sentences)

        if request.task in {NLPTask.GENERIC, NLPTask.EMBEDDING}:
            embedding = self.embed(normalized, dim=request.embedding_dim)

        if request.task == NLPTask.NORMALIZE:
            explanation = NLPExplanation(method="normalization", features={"mode": request.normalization_mode.value})
        elif request.explain:
            explanation = NLPExplanation(
                method="local_deterministic_nlp",
                evidence=keywords[:6],
                features={
                    "language": language,
                    "text_chars": len(request.text),
                    "normalized_chars": len(normalized),
                    "entity_count": len(entities),
                    "keyword_count": len(keywords),
                    "pii_strategy": request.pii_strategy.value,
                },
            )

        return NLPResult(
            request_id=request.request_id,
            task=request.task,
            tenant_id=request.tenant_id,
            original_text_hash=original_hash,
            normalized_text=normalized,
            language=language,
            classification=classification,
            sentiment=sentiment,
            entities=entities,
            keywords=keywords,
            summary=summary,
            embedding=embedding,
            masked_text=masked_text,
            explanation=explanation,
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"provider": "local", "text_length": len(request.text)},
        )

    async def process_batch(self, request: BatchNLPRequest) -> Sequence[NLPResult]:
        results: List[NLPResult] = []
        for index, document in enumerate(request.documents):
            item_request = NLPRequest(
                text=document.text,
                task=request.task,
                tenant_id=document.tenant_id or request.tenant_id,
                request_id=f"{request.request_id}:{index}",
                language_hint=document.language_hint,
                labels=request.labels,
                pii_strategy=request.pii_strategy,
                normalization_mode=request.normalization_mode,
                max_summary_sentences=request.max_summary_sentences,
                max_keywords=request.max_keywords,
                embedding_dim=request.embedding_dim,
                explain=request.explain,
                use_cache=request.use_cache,
                timeout_ms=request.timeout_ms,
                metadata={**dict(request.metadata), **dict(document.metadata), "document_id": document.document_id},
            )
            results.append(await self.process(item_request))
        return results

    def detect_language(self, text: str) -> str:
        tokens = set(_tokenize(_strip_accents(text)))
        if not tokens:
            return self.config.default_language
        scores = {}
        for lang, hints in self.LANGUAGE_HINTS.items():
            normalized_hints = {_strip_accents(h) for h in hints}
            scores[lang] = len(tokens.intersection(normalized_hints))
        best_lang, best_score = max(scores.items(), key=lambda item: item[1])
        return best_lang if best_score > 0 else self.config.default_language

    def extract_named_entities(self, text: str) -> List[NLPEntity]:
        entities: List[NLPEntity] = []
        for match in re.finditer(r"\b(?:[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÀ-ÿ'-]+\s+){1,3}[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÀ-ÿ'-]+\b", text):
            value = match.group(0).strip()
            entity_type = EntityType.ORGANIZATION if any(tok in value.lower() for tok in ["lda", "sa", "bank", "banco", "empresa", "super"]) else EntityType.PERSON
            entities.append(
                NLPEntity(
                    text=value,
                    type=entity_type,
                    start=match.start(),
                    end=match.end(),
                    confidence=0.68,
                    normalized=value,
                    metadata={"source": "capitalization_pattern"},
                )
            )
        return entities

    def extract_keywords(self, text: str, language: str, max_keywords: int) -> List[str]:
        stopwords = set(self.config.stopwords.get(language, ()))
        tokens = [
            token
            for token in _tokenize(text)
            if len(token) >= self.config.min_keyword_length and token not in stopwords and not token.isdigit()
        ]
        counts = Counter(tokens)
        scored = []
        total = max(len(tokens), 1)
        for token, freq in counts.items():
            length_bonus = min(len(token) / 12, 1.0)
            score = (freq / total) + (0.05 * length_bonus)
            scored.append((token, score, freq))
        scored.sort(key=lambda item: (item[1], item[2], item[0]), reverse=True)
        return [token for token, _, _ in scored[:max_keywords]]

    def analyze_sentiment(self, text: str) -> NLPSentiment:
        tokens = {_strip_accents(token) for token in _tokenize(text)}
        positive = {_strip_accents(t) for t in self.POSITIVE_TERMS}
        negative = {_strip_accents(t) for t in self.NEGATIVE_TERMS}
        pos_hits = sorted(tokens.intersection(positive))
        neg_hits = sorted(tokens.intersection(negative))
        raw = len(pos_hits) - len(neg_hits)
        if raw > 0:
            label = SentimentLabel.POSITIVE
        elif raw < 0:
            label = SentimentLabel.NEGATIVE
        elif pos_hits and neg_hits:
            label = SentimentLabel.MIXED
        else:
            label = SentimentLabel.NEUTRAL
        magnitude = min(abs(raw) / 5, 1.0)
        confidence = 0.55 + (0.35 * magnitude) if raw else 0.52
        return NLPSentiment(label=label, score=round(raw / 5, 4), confidence=round(_clamp(confidence), 4), evidence=pos_hits + neg_hits)

    def classify(
        self,
        text: str,
        labels: Sequence[str],
        keywords: Sequence[str],
        sentiment: Optional[NLPSentiment],
    ) -> Optional[NLPClassification]:
        candidate_labels = list(labels) or [
            "fraud", "support", "payment", "cashflow", "compliance", "sales", "operations", "general"
        ]
        normalized_text = _strip_accents(text.lower())
        keyword_set = {_strip_accents(k.lower()) for k in keywords}
        scores: Dict[str, float] = {}

        label_hints = {
            "fraud": {"fraude", "fraud", "suspeito", "bloqueado", "risco", "chargeback"},
            "support": {"ajuda", "suporte", "erro", "problema", "ticket", "reclamacao", "reclamação"},
            "payment": {"pagamento", "payment", "transferencia", "transferência", "pix", "cartao", "cartão"},
            "cashflow": {"caixa", "cashflow", "receita", "despesa", "saldo", "fluxo"},
            "compliance": {"compliance", "politica", "política", "regulamento", "auditoria", "lgpd", "kyc"},
            "sales": {"venda", "cliente", "oferta", "produto", "comercial", "sales"},
            "operations": {"operacao", "operação", "processo", "logistica", "logística", "estoque"},
            "general": set(),
        }

        for label in candidate_labels:
            norm_label = _strip_accents(label.lower())
            hints = {_strip_accents(h) for h in label_hints.get(norm_label, {norm_label})}
            direct = 1.0 if norm_label in normalized_text else 0.0
            hint_hits = len(keyword_set.intersection(hints)) + sum(1 for hint in hints if hint in normalized_text)
            scores[label] = direct + (0.35 * hint_hits)

        if sentiment and sentiment.label == SentimentLabel.NEGATIVE:
            for label in candidate_labels:
                if _strip_accents(label.lower()) in {"fraud", "support", "compliance"}:
                    scores[label] += 0.1

        best_label, best_score = max(scores.items(), key=lambda item: item[1])
        if best_score <= 0:
            best_label = "general" if "general" in candidate_labels else candidate_labels[0]
            best_score = 0.25

        total = sum(max(v, 0.01) for v in scores.values()) or 1.0
        probabilities = {label: round(max(score, 0.01) / total, 4) for label, score in scores.items()}
        confidence = _clamp(probabilities.get(best_label, 0.5))
        return NLPClassification(
            label=best_label,
            confidence=round(confidence, 4),
            probabilities=probabilities,
            rationale=f"Matched label '{best_label}' using lexical hints and keyword evidence.",
        )

    def summarize(self, text: str, language: str, max_sentences: int) -> Optional[str]:
        sentences = _sentence_split(text)
        if not sentences:
            return None
        if len(sentences) <= max_sentences:
            return " ".join(sentences)
        keywords = set(self.extract_keywords(text, language, max_keywords=20))
        scored: List[Tuple[int, float, str]] = []
        for idx, sentence in enumerate(sentences):
            tokens = set(_tokenize(sentence))
            keyword_overlap = len(tokens.intersection(keywords))
            position_bonus = 0.2 if idx == 0 else 0.1 if idx == len(sentences) - 1 else 0.0
            length_penalty = 0.0 if 8 <= len(tokens) <= 35 else -0.05
            score = keyword_overlap + position_bonus + length_penalty
            scored.append((idx, score, sentence))
        selected = sorted(scored, key=lambda item: item[1], reverse=True)[:max_sentences]
        selected.sort(key=lambda item: item[0])
        return " ".join(sentence for _, _, sentence in selected)

    def embed(self, text: str, dim: int = 256) -> List[float]:
        dim = max(8, min(dim, 4096))
        vector = [0.0] * dim
        tokens = _tokenize(_strip_accents(text))
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [round(v / norm, 8) for v in vector]

    def _dedupe_entities(self, entities: Sequence[NLPEntity]) -> List[NLPEntity]:
        unique: Dict[Tuple[int, int, str], NLPEntity] = {}
        for entity in entities:
            key = (entity.start, entity.end, entity.type.value)
            current = unique.get(key)
            if current is None or entity.confidence > current.confidence:
                unique[key] = entity
        return sorted(unique.values(), key=lambda e: (e.start, e.end))


# =============================================================================
# Main service
# =============================================================================


class NLPService:
    def __init__(
        self,
        provider: Optional[NLPProvider] = None,
        config: Optional[NLPServiceConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[AsyncTTLCache] = None,
    ) -> None:
        self.config = config or NLPServiceConfig()
        self.config.validate()
        self.provider = provider or LocalNLPProvider(self.config)
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.cache = cache or AsyncTTLCache(self.config.cache_ttl_seconds, self.config.cache_max_size)

    async def process(self, request: NLPRequest) -> NLPResult:
        started = time.perf_counter()
        self._validate_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value}
        self.metrics.increment("nlp.request.started", tags=tags)

        cache_key = self._cache_key(request)
        if request.use_cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                self.metrics.increment("nlp.cache.hit", tags=tags)
                return self._mark_cached(cached, started)
        self.metrics.increment("nlp.cache.miss", tags=tags)

        try:
            result = await self._call_with_retry(request)
            if request.use_cache:
                await self.cache.set(cache_key, result)
            self.metrics.increment("nlp.request.completed", tags={**tags, "status": "success"})
            self.metrics.timing("nlp.latency_ms", result.latency_ms, tags=tags)
            await self._audit_result("nlp.request.completed", request, result)
            return result
        except Exception as exc:
            self.metrics.increment("nlp.request.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("NLP processing failed", extra={"request_id": request.request_id, "task": request.task.value})
            if not self.config.fail_open:
                raise
            result = self._fallback_result(request, started, exc)
            await self._audit_result("nlp.request.failed", request, result)
            return result

    async def process_batch(self, request: BatchNLPRequest) -> BatchNLPResult:
        started = time.perf_counter()
        self._validate_batch_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value}
        self.metrics.increment("nlp.batch.started", tags=tags)

        try:
            timeout_ms = request.timeout_ms or self.config.default_timeout_ms
            results = await asyncio.wait_for(self.provider.process_batch(request), timeout=timeout_ms / 1000)
            latency_ms = round((time.perf_counter() - started) * 1000, 4)
            batch = BatchNLPResult(
                request_id=request.request_id,
                task=request.task,
                tenant_id=request.tenant_id,
                results=results,
                total=len(request.documents),
                succeeded=len(results),
                failed=max(0, len(request.documents) - len(results)),
                latency_ms=latency_ms,
                created_at=_utc_now(),
                metadata={"provider": self.provider.__class__.__name__, "mean_item_latency_ms": self._mean_latency(results)},
            )
            self.metrics.increment("nlp.batch.completed", tags={**tags, "status": "success"})
            self.metrics.timing("nlp.batch.latency_ms", latency_ms, tags=tags)
            await self._audit_batch_result("nlp.batch.completed", request, batch)
            return batch
        except asyncio.TimeoutError as exc:
            error = NLPTimeoutError(f"NLP batch timed out after {request.timeout_ms or self.config.default_timeout_ms}ms")
            self.metrics.increment("nlp.batch.failed", tags={**tags, "error": "timeout"})
            if not self.config.fail_open:
                raise error from exc
            results = [self._fallback_result(self._request_from_document(request, doc, idx), started, error) for idx, doc in enumerate(request.documents)]
            batch = BatchNLPResult(
                request_id=request.request_id,
                task=request.task,
                tenant_id=request.tenant_id,
                results=results,
                total=len(request.documents),
                succeeded=0,
                failed=len(request.documents),
                latency_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={"fallback": True, "error": "timeout"},
            )
            await self._audit_batch_result("nlp.batch.failed", request, batch)
            return batch
        except Exception as exc:
            self.metrics.increment("nlp.batch.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("NLP batch processing failed", extra={"request_id": request.request_id})
            if not self.config.fail_open:
                raise
            results = [self._fallback_result(self._request_from_document(request, doc, idx), started, exc) for idx, doc in enumerate(request.documents)]
            batch = BatchNLPResult(
                request_id=request.request_id,
                task=request.task,
                tenant_id=request.tenant_id,
                results=results,
                total=len(request.documents),
                succeeded=0,
                failed=len(request.documents),
                latency_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={"fallback": True, "error": exc.__class__.__name__},
            )
            await self._audit_batch_result("nlp.batch.failed", request, batch)
            return batch

    async def normalize(self, text: str, tenant_id: Optional[str] = None) -> NLPResult:
        return await self.process(NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.NORMALIZE))

    async def classify(self, text: str, labels: Sequence[str], tenant_id: Optional[str] = None) -> NLPResult:
        return await self.process(NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.CLASSIFICATION, labels=labels))

    async def sentiment(self, text: str, tenant_id: Optional[str] = None) -> NLPResult:
        return await self.process(NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.SENTIMENT))

    async def extract_entities(self, text: str, tenant_id: Optional[str] = None) -> NLPResult:
        return await self.process(NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.ENTITY_EXTRACTION))

    async def embed(self, text: str, tenant_id: Optional[str] = None, dim: int = 256) -> NLPResult:
        return await self.process(NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.EMBEDDING, embedding_dim=dim))

    async def summarize(self, text: str, tenant_id: Optional[str] = None, max_sentences: int = 3) -> NLPResult:
        return await self.process(
            NLPRequest(text=text, tenant_id=tenant_id, task=NLPTask.SUMMARIZATION, max_summary_sentences=max_sentences)
        )

    async def _call_with_retry(self, request: NLPRequest) -> NLPResult:
        timeout_ms = request.timeout_ms or self.config.default_timeout_ms
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.retries + 1):
            try:
                return await asyncio.wait_for(self.provider.process(request), timeout=timeout_ms / 1000)
            except asyncio.TimeoutError as exc:
                last_exc = NLPTimeoutError(f"NLP provider timed out after {timeout_ms}ms")
            except Exception as exc:
                last_exc = exc

            if attempt < self.config.retries:
                delay_ms = self.config.retry_base_delay_ms * (2**attempt)
                jitter_ms = min(self.config.retry_jitter_ms, max(0, self.config.retry_jitter_ms))
                if jitter_ms:
                    delay_ms += int(hashlib.sha256(request.request_id.encode()).hexdigest()[:2], 16) % jitter_ms
                await asyncio.sleep(delay_ms / 1000)

        assert last_exc is not None
        raise last_exc

    def _validate_request(self, request: NLPRequest) -> None:
        if not isinstance(request.text, str):
            raise NLPValidationError("text must be a string.")
        if not request.text.strip():
            raise NLPValidationError("text cannot be empty.")
        if len(request.text) > self.config.max_text_chars:
            raise NLPValidationError(f"text exceeds max length of {self.config.max_text_chars} characters.")
        if request.max_summary_sentences <= 0:
            raise NLPValidationError("max_summary_sentences must be positive.")
        if request.max_keywords <= 0:
            raise NLPValidationError("max_keywords must be positive.")
        if request.embedding_dim <= 0:
            raise NLPValidationError("embedding_dim must be positive.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise NLPValidationError("timeout_ms must be positive.")

    def _validate_batch_request(self, request: BatchNLPRequest) -> None:
        if not request.documents:
            raise NLPValidationError("documents cannot be empty.")
        if len(request.documents) > self.config.max_batch_size:
            raise NLPValidationError(f"batch size exceeds max_batch_size={self.config.max_batch_size}.")
        for idx, document in enumerate(request.documents):
            if not isinstance(document.text, str) or not document.text.strip():
                raise NLPValidationError(f"documents[{idx}].text cannot be empty.")
            if len(document.text) > self.config.max_text_chars:
                raise NLPValidationError(f"documents[{idx}].text exceeds max length.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise NLPValidationError("timeout_ms must be positive.")

    def _cache_key(self, request: NLPRequest) -> str:
        payload = {
            "text_hash": _hash_text(request.text, self.config.privacy_hash_salt),
            "task": request.task.value,
            "tenant_id": request.tenant_id,
            "language_hint": request.language_hint,
            "labels": list(request.labels),
            "pii_strategy": request.pii_strategy.value,
            "normalization_mode": request.normalization_mode.value,
            "max_summary_sentences": request.max_summary_sentences,
            "max_keywords": request.max_keywords,
            "embedding_dim": request.embedding_dim,
            "explain": request.explain,
        }
        return f"nlp:{_hash_payload(payload)}"

    def _mark_cached(self, result: NLPResult, started: float) -> NLPResult:
        return NLPResult(
            request_id=result.request_id,
            task=result.task,
            tenant_id=result.tenant_id,
            original_text_hash=result.original_text_hash,
            normalized_text=result.normalized_text,
            language=result.language,
            classification=result.classification,
            sentiment=result.sentiment,
            entities=result.entities,
            keywords=result.keywords,
            summary=result.summary,
            embedding=result.embedding,
            masked_text=result.masked_text,
            explanation=result.explanation,
            cached=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={**dict(result.metadata), "cache_returned_at": _utc_now().isoformat()},
        )

    def _fallback_result(self, request: NLPRequest, started: float, exc: Exception) -> NLPResult:
        normalizer = TextNormalizer()
        pii_detector = PIIDetector()
        normalized = normalizer.normalize(request.text, request.normalization_mode)
        entities = pii_detector.detect(normalized)
        masked_text = pii_detector.mask(normalized, entities, request.pii_strategy, self.config.privacy_hash_salt)
        return NLPResult(
            request_id=request.request_id,
            task=request.task,
            tenant_id=request.tenant_id,
            original_text_hash=_hash_text(request.text, self.config.privacy_hash_salt),
            normalized_text=normalized,
            language=request.language_hint or self.config.default_language,
            classification=None,
            sentiment=None,
            entities=entities,
            keywords=[],
            summary=None,
            embedding=None,
            masked_text=masked_text,
            explanation=NLPExplanation(
                method="service_fallback",
                warnings=[f"NLP fallback activated due to {exc.__class__.__name__}"],
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"fallback": True, "error": exc.__class__.__name__, "message": str(exc)},
        )

    def _request_from_document(self, batch: BatchNLPRequest, document: TextDocument, index: int) -> NLPRequest:
        return NLPRequest(
            text=document.text,
            task=batch.task,
            tenant_id=document.tenant_id or batch.tenant_id,
            request_id=f"{batch.request_id}:{index}",
            language_hint=document.language_hint,
            labels=batch.labels,
            pii_strategy=batch.pii_strategy,
            normalization_mode=batch.normalization_mode,
            max_summary_sentences=batch.max_summary_sentences,
            max_keywords=batch.max_keywords,
            embedding_dim=batch.embedding_dim,
            explain=batch.explain,
            use_cache=batch.use_cache,
            timeout_ms=batch.timeout_ms,
            metadata={**dict(batch.metadata), **dict(document.metadata), "document_id": document.document_id},
        )

    def _mean_latency(self, results: Sequence[NLPResult]) -> float:
        if not results:
            return 0.0
        return round(statistics.mean(result.latency_ms for result in results), 4)

    async def _audit_result(self, event_name: str, request: NLPRequest, result: NLPResult) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(
                event_name,
                {
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    "task": request.task.value,
                    "text_hash": result.original_text_hash,
                    "language": result.language,
                    "cached": result.cached,
                    "latency_ms": result.latency_ms,
                    "entity_count": len(result.entities),
                    "keyword_count": len(result.keywords),
                    "has_embedding": result.embedding is not None,
                    "has_summary": result.summary is not None,
                    "classification": result.classification.label if result.classification else None,
                    "sentiment": result.sentiment.label.value if result.sentiment else None,
                    "fallback": bool(result.metadata.get("fallback")),
                    "created_at": result.created_at.isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to write NLP audit event", extra={"request_id": request.request_id})

    async def _audit_batch_result(self, event_name: str, request: BatchNLPRequest, result: BatchNLPResult) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(
                event_name,
                {
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    "task": request.task.value,
                    "total": result.total,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "latency_ms": result.latency_ms,
                    "fallback": bool(result.metadata.get("fallback")),
                    "created_at": result.created_at.isoformat(),
                },
            )
        except Exception:
            logger.exception("Failed to write NLP batch audit event", extra={"request_id": request.request_id})

    @classmethod
    def request_from_payload(cls, payload: Mapping[str, Any]) -> NLPRequest:
        return NLPRequest(
            text=str(payload.get("text") or ""),
            task=NLPTask(payload.get("task", NLPTask.GENERIC.value)),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            language_hint=payload.get("language_hint"),
            labels=tuple(payload.get("labels") or ()),
            pii_strategy=PIIStrategy(payload.get("pii_strategy", PIIStrategy.MASK.value)),
            normalization_mode=TextNormalizationMode(payload.get("normalization_mode", TextNormalizationMode.STANDARD.value)),
            max_summary_sentences=int(payload.get("max_summary_sentences", 3)),
            max_keywords=int(payload.get("max_keywords", 12)),
            embedding_dim=int(payload.get("embedding_dim", 256)),
            explain=bool(payload.get("explain", True)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            metadata=payload.get("metadata") or {},
        )

    @classmethod
    def batch_request_from_payload(cls, payload: Mapping[str, Any]) -> BatchNLPRequest:
        documents = [
            TextDocument(
                text=str(item.get("text") or ""),
                document_id=str(item.get("document_id") or uuid.uuid4()),
                tenant_id=item.get("tenant_id") or payload.get("tenant_id"),
                language_hint=item.get("language_hint"),
                source=item.get("source"),
                title=item.get("title"),
                metadata=item.get("metadata") or {},
            )
            for item in payload.get("documents", [])
        ]
        return BatchNLPRequest(
            documents=documents,
            task=NLPTask(payload.get("task", NLPTask.GENERIC.value)),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            labels=tuple(payload.get("labels") or ()),
            pii_strategy=PIIStrategy(payload.get("pii_strategy", PIIStrategy.MASK.value)),
            normalization_mode=TextNormalizationMode(payload.get("normalization_mode", TextNormalizationMode.STANDARD.value)),
            max_summary_sentences=int(payload.get("max_summary_sentences", 3)),
            max_keywords=int(payload.get("max_keywords", 12)),
            embedding_dim=int(payload.get("embedding_dim", 256)),
            explain=bool(payload.get("explain", True)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_nlp_service(
    provider: Optional[NLPProvider] = None,
    config: Optional[NLPServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> NLPService:
    return NLPService(
        provider=provider,
        config=config,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    service = build_nlp_service(
        config=NLPServiceConfig(
            privacy_hash_salt="local-dev-salt",
            default_language="pt",
        )
    )

    request = NLPRequest(
        tenant_id="tenant-ao",
        task=NLPTask.GENERIC,
        text=(
            "Cliente João Silva reportou uma possível fraude no pagamento de 250.000 AOA. "
            "O contato é joao.silva@example.com e o telefone +244 923 123 456. "
            "A transação foi bloqueada, mas o cliente ficou satisfeito com a resposta rápida."
        ),
        labels=("fraud", "support", "payment", "compliance"),
        pii_strategy=PIIStrategy.MASK,
        explain=True,
        embedding_dim=32,
    )

    result = await service.process(request)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

    batch = BatchNLPRequest(
        tenant_id="tenant-ao",
        task=NLPTask.CLASSIFICATION,
        labels=("fraud", "support", "sales", "cashflow"),
        documents=(
            TextDocument(text="Pagamento suspeito bloqueado por risco de fraude."),
            TextDocument(text="Cliente quer saber novas ofertas e produtos disponíveis."),
        ),
    )
    batch_result = await service.process_batch(batch)
    print(json.dumps(batch_result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
