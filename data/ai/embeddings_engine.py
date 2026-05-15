"""
data/ai/embeddings_engine.py

Motor enterprise de embeddings para pipelines de IA, busca semântica, RAG,
feature stores e vector databases.

Recursos principais:
- Interface plugável para múltiplos providers de embeddings.
- Provider local determinístico para testes/desenvolvimento.
- Batching, retry com backoff e jitter.
- Cache em memória com TTL.
- Normalização L2 opcional.
- Chunking de textos com overlap.
- Validação de entrada e limites de tamanho.
- Métricas internas de latência, tokens estimados, cache hit/miss e erros.
- Sinks/vector stores plugáveis.
- Auditoria opcional via callback.
- Deduplicação por hash de texto/modelo/provider.
- Execução síncrona e helpers de alto nível.

Dependências obrigatórias:
    pip install pydantic

Dependências opcionais:
    pip install numpy
    pip install openai

Observação:
- O provider OpenAI está implementado de forma opcional e só é usado quando
  a dependência `openai` estiver instalada e a API key configurada.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import socket
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ai.embeddings_engine") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "embeddings-engine")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums e Exceptions
# =============================================================================


class EmbeddingProviderType(str, Enum):
    LOCAL_HASH = "local_hash"
    OPENAI = "openai"
    CUSTOM = "custom"


class EmbeddingStatus(str, Enum):
    CREATED = "created"
    EMBEDDED = "embedded"
    FAILED = "failed"
    CACHE_HIT = "cache_hit"
    SKIPPED = "skipped"


class VectorDistance(str, Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLIDEAN = "euclidean"


class ChunkStrategy(str, Enum):
    NONE = "none"
    CHARACTER = "character"
    WORD = "word"


class EmbeddingsEngineError(Exception):
    """Erro base do motor de embeddings."""


class EmbeddingProviderError(EmbeddingsEngineError):
    """Erro retornado por provider de embeddings."""


class EmbeddingValidationError(EmbeddingsEngineError):
    """Erro de validação de input."""


class VectorStoreError(EmbeddingsEngineError):
    """Erro em vector store."""


# =============================================================================
# Models
# =============================================================================


class EmbeddingInput(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None
    tenant_id: Optional[str] = None
    correlation_id: Optional[str] = None


class EmbeddingVector(BaseModel):
    id: str
    text_hash: str
    vector: List[float]
    dimensions: int
    model: str
    provider: str
    status: EmbeddingStatus = EmbeddingStatus.EMBEDDED
    text: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None
    tenant_id: Optional[str] = None
    correlation_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utc_now_iso())


class EmbeddingBatchResult(BaseModel):
    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    vectors: List[EmbeddingVector] = Field(default_factory=list)
    failed: List[Dict[str, Any]] = Field(default_factory=list)
    provider: str
    model: str
    dimensions: int
    elapsed_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0
    total_inputs: int = 0
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_seconds: float = 0.5
    max_seconds: float = 20.0
    jitter: bool = True

    def sleep_seconds(self, attempt: int) -> float:
        base = self.base_seconds * (2 ** max(0, attempt - 1))
        jitter_value = random.uniform(0, self.base_seconds) if self.jitter else 0.0
        return min(base + jitter_value, self.max_seconds)


@dataclass(frozen=True)
class ChunkingConfig:
    strategy: ChunkStrategy = ChunkStrategy.NONE
    chunk_size: int = 1200
    chunk_overlap: int = 150
    min_chunk_chars: int = 20


@dataclass(frozen=True)
class EmbeddingsEngineConfig:
    provider_type: EmbeddingProviderType = EmbeddingProviderType.LOCAL_HASH
    model: str = "local-hash-embedding-v1"
    dimensions: int = 384
    batch_size: int = 100
    normalize_vectors: bool = True
    include_text_in_output: bool = False
    max_text_chars: int = 50_000
    skip_empty_texts: bool = True

    cache_enabled: bool = True
    cache_ttl_seconds: Optional[int] = 86_400
    cache_max_items: int = 100_000

    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)

    openai_api_key: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_timeout_seconds: float = 60.0

    vector_store_enabled: bool = False
    dry_run: bool = False

    @staticmethod
    def from_env() -> "EmbeddingsEngineConfig":
        return EmbeddingsEngineConfig(
            provider_type=EmbeddingProviderType(os.getenv("EMBEDDINGS_PROVIDER", EmbeddingProviderType.LOCAL_HASH.value)),
            model=os.getenv("EMBEDDINGS_MODEL", "local-hash-embedding-v1"),
            dimensions=int(os.getenv("EMBEDDINGS_DIMENSIONS", "384")),
            batch_size=int(os.getenv("EMBEDDINGS_BATCH_SIZE", "100")),
            normalize_vectors=env_bool("EMBEDDINGS_NORMALIZE", True),
            include_text_in_output=env_bool("EMBEDDINGS_INCLUDE_TEXT", False),
            max_text_chars=int(os.getenv("EMBEDDINGS_MAX_TEXT_CHARS", "50000")),
            skip_empty_texts=env_bool("EMBEDDINGS_SKIP_EMPTY_TEXTS", True),
            cache_enabled=env_bool("EMBEDDINGS_CACHE_ENABLED", True),
            cache_ttl_seconds=int(os.getenv("EMBEDDINGS_CACHE_TTL_SECONDS", "86400"))
            if os.getenv("EMBEDDINGS_CACHE_TTL_SECONDS", "86400").lower() not in {"", "none", "null"}
            else None,
            cache_max_items=int(os.getenv("EMBEDDINGS_CACHE_MAX_ITEMS", "100000")),
            retry_policy=RetryPolicy(
                max_retries=int(os.getenv("EMBEDDINGS_MAX_RETRIES", "3")),
                base_seconds=float(os.getenv("EMBEDDINGS_RETRY_BASE_SECONDS", "0.5")),
                max_seconds=float(os.getenv("EMBEDDINGS_RETRY_MAX_SECONDS", "20")),
                jitter=env_bool("EMBEDDINGS_RETRY_JITTER", True),
            ),
            chunking=ChunkingConfig(
                strategy=ChunkStrategy(os.getenv("EMBEDDINGS_CHUNK_STRATEGY", ChunkStrategy.NONE.value)),
                chunk_size=int(os.getenv("EMBEDDINGS_CHUNK_SIZE", "1200")),
                chunk_overlap=int(os.getenv("EMBEDDINGS_CHUNK_OVERLAP", "150")),
                min_chunk_chars=int(os.getenv("EMBEDDINGS_MIN_CHUNK_CHARS", "20")),
            ),
            openai_api_key=os.getenv("OPENAI_API_KEY") or os.getenv("EMBEDDINGS_OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("EMBEDDINGS_OPENAI_BASE_URL") or None,
            openai_timeout_seconds=float(os.getenv("EMBEDDINGS_OPENAI_TIMEOUT_SECONDS", "60")),
            vector_store_enabled=env_bool("EMBEDDINGS_VECTOR_STORE_ENABLED", False),
            dry_run=env_bool("EMBEDDINGS_DRY_RUN", False),
        )


@dataclass
class EmbeddingsMetrics:
    requests: int = 0
    inputs_received: int = 0
    inputs_embedded: int = 0
    inputs_failed: int = 0
    batches_processed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    retries: int = 0
    vectors_stored: int = 0
    total_tokens_estimated: int = 0
    total_latency_ms: float = 0.0
    last_request_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        avg_latency = self.total_latency_ms / self.requests if self.requests else 0.0
        return {
            "requests": self.requests,
            "inputs_received": self.inputs_received,
            "inputs_embedded": self.inputs_embedded,
            "inputs_failed": self.inputs_failed,
            "batches_processed": self.batches_processed,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "retries": self.retries,
            "vectors_stored": self.vectors_stored,
            "total_tokens_estimated": self.total_tokens_estimated,
            "average_latency_ms": round(avg_latency, 6),
            "total_latency_ms": round(self.total_latency_ms, 6),
            "last_request_at": self.last_request_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class EmbeddingProvider(Protocol):
    provider_name: str
    model: str
    dimensions: int

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Retorna embeddings para uma sequência de textos."""


class VectorStore(Protocol):
    def upsert(self, vectors: Sequence[EmbeddingVector]) -> int:
        """Insere/atualiza vetores e retorna quantidade persistida."""

    def search(self, query_vector: Sequence[float], top_k: int = 10, filters: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        """Busca vetores similares."""


class EmbeddingAuditHook(Protocol):
    def on_embedding_batch(self, result: EmbeddingBatchResult) -> None:
        """Hook chamado após batch de embeddings."""


# =============================================================================
# Providers
# =============================================================================


class LocalHashEmbeddingProvider:
    """
    Provider local determinístico para testes, desenvolvimento e fallback.

    Não substitui embeddings semânticos reais em produção, mas é útil para
    validar pipeline, vector store, cache e contratos.
    """

    provider_name = EmbeddingProviderType.LOCAL_HASH.value

    def __init__(self, model: str = "local-hash-embedding-v1", dimensions: int = 384) -> None:
        self.model = model
        self.dimensions = dimensions

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        normalized_text = text.strip().lower()

        if not normalized_text:
            return vector

        tokens = normalized_text.split()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i, byte in enumerate(digest):
                idx = (byte + i * 31) % self.dimensions
                vector[idx] += ((byte / 255.0) * 2.0) - 1.0

        return vector


class OpenAIEmbeddingProvider:
    provider_name = EmbeddingProviderType.OPENAI.value

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimensions: Optional[int] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if OpenAI is None:
            raise EmbeddingProviderError("Provider OpenAI exige `pip install openai`.")
        if not api_key:
            raise EmbeddingProviderError("OPENAI_API_KEY/EMBEDDINGS_OPENAI_API_KEY não configurada.")

        self.model = model
        self.dimensions = dimensions or 1536
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        kwargs: Dict[str, Any] = {"model": self.model, "input": list(texts)}
        if self.dimensions:
            kwargs["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]


class FunctionEmbeddingProvider:
    provider_name = EmbeddingProviderType.CUSTOM.value

    def __init__(self, fn: Callable[[Sequence[str]], List[List[float]]], model: str, dimensions: int) -> None:
        self.fn = fn
        self.model = model
        self.dimensions = dimensions

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        return self.fn(texts)


# =============================================================================
# Cache
# =============================================================================


@dataclass
class CacheItem:
    vector: EmbeddingVector
    created_monotonic: float


class EmbeddingCache:
    def __init__(self, max_items: int = 100_000, ttl_seconds: Optional[int] = 86_400) -> None:
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._items: Dict[str, CacheItem] = {}

    def get(self, key: str) -> Optional[EmbeddingVector]:
        item = self._items.get(key)
        if not item:
            return None
        if self.ttl_seconds is not None and time.monotonic() - item.created_monotonic > self.ttl_seconds:
            self._items.pop(key, None)
            return None
        return item.vector

    def set(self, key: str, vector: EmbeddingVector) -> None:
        if len(self._items) >= self.max_items:
            oldest_key = min(self._items, key=lambda k: self._items[k].created_monotonic)
            self._items.pop(oldest_key, None)
        self._items[key] = CacheItem(vector=vector, created_monotonic=time.monotonic())

    def clear(self) -> None:
        self._items.clear()

    def size(self) -> int:
        return len(self._items)


# =============================================================================
# Vector stores
# =============================================================================


class MemoryVectorStore:
    def __init__(self, distance: VectorDistance = VectorDistance.COSINE) -> None:
        self.distance = distance
        self.vectors: Dict[str, EmbeddingVector] = {}

    def upsert(self, vectors: Sequence[EmbeddingVector]) -> int:
        for vector in vectors:
            self.vectors[vector.id] = vector
        return len(vectors)

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 10,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        for vector in self.vectors.values():
            if filters and not metadata_matches(vector.metadata, filters):
                continue
            score = similarity(query_vector, vector.vector, self.distance)
            results.append({
                "id": vector.id,
                "score": score,
                "text": vector.text,
                "metadata": vector.metadata,
                "source": vector.source,
                "tenant_id": vector.tenant_id,
            })

        return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]


# =============================================================================
# Engine
# =============================================================================


class EmbeddingsEngine:
    def __init__(
        self,
        config: Optional[EmbeddingsEngineConfig] = None,
        provider: Optional[EmbeddingProvider] = None,
        vector_store: Optional[VectorStore] = None,
        audit_hooks: Optional[Sequence[EmbeddingAuditHook]] = None,
    ) -> None:
        self.config = config or EmbeddingsEngineConfig.from_env()
        self.provider = provider or self._build_provider()
        self.vector_store = vector_store
        self.audit_hooks = list(audit_hooks or [])
        self.cache = EmbeddingCache(self.config.cache_max_items, self.config.cache_ttl_seconds)
        self.metrics = EmbeddingsMetrics()

    @classmethod
    def from_env(cls) -> "EmbeddingsEngine":
        return cls(config=EmbeddingsEngineConfig.from_env())

    def embed(self, inputs: Sequence[Union[str, EmbeddingInput, Mapping[str, Any]]]) -> EmbeddingBatchResult:
        started = time.perf_counter()
        self.metrics.requests += 1
        self.metrics.last_request_at = utc_now_iso()

        normalized_inputs = self._prepare_inputs(inputs)
        self.metrics.inputs_received += len(normalized_inputs)
        self.metrics.total_tokens_estimated += sum(estimate_tokens(item.text) for item in normalized_inputs)

        all_vectors: List[EmbeddingVector] = []
        failed: List[Dict[str, Any]] = []
        cache_hits = 0
        cache_misses = 0

        for batch in batched(normalized_inputs, self.config.batch_size):
            batch_vectors, batch_failed, hits, misses = self._embed_batch(batch)
            all_vectors.extend(batch_vectors)
            failed.extend(batch_failed)
            cache_hits += hits
            cache_misses += misses
            self.metrics.batches_processed += 1

        if self.vector_store and self.config.vector_store_enabled and not self.config.dry_run:
            stored = self.vector_store.upsert(all_vectors)
            self.metrics.vectors_stored += stored

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.metrics.total_latency_ms += elapsed_ms
        self.metrics.cache_hits += cache_hits
        self.metrics.cache_misses += cache_misses
        self.metrics.inputs_embedded += len(all_vectors)
        self.metrics.inputs_failed += len(failed)

        result = EmbeddingBatchResult(
            vectors=all_vectors,
            failed=failed,
            provider=self.provider.provider_name,
            model=self.provider.model,
            dimensions=self.provider.dimensions,
            elapsed_ms=elapsed_ms,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            total_inputs=len(normalized_inputs),
            metadata={"cache_size": self.cache.size()},
        )

        self._notify_audit_hooks(result)
        logger.info("Embedding batch finalizado. total=%s vectors=%s failed=%s elapsed_ms=%.2f", len(normalized_inputs), len(all_vectors), len(failed), elapsed_ms)
        return result

    def embed_text(self, text: str, metadata: Optional[Mapping[str, Any]] = None) -> EmbeddingVector:
        result = self.embed([EmbeddingInput(text=text, metadata=dict(metadata or {}))])
        if not result.vectors:
            raise EmbeddingProviderError("Falha ao gerar embedding para texto único.")
        return result.vectors[0]

    def embed_documents(
        self,
        documents: Sequence[Mapping[str, Any]],
        text_field: str = "text",
        id_field: Optional[str] = "id",
        metadata_fields: Optional[Sequence[str]] = None,
    ) -> EmbeddingBatchResult:
        inputs: List[EmbeddingInput] = []
        for doc in documents:
            text = str(get_path_value(doc, text_field) or "")
            doc_id = str(get_path_value(doc, id_field) or uuid.uuid4()) if id_field else str(uuid.uuid4())
            metadata = {field: get_path_value(doc, field) for field in metadata_fields or []}
            inputs.append(EmbeddingInput(id=doc_id, text=text, metadata=metadata, source=str(doc.get("source")) if doc.get("source") else None))
        return self.embed(inputs)

    def chunk_and_embed(
        self,
        text: str,
        document_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> EmbeddingBatchResult:
        chunks = chunk_text(text, self.config.chunking)
        inputs = [
            EmbeddingInput(
                id=f"{document_id or 'doc'}::chunk::{i}",
                text=chunk,
                metadata={**dict(metadata or {}), "chunk_index": i, "document_id": document_id},
            )
            for i, chunk in enumerate(chunks)
        ]
        return self.embed(inputs)

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not self.vector_store:
            raise VectorStoreError("Vector store não configurado.")
        query_vector = self.embed_text(query).vector
        return self.vector_store.search(query_vector, top_k=top_k, filters=filters)

    def _embed_batch(
        self,
        inputs: Sequence[EmbeddingInput],
    ) -> Tuple[List[EmbeddingVector], List[Dict[str, Any]], int, int]:
        vectors: List[EmbeddingVector] = []
        failed: List[Dict[str, Any]] = []
        to_embed: List[EmbeddingInput] = []
        cache_hits = 0
        cache_misses = 0

        for item in inputs:
            cache_key = self._cache_key(item.text)
            cached = self.cache.get(cache_key) if self.config.cache_enabled else None
            if cached:
                cache_hits += 1
                vectors.append(copy_vector_for_input(cached, item, include_text=self.config.include_text_in_output, status=EmbeddingStatus.CACHE_HIT))
            else:
                cache_misses += 1
                to_embed.append(item)

        if not to_embed:
            return vectors, failed, cache_hits, cache_misses

        try:
            raw_vectors = self._call_provider_with_retry([item.text for item in to_embed])
            if len(raw_vectors) != len(to_embed):
                raise EmbeddingProviderError(f"Provider retornou {len(raw_vectors)} vetores para {len(to_embed)} textos.")

            for item, raw_vector in zip(to_embed, raw_vectors):
                vector = list(map(float, raw_vector))
                if self.config.normalize_vectors:
                    vector = normalize_l2(vector)

                embedding = EmbeddingVector(
                    id=item.id,
                    text_hash=text_hash(item.text),
                    vector=vector,
                    dimensions=len(vector),
                    model=self.provider.model,
                    provider=self.provider.provider_name,
                    status=EmbeddingStatus.EMBEDDED,
                    text=item.text if self.config.include_text_in_output else None,
                    metadata=item.metadata,
                    source=item.source,
                    tenant_id=item.tenant_id,
                    correlation_id=item.correlation_id,
                )
                vectors.append(embedding)

                if self.config.cache_enabled:
                    self.cache.set(self._cache_key(item.text), embedding)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Falha ao gerar embeddings do batch. error=%s", exc)
            for item in to_embed:
                failed.append({"id": item.id, "error": str(exc), "text_hash": text_hash(item.text)})

        return vectors, failed, cache_hits, cache_misses

    def _call_provider_with_retry(self, texts: Sequence[str]) -> List[List[float]]:
        attempts = self.config.retry_policy.max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                return self.provider.embed_texts(texts)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                if attempt >= attempts:
                    break
                self.metrics.retries += 1
                sleep_seconds = self.config.retry_policy.sleep_seconds(attempt)
                logger.warning("Erro em provider de embeddings. attempt=%s/%s sleep=%.2fs error=%s", attempt, attempts, sleep_seconds, exc)
                time.sleep(sleep_seconds)

        raise EmbeddingProviderError("Falha ao gerar embeddings após retries máximos.") from last_error

    def _prepare_inputs(self, inputs: Sequence[Union[str, EmbeddingInput, Mapping[str, Any]]]) -> List[EmbeddingInput]:
        prepared: List[EmbeddingInput] = []

        for item in inputs:
            if isinstance(item, str):
                embedding_input = EmbeddingInput(text=item)
            elif isinstance(item, EmbeddingInput):
                embedding_input = item
            else:
                embedding_input = parse_model(EmbeddingInput, item)

            text = normalize_text(embedding_input.text)
            if not text and self.config.skip_empty_texts:
                continue
            if len(text) > self.config.max_text_chars:
                text = text[: self.config.max_text_chars]
            embedding_input.text = text
            prepared.append(embedding_input)

        return prepared

    def _cache_key(self, text: str) -> str:
        payload = {
            "provider": self.provider.provider_name,
            "model": self.provider.model,
            "dimensions": self.provider.dimensions,
            "normalize": self.config.normalize_vectors,
            "text_hash": text_hash(text),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _build_provider(self) -> EmbeddingProvider:
        if self.config.provider_type == EmbeddingProviderType.OPENAI:
            return OpenAIEmbeddingProvider(
                model=self.config.model,
                dimensions=self.config.dimensions,
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url,
                timeout_seconds=self.config.openai_timeout_seconds,
            )

        if self.config.provider_type == EmbeddingProviderType.LOCAL_HASH:
            return LocalHashEmbeddingProvider(model=self.config.model, dimensions=self.config.dimensions)

        raise EmbeddingProviderError("Provider CUSTOM exige passar uma implementação de EmbeddingProvider no construtor.")

    def _notify_audit_hooks(self, result: EmbeddingBatchResult) -> None:
        for hook in self.audit_hooks:
            try:
                hook.on_embedding_batch(result)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Hook de auditoria de embeddings falhou. error=%s", exc)


# =============================================================================
# Utilities
# =============================================================================


def chunk_text(text: str, config: ChunkingConfig) -> List[str]:
    cleaned = normalize_text(text)
    if config.strategy == ChunkStrategy.NONE:
        return [cleaned] if cleaned else []

    if config.chunk_size <= 0:
        raise EmbeddingValidationError("chunk_size deve ser maior que zero.")

    overlap = max(0, min(config.chunk_overlap, config.chunk_size - 1))

    if config.strategy == ChunkStrategy.WORD:
        words = cleaned.split()
        chunks: List[str] = []
        step = config.chunk_size - overlap
        for start in range(0, len(words), step):
            chunk = " ".join(words[start : start + config.chunk_size])
            if len(chunk) >= config.min_chunk_chars:
                chunks.append(chunk)
        return chunks

    chunks = []
    step = config.chunk_size - overlap
    for start in range(0, len(cleaned), step):
        chunk = cleaned[start : start + config.chunk_size].strip()
        if len(chunk) >= config.min_chunk_chars:
            chunks.append(chunk)
    return chunks


def normalize_text(text: str) -> str:
    return " ".join((text or "").replace("\x00", " ").split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    # Estimativa simples e estável: ~4 caracteres por token em muitos cenários.
    return max(1, math.ceil(len(text or "") / 4))


def normalize_l2(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm == 0:
        return list(map(float, vector))
    return [float(x) / norm for x in vector]


def similarity(a: Sequence[float], b: Sequence[float], distance: VectorDistance = VectorDistance.COSINE) -> float:
    if len(a) != len(b):
        raise ValueError("Vetores com dimensões diferentes.")

    if distance == VectorDistance.DOT:
        return sum(float(x) * float(y) for x, y in zip(a, b))

    if distance == VectorDistance.EUCLIDEAN:
        return -math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))

    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = math.sqrt(sum(float(x) * float(x) for x in a))
    norm_b = math.sqrt(sum(float(y) * float(y) for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def metadata_matches(metadata: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    for key, expected in filters.items():
        actual = get_path_value(metadata, key)
        if actual != expected:
            return False
    return True


def copy_vector_for_input(
    cached: EmbeddingVector,
    item: EmbeddingInput,
    include_text: bool,
    status: EmbeddingStatus,
) -> EmbeddingVector:
    return EmbeddingVector(
        id=item.id,
        text_hash=text_hash(item.text),
        vector=list(cached.vector),
        dimensions=cached.dimensions,
        model=cached.model,
        provider=cached.provider,
        status=status,
        text=item.text if include_text else None,
        metadata=item.metadata,
        source=item.source,
        tenant_id=item.tenant_id,
        correlation_id=item.correlation_id,
    )


def batched(items: Sequence[Any], batch_size: int) -> Iterable[Sequence[Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size deve ser maior que zero.")
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def get_path_value(payload: Mapping[str, Any], path: Optional[str]) -> Any:
    if not path:
        return None
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def parse_model(model_class: Any, payload: Mapping[str, Any]) -> Any:
    if hasattr(model_class, "model_validate"):
        return model_class.model_validate(payload)
    return model_class.parse_obj(payload)


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# CLI example
# =============================================================================


def main() -> None:
    config = EmbeddingsEngineConfig.from_env()
    vector_store = MemoryVectorStore()
    engine = EmbeddingsEngine(config=config, vector_store=vector_store)

    result = engine.embed([
        EmbeddingInput(text="Governança de IA garante uso responsável de modelos.", metadata={"topic": "governance"}),
        EmbeddingInput(text="Embeddings permitem busca semântica e RAG.", metadata={"topic": "embeddings"}),
    ])

    logger.info("Resultado embeddings: %s", json.dumps(model_to_dict(result), ensure_ascii=False, default=json_default)[:2000])
    logger.info("Métricas: %s", json.dumps(engine.metrics.snapshot(), ensure_ascii=False))


if __name__ == "__main__":
    main()
