"""
data/ai/semantic_search.py

Enterprise-grade semantic search engine.

This module provides a production-oriented semantic search layer that can be
used by RAG pipelines, knowledge assistants, document platforms and AI services.
It is dependency-light by default, while exposing clean protocols for integrating
real embedding providers, vector databases, rerankers, audit pipelines and
observability stacks.

Core capabilities:

- Provider-neutral embedding interface
- In-memory vector index for local/test deployments
- Metadata and security filters
- Vector search with cosine/dot/euclidean similarity
- Hybrid search using semantic + keyword scoring
- MMR diversity selection
- Optional reranker hook
- Batch indexing and batch search
- TTL cache for repeated queries
- Audit and metrics hooks
- Explainable search traces
- Sync and async convenience APIs

Recommended package position:
    data/ai/semantic_search.py

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import logging
import math
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class SemanticSearchError(Exception):
    """Base exception for semantic search errors."""


class SemanticSearchConfigurationError(SemanticSearchError):
    """Raised when configuration is invalid."""


class SemanticSearchValidationError(SemanticSearchError):
    """Raised when input data is invalid."""


class EmbeddingProviderError(SemanticSearchError):
    """Raised when embedding generation fails."""


class VectorIndexError(SemanticSearchError):
    """Raised when vector index operations fail."""


class SearchPolicyError(SemanticSearchError):
    """Raised when a search is blocked by policy/filter rules."""


# =============================================================================
# Enums
# =============================================================================


class SimilarityMetric(str, Enum):
    """Supported vector similarity metrics."""

    COSINE = "cosine"
    DOT = "dot"
    EUCLIDEAN = "euclidean"


class SearchMode(str, Enum):
    """Search execution mode."""

    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class RankingStrategy(str, Enum):
    """Ranking strategy after initial retrieval."""

    SCORE = "score"
    MMR = "mmr"
    RERANKER = "reranker"
    HYBRID_THEN_MMR = "hybrid_then_mmr"


class DocumentStatus(str, Enum):
    """Document availability status."""

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    DISABLED = "disabled"


class SensitivityLevel(str, Enum):
    """Document sensitivity level."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class SemanticSearchConfig:
    """Configuration for semantic search."""

    metric: SimilarityMetric = SimilarityMetric.COSINE
    default_top_k: int = 10
    candidate_multiplier: int = 5
    min_score: float = 0.0
    hybrid_semantic_weight: float = 0.75
    hybrid_keyword_weight: float = 0.25
    mmr_lambda: float = 0.72
    max_query_chars: int = 8_000
    max_document_chars: int = 200_000
    normalize_vectors: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 600
    cache_max_items: int = 2048
    include_vectors_in_results: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.default_top_k <= 0:
            raise SemanticSearchConfigurationError("default_top_k must be positive")
        if self.candidate_multiplier <= 0:
            raise SemanticSearchConfigurationError("candidate_multiplier must be positive")
        if not 0 <= self.min_score <= 1:
            raise SemanticSearchConfigurationError("min_score must be between 0 and 1")
        if not 0 <= self.hybrid_semantic_weight <= 1:
            raise SemanticSearchConfigurationError("hybrid_semantic_weight must be between 0 and 1")
        if not 0 <= self.hybrid_keyword_weight <= 1:
            raise SemanticSearchConfigurationError("hybrid_keyword_weight must be between 0 and 1")
        if not 0 <= self.mmr_lambda <= 1:
            raise SemanticSearchConfigurationError("mmr_lambda must be between 0 and 1")
        if self.max_query_chars <= 0:
            raise SemanticSearchConfigurationError("max_query_chars must be positive")
        if self.max_document_chars <= 0:
            raise SemanticSearchConfigurationError("max_document_chars must be positive")
        if self.cache_ttl_seconds <= 0:
            raise SemanticSearchConfigurationError("cache_ttl_seconds must be positive")
        if self.cache_max_items <= 0:
            raise SemanticSearchConfigurationError("cache_max_items must be positive")


@dataclass(frozen=True)
class SearchContext:
    """Context metadata for search requests."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    domain: Optional[str] = None
    locale: Optional[str] = None
    trace_id: Optional[str] = None
    allowed_sensitivity: Sequence[SensitivityLevel] = (
        SensitivityLevel.PUBLIC,
        SensitivityLevel.INTERNAL,
    )
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchDocument:
    """Document/chunk stored in semantic index."""

    id: str
    text: str
    title: Optional[str] = None
    source_id: Optional[str] = None
    document_id: Optional[str] = None
    url: Optional[str] = None
    vector: Optional[Sequence[float]] = None
    status: DocumentStatus = DocumentStatus.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    tags: Sequence[str] = field(default_factory=tuple)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self, max_document_chars: int) -> None:
        if not self.id:
            raise SemanticSearchValidationError("document id is required")
        if not isinstance(self.text, str) or not self.text.strip():
            raise SemanticSearchValidationError(f"document {self.id} text must be non-empty")
        if len(self.text) > max_document_chars:
            raise SemanticSearchValidationError(
                f"document {self.id} exceeds max_document_chars: {len(self.text)} > {max_document_chars}"
            )


@dataclass(frozen=True)
class SearchFilter:
    """Structured filter for semantic search."""

    source_ids: Sequence[str] = field(default_factory=tuple)
    document_ids: Sequence[str] = field(default_factory=tuple)
    include_tags: Sequence[str] = field(default_factory=tuple)
    exclude_tags: Sequence[str] = field(default_factory=tuple)
    statuses: Sequence[DocumentStatus] = (DocumentStatus.ACTIVE,)
    sensitivity_levels: Sequence[SensitivityLevel] = field(default_factory=tuple)
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)
    created_after: Optional[str] = None
    created_before: Optional[str] = None


@dataclass(frozen=True)
class SearchQuery:
    """Search query payload."""

    query: str
    top_k: Optional[int] = None
    mode: SearchMode = SearchMode.HYBRID
    ranking_strategy: RankingStrategy = RankingStrategy.HYBRID_THEN_MMR
    filters: SearchFilter = field(default_factory=SearchFilter)
    context: SearchContext = field(default_factory=SearchContext)
    query_vector: Optional[Sequence[float]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchScore:
    """Detailed score components."""

    final_score: float
    semantic_score: float = 0.0
    keyword_score: float = 0.0
    rerank_score: Optional[float] = None
    diversity_penalty: float = 0.0
    metadata_boost: float = 0.0
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class SearchResult:
    """One search result."""

    document: SearchDocument
    score: SearchScore
    rank: int
    highlights: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchTraceEvent:
    """Trace event for observability."""

    stage: str
    latency_ms: float
    success: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResponse:
    """Search response."""

    request_id: str
    query: str
    results: Sequence[SearchResult]
    total_candidates: int
    cached: bool
    trace: Sequence[SearchTraceEvent]
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class BatchSearchResponse:
    """Batch search response."""

    batch_id: str
    created_at: str
    total_items: int
    responses: Sequence[SearchResponse]
    failures: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


# =============================================================================
# Protocols
# =============================================================================


class EmbeddingProvider(Protocol):
    """Embedding provider protocol."""

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return embeddings for texts."""


class VectorIndex(Protocol):
    """Vector index protocol."""

    async def upsert(self, documents: Sequence[SearchDocument]) -> None:
        """Insert or update documents."""

    async def delete(self, ids: Sequence[str]) -> None:
        """Delete documents by id."""

    async def get(self, ids: Sequence[str]) -> Sequence[SearchDocument]:
        """Get documents by id."""

    async def all_documents(self) -> Sequence[SearchDocument]:
        """Return all documents."""

    async def search(
        self,
        vector: Sequence[float],
        top_k: int,
        metric: SimilarityMetric,
        filters: SearchFilter,
        context: SearchContext,
    ) -> Sequence[Tuple[SearchDocument, float]]:
        """Search by vector and return document-score pairs."""


class Reranker(Protocol):
    """Optional reranker protocol."""

    async def rerank(self, query: SearchQuery, results: Sequence[SearchResult], top_k: int) -> Sequence[SearchResult]:
        """Return reranked results."""


class AuditSink(Protocol):
    """Audit sink protocol."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    """Metrics sink protocol."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment counter."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe value."""


# =============================================================================
# Utilities
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def tokenize(text: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ]+", (text or "").lower(), flags=re.UNICODE)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def vector_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in vector))


def normalize_vector(vector: Sequence[float]) -> Tuple[float, ...]:
    norm = vector_norm(vector)
    if norm == 0:
        return tuple(float(x) for x in vector)
    return tuple(float(x) / norm for x in vector)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = vector_norm(a) * vector_norm(b)
    if denom == 0:
        return 0.0
    return clamp(sum(float(x) * float(y) for x, y in zip(a, b)) / denom, -1.0, 1.0)


def dot_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(a, b))


def euclidean_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    distance = math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))
    return 1.0 / (1.0 + distance)


def compute_similarity(a: Sequence[float], b: Sequence[float], metric: SimilarityMetric) -> float:
    if metric == SimilarityMetric.DOT:
        raw = dot_similarity(a, b)
        return clamp((raw + 1.0) / 2.0) if raw < 0 or raw > 1 else clamp(raw)
    if metric == SimilarityMetric.EUCLIDEAN:
        return clamp(euclidean_similarity(a, b))
    raw = cosine_similarity(a, b)
    return clamp((raw + 1.0) / 2.0)


def keyword_score(query: str, text: str) -> float:
    q_tokens = set(tokenize(query))
    d_tokens = tokenize(text)
    if not q_tokens or not d_tokens:
        return 0.0
    d_set = set(d_tokens)
    overlap = len(q_tokens & d_set) / len(q_tokens)
    phrase_bonus = 0.15 if normalize_text(query).lower() in normalize_text(text).lower() else 0.0
    density = sum(1 for token in d_tokens if token in q_tokens) / max(len(d_tokens), 1)
    return clamp((overlap * 0.75) + (density * 0.10) + phrase_bonus)


def build_cache_key(query: SearchQuery, config: SemanticSearchConfig) -> str:
    payload = {
        "query": normalize_text(query.query),
        "top_k": query.top_k,
        "mode": query.mode.value,
        "ranking_strategy": query.ranking_strategy.value,
        "filters": asdict(query.filters),
        "tenant_id": query.context.tenant_id,
        "application": query.context.application,
        "domain": query.context.domain,
        "allowed_sensitivity": [item.value for item in query.context.allowed_sensitivity],
        "metric": config.metric.value,
        "min_score": config.min_score,
    }
    return stable_hash(safe_json(payload))


def highlight_terms(text: str, query: str, *, max_fragments: int = 3, fragment_chars: int = 220) -> Sequence[str]:
    terms = [re.escape(t) for t in tokenize(query) if len(t) >= 3]
    if not terms:
        return tuple()
    pattern = re.compile("|".join(terms), re.IGNORECASE)
    fragments: List[str] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - fragment_chars // 2)
        end = min(len(text), match.end() + fragment_chars // 2)
        fragment = text[start:end].strip()
        if start > 0:
            fragment = "..." + fragment
        if end < len(text):
            fragment += "..."
        fragments.append(fragment)
        if len(fragments) >= max_fragments:
            break
    return tuple(fragments)


# =============================================================================
# Default implementations
# =============================================================================


class HashingEmbeddingProvider:
    """Deterministic dependency-light embedding provider.

    This is useful for tests and local execution. For production semantic search,
    inject a real embedding model/provider.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 16:
            raise SemanticSearchConfigurationError("dimensions must be >= 16")
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        await asyncio.sleep(0)
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> Tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).hexdigest()
            idx = int(digest[:8], 16) % self.dimensions
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vector[idx] += sign
        return normalize_vector(vector)


class InMemoryVectorIndex:
    """Simple in-memory vector index.

    Suitable for tests, prototypes and small local deployments. Replace with a
    dedicated vector database in production.
    """

    def __init__(self) -> None:
        self._documents: Dict[str, SearchDocument] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, documents: Sequence[SearchDocument]) -> None:
        async with self._lock:
            for doc in documents:
                if doc.vector is None:
                    raise VectorIndexError(f"document {doc.id} has no vector")
                self._documents[doc.id] = doc

    async def delete(self, ids: Sequence[str]) -> None:
        async with self._lock:
            for doc_id in ids:
                self._documents.pop(doc_id, None)

    async def get(self, ids: Sequence[str]) -> Sequence[SearchDocument]:
        async with self._lock:
            return tuple(self._documents[doc_id] for doc_id in ids if doc_id in self._documents)

    async def all_documents(self) -> Sequence[SearchDocument]:
        async with self._lock:
            return tuple(self._documents.values())

    async def search(
        self,
        vector: Sequence[float],
        top_k: int,
        metric: SimilarityMetric,
        filters: SearchFilter,
        context: SearchContext,
    ) -> Sequence[Tuple[SearchDocument, float]]:
        async with self._lock:
            candidates = []
            for doc in self._documents.values():
                if doc.vector is None:
                    continue
                if not document_matches_filter(doc, filters, context):
                    continue
                score = compute_similarity(vector, doc.vector, metric)
                candidates.append((doc, score))
            return tuple(heapq.nlargest(top_k, candidates, key=lambda item: item[1]))


class InMemoryTTLCache:
    """Simple async TTL LRU cache."""

    def __init__(self, max_items: int) -> None:
        self.max_items = max_items
        self._items: OrderedDict[str, Tuple[float, SearchResponse]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[SearchResponse]:
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, response = item
            if expires_at < time.time():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return SearchResponse(**{**response.to_dict(), "cached": True})

    async def set(self, key: str, response: SearchResponse, ttl_seconds: int) -> None:
        async with self._lock:
            self._items[key] = (time.time() + ttl_seconds, response)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)


class LoggingAuditSink:
    """Audit sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("semantic_search_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("semantic_search_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("semantic_search_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


class LexicalReranker:
    """Default lightweight reranker using existing scores plus lexical match."""

    async def rerank(self, query: SearchQuery, results: Sequence[SearchResult], top_k: int) -> Sequence[SearchResult]:
        await asyncio.sleep(0)
        reranked: List[SearchResult] = []
        for result in results:
            lexical = keyword_score(query.query, result.document.text)
            rerank = clamp((result.score.final_score * 0.70) + (lexical * 0.30))
            score = SearchScore(
                final_score=rerank,
                semantic_score=result.score.semantic_score,
                keyword_score=result.score.keyword_score,
                rerank_score=rerank,
                diversity_penalty=result.score.diversity_penalty,
                metadata_boost=result.score.metadata_boost,
                reasons=tuple(list(result.score.reasons) + [f"lexical_rerank={lexical:.3f}"]),
            )
            reranked.append(
                SearchResult(
                    document=result.document,
                    score=score,
                    rank=result.rank,
                    highlights=result.highlights,
                    metadata=result.metadata,
                )
            )
        ordered = sorted(reranked, key=lambda item: item.score.final_score, reverse=True)[:top_k]
        return tuple(
            SearchResult(document=item.document, score=item.score, rank=index + 1, highlights=item.highlights, metadata=item.metadata)
            for index, item in enumerate(ordered)
        )


# =============================================================================
# Filter helper
# =============================================================================


def document_matches_filter(doc: SearchDocument, filters: SearchFilter, context: SearchContext) -> bool:
    if doc.status == DocumentStatus.DELETED:
        return False
    if filters.statuses and doc.status not in filters.statuses:
        return False
    allowed_sensitivity = set(filters.sensitivity_levels or context.allowed_sensitivity)
    if allowed_sensitivity and doc.sensitivity not in allowed_sensitivity:
        return False
    if filters.source_ids and doc.source_id not in filters.source_ids:
        return False
    if filters.document_ids and doc.document_id not in filters.document_ids:
        return False
    tags = set(doc.tags or ())
    if filters.include_tags and not set(filters.include_tags).issubset(tags):
        return False
    if filters.exclude_tags and set(filters.exclude_tags) & tags:
        return False
    for key, expected in filters.metadata_equals.items():
        if doc.metadata.get(key) != expected:
            return False
    if filters.created_after and doc.created_at and doc.created_at < filters.created_after:
        return False
    if filters.created_before and doc.created_at and doc.created_at > filters.created_before:
        return False
    return True


# =============================================================================
# Semantic Search Engine
# =============================================================================


class SemanticSearchEngine:
    """Enterprise semantic search engine."""

    def __init__(
        self,
        *,
        config: Optional[SemanticSearchConfig] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        index: Optional[VectorIndex] = None,
        reranker: Optional[Reranker] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or SemanticSearchConfig()
        self.config.validate()
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self.index = index or InMemoryVectorIndex()
        self.reranker = reranker or LexicalReranker()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        self.cache = InMemoryTTLCache(max_items=self.config.cache_max_items)

    async def index_documents(self, documents: Sequence[SearchDocument], *, batch_size: int = 64) -> None:
        """Embed and upsert documents into the vector index."""

        if batch_size <= 0:
            raise SemanticSearchValidationError("batch_size must be positive")
        started = time.perf_counter()
        total = 0
        try:
            for doc in documents:
                doc.validate(self.config.max_document_chars)

            for offset in range(0, len(documents), batch_size):
                batch = tuple(documents[offset : offset + batch_size])
                vectors = await self.embedding_provider.embed([doc.text for doc in batch])
                enriched_docs = []
                for doc, vector in zip(batch, vectors):
                    normalized = normalize_vector(vector) if self.config.normalize_vectors else tuple(float(x) for x in vector)
                    enriched_docs.append(
                        SearchDocument(
                            id=doc.id,
                            text=doc.text,
                            title=doc.title,
                            source_id=doc.source_id,
                            document_id=doc.document_id,
                            url=doc.url,
                            vector=normalized,
                            status=doc.status,
                            sensitivity=doc.sensitivity,
                            tags=doc.tags,
                            created_at=doc.created_at,
                            updated_at=doc.updated_at,
                            metadata=doc.metadata,
                        )
                    )
                await self.index.upsert(tuple(enriched_docs))
                total += len(enriched_docs)

            latency_ms = (time.perf_counter() - started) * 1000
            await self._metric_increment("ai.semantic_search.indexed_documents", total)
            await self._metric_observe("ai.semantic_search.index_latency_ms", latency_ms)
            await self._audit("semantic_search_index_completed", {
                "documents": total,
                "latency_ms": round(latency_ms, 3),
            })
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._metric_increment("ai.semantic_search.index_failure", 1, {"error_type": type(exc).__name__})
            await self._audit("semantic_search_index_failed", {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latency_ms": round(latency_ms, 3),
            })
            raise

    async def delete_documents(self, ids: Sequence[str]) -> None:
        if not ids:
            return
        await self.index.delete(ids)
        await self._audit("semantic_search_delete_completed", {"document_ids": list(ids), "count": len(ids)})

    async def search(self, query: SearchQuery) -> SearchResponse:
        """Execute semantic/hybrid/keyword search."""

        started = time.perf_counter()
        trace: List[SearchTraceEvent] = []
        try:
            await self._stage("validation", trace, lambda: self._validate_query(query))
            cache_key = build_cache_key(query, self.config)
            if self.config.enable_cache:
                cached = await self._stage("cache_lookup", trace, lambda: self.cache.get(cache_key))
                if cached is not None:
                    await self._record_success(query, cached, cached=True, latency_ms=(time.perf_counter() - started) * 1000)
                    return cached

            query_vector = await self._stage("query_embedding", trace, lambda: self._query_vector(query))
            candidates = await self._stage("candidate_retrieval", trace, lambda: self._retrieve_candidates(query, query_vector))
            ranked = await self._stage("ranking", trace, lambda: self._rank_candidates(query, query_vector, candidates))
            final_results = await self._stage("post_ranking", trace, lambda: self._post_rank(query, ranked))

            response = SearchResponse(
                request_id=query.context.request_id,
                query=query.query,
                results=tuple(self._result_without_vector(item) for item in final_results),
                total_candidates=len(candidates),
                cached=False,
                trace=tuple(trace),
                created_at=utc_now_iso(),
                metadata={
                    "search_version": self.config.version,
                    "mode": query.mode.value,
                    "ranking_strategy": query.ranking_strategy.value,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            if self.config.enable_cache:
                await self.cache.set(cache_key, response, self.config.cache_ttl_seconds)
            await self._record_success(query, response, cached=False, latency_ms=(time.perf_counter() - started) * 1000)
            await self._audit_search_completed(query, response)
            return response
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(query, exc, latency_ms)
            await self._audit("semantic_search_failed", {
                "request_id": query.context.request_id,
                "tenant_id": query.context.tenant_id,
                "application": query.context.application,
                "domain": query.context.domain,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latency_ms": round(latency_ms, 3),
                "trace": [asdict(item) for item in trace],
            })
            raise

    def search_sync(self, query: SearchQuery) -> SearchResponse:
        """Synchronous search convenience wrapper."""

        return asyncio.run(self.search(query))

    async def batch_search(
        self,
        queries: Sequence[SearchQuery],
        *,
        concurrency: int = 8,
        continue_on_error: bool = True,
    ) -> BatchSearchResponse:
        if concurrency <= 0:
            raise SemanticSearchValidationError("concurrency must be positive")
        batch_id = str(uuid.uuid4())
        semaphore = asyncio.Semaphore(concurrency)
        responses: List[Optional[SearchResponse]] = [None] * len(queries)
        failures: List[Mapping[str, Any]] = []

        async def run_one(index: int, item: SearchQuery) -> None:
            async with semaphore:
                try:
                    responses[index] = await self.search(item)
                except Exception as exc:  # noqa: BLE001
                    failures.append({
                        "index": index,
                        "request_id": item.context.request_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
                    if not continue_on_error:
                        raise

        await asyncio.gather(*(run_one(i, q) for i, q in enumerate(queries)))
        return BatchSearchResponse(
            batch_id=batch_id,
            created_at=utc_now_iso(),
            total_items=len(queries),
            responses=tuple(item for item in responses if item is not None),
            failures=tuple(failures),
        )

    def _validate_query(self, query: SearchQuery) -> None:
        if not query.query or not query.query.strip():
            raise SemanticSearchValidationError("query must be non-empty")
        if len(query.query) > self.config.max_query_chars:
            raise SemanticSearchValidationError(
                f"query exceeds max_query_chars: {len(query.query)} > {self.config.max_query_chars}"
            )
        if query.top_k is not None and query.top_k <= 0:
            raise SemanticSearchValidationError("top_k must be positive")

    async def _query_vector(self, query: SearchQuery) -> Sequence[float]:
        if query.query_vector is not None:
            return normalize_vector(query.query_vector) if self.config.normalize_vectors else query.query_vector
        vectors = await self.embedding_provider.embed((query.query,))
        if not vectors:
            raise EmbeddingProviderError("embedding provider returned no vector")
        return normalize_vector(vectors[0]) if self.config.normalize_vectors else tuple(float(x) for x in vectors[0])

    async def _retrieve_candidates(self, query: SearchQuery, query_vector: Sequence[float]) -> Sequence[Tuple[SearchDocument, float]]:
        top_k = query.top_k or self.config.default_top_k
        candidate_k = max(top_k, top_k * self.config.candidate_multiplier)
        if query.mode == SearchMode.KEYWORD:
            docs = await self.index.all_documents()
            results = [
                (doc, keyword_score(query.query, doc.text))
                for doc in docs
                if document_matches_filter(doc, query.filters, query.context)
            ]
            return tuple(heapq.nlargest(candidate_k, results, key=lambda item: item[1]))
        return await self.index.search(query_vector, candidate_k, self.config.metric, query.filters, query.context)

    async def _rank_candidates(
        self,
        query: SearchQuery,
        query_vector: Sequence[float],
        candidates: Sequence[Tuple[SearchDocument, float]],
    ) -> Sequence[SearchResult]:
        results: List[SearchResult] = []
        for doc, semantic in candidates:
            lexical = keyword_score(query.query, doc.text)
            if query.mode == SearchMode.SEMANTIC:
                final = semantic
                reasons = (f"semantic={semantic:.3f}",)
            elif query.mode == SearchMode.KEYWORD:
                final = lexical
                reasons = (f"keyword={lexical:.3f}",)
            else:
                total_weight = self.config.hybrid_semantic_weight + self.config.hybrid_keyword_weight
                final = (
                    (semantic * self.config.hybrid_semantic_weight)
                    + (lexical * self.config.hybrid_keyword_weight)
                ) / max(total_weight, 1e-9)
                reasons = (f"semantic={semantic:.3f}", f"keyword={lexical:.3f}")

            final = clamp(final + self._metadata_boost(doc, query))
            if final < self.config.min_score:
                continue
            score = SearchScore(
                final_score=final,
                semantic_score=semantic,
                keyword_score=lexical,
                metadata_boost=self._metadata_boost(doc, query),
                reasons=reasons,
            )
            results.append(
                SearchResult(
                    document=doc,
                    score=score,
                    rank=0,
                    highlights=highlight_terms(doc.text, query.query),
                    metadata={"candidate": True},
                )
            )
        return tuple(sorted(results, key=lambda item: item.score.final_score, reverse=True))

    async def _post_rank(self, query: SearchQuery, ranked: Sequence[SearchResult]) -> Sequence[SearchResult]:
        top_k = query.top_k or self.config.default_top_k
        if not ranked:
            return tuple()

        if query.ranking_strategy == RankingStrategy.RERANKER:
            return await self.reranker.rerank(query, ranked, top_k)

        if query.ranking_strategy in {RankingStrategy.MMR, RankingStrategy.HYBRID_THEN_MMR}:
            return self._mmr_select(ranked, top_k)

        selected = ranked[:top_k]
        return tuple(
            SearchResult(document=item.document, score=item.score, rank=index + 1, highlights=item.highlights, metadata=item.metadata)
            for index, item in enumerate(selected)
        )

    def _mmr_select(self, ranked: Sequence[SearchResult], top_k: int) -> Sequence[SearchResult]:
        selected: List[SearchResult] = []
        remaining = list(ranked)
        while remaining and len(selected) < top_k:
            if not selected:
                chosen = remaining.pop(0)
                selected.append(chosen)
                continue
            best_index = 0
            best_value = -float("inf")
            for idx, candidate in enumerate(remaining):
                diversity = max(
                    self._document_similarity(candidate.document, chosen.document)
                    for chosen in selected
                )
                mmr = (self.config.mmr_lambda * candidate.score.final_score) - ((1 - self.config.mmr_lambda) * diversity)
                if mmr > best_value:
                    best_value = mmr
                    best_index = idx
            chosen = remaining.pop(best_index)
            selected.append(chosen)

        output: List[SearchResult] = []
        for index, item in enumerate(selected, start=1):
            diversity_penalty = 0.0
            if index > 1:
                diversity_penalty = max(self._document_similarity(item.document, prev.document) for prev in selected[: index - 1])
            score = SearchScore(
                final_score=item.score.final_score,
                semantic_score=item.score.semantic_score,
                keyword_score=item.score.keyword_score,
                rerank_score=item.score.rerank_score,
                diversity_penalty=diversity_penalty,
                metadata_boost=item.score.metadata_boost,
                reasons=tuple(list(item.score.reasons) + [f"mmr_diversity_penalty={diversity_penalty:.3f}"]),
            )
            output.append(
                SearchResult(
                    document=item.document,
                    score=score,
                    rank=index,
                    highlights=item.highlights,
                    metadata=item.metadata,
                )
            )
        return tuple(output)

    def _document_similarity(self, a: SearchDocument, b: SearchDocument) -> float:
        if a.vector is not None and b.vector is not None:
            return compute_similarity(a.vector, b.vector, self.config.metric)
        return keyword_score(a.text, b.text)

    def _metadata_boost(self, doc: SearchDocument, query: SearchQuery) -> float:
        boost = 0.0
        preferred_sources = query.metadata.get("preferred_source_ids", ()) if query.metadata else ()
        if preferred_sources and doc.source_id in preferred_sources:
            boost += 0.03
        preferred_tags = set(query.metadata.get("preferred_tags", ()) if query.metadata else ())
        if preferred_tags and preferred_tags & set(doc.tags or ()):
            boost += 0.02
        return clamp(boost, 0.0, 0.10)

    def _result_without_vector(self, result: SearchResult) -> SearchResult:
        if self.config.include_vectors_in_results:
            return result
        doc = result.document
        stripped = SearchDocument(
            id=doc.id,
            text=doc.text,
            title=doc.title,
            source_id=doc.source_id,
            document_id=doc.document_id,
            url=doc.url,
            vector=None,
            status=doc.status,
            sensitivity=doc.sensitivity,
            tags=doc.tags,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            metadata=doc.metadata,
        )
        return SearchResult(document=stripped, score=result.score, rank=result.rank, highlights=result.highlights, metadata=result.metadata)

    async def _stage(self, stage: str, trace: List[SearchTraceEvent], func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            trace.append(SearchTraceEvent(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=True))
            return result
        except Exception as exc:
            trace.append(
                SearchTraceEvent(
                    stage=stage,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    success=False,
                    metadata={"error_type": type(exc).__name__, "error": str(exc)},
                )
            )
            raise

    async def _record_success(self, query: SearchQuery, response: SearchResponse, *, cached: bool, latency_ms: float) -> None:
        tags = self._metric_tags(query, cached=cached)
        await self._metric_increment("ai.semantic_search.success", 1, tags)
        await self._metric_observe("ai.semantic_search.latency_ms", latency_ms, tags)
        await self._metric_observe("ai.semantic_search.result_count", len(response.results), tags)
        await self._metric_observe("ai.semantic_search.candidate_count", response.total_candidates, tags)

    async def _record_failure(self, query: SearchQuery, exc: BaseException, latency_ms: float) -> None:
        tags = {**self._metric_tags(query, cached=False), "error_type": type(exc).__name__}
        await self._metric_increment("ai.semantic_search.failure", 1, tags)
        await self._metric_observe("ai.semantic_search.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, query: SearchQuery, *, cached: bool) -> Mapping[str, str]:
        return {
            "tenant_id": query.context.tenant_id or "unknown",
            "application": query.context.application or "unknown",
            "domain": query.context.domain or "unknown",
            "mode": query.mode.value,
            "ranking_strategy": query.ranking_strategy.value,
            "cached": str(cached).lower(),
        }

    async def _metric_increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        if self.config.metrics_enabled:
            await self.metrics_sink.increment(name, value, tags)

    async def _metric_observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        if self.config.metrics_enabled:
            await self.metrics_sink.observe(name, value, tags)

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if self.config.audit_enabled:
            await self.audit_sink.emit(event_name, payload)

    async def _audit_search_completed(self, query: SearchQuery, response: SearchResponse) -> None:
        await self._audit("semantic_search_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": query.context.request_id,
            "tenant_id": query.context.tenant_id,
            "user_id": query.context.user_id,
            "application": query.context.application,
            "domain": query.context.domain,
            "trace_id": query.context.trace_id,
            "query_hash": stable_hash(query.query),
            "mode": query.mode.value,
            "ranking_strategy": query.ranking_strategy.value,
            "result_count": len(response.results),
            "total_candidates": response.total_candidates,
            "top_result_ids": [item.document.id for item in response.results[:5]],
            "latency_ms": response.metadata.get("latency_ms"),
        })


# =============================================================================
# RAG adapter
# =============================================================================


class SemanticSearchRetrieverAdapter:
    """Adapter exposing SemanticSearchEngine as rag_pipeline.Retriever."""

    def __init__(self, engine: SemanticSearchEngine, *, name: str = "semantic_search") -> None:
        self.engine = engine
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def retrieve(self, query: Any, rewritten_query: Optional[str], top_k: int) -> Sequence[Any]:
        search_query = SearchQuery(
            query=rewritten_query or getattr(query, "query"),
            top_k=top_k,
            mode=SearchMode.HYBRID,
            ranking_strategy=RankingStrategy.HYBRID_THEN_MMR,
            filters=self._filters_from_rag_query(query),
            context=self._context_from_rag_query(query),
        )
        response = await self.engine.search(search_query)
        return tuple(self._to_retrieved_document(result) for result in response.results)

    def _filters_from_rag_query(self, query: Any) -> SearchFilter:
        filters = getattr(query, "filters", None)
        if filters is None:
            return SearchFilter()
        return SearchFilter(
            source_ids=tuple(getattr(filters, "source_ids", ()) or ()),
            document_ids=tuple(getattr(filters, "document_ids", ()) or ()),
            include_tags=tuple(getattr(filters, "allowed_tags", ()) or ()),
            exclude_tags=tuple(getattr(filters, "denied_tags", ()) or ()),
            metadata_equals=getattr(filters, "metadata_equals", {}) or {},
            created_after=getattr(filters, "created_after", None),
            created_before=getattr(filters, "created_before", None),
        )

    def _context_from_rag_query(self, query: Any) -> SearchContext:
        context = getattr(query, "context")
        return SearchContext(
            request_id=getattr(context, "request_id", str(uuid.uuid4())),
            tenant_id=getattr(context, "tenant_id", None),
            user_id=getattr(context, "user_id", None),
            application=getattr(context, "application", None),
            domain=getattr(context, "domain", None),
            locale=getattr(context, "locale", None),
            trace_id=getattr(context, "trace_id", None),
            metadata=getattr(context, "metadata", {}) or {},
        )

    def _to_retrieved_document(self, result: SearchResult) -> Any:
        try:
            from data.ai.rag_pipeline import RetrievedDocument
        except Exception:  # noqa: BLE001
            from rag_pipeline import RetrievedDocument  # type: ignore

        doc = result.document
        return RetrievedDocument(
            id=doc.id,
            text=doc.text,
            source_id=doc.source_id,
            document_id=doc.document_id,
            title=doc.title,
            url=doc.url,
            score=result.score.final_score,
            rank=result.rank,
            retriever=self.name,
            created_at=doc.created_at,
            tags=doc.tags,
            metadata={**dict(doc.metadata), "search_score": asdict(result.score)},
        )


# =============================================================================
# Factory helpers
# =============================================================================


def build_default_semantic_search_engine(
    *,
    documents: Optional[Sequence[SearchDocument]] = None,
    config_overrides: Optional[Mapping[str, Any]] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> SemanticSearchEngine:
    config_data = asdict(SemanticSearchConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = SemanticSearchConfig(**config_data)
    engine = SemanticSearchEngine(config=config, embedding_provider=embedding_provider or HashingEmbeddingProvider())
    if documents:
        asyncio.run(engine.index_documents(documents))
    return engine


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    docs = (
        SearchDocument(
            id="doc-001-chunk-001",
            document_id="doc-001",
            source_id="policy",
            title="AI Governance Policy",
            text="All AI-generated financial recommendations must be reviewed by a qualified analyst before publication.",
            tags=("finance", "governance"),
            sensitivity=SensitivityLevel.INTERNAL,
        ),
        SearchDocument(
            id="doc-002-chunk-001",
            document_id="doc-002",
            source_id="engineering",
            title="RAG Architecture Guide",
            text="A robust RAG pipeline combines semantic retrieval, keyword retrieval, reranking, context packing and grounded generation.",
            tags=("rag", "architecture"),
            sensitivity=SensitivityLevel.INTERNAL,
        ),
    )

    engine = SemanticSearchEngine()
    await engine.index_documents(docs)
    response = await engine.search(
        SearchQuery(
            query="Como funciona uma arquitetura RAG robusta?",
            top_k=5,
            context=SearchContext(tenant_id="demo", application="ai-platform", domain="architecture"),
        )
    )
    print(response.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())
