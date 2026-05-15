"""
data/ai/rag_pipeline.py

Enterprise-grade Retrieval-Augmented Generation (RAG) pipeline.

This module orchestrates robust RAG workflows for production AI platforms:

- Query normalization and optional rewrite/expansion
- Multi-retriever support: vector, keyword, hybrid, graph, SQL/document stores
- Reciprocal Rank Fusion (RRF)
- Metadata/security filtering
- Diversity-aware context selection
- Cross-encoder/LLM reranker hooks
- Context packing with token/character budgets
- Prompt enrichment integration
- Inference pipeline integration
- Citation grounding
- Faithfulness/hallucination validation hooks
- Cache, audit, metrics and explainable trace output
- Batch RAG execution

Recommended package position:
    data/ai/rag_pipeline.py

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class RagPipelineError(Exception):
    """Base exception for RAG pipeline errors."""


class RagConfigurationError(RagPipelineError):
    """Raised when RAG configuration is invalid."""


class RagValidationError(RagPipelineError):
    """Raised when RAG input is invalid."""


class RetrievalError(RagPipelineError):
    """Raised when retrieval fails."""


class GenerationError(RagPipelineError):
    """Raised when answer generation fails."""


class GroundingError(RagPipelineError):
    """Raised when grounding policy fails."""


# =============================================================================
# Enums
# =============================================================================


class RetrievalMode(str, Enum):
    """Retrieval mode."""

    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"
    GRAPH = "graph"
    CUSTOM = "custom"


class FusionStrategy(str, Enum):
    """How results from multiple retrievers are fused."""

    NONE = "none"
    SCORE_SUM = "score_sum"
    MAX_SCORE = "max_score"
    RRF = "reciprocal_rank_fusion"


class ContextPackingStrategy(str, Enum):
    """How selected chunks are packed into context."""

    SCORE_DESC = "score_desc"
    SOURCE_DIVERSITY = "source_diversity"
    DOCUMENT_ORDER = "document_order"
    RECENCY_DESC = "recency_desc"


class CitationStyle(str, Enum):
    """Citation output style."""

    BRACKET = "bracket"  # [C1]
    INLINE_SOURCE = "inline_source"
    FOOTNOTE = "footnote"
    NONE = "none"


class GroundingPolicy(str, Enum):
    """Grounding enforcement policy."""

    OFF = "off"
    WARN = "warn"
    REQUIRE_CONTEXT = "require_context"
    REQUIRE_CITATIONS = "require_citations"
    BLOCK_UNGROUNDED = "block_ungrounded"


class RagDecision(str, Enum):
    """Final RAG decision."""

    ANSWERED = "answered"
    ANSWERED_WITH_WARNINGS = "answered_with_warnings"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    BLOCKED = "blocked"
    FAILED = "failed"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class RagConfig:
    """RAG pipeline configuration."""

    top_k_per_retriever: int = 20
    final_top_k: int = 8
    min_relevance_score: float = 0.0
    fusion_strategy: FusionStrategy = FusionStrategy.RRF
    rrf_k: int = 60
    max_context_chars: int = 40_000
    max_query_chars: int = 8_000
    context_packing_strategy: ContextPackingStrategy = ContextPackingStrategy.SOURCE_DIVERSITY
    citation_style: CitationStyle = CitationStyle.BRACKET
    grounding_policy: GroundingPolicy = GroundingPolicy.REQUIRE_CITATIONS
    enable_query_rewrite: bool = True
    enable_reranking: bool = True
    enable_generation: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 900
    fail_open_on_retriever_error: bool = True
    include_raw_documents: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.top_k_per_retriever <= 0:
            raise RagConfigurationError("top_k_per_retriever must be positive")
        if self.final_top_k <= 0:
            raise RagConfigurationError("final_top_k must be positive")
        if not 0 <= self.min_relevance_score <= 1:
            raise RagConfigurationError("min_relevance_score must be between 0 and 1")
        if self.rrf_k <= 0:
            raise RagConfigurationError("rrf_k must be positive")
        if self.max_context_chars < 0:
            raise RagConfigurationError("max_context_chars must be >= 0")
        if self.max_query_chars <= 0:
            raise RagConfigurationError("max_query_chars must be positive")
        if self.cache_ttl_seconds <= 0:
            raise RagConfigurationError("cache_ttl_seconds must be positive")


@dataclass(frozen=True)
class RagContext:
    """Request context for RAG execution."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    domain: Optional[str] = None
    locale: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalFilter:
    """Structured filter applied to retrievers."""

    source_ids: Sequence[str] = field(default_factory=tuple)
    document_ids: Sequence[str] = field(default_factory=tuple)
    allowed_tags: Sequence[str] = field(default_factory=tuple)
    denied_tags: Sequence[str] = field(default_factory=tuple)
    metadata_equals: Mapping[str, Any] = field(default_factory=dict)
    created_after: Optional[str] = None
    created_before: Optional[str] = None
    security_labels: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class RagQuery:
    """Input query for RAG."""

    query: str
    context: RagContext = field(default_factory=RagContext)
    filters: RetrievalFilter = field(default_factory=RetrievalFilter)
    retrieval_mode: RetrievalMode = RetrievalMode.HYBRID
    top_k: Optional[int] = None
    generation_instructions: Optional[str] = None
    output_format: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedDocument:
    """Document/chunk returned by a retriever."""

    id: str
    text: str
    source_id: Optional[str] = None
    document_id: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    score: float = 0.0
    rank: Optional[int] = None
    retriever: Optional[str] = None
    created_at: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FusedDocument:
    """Document after fusion/reranking."""

    document: RetrievedDocument
    fused_score: float
    fused_rank: int
    source_scores: Mapping[str, float] = field(default_factory=dict)
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class PackedContext:
    """Packed context used for generation."""

    text: str
    documents: Sequence[FusedDocument]
    citations: Mapping[str, str]
    chars_used: int
    truncated: bool = False
    warnings: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class RagTraceEvent:
    """Trace event for explainability."""

    stage: str
    latency_ms: float
    success: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagAnswer:
    """Generated RAG answer."""

    text: str
    model: Optional[str] = None
    provider: Optional[str] = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundingAssessment:
    """Grounding and citation assessment."""

    grounded: bool
    citation_count: int
    missing_citation_warning: bool
    unsupported_claim_warning: bool
    score: float
    warnings: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RagResult:
    """Final RAG result."""

    request_id: str
    decision: RagDecision
    query: str
    rewritten_query: Optional[str]
    answer: Optional[RagAnswer]
    packed_context: PackedContext
    grounding: GroundingAssessment
    trace: Sequence[RagTraceEvent]
    warnings: Sequence[str]
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class BatchRagResult:
    """Batch RAG result."""

    batch_id: str
    created_at: str
    total_items: int
    results: Sequence[RagResult]
    failures: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


# =============================================================================
# Protocols
# =============================================================================


class Retriever(Protocol):
    """Retriever protocol."""

    @property
    def name(self) -> str:
        """Retriever name."""

    async def retrieve(self, query: RagQuery, rewritten_query: Optional[str], top_k: int) -> Sequence[RetrievedDocument]:
        """Retrieve relevant documents."""


class QueryRewriter(Protocol):
    """Query rewrite/expansion protocol."""

    async def rewrite(self, query: RagQuery) -> str:
        """Return rewritten query."""


class Reranker(Protocol):
    """Reranker protocol."""

    async def rerank(self, query: RagQuery, documents: Sequence[FusedDocument], top_k: int) -> Sequence[FusedDocument]:
        """Return reranked documents."""


class AnswerGenerator(Protocol):
    """Answer generation protocol."""

    async def generate(self, query: RagQuery, context: PackedContext) -> RagAnswer:
        """Generate an answer using query and packed context."""


class RagCache(Protocol):
    """Cache protocol."""

    async def get(self, key: str) -> Optional[RagResult]:
        """Get cached result."""

    async def set(self, key: str, value: RagResult, ttl_seconds: int) -> None:
        """Store cached result."""


class AuditSink(Protocol):
    """Audit sink protocol."""

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    """Metrics sink protocol."""

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment counter."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric value."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def tokenize(text: str) -> List[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower(), flags=re.UNICODE)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokenize(a)), set(tokenize(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def build_cache_key(query: RagQuery, config: RagConfig) -> str:
    payload = {
        "query": normalize_query(query.query),
        "tenant_id": query.context.tenant_id,
        "application": query.context.application,
        "domain": query.context.domain,
        "filters": asdict(query.filters),
        "retrieval_mode": query.retrieval_mode.value,
        "top_k": query.top_k,
        "generation_instructions": query.generation_instructions,
        "output_format": query.output_format,
        "config": {
            "final_top_k": config.final_top_k,
            "fusion_strategy": config.fusion_strategy.value,
            "grounding_policy": config.grounding_policy.value,
        },
    }
    return stable_hash(safe_json(payload))


# =============================================================================
# Default Sinks / Cache
# =============================================================================


class InMemoryRagCache:
    """Simple async in-memory TTL cache."""

    def __init__(self, max_items: int = 2048) -> None:
        self.max_items = max_items
        self._items: Dict[str, Tuple[float, RagResult]] = {}
        self._order: List[str] = []
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[RagResult]:
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.time():
                self._items.pop(key, None)
                if key in self._order:
                    self._order.remove(key)
                return None
            return value

    async def set(self, key: str, value: RagResult, ttl_seconds: int) -> None:
        async with self._lock:
            self._items[key] = (time.time() + ttl_seconds, value)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            while len(self._order) > self.max_items:
                oldest = self._order.pop(0)
                self._items.pop(oldest, None)


class LoggingAuditSink:
    """Audit sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("rag_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("rag_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("rag_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Default Implementations
# =============================================================================


class SimpleQueryRewriter:
    """Deterministic query normalizer/expander fallback."""

    async def rewrite(self, query: RagQuery) -> str:
        await asyncio.sleep(0)
        normalized = normalize_query(query.query)
        domain = query.context.domain
        if domain and domain.lower() not in normalized.lower():
            return f"{normalized} domain:{domain}"
        return normalized


class InMemoryKeywordRetriever:
    """Dependency-light keyword retriever for tests/local usage."""

    def __init__(self, documents: Sequence[RetrievedDocument], *, name: str = "memory_keyword") -> None:
        self._documents = tuple(documents)
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def retrieve(self, query: RagQuery, rewritten_query: Optional[str], top_k: int) -> Sequence[RetrievedDocument]:
        await asyncio.sleep(0)
        q = rewritten_query or query.query
        results: List[RetrievedDocument] = []
        for doc in self._documents:
            if not self._passes_filters(doc, query.filters):
                continue
            score = max(jaccard(q, doc.text), jaccard(query.query, doc.text), doc.score)
            if score <= 0:
                continue
            results.append(
                RetrievedDocument(
                    id=doc.id,
                    text=doc.text,
                    source_id=doc.source_id,
                    document_id=doc.document_id,
                    title=doc.title,
                    url=doc.url,
                    score=clamp(score),
                    retriever=self.name,
                    created_at=doc.created_at,
                    tags=doc.tags,
                    metadata=doc.metadata,
                )
            )
        ranked = sorted(results, key=lambda d: d.score, reverse=True)[:top_k]
        return tuple(
            RetrievedDocument(**{**asdict(doc), "rank": index + 1})
            for index, doc in enumerate(ranked)
        )

    def _passes_filters(self, doc: RetrievedDocument, filters: RetrievalFilter) -> bool:
        if filters.source_ids and doc.source_id not in filters.source_ids:
            return False
        if filters.document_ids and doc.document_id not in filters.document_ids:
            return False
        doc_tags = set(doc.tags or ())
        if filters.allowed_tags and not set(filters.allowed_tags).issubset(doc_tags):
            return False
        if filters.denied_tags and set(filters.denied_tags) & doc_tags:
            return False
        for key, expected in filters.metadata_equals.items():
            if doc.metadata.get(key) != expected:
                return False
        if filters.created_after and doc.created_at and doc.created_at < filters.created_after:
            return False
        if filters.created_before and doc.created_at and doc.created_at > filters.created_before:
            return False
        return True


class SimpleReranker:
    """Lightweight reranker using lexical overlap plus existing fused score."""

    async def rerank(self, query: RagQuery, documents: Sequence[FusedDocument], top_k: int) -> Sequence[FusedDocument]:
        await asyncio.sleep(0)
        scored: List[FusedDocument] = []
        for item in documents:
            lexical = jaccard(query.query, item.document.text)
            score = clamp((item.fused_score * 0.70) + (lexical * 0.30))
            scored.append(
                FusedDocument(
                    document=item.document,
                    fused_score=score,
                    fused_rank=item.fused_rank,
                    source_scores=item.source_scores,
                    reasons=tuple(list(item.reasons) + [f"rerank_lexical={lexical:.3f}"]),
                )
            )
        ranked = sorted(scored, key=lambda d: d.fused_score, reverse=True)[:top_k]
        return tuple(
            FusedDocument(
                document=item.document,
                fused_score=item.fused_score,
                fused_rank=index + 1,
                source_scores=item.source_scores,
                reasons=item.reasons,
            )
            for index, item in enumerate(ranked)
        )


class ExtractiveAnswerGenerator:
    """Fallback generator that creates an extractive answer from context.

    Production deployments should inject an LLM-backed generator, usually through
    inference_pipeline.py and prompt_enrichment.py.
    """

    async def generate(self, query: RagQuery, context: PackedContext) -> RagAnswer:
        await asyncio.sleep(0)
        if not context.documents:
            return RagAnswer(text="Não encontrei contexto suficiente para responder com segurança.")
        lines = ["Com base no contexto recuperado:"]
        for citation, doc_id in context.citations.items():
            doc = next((item.document for item in context.documents if item.document.id == doc_id), None)
            if not doc:
                continue
            snippet = re.sub(r"\s+", " ", doc.text).strip()[:350]
            lines.append(f"- {snippet} {citation}")
        return RagAnswer(text="\n".join(lines), metadata={"generator": "extractive_fallback"})


class InferencePipelineAnswerGenerator:
    """LLM-backed generator adapter for inference_pipeline.py."""

    def __init__(self, inference_pipeline: Any, *, system_prompt: Optional[str] = None) -> None:
        self.inference_pipeline = inference_pipeline
        self.system_prompt = system_prompt or (
            "Você é um assistente RAG enterprise. Responda somente com base no contexto fornecido. "
            "Se o contexto não for suficiente, diga isso claramente. Use citações como [C1], [C2]."
        )

    async def generate(self, query: RagQuery, context: PackedContext) -> RagAnswer:
        try:
            from data.ai.inference_pipeline import ChatMessage, InferenceContext, InferenceMode, InferenceOptions, InferenceRequest, MessageRole
        except Exception:  # noqa: BLE001
            from inference_pipeline import ChatMessage, InferenceContext, InferenceMode, InferenceOptions, InferenceRequest, MessageRole  # type: ignore

        instructions = query.generation_instructions or "Responda de forma objetiva, precisa e fundamentada."
        user_content = (
            f"# Pergunta\n{query.query}\n\n"
            f"# Contexto recuperado\n{context.text}\n\n"
            f"# Instruções\n{instructions}\n"
        )
        if query.output_format:
            user_content += f"\nFormato desejado: {query.output_format}\n"

        request = InferenceRequest(
            mode=InferenceMode.CHAT,
            messages=(
                ChatMessage(role=MessageRole.SYSTEM, content=self.system_prompt),
                ChatMessage(role=MessageRole.USER, content=user_content),
            ),
            options=InferenceOptions(temperature=0.1, max_tokens=1800),
            context=InferenceContext(
                request_id=query.context.request_id,
                tenant_id=query.context.tenant_id,
                user_id=query.context.user_id,
                application=query.context.application,
                domain=query.context.domain,
                locale=query.context.locale,
                trace_id=query.context.trace_id,
                metadata=query.context.metadata,
            ),
            metadata={"rag": True},
        )
        response = await self.inference_pipeline.run(request)
        return RagAnswer(
            text=getattr(response, "output_text", ""),
            model=getattr(response, "model", None),
            provider=getattr(response, "provider", None),
            usage=asdict(getattr(response, "usage", {})) if not isinstance(getattr(response, "usage", {}), dict) else getattr(response, "usage", {}),
            metadata=getattr(response, "metadata", {}) or {},
        )


# =============================================================================
# RAG Pipeline
# =============================================================================


class RagPipeline:
    """Enterprise RAG pipeline."""

    def __init__(
        self,
        *,
        config: Optional[RagConfig] = None,
        retrievers: Sequence[Retriever],
        query_rewriter: Optional[QueryRewriter] = None,
        reranker: Optional[Reranker] = None,
        answer_generator: Optional[AnswerGenerator] = None,
        cache: Optional[RagCache] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or RagConfig()
        self.config.validate()
        if not retrievers:
            raise RagConfigurationError("At least one retriever is required")
        self.retrievers = tuple(retrievers)
        self.query_rewriter = query_rewriter or SimpleQueryRewriter()
        self.reranker = reranker or SimpleReranker()
        self.answer_generator = answer_generator or ExtractiveAnswerGenerator()
        self.cache = cache or InMemoryRagCache()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()

    async def run(self, query: RagQuery) -> RagResult:
        """Execute a full RAG request."""

        started = time.perf_counter()
        trace: List[RagTraceEvent] = []
        warnings: List[str] = []
        rewritten_query: Optional[str] = None

        try:
            await self._stage("validation", trace, lambda: self._validate_query(query))

            cache_key = build_cache_key(query, self.config)
            if self.config.enable_cache:
                cached = await self._stage("cache_lookup", trace, lambda: self.cache.get(cache_key))
                if cached is not None:
                    await self._record_success(query, cached, cached=True, latency_ms=(time.perf_counter() - started) * 1000)
                    await self._audit("rag_cache_hit", query, cached)
                    return cached

            if self.config.enable_query_rewrite:
                rewritten_query = await self._stage("query_rewrite", trace, lambda: self.query_rewriter.rewrite(query))

            retrieved_groups = await self._stage("retrieval", trace, lambda: self._retrieve_all(query, rewritten_query))
            fused = await self._stage("fusion", trace, lambda: self._fuse_results(retrieved_groups))
            filtered = [item for item in fused if item.fused_score >= self.config.min_relevance_score]
            if not filtered:
                warnings.append("No retrieved context met the minimum relevance threshold.")

            final_top_k = query.top_k or self.config.final_top_k
            if self.config.enable_reranking and filtered:
                ranked = list(await self._stage("reranking", trace, lambda: self.reranker.rerank(query, filtered, final_top_k)))
            else:
                ranked = filtered[:final_top_k]

            packed_context = await self._stage("context_packing", trace, lambda: self._pack_context(ranked))
            warnings.extend(packed_context.warnings)

            grounding_pre = self._pre_generation_grounding_check(packed_context)
            if grounding_pre and self.config.grounding_policy in {GroundingPolicy.REQUIRE_CONTEXT, GroundingPolicy.BLOCK_UNGROUNDED}:
                result = self._build_result(
                    query=query,
                    rewritten_query=rewritten_query,
                    answer=None,
                    packed_context=packed_context,
                    grounding=grounding_pre,
                    trace=trace,
                    warnings=warnings + list(grounding_pre.warnings),
                    decision=RagDecision.INSUFFICIENT_CONTEXT,
                    started=started,
                )
                await self._maybe_cache(cache_key, result)
                await self._record_success(query, result, cached=False, latency_ms=(time.perf_counter() - started) * 1000)
                await self._audit("rag_completed_insufficient_context", query, result)
                return result

            answer: Optional[RagAnswer] = None
            if self.config.enable_generation:
                answer = await self._stage("generation", trace, lambda: self.answer_generator.generate(query, packed_context))
            else:
                answer = RagAnswer(text="", metadata={"generation_disabled": True})

            grounding = await self._stage("grounding_assessment", trace, lambda: self._assess_grounding(answer, packed_context))
            warnings.extend(grounding.warnings)
            decision = self._decision_from_grounding(grounding, warnings)

            if decision == RagDecision.BLOCKED:
                raise GroundingError("RAG answer blocked by grounding policy")

            result = self._build_result(
                query=query,
                rewritten_query=rewritten_query,
                answer=answer,
                packed_context=packed_context,
                grounding=grounding,
                trace=trace,
                warnings=warnings,
                decision=decision,
                started=started,
            )
            await self._maybe_cache(cache_key, result)
            await self._record_success(query, result, cached=False, latency_ms=(time.perf_counter() - started) * 1000)
            await self._audit("rag_completed", query, result)
            return result

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(query, exc, latency_ms)
            await self._audit_failure("rag_failed", query, exc, trace, latency_ms)
            raise

    async def run_batch(
        self,
        queries: Sequence[RagQuery],
        *,
        concurrency: int = 5,
        continue_on_error: bool = True,
    ) -> BatchRagResult:
        """Execute multiple RAG queries with bounded concurrency."""

        if concurrency <= 0:
            raise RagValidationError("concurrency must be positive")
        batch_id = str(uuid.uuid4())
        semaphore = asyncio.Semaphore(concurrency)
        results: List[Optional[RagResult]] = [None] * len(queries)
        failures: List[Mapping[str, Any]] = []

        async def run_one(index: int, item: RagQuery) -> None:
            async with semaphore:
                try:
                    results[index] = await self.run(item)
                except Exception as exc:  # noqa: BLE001
                    failures.append({
                        "index": index,
                        "request_id": item.context.request_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
                    if not continue_on_error:
                        raise

        await asyncio.gather(*(run_one(i, query) for i, query in enumerate(queries)))
        return BatchRagResult(
            batch_id=batch_id,
            created_at=utc_now_iso(),
            total_items=len(queries),
            results=tuple(item for item in results if item is not None),
            failures=tuple(failures),
        )

    def _validate_query(self, query: RagQuery) -> None:
        if not query.query or not query.query.strip():
            raise RagValidationError("query must be non-empty")
        if len(query.query) > self.config.max_query_chars:
            raise RagValidationError(f"query exceeds max_query_chars: {len(query.query)} > {self.config.max_query_chars}")

    async def _retrieve_all(self, query: RagQuery, rewritten_query: Optional[str]) -> Mapping[str, Sequence[RetrievedDocument]]:
        async def call_retriever(retriever: Retriever) -> Tuple[str, Sequence[RetrievedDocument]]:
            try:
                docs = await retriever.retrieve(query, rewritten_query, self.config.top_k_per_retriever)
                return retriever.name, docs
            except Exception as exc:  # noqa: BLE001
                logger.exception("Retriever failed: %s", retriever.name)
                if not self.config.fail_open_on_retriever_error:
                    raise RetrievalError(f"Retriever failed: {retriever.name}: {exc}") from exc
                return retriever.name, tuple()

        pairs = await asyncio.gather(*(call_retriever(retriever) for retriever in self.retrievers))
        return dict(pairs)

    def _fuse_results(self, groups: Mapping[str, Sequence[RetrievedDocument]]) -> Sequence[FusedDocument]:
        if self.config.fusion_strategy == FusionStrategy.NONE:
            flat = [doc for docs in groups.values() for doc in docs]
            ranked = sorted(flat, key=lambda d: d.score, reverse=True)
            return tuple(
                FusedDocument(document=doc, fused_score=clamp(doc.score), fused_rank=i + 1, source_scores={doc.retriever or "unknown": doc.score})
                for i, doc in enumerate(ranked)
            )

        by_id: Dict[str, RetrievedDocument] = {}
        scores: Dict[str, Dict[str, float]] = {}
        rank_scores: Dict[str, float] = {}

        for retriever_name, docs in groups.items():
            for index, doc in enumerate(docs):
                by_id.setdefault(doc.id, doc)
                scores.setdefault(doc.id, {})[retriever_name] = doc.score
                rank = doc.rank or index + 1
                if self.config.fusion_strategy == FusionStrategy.RRF:
                    rank_scores[doc.id] = rank_scores.get(doc.id, 0.0) + (1.0 / (self.config.rrf_k + rank))
                elif self.config.fusion_strategy == FusionStrategy.SCORE_SUM:
                    rank_scores[doc.id] = rank_scores.get(doc.id, 0.0) + doc.score
                elif self.config.fusion_strategy == FusionStrategy.MAX_SCORE:
                    rank_scores[doc.id] = max(rank_scores.get(doc.id, 0.0), doc.score)

        max_score = max(rank_scores.values(), default=1.0)
        fused: List[FusedDocument] = []
        for doc_id, raw_score in rank_scores.items():
            normalized = raw_score / max_score if max_score else 0.0
            fused.append(
                FusedDocument(
                    document=by_id[doc_id],
                    fused_score=clamp(normalized),
                    fused_rank=0,
                    source_scores=scores.get(doc_id, {}),
                    reasons=(f"fusion={self.config.fusion_strategy.value}",),
                )
            )
        ranked = sorted(fused, key=lambda item: item.fused_score, reverse=True)
        return tuple(
            FusedDocument(
                document=item.document,
                fused_score=item.fused_score,
                fused_rank=index + 1,
                source_scores=item.source_scores,
                reasons=item.reasons,
            )
            for index, item in enumerate(ranked)
        )

    def _pack_context(self, documents: Sequence[FusedDocument]) -> PackedContext:
        if not documents or self.config.max_context_chars == 0:
            return PackedContext(text="", documents=tuple(), citations={}, chars_used=0, warnings=("No context documents selected.",))

        ordered = self._order_for_packing(documents)
        selected: List[FusedDocument] = []
        citations: Dict[str, str] = {}
        blocks: List[str] = []
        chars_used = 0
        truncated = False
        warnings: List[str] = []

        for index, item in enumerate(ordered, start=1):
            citation = self._citation_marker(index, item.document)
            block = self._format_context_block(citation, item)
            remaining = self.config.max_context_chars - chars_used
            if remaining <= 0:
                truncated = True
                break
            if len(block) > remaining:
                block = block[: max(0, remaining - 18)].rstrip() + "\n...[TRUNCATED]"
                truncated = True
            blocks.append(block)
            selected.append(item)
            citations[citation] = item.document.id
            chars_used += len(block) + 2
            if len(selected) >= self.config.final_top_k:
                break

        if truncated:
            warnings.append("Context was truncated to fit max_context_chars.")

        return PackedContext(
            text="\n\n".join(blocks),
            documents=tuple(selected),
            citations=citations,
            chars_used=min(chars_used, self.config.max_context_chars),
            truncated=truncated,
            warnings=tuple(warnings),
        )

    def _order_for_packing(self, documents: Sequence[FusedDocument]) -> List[FusedDocument]:
        if self.config.context_packing_strategy == ContextPackingStrategy.DOCUMENT_ORDER:
            return sorted(documents, key=lambda item: (item.document.document_id or "", item.document.rank or item.fused_rank))
        if self.config.context_packing_strategy == ContextPackingStrategy.RECENCY_DESC:
            return sorted(documents, key=lambda item: item.document.created_at or "", reverse=True)
        if self.config.context_packing_strategy == ContextPackingStrategy.SOURCE_DIVERSITY:
            return self._source_diversity_order(documents)
        return sorted(documents, key=lambda item: item.fused_score, reverse=True)

    def _source_diversity_order(self, documents: Sequence[FusedDocument]) -> List[FusedDocument]:
        by_source: Dict[str, List[FusedDocument]] = {}
        for item in sorted(documents, key=lambda d: d.fused_score, reverse=True):
            by_source.setdefault(item.document.source_id or item.document.document_id or "unknown", []).append(item)
        ordered: List[FusedDocument] = []
        while any(by_source.values()):
            for source in list(by_source.keys()):
                if by_source[source]:
                    ordered.append(by_source[source].pop(0))
        return ordered

    def _citation_marker(self, index: int, doc: RetrievedDocument) -> str:
        if self.config.citation_style == CitationStyle.NONE:
            return ""
        if self.config.citation_style == CitationStyle.INLINE_SOURCE:
            return f"[{doc.source_id or doc.document_id or index}]"
        if self.config.citation_style == CitationStyle.FOOTNOTE:
            return f"[^{index}]"
        return f"[C{index}]"

    def _format_context_block(self, citation: str, item: FusedDocument) -> str:
        doc = item.document
        header = [citation or "Context"]
        if doc.title:
            header.append(f"Title: {doc.title}")
        if doc.source_id:
            header.append(f"Source: {doc.source_id}")
        if doc.url:
            header.append(f"URL: {doc.url}")
        header.append(f"Score: {item.fused_score:.3f}")
        return " | ".join(header) + "\n" + doc.text.strip()

    def _pre_generation_grounding_check(self, context: PackedContext) -> Optional[GroundingAssessment]:
        if context.documents:
            return None
        return GroundingAssessment(
            grounded=False,
            citation_count=0,
            missing_citation_warning=True,
            unsupported_claim_warning=True,
            score=0.0,
            warnings=("No retrieved context is available for grounded generation.",),
        )

    def _assess_grounding(self, answer: Optional[RagAnswer], context: PackedContext) -> GroundingAssessment:
        text = answer.text if answer else ""
        citation_count = 0
        for citation in context.citations.keys():
            if citation and citation in text:
                citation_count += 1

        has_context = bool(context.documents)
        requires_citations = self.config.grounding_policy in {
            GroundingPolicy.REQUIRE_CITATIONS,
            GroundingPolicy.BLOCK_UNGROUNDED,
        }
        missing_citation = requires_citations and has_context and citation_count == 0
        unsupported_warning = bool(text.strip()) and not has_context
        score = 0.0
        if has_context:
            score += 0.55
        if citation_count > 0:
            score += 0.35
        if not missing_citation and not unsupported_warning:
            score += 0.10
        score = clamp(score)

        warnings: List[str] = []
        if missing_citation:
            warnings.append("Answer does not contain citations to retrieved context.")
        if unsupported_warning:
            warnings.append("Answer was generated without retrieved context.")

        return GroundingAssessment(
            grounded=score >= 0.65 and not missing_citation and not unsupported_warning,
            citation_count=citation_count,
            missing_citation_warning=missing_citation,
            unsupported_claim_warning=unsupported_warning,
            score=score,
            warnings=tuple(warnings),
        )

    def _decision_from_grounding(self, grounding: GroundingAssessment, warnings: Sequence[str]) -> RagDecision:
        if self.config.grounding_policy == GroundingPolicy.OFF:
            return RagDecision.ANSWERED
        if self.config.grounding_policy == GroundingPolicy.WARN:
            return RagDecision.ANSWERED_WITH_WARNINGS if warnings else RagDecision.ANSWERED
        if self.config.grounding_policy == GroundingPolicy.REQUIRE_CONTEXT and grounding.unsupported_claim_warning:
            return RagDecision.INSUFFICIENT_CONTEXT
        if self.config.grounding_policy == GroundingPolicy.REQUIRE_CITATIONS and grounding.missing_citation_warning:
            return RagDecision.ANSWERED_WITH_WARNINGS
        if self.config.grounding_policy == GroundingPolicy.BLOCK_UNGROUNDED and not grounding.grounded:
            return RagDecision.BLOCKED
        return RagDecision.ANSWERED_WITH_WARNINGS if warnings else RagDecision.ANSWERED

    def _build_result(
        self,
        *,
        query: RagQuery,
        rewritten_query: Optional[str],
        answer: Optional[RagAnswer],
        packed_context: PackedContext,
        grounding: GroundingAssessment,
        trace: Sequence[RagTraceEvent],
        warnings: Sequence[str],
        decision: RagDecision,
        started: float,
    ) -> RagResult:
        context_to_return = packed_context
        if not self.config.include_raw_documents:
            redacted_docs = tuple(
                FusedDocument(
                    document=RetrievedDocument(
                        id=item.document.id,
                        text="",
                        source_id=item.document.source_id,
                        document_id=item.document.document_id,
                        title=item.document.title,
                        url=item.document.url,
                        score=item.document.score,
                        rank=item.document.rank,
                        retriever=item.document.retriever,
                        created_at=item.document.created_at,
                        tags=item.document.tags,
                        metadata=item.document.metadata,
                    ),
                    fused_score=item.fused_score,
                    fused_rank=item.fused_rank,
                    source_scores=item.source_scores,
                    reasons=item.reasons,
                )
                for item in packed_context.documents
            )
            context_to_return = PackedContext(
                text=packed_context.text,
                documents=redacted_docs,
                citations=packed_context.citations,
                chars_used=packed_context.chars_used,
                truncated=packed_context.truncated,
                warnings=packed_context.warnings,
            )

        return RagResult(
            request_id=query.context.request_id,
            decision=decision,
            query=query.query,
            rewritten_query=rewritten_query,
            answer=answer,
            packed_context=context_to_return,
            grounding=grounding,
            trace=tuple(trace),
            warnings=tuple(dict.fromkeys(warnings)),
            created_at=utc_now_iso(),
            metadata={
                "rag_version": self.config.version,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "retriever_count": len(self.retrievers),
                "context_chars": packed_context.chars_used,
                "context_documents": len(packed_context.documents),
            },
        )

    async def _maybe_cache(self, cache_key: str, result: RagResult) -> None:
        if self.config.enable_cache:
            await self.cache.set(cache_key, result, self.config.cache_ttl_seconds)

    async def _stage(self, name: str, trace: List[RagTraceEvent], func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            trace.append(RagTraceEvent(stage=name, latency_ms=(time.perf_counter() - started) * 1000, success=True))
            return result
        except Exception as exc:
            trace.append(
                RagTraceEvent(
                    stage=name,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    success=False,
                    metadata={"error_type": type(exc).__name__, "error": str(exc)},
                )
            )
            raise

    async def _record_success(self, query: RagQuery, result: RagResult, *, cached: bool, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(query, cached=cached)
        await self.metrics_sink.increment("ai.rag.success", 1, tags)
        await self.metrics_sink.observe("ai.rag.latency_ms", latency_ms, tags)
        await self.metrics_sink.observe("ai.rag.context_documents", len(result.packed_context.documents), tags)
        await self.metrics_sink.observe("ai.rag.grounding_score", result.grounding.score, tags)

    async def _record_failure(self, query: RagQuery, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(query, cached=False), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("ai.rag.failure", 1, tags)
        await self.metrics_sink.observe("ai.rag.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, query: RagQuery, *, cached: bool) -> Mapping[str, str]:
        return {
            "tenant_id": query.context.tenant_id or "unknown",
            "application": query.context.application or "unknown",
            "domain": query.context.domain or "unknown",
            "retrieval_mode": query.retrieval_mode.value,
            "cached": str(cached).lower(),
        }

    async def _audit(self, event_name: str, query: RagQuery, result: RagResult) -> None:
        if not self.config.audit_enabled:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": query.context.request_id,
            "tenant_id": query.context.tenant_id,
            "user_id": query.context.user_id,
            "application": query.context.application,
            "domain": query.context.domain,
            "trace_id": query.context.trace_id,
            "decision": result.decision.value,
            "grounding": asdict(result.grounding),
            "context_document_ids": [item.document.id for item in result.packed_context.documents],
            "warnings": list(result.warnings),
            "metadata": result.metadata,
        }
        await self.audit_sink.emit(event_name, payload)

    async def _audit_failure(
        self,
        event_name: str,
        query: RagQuery,
        exc: BaseException,
        trace: Sequence[RagTraceEvent],
        latency_ms: float,
    ) -> None:
        if not self.config.audit_enabled:
            return
        payload = {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": query.context.request_id,
            "tenant_id": query.context.tenant_id,
            "user_id": query.context.user_id,
            "application": query.context.application,
            "domain": query.context.domain,
            "trace_id": query.context.trace_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
            "trace": [asdict(item) for item in trace],
        }
        await self.audit_sink.emit(event_name, payload)


# =============================================================================
# Factory Helpers
# =============================================================================


def build_default_rag_pipeline(
    *,
    documents: Sequence[RetrievedDocument],
    answer_generator: Optional[AnswerGenerator] = None,
    config_overrides: Optional[Mapping[str, Any]] = None,
) -> RagPipeline:
    """Build a default local RAG pipeline using in-memory keyword retrieval."""

    config_data = asdict(RagConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = RagConfig(**config_data)
    retriever = InMemoryKeywordRetriever(documents)
    return RagPipeline(
        config=config,
        retrievers=(retriever,),
        answer_generator=answer_generator or ExtractiveAnswerGenerator(),
    )


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    docs = (
        RetrievedDocument(
            id="chunk-001",
            document_id="policy-001",
            source_id="internal_policy",
            title="AI Governance Policy",
            text="All AI-generated financial recommendations must be reviewed by a qualified analyst before publication.",
            score=0.9,
            tags=("finance", "governance"),
        ),
        RetrievedDocument(
            id="chunk-002",
            document_id="policy-002",
            source_id="internal_policy",
            title="Citation Policy",
            text="Generated answers must cite retrieved context using bracket citations such as [C1] and [C2].",
            score=0.8,
            tags=("citations", "governance"),
        ),
    )

    pipeline = build_default_rag_pipeline(documents=docs, config_overrides={"include_raw_documents": True})
    result = await pipeline.run(
        RagQuery(
            query="Como responder sobre recomendações financeiras geradas por IA?",
            context=RagContext(
                tenant_id="demo",
                application="ai-platform",
                domain="finance",
                locale="pt-BR",
            ),
            generation_instructions="Responda em português e cite o contexto.",
        )
    )
    print(result.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())
