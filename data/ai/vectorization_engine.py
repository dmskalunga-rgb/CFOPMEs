"""
data/ai/vectorization_engine.py

Enterprise-grade document vectorization engine.

This module prepares documents for semantic search, RAG and AI knowledge systems.
It converts raw documents into normalized chunks with embeddings and rich metadata,
with production-ready hooks for storage, audit, metrics and governance.

Core capabilities:

- Document normalization
- Configurable chunking with overlap
- Metadata propagation
- Stable chunk IDs and content hashing
- Deduplication by content hash
- Batch embedding generation
- Vector normalization
- Embedding model/version tracking
- Retry-safe batch orchestration
- In-memory repository for tests/local usage
- Pluggable embedding provider and vector repository
- Audit and metrics hooks
- Sync and async APIs

Recommended package position:
    data/ai/vectorization_engine.py

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


class VectorizationError(Exception):
    """Base exception for vectorization failures."""


class VectorizationConfigurationError(VectorizationError):
    """Raised when configuration is invalid."""


class VectorizationValidationError(VectorizationError):
    """Raised when input document data is invalid."""


class ChunkingError(VectorizationError):
    """Raised when chunking fails."""


class EmbeddingError(VectorizationError):
    """Raised when embedding generation fails."""


class VectorRepositoryError(VectorizationError):
    """Raised when repository operations fail."""


# =============================================================================
# Enums
# =============================================================================


class ChunkingStrategy(str, Enum):
    """Supported chunking strategies."""

    FIXED_CHARS = "fixed_chars"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    MARKDOWN_AWARE = "markdown_aware"
    TOKEN_ESTIMATE = "token_estimate"


class VectorMetric(str, Enum):
    """Vector metric hint for downstream stores."""

    COSINE = "cosine"
    DOT = "dot"
    EUCLIDEAN = "euclidean"


class DocumentStatus(str, Enum):
    """Document lifecycle status."""

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


class VectorizationDecision(str, Enum):
    """Vectorization result decision."""

    INDEXED = "indexed"
    PARTIALLY_INDEXED = "partially_indexed"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class VectorizationConfig:
    """Vectorization engine configuration."""

    chunking_strategy: ChunkingStrategy = ChunkingStrategy.MARKDOWN_AWARE
    chunk_size_chars: int = 1800
    chunk_overlap_chars: int = 180
    min_chunk_chars: int = 80
    max_document_chars: int = 2_000_000
    embedding_batch_size: int = 64
    max_concurrency: int = 4
    normalize_vectors: bool = True
    vector_metric: VectorMetric = VectorMetric.COSINE
    deduplicate_chunks: bool = True
    deduplicate_documents: bool = False
    preserve_line_breaks: bool = True
    strip_html: bool = True
    include_raw_text: bool = True
    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.chunk_size_chars <= 0:
            raise VectorizationConfigurationError("chunk_size_chars must be positive")
        if self.chunk_overlap_chars < 0:
            raise VectorizationConfigurationError("chunk_overlap_chars must be >= 0")
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise VectorizationConfigurationError("chunk_overlap_chars must be smaller than chunk_size_chars")
        if self.min_chunk_chars < 0:
            raise VectorizationConfigurationError("min_chunk_chars must be >= 0")
        if self.max_document_chars <= 0:
            raise VectorizationConfigurationError("max_document_chars must be positive")
        if self.embedding_batch_size <= 0:
            raise VectorizationConfigurationError("embedding_batch_size must be positive")
        if self.max_concurrency <= 0:
            raise VectorizationConfigurationError("max_concurrency must be positive")


@dataclass(frozen=True)
class VectorizationContext:
    """Request context for vectorization."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    domain: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceDocument:
    """Raw source document to vectorize."""

    id: str
    text: str
    title: Optional[str] = None
    source_id: Optional[str] = None
    url: Optional[str] = None
    mime_type: Optional[str] = None
    language: Optional[str] = None
    status: DocumentStatus = DocumentStatus.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    tags: Sequence[str] = field(default_factory=tuple)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self, config: VectorizationConfig) -> None:
        if not self.id:
            raise VectorizationValidationError("document id is required")
        if not isinstance(self.text, str) or not self.text.strip():
            raise VectorizationValidationError(f"document {self.id} text must be non-empty")
        if len(self.text) > config.max_document_chars:
            raise VectorizationValidationError(
                f"document {self.id} exceeds max_document_chars: {len(self.text)} > {config.max_document_chars}"
            )


@dataclass(frozen=True)
class DocumentChunk:
    """Chunk generated from a source document."""

    id: str
    document_id: str
    text: str
    ordinal: int
    content_hash: str
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    title: Optional[str] = None
    source_id: Optional[str] = None
    url: Optional[str] = None
    language: Optional[str] = None
    status: DocumentStatus = DocumentStatus.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    tags: Sequence[str] = field(default_factory=tuple)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorRecord:
    """Vectorized chunk ready for storage/search."""

    id: str
    document_id: str
    chunk_id: str
    text: str
    vector: Sequence[float]
    vector_dimension: int
    embedding_model: str
    embedding_version: str
    content_hash: str
    title: Optional[str] = None
    source_id: Optional[str] = None
    url: Optional[str] = None
    language: Optional[str] = None
    status: DocumentStatus = DocumentStatus.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    tags: Sequence[str] = field(default_factory=tuple)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorizationTraceEvent:
    """Trace event for vectorization observability."""

    stage: str
    latency_ms: float
    success: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentVectorizationResult:
    """Vectorization result for one document."""

    document_id: str
    decision: VectorizationDecision
    document_hash: str
    chunks_created: int
    chunks_indexed: int
    chunks_skipped: int
    vector_dimension: Optional[int]
    records: Sequence[VectorRecord]
    warnings: Sequence[str] = field(default_factory=tuple)
    error: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorizationResult:
    """Batch vectorization result."""

    request_id: str
    created_at: str
    total_documents: int
    indexed_documents: int
    partially_indexed_documents: int
    skipped_documents: int
    failed_documents: int
    total_chunks_created: int
    total_chunks_indexed: int
    results: Sequence[DocumentVectorizationResult]
    trace: Sequence[VectorizationTraceEvent]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


# =============================================================================
# Protocols
# =============================================================================


class EmbeddingProvider(Protocol):
    """Embedding provider protocol."""

    @property
    def model_name(self) -> str:
        """Embedding model name."""

    @property
    def model_version(self) -> str:
        """Embedding model version."""

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return embeddings for texts."""


class VectorRepository(Protocol):
    """Vector storage repository protocol."""

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        """Insert or update vector records."""

    async def delete_by_document_ids(self, document_ids: Sequence[str]) -> None:
        """Delete records for documents."""

    async def exists_content_hash(self, content_hash: str) -> bool:
        """Return whether a content hash already exists."""

    async def list_records(self) -> Sequence[VectorRecord]:
        """List records. Intended for tests/local usage."""


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


def normalize_whitespace(text: str, *, preserve_line_breaks: bool = True) -> str:
    if preserve_line_breaks:
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return re.sub(r"\s+", " ", text).strip()


def strip_html_tags(text: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>", " ", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", " ", text)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def vector_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in vector))


def normalize_vector(vector: Sequence[float]) -> Tuple[float, ...]:
    norm = vector_norm(vector)
    if norm == 0:
        return tuple(float(x) for x in vector)
    return tuple(float(x) / norm for x in vector)


def chunk_id(document_id: str, ordinal: int, content_hash: str) -> str:
    return f"{document_id}::chunk::{ordinal:06d}::{content_hash[:12]}"


def vector_record_id(chunk_identifier: str, embedding_model: str, embedding_version: str) -> str:
    return stable_hash(f"{chunk_identifier}|{embedding_model}|{embedding_version}")


def batched(items: Sequence[Any], batch_size: int) -> Iterable[Sequence[Any]]:
    for offset in range(0, len(items), batch_size):
        yield items[offset : offset + batch_size]


# =============================================================================
# Default Implementations
# =============================================================================


class HashingEmbeddingProvider:
    """Deterministic embedding provider for local/test use.

    Replace this with a real embedding provider in production.
    """

    def __init__(self, dimensions: int = 384, model_name: str = "hashing-embedding", model_version: str = "1.0.0") -> None:
        if dimensions < 16:
            raise VectorizationConfigurationError("dimensions must be >= 16")
        self.dimensions = dimensions
        self._model_name = model_name
        self._model_version = model_version

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def model_version(self) -> str:
        return self._model_version

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        await asyncio.sleep(0)
        return tuple(self._embed_one(text) for text in texts)

    def _embed_one(self, text: str) -> Tuple[float, ...]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[\wÀ-ÿ]+", text.lower(), flags=re.UNICODE):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).hexdigest()
            idx = int(digest[:8], 16) % self.dimensions
            sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
            vector[idx] += sign
        return normalize_vector(vector)


class InMemoryVectorRepository:
    """In-memory vector repository for tests/local usage."""

    def __init__(self) -> None:
        self._records: Dict[str, VectorRecord] = {}
        self._content_hashes: set[str] = set()
        self._lock = asyncio.Lock()

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        async with self._lock:
            for record in records:
                self._records[record.id] = record
                self._content_hashes.add(record.content_hash)

    async def delete_by_document_ids(self, document_ids: Sequence[str]) -> None:
        ids = set(document_ids)
        async with self._lock:
            to_delete = [record_id for record_id, record in self._records.items() if record.document_id in ids]
            for record_id in to_delete:
                self._records.pop(record_id, None)
            self._content_hashes = {record.content_hash for record in self._records.values()}

    async def exists_content_hash(self, content_hash: str) -> bool:
        async with self._lock:
            return content_hash in self._content_hashes

    async def list_records(self) -> Sequence[VectorRecord]:
        async with self._lock:
            return tuple(self._records.values())


class LoggingAuditSink:
    """Audit sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("vectorization_audit=%s payload=%s", event_name, safe_json(payload))


class LoggingMetricsSink:
    """Metrics sink using Python logging."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("vectorization_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("vectorization_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Chunker
# =============================================================================


class DocumentChunker:
    """Configurable document chunker."""

    SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")

    def __init__(self, config: VectorizationConfig) -> None:
        self.config = config

    def chunk(self, document: SourceDocument, normalized_text: str) -> Sequence[DocumentChunk]:
        try:
            if self.config.chunking_strategy == ChunkingStrategy.PARAGRAPH:
                parts = self._paragraph_units(normalized_text)
            elif self.config.chunking_strategy == ChunkingStrategy.SENTENCE:
                parts = self._sentence_units(normalized_text)
            elif self.config.chunking_strategy == ChunkingStrategy.MARKDOWN_AWARE:
                parts = self._markdown_units(normalized_text)
            else:
                parts = [normalized_text]

            chunks_text = self._pack_units(parts)
            chunks: List[DocumentChunk] = []
            cursor = 0
            for ordinal, text in enumerate(chunks_text, start=1):
                clean = text.strip()
                if len(clean) < self.config.min_chunk_chars and len(chunks_text) > 1:
                    continue
                start = normalized_text.find(clean[: min(64, len(clean))], cursor)
                if start < 0:
                    start = None
                    end = None
                else:
                    end = start + len(clean)
                    cursor = end
                content_hash = stable_hash(clean)
                chunks.append(
                    DocumentChunk(
                        id=chunk_id(document.id, len(chunks) + 1, content_hash),
                        document_id=document.id,
                        text=clean,
                        ordinal=len(chunks) + 1,
                        content_hash=content_hash,
                        start_char=start,
                        end_char=end,
                        title=document.title,
                        source_id=document.source_id,
                        url=document.url,
                        language=document.language,
                        status=document.status,
                        sensitivity=document.sensitivity,
                        tags=document.tags,
                        created_at=document.created_at,
                        updated_at=document.updated_at,
                        metadata=document.metadata,
                    )
                )
            return tuple(chunks)
        except Exception as exc:  # noqa: BLE001
            raise ChunkingError(f"Failed to chunk document {document.id}: {exc}") from exc

    def _paragraph_units(self, text: str) -> List[str]:
        return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]

    def _sentence_units(self, text: str) -> List[str]:
        return [part.strip() for part in self.SENTENCE_RE.split(text) if part.strip()]

    def _markdown_units(self, text: str) -> List[str]:
        blocks: List[str] = []
        current: List[str] = []
        for line in text.splitlines():
            if line.startswith("#") and current:
                blocks.append("\n".join(current).strip())
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append("\n".join(current).strip())
        expanded: List[str] = []
        for block in blocks:
            if len(block) <= self.config.chunk_size_chars:
                expanded.append(block)
            else:
                expanded.extend(self._paragraph_units(block))
        return [part for part in expanded if part]

    def _pack_units(self, units: Sequence[str]) -> List[str]:
        if not units:
            return []
        if self.config.chunking_strategy in {ChunkingStrategy.FIXED_CHARS, ChunkingStrategy.TOKEN_ESTIMATE}:
            return self._fixed_windows("\n\n".join(units))

        chunks: List[str] = []
        current = ""
        for unit in units:
            separator = "\n\n" if current else ""
            candidate = current + separator + unit
            if len(candidate) <= self.config.chunk_size_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            if len(unit) > self.config.chunk_size_chars:
                chunks.extend(self._fixed_windows(unit))
                current = ""
            else:
                current = unit
        if current:
            chunks.append(current)
        return self._apply_overlap(chunks)

    def _fixed_windows(self, text: str) -> List[str]:
        chunks: List[str] = []
        step = self.config.chunk_size_chars - self.config.chunk_overlap_chars
        cursor = 0
        while cursor < len(text):
            chunk = text[cursor : cursor + self.config.chunk_size_chars]
            if chunk.strip():
                chunks.append(chunk.strip())
            cursor += step
        return chunks

    def _apply_overlap(self, chunks: Sequence[str]) -> List[str]:
        if self.config.chunk_overlap_chars <= 0 or len(chunks) <= 1:
            return list(chunks)
        output: List[str] = []
        previous_tail = ""
        for chunk in chunks:
            enriched = (previous_tail + "\n" + chunk).strip() if previous_tail else chunk
            output.append(enriched)
            previous_tail = chunk[-self.config.chunk_overlap_chars :]
        return output


# =============================================================================
# Vectorization Engine
# =============================================================================


class VectorizationEngine:
    """Enterprise document vectorization engine."""

    def __init__(
        self,
        *,
        config: Optional[VectorizationConfig] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        repository: Optional[VectorRepository] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or VectorizationConfig()
        self.config.validate()
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self.repository = repository or InMemoryVectorRepository()
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()
        self.chunker = DocumentChunker(self.config)

    async def vectorize_documents(
        self,
        documents: Sequence[SourceDocument],
        *,
        context: Optional[VectorizationContext] = None,
        replace_existing: bool = True,
    ) -> VectorizationResult:
        """Vectorize and persist a batch of source documents."""

        context = context or VectorizationContext()
        started = time.perf_counter()
        trace: List[VectorizationTraceEvent] = []
        results: List[DocumentVectorizationResult] = []

        try:
            await self._stage("validation", trace, lambda: self._validate_documents(documents))

            semaphore = asyncio.Semaphore(self.config.max_concurrency)

            async def process_one(document: SourceDocument) -> DocumentVectorizationResult:
                async with semaphore:
                    try:
                        return await self._vectorize_one(document, context=context, replace_existing=replace_existing)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Vectorization failed for document_id=%s", document.id)
                        if self.config.fail_fast:
                            raise
                        return DocumentVectorizationResult(
                            document_id=document.id,
                            decision=VectorizationDecision.FAILED,
                            document_hash=stable_hash(document.text or ""),
                            chunks_created=0,
                            chunks_indexed=0,
                            chunks_skipped=0,
                            vector_dimension=None,
                            records=tuple(),
                            error=str(exc),
                        )

            results = list(await self._stage("document_vectorization", trace, lambda: asyncio.gather(*(process_one(doc) for doc in documents))))

            result = self._build_result(context, documents, results, trace, started)
            await self._record_success(context, result, (time.perf_counter() - started) * 1000)
            await self._audit_completed(context, result)
            return result

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(context, exc, latency_ms)
            await self._audit("vectorization_failed", {
                "event_id": str(uuid.uuid4()),
                "created_at": utc_now_iso(),
                "request_id": context.request_id,
                "tenant_id": context.tenant_id,
                "application": context.application,
                "domain": context.domain,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "latency_ms": round(latency_ms, 3),
                "trace": [asdict(item) for item in trace],
            })
            raise

    def vectorize_documents_sync(
        self,
        documents: Sequence[SourceDocument],
        *,
        context: Optional[VectorizationContext] = None,
        replace_existing: bool = True,
    ) -> VectorizationResult:
        """Synchronous convenience wrapper."""

        return asyncio.run(self.vectorize_documents(documents, context=context, replace_existing=replace_existing))

    async def delete_documents(self, document_ids: Sequence[str], *, context: Optional[VectorizationContext] = None) -> None:
        context = context or VectorizationContext()
        if not document_ids:
            return
        await self.repository.delete_by_document_ids(document_ids)
        await self._audit("vectorization_delete_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "document_ids": list(document_ids),
            "count": len(document_ids),
        })

    async def list_records(self) -> Sequence[VectorRecord]:
        return await self.repository.list_records()

    async def _vectorize_one(
        self,
        document: SourceDocument,
        *,
        context: VectorizationContext,
        replace_existing: bool,
    ) -> DocumentVectorizationResult:
        document.validate(self.config)
        document_hash = stable_hash(document.text)
        warnings: List[str] = []

        if self.config.deduplicate_documents and await self.repository.exists_content_hash(document_hash):
            return DocumentVectorizationResult(
                document_id=document.id,
                decision=VectorizationDecision.SKIPPED_DUPLICATE,
                document_hash=document_hash,
                chunks_created=0,
                chunks_indexed=0,
                chunks_skipped=0,
                vector_dimension=None,
                records=tuple(),
                warnings=("Document skipped because identical content hash already exists.",),
            )

        if replace_existing:
            await self.repository.delete_by_document_ids((document.id,))

        normalized = self._normalize_document_text(document.text)
        chunks = list(self.chunker.chunk(document, normalized))
        if not chunks:
            return DocumentVectorizationResult(
                document_id=document.id,
                decision=VectorizationDecision.FAILED,
                document_hash=document_hash,
                chunks_created=0,
                chunks_indexed=0,
                chunks_skipped=0,
                vector_dimension=None,
                records=tuple(),
                error="No chunks were generated.",
            )

        chunks_to_embed: List[DocumentChunk] = []
        skipped = 0
        for chunk in chunks:
            if self.config.deduplicate_chunks and await self.repository.exists_content_hash(chunk.content_hash):
                skipped += 1
                continue
            chunks_to_embed.append(chunk)

        if not chunks_to_embed:
            return DocumentVectorizationResult(
                document_id=document.id,
                decision=VectorizationDecision.SKIPPED_DUPLICATE,
                document_hash=document_hash,
                chunks_created=len(chunks),
                chunks_indexed=0,
                chunks_skipped=skipped,
                vector_dimension=None,
                records=tuple(),
                warnings=("All chunks were skipped as duplicates.",),
            )

        records: List[VectorRecord] = []
        for batch in batched(chunks_to_embed, self.config.embedding_batch_size):
            embeddings = await self._embed_batch([chunk.text for chunk in batch])
            if len(embeddings) != len(batch):
                raise EmbeddingError(f"Embedding provider returned {len(embeddings)} vectors for {len(batch)} chunks")
            for chunk, vector in zip(batch, embeddings):
                normalized_vector = normalize_vector(vector) if self.config.normalize_vectors else tuple(float(x) for x in vector)
                if not normalized_vector:
                    raise EmbeddingError(f"Empty vector returned for chunk {chunk.id}")
                records.append(self._record_from_chunk(chunk, normalized_vector))

        await self.repository.upsert(tuple(records))
        decision = VectorizationDecision.INDEXED if skipped == 0 else VectorizationDecision.PARTIALLY_INDEXED
        vector_dimension = len(records[0].vector) if records else None
        if skipped:
            warnings.append(f"Skipped {skipped} duplicate chunk(s).")

        return DocumentVectorizationResult(
            document_id=document.id,
            decision=decision,
            document_hash=document_hash,
            chunks_created=len(chunks),
            chunks_indexed=len(records),
            chunks_skipped=skipped,
            vector_dimension=vector_dimension,
            records=tuple(records if self.config.include_raw_text else self._strip_record_text(records)),
            warnings=tuple(warnings),
            metadata={
                "embedding_model": self.embedding_provider.model_name,
                "embedding_version": self.embedding_provider.model_version,
                "chunking_strategy": self.config.chunking_strategy.value,
            },
        )

    def _normalize_document_text(self, text: str) -> str:
        result = text
        if self.config.strip_html:
            result = strip_html_tags(result)
        result = normalize_whitespace(result, preserve_line_breaks=self.config.preserve_line_breaks)
        return result

    async def _embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        try:
            return await self.embedding_provider.embed(texts)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(str(exc)) from exc

    def _record_from_chunk(self, chunk: DocumentChunk, vector: Sequence[float]) -> VectorRecord:
        record_id = vector_record_id(chunk.id, self.embedding_provider.model_name, self.embedding_provider.model_version)
        return VectorRecord(
            id=record_id,
            document_id=chunk.document_id,
            chunk_id=chunk.id,
            text=chunk.text,
            vector=tuple(float(x) for x in vector),
            vector_dimension=len(vector),
            embedding_model=self.embedding_provider.model_name,
            embedding_version=self.embedding_provider.model_version,
            content_hash=chunk.content_hash,
            title=chunk.title,
            source_id=chunk.source_id,
            url=chunk.url,
            language=chunk.language,
            status=chunk.status,
            sensitivity=chunk.sensitivity,
            tags=chunk.tags,
            created_at=chunk.created_at,
            updated_at=chunk.updated_at,
            metadata={
                **dict(chunk.metadata),
                "ordinal": chunk.ordinal,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "vector_metric": self.config.vector_metric.value,
                "vectorization_version": self.config.version,
            },
        )

    def _strip_record_text(self, records: Sequence[VectorRecord]) -> Sequence[VectorRecord]:
        return tuple(VectorRecord(**{**asdict(record), "text": ""}) for record in records)

    def _validate_documents(self, documents: Sequence[SourceDocument]) -> None:
        if not documents:
            raise VectorizationValidationError("documents must not be empty")
        seen: set[str] = set()
        for document in documents:
            document.validate(self.config)
            if document.id in seen:
                raise VectorizationValidationError(f"duplicate document id in batch: {document.id}")
            seen.add(document.id)

    def _build_result(
        self,
        context: VectorizationContext,
        documents: Sequence[SourceDocument],
        results: Sequence[DocumentVectorizationResult],
        trace: Sequence[VectorizationTraceEvent],
        started: float,
    ) -> VectorizationResult:
        indexed = sum(1 for r in results if r.decision == VectorizationDecision.INDEXED)
        partial = sum(1 for r in results if r.decision == VectorizationDecision.PARTIALLY_INDEXED)
        skipped = sum(1 for r in results if r.decision == VectorizationDecision.SKIPPED_DUPLICATE)
        failed = sum(1 for r in results if r.decision == VectorizationDecision.FAILED)
        return VectorizationResult(
            request_id=context.request_id,
            created_at=utc_now_iso(),
            total_documents=len(documents),
            indexed_documents=indexed,
            partially_indexed_documents=partial,
            skipped_documents=skipped,
            failed_documents=failed,
            total_chunks_created=sum(r.chunks_created for r in results),
            total_chunks_indexed=sum(r.chunks_indexed for r in results),
            results=tuple(results),
            trace=tuple(trace),
            metadata={
                "vectorization_version": self.config.version,
                "embedding_model": self.embedding_provider.model_name,
                "embedding_version": self.embedding_provider.model_version,
                "chunking_strategy": self.config.chunking_strategy.value,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        )

    async def _stage(self, stage: str, trace: List[VectorizationTraceEvent], func: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            trace.append(VectorizationTraceEvent(stage=stage, latency_ms=(time.perf_counter() - started) * 1000, success=True))
            return result
        except Exception as exc:
            trace.append(
                VectorizationTraceEvent(
                    stage=stage,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    success=False,
                    metadata={"error_type": type(exc).__name__, "error": str(exc)},
                )
            )
            raise

    async def _record_success(self, context: VectorizationContext, result: VectorizationResult, latency_ms: float) -> None:
        tags = self._metric_tags(context)
        await self._metric_increment("ai.vectorization.success", 1, tags)
        await self._metric_observe("ai.vectorization.latency_ms", latency_ms, tags)
        await self._metric_observe("ai.vectorization.documents", result.total_documents, tags)
        await self._metric_observe("ai.vectorization.chunks_indexed", result.total_chunks_indexed, tags)

    async def _record_failure(self, context: VectorizationContext, exc: BaseException, latency_ms: float) -> None:
        tags = {**self._metric_tags(context), "error_type": type(exc).__name__}
        await self._metric_increment("ai.vectorization.failure", 1, tags)
        await self._metric_observe("ai.vectorization.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: VectorizationContext) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "domain": context.domain or "unknown",
            "embedding_model": self.embedding_provider.model_name,
            "chunking_strategy": self.config.chunking_strategy.value,
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

    async def _audit_completed(self, context: VectorizationContext, result: VectorizationResult) -> None:
        await self._audit("vectorization_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "domain": context.domain,
            "trace_id": context.trace_id,
            "total_documents": result.total_documents,
            "indexed_documents": result.indexed_documents,
            "partially_indexed_documents": result.partially_indexed_documents,
            "skipped_documents": result.skipped_documents,
            "failed_documents": result.failed_documents,
            "total_chunks_indexed": result.total_chunks_indexed,
            "metadata": result.metadata,
        })


# =============================================================================
# Adapters
# =============================================================================


class SemanticSearchDocumentAdapter:
    """Converts VectorRecord objects to semantic_search.SearchDocument objects."""

    @staticmethod
    def to_search_documents(records: Sequence[VectorRecord]) -> Sequence[Any]:
        try:
            from data.ai.semantic_search import DocumentStatus as SearchDocumentStatus
            from data.ai.semantic_search import SearchDocument, SensitivityLevel as SearchSensitivityLevel
        except Exception:  # noqa: BLE001
            from semantic_search import DocumentStatus as SearchDocumentStatus  # type: ignore
            from semantic_search import SearchDocument, SensitivityLevel as SearchSensitivityLevel  # type: ignore

        return tuple(
            SearchDocument(
                id=record.chunk_id,
                text=record.text,
                title=record.title,
                source_id=record.source_id,
                document_id=record.document_id,
                url=record.url,
                vector=record.vector,
                status=SearchDocumentStatus(record.status.value),
                sensitivity=SearchSensitivityLevel(record.sensitivity.value),
                tags=record.tags,
                created_at=record.created_at,
                updated_at=record.updated_at,
                metadata={
                    **dict(record.metadata),
                    "vector_record_id": record.id,
                    "embedding_model": record.embedding_model,
                    "embedding_version": record.embedding_version,
                    "content_hash": record.content_hash,
                },
            )
            for record in records
        )


# =============================================================================
# Factory Helpers
# =============================================================================


def build_default_vectorization_engine(
    *,
    config_overrides: Optional[Mapping[str, Any]] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    repository: Optional[VectorRepository] = None,
) -> VectorizationEngine:
    config_data = asdict(VectorizationConfig())
    if config_overrides:
        config_data.update(dict(config_overrides))
    config = VectorizationConfig(**config_data)
    return VectorizationEngine(
        config=config,
        embedding_provider=embedding_provider or HashingEmbeddingProvider(),
        repository=repository or InMemoryVectorRepository(),
    )


# =============================================================================
# Demo
# =============================================================================


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)

    engine = build_default_vectorization_engine(config_overrides={"include_raw_text": True})
    docs = (
        SourceDocument(
            id="doc-001",
            title="AI Governance Policy",
            source_id="policy",
            text=(
                "# AI Governance Policy\n\n"
                "All AI-generated financial recommendations must be reviewed by a qualified analyst before publication.\n\n"
                "Generated answers must cite retrieved context and state uncertainty when evidence is incomplete."
            ),
            tags=("governance", "finance"),
            sensitivity=SensitivityLevel.INTERNAL,
        ),
    )
    result = await engine.vectorize_documents(
        docs,
        context=VectorizationContext(tenant_id="demo", application="ai-platform", domain="governance"),
    )
    print(result.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())
