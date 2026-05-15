"""
vector_processor.py
===================

Enterprise-grade vector processing module for data/AI pipelines.

Core capabilities
-----------------
- Vector validation, dimensionality checks and safe coercion.
- Normalization: L1, L2, max, z-score and min-max.
- Distance/similarity metrics: cosine, dot product, euclidean, manhattan.
- Batch vector processing with audit reports and quality metrics.
- In-memory vector index for exact nearest-neighbor search.
- Metadata filters for semantic/vector retrieval workflows.
- Duplicate/near-duplicate detection.
- Optional numpy acceleration with pure-Python fallback where practical.
- Extensible hooks for ANN backends such as FAISS, Milvus, pgvector, Qdrant.

This module does not force a vendor-specific vector database. It provides a
clean processing and exact-search foundation that can be wrapped by production
storage/index backends.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import heapq
import json
import logging
import math
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
VectorLike = Union[Sequence[float], Sequence[int]]
MetadataFilter = Callable[[Mapping[str, Any]], bool]


class VectorProcessingError(Exception):
    """Base exception for vector processing errors."""


class VectorValidationError(VectorProcessingError):
    """Raised when a vector fails validation."""


class VectorDimensionError(VectorValidationError):
    """Raised when vector dimensionality is invalid."""


class SimilarityMetric(str, enum.Enum):
    COSINE = "cosine"
    DOT = "dot"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"


class NormalizationMode(str, enum.Enum):
    NONE = "none"
    L1 = "l1"
    L2 = "l2"
    MAX = "max"
    ZSCORE = "zscore"
    MINMAX = "minmax"


class InvalidVectorPolicy(str, enum.Enum):
    FAIL = "fail"
    SKIP = "skip"
    ZERO_FILL = "zero_fill"


class DuplicatePolicy(str, enum.Enum):
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    FAIL = "fail"


@dataclass(frozen=True)
class VectorRecord:
    id: str
    vector: Sequence[float]
    metadata: JsonDict = field(default_factory=dict)
    namespace: str = "default"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id,
            "namespace": self.namespace,
            "vector": list(self.vector),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class VectorProcessorConfig:
    expected_dimension: Optional[int] = None
    normalization: NormalizationMode = NormalizationMode.L2
    invalid_vector_policy: InvalidVectorPolicy = InvalidVectorPolicy.FAIL
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.KEEP_LAST
    allow_nan: bool = False
    allow_inf: bool = False
    zero_epsilon: float = 1e-12
    default_namespace: str = "default"
    metric: SimilarityMetric = SimilarityMetric.COSINE
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.expected_dimension is not None and self.expected_dimension <= 0:
            raise ValueError("expected_dimension must be > 0")
        if self.zero_epsilon <= 0:
            raise ValueError("zero_epsilon must be > 0")


@dataclass
class VectorIssue:
    code: str
    message: str
    record_id: Optional[str] = None
    context: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "code": self.code,
            "message": self.message,
            "record_id": self.record_id,
            "context": dict(self.context),
        }


@dataclass
class VectorProcessingReport:
    total_input: int = 0
    total_output: int = 0
    skipped: int = 0
    duplicates: int = 0
    dimensions: Counter = field(default_factory=Counter)
    issues: List[VectorIssue] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "total_input": self.total_input,
            "total_output": self.total_output,
            "skipped": self.skipped,
            "duplicates": self.duplicates,
            "dimensions": dict(self.dimensions),
            "issues": [issue.to_dict() for issue in self.issues],
            "duration_ms": self.duration_ms,
        }


@dataclass
class VectorProcessingResult:
    records: List[VectorRecord]
    report: VectorProcessingReport

    def to_dict(self) -> JsonDict:
        return {
            "records": [record.to_dict() for record in self.records],
            "report": self.report.to_dict(),
        }


@dataclass(frozen=True)
class SearchResult:
    id: str
    score: float
    distance: float
    metadata: JsonDict
    namespace: str

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id,
            "score": self.score,
            "distance": self.distance,
            "metadata": dict(self.metadata),
            "namespace": self.namespace,
        }


@runtime_checkable
class VectorIndexBackend(Protocol):
    """Protocol for plugging external vector stores/indexes."""

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        ...

    def delete(self, ids: Sequence[str], namespace: str = "default") -> int:
        ...

    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 10,
        namespace: str = "default",
        metric: SimilarityMetric = SimilarityMetric.COSINE,
        metadata_filter: Optional[MetadataFilter] = None,
    ) -> List[SearchResult]:
        ...

    def count(self, namespace: Optional[str] = None) -> int:
        ...


class VectorMath:
    """Vector math helpers with numpy acceleration when available."""

    @staticmethod
    def to_float_list(vector: VectorLike) -> List[float]:
        try:
            return [float(x) for x in vector]
        except Exception as exc:
            raise VectorValidationError(f"Vector cannot be converted to floats: {exc}") from exc

    @staticmethod
    def norm(vector: Sequence[float], ord_: Union[int, float] = 2) -> float:
        if np is not None:
            return float(np.linalg.norm(np.asarray(vector, dtype=float), ord=ord_))
        if ord_ == 1:
            return sum(abs(x) for x in vector)
        if ord_ == 2:
            return math.sqrt(sum(x * x for x in vector))
        if ord_ == math.inf:
            return max(abs(x) for x in vector) if vector else 0.0
        return sum(abs(x) ** float(ord_) for x in vector) ** (1.0 / float(ord_))

    @staticmethod
    def dot(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise VectorDimensionError(f"Dimension mismatch: {len(a)} != {len(b)}")
        if np is not None:
            return float(np.dot(np.asarray(a, dtype=float), np.asarray(b, dtype=float)))
        return sum(x * y for x, y in zip(a, b))

    @staticmethod
    def cosine(a: Sequence[float], b: Sequence[float], epsilon: float = 1e-12) -> float:
        denominator = VectorMath.norm(a, 2) * VectorMath.norm(b, 2)
        if denominator <= epsilon:
            return 0.0
        return VectorMath.dot(a, b) / denominator

    @staticmethod
    def euclidean(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise VectorDimensionError(f"Dimension mismatch: {len(a)} != {len(b)}")
        if np is not None:
            return float(np.linalg.norm(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)))
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    @staticmethod
    def manhattan(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise VectorDimensionError(f"Dimension mismatch: {len(a)} != {len(b)}")
        return sum(abs(x - y) for x, y in zip(a, b))

    @staticmethod
    def normalize(vector: Sequence[float], mode: NormalizationMode, epsilon: float = 1e-12) -> List[float]:
        values = list(vector)
        if mode == NormalizationMode.NONE:
            return values
        if not values:
            return values

        if mode == NormalizationMode.L1:
            denom = VectorMath.norm(values, 1)
            return values if denom <= epsilon else [x / denom for x in values]
        if mode == NormalizationMode.L2:
            denom = VectorMath.norm(values, 2)
            return values if denom <= epsilon else [x / denom for x in values]
        if mode == NormalizationMode.MAX:
            denom = VectorMath.norm(values, math.inf)
            return values if denom <= epsilon else [x / denom for x in values]
        if mode == NormalizationMode.ZSCORE:
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / len(values)
            std = math.sqrt(variance)
            return [0.0 for _ in values] if std <= epsilon else [(x - mean) / std for x in values]
        if mode == NormalizationMode.MINMAX:
            minimum = min(values)
            maximum = max(values)
            span = maximum - minimum
            return [0.0 for _ in values] if abs(span) <= epsilon else [(x - minimum) / span for x in values]
        raise ValueError(f"Unsupported normalization mode: {mode}")

    @staticmethod
    def score_and_distance(
        a: Sequence[float],
        b: Sequence[float],
        metric: SimilarityMetric,
        epsilon: float = 1e-12,
    ) -> Tuple[float, float]:
        if metric == SimilarityMetric.COSINE:
            score = VectorMath.cosine(a, b, epsilon)
            return score, 1.0 - score
        if metric == SimilarityMetric.DOT:
            score = VectorMath.dot(a, b)
            return score, -score
        if metric == SimilarityMetric.EUCLIDEAN:
            distance = VectorMath.euclidean(a, b)
            return 1.0 / (1.0 + distance), distance
        if metric == SimilarityMetric.MANHATTAN:
            distance = VectorMath.manhattan(a, b)
            return 1.0 / (1.0 + distance), distance
        raise ValueError(f"Unsupported metric: {metric}")


class VectorProcessor:
    """Enterprise vector validator, normalizer and batch processor."""

    def __init__(self, config: Optional[VectorProcessorConfig] = None, *, log: Optional[logging.Logger] = None) -> None:
        self.config = config or VectorProcessorConfig()
        self.log = log or logger

    def process_vector(self, vector: VectorLike, *, record_id: Optional[str] = None) -> List[float]:
        values = VectorMath.to_float_list(vector)
        self._validate_vector(values, record_id=record_id)
        return VectorMath.normalize(values, self.config.normalization, self.config.zero_epsilon)

    def process_records(self, records: Iterable[Union[VectorRecord, Mapping[str, Any]]]) -> VectorProcessingResult:
        report = VectorProcessingReport()
        output: Dict[Tuple[str, str], VectorRecord] = {}

        for raw in records:
            report.total_input += 1
            try:
                record = self._coerce_record(raw)
                report.dimensions[len(record.vector)] += 1
                processed_vector = self.process_vector(record.vector, record_id=record.id)
                processed = dataclasses.replace(record, vector=processed_vector)
                key = (processed.namespace, processed.id)

                if key in output:
                    report.duplicates += 1
                    if self.config.duplicate_policy == DuplicatePolicy.FAIL:
                        raise VectorValidationError(f"Duplicate vector id in namespace: {processed.namespace}/{processed.id}")
                    if self.config.duplicate_policy == DuplicatePolicy.KEEP_FIRST:
                        continue

                output[key] = processed
            except Exception as exc:
                issue = VectorIssue(
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    record_id=self._safe_record_id(raw),
                )
                report.issues.append(issue)
                if self.config.invalid_vector_policy == InvalidVectorPolicy.FAIL:
                    raise
                if self.config.invalid_vector_policy == InvalidVectorPolicy.SKIP:
                    report.skipped += 1
                    continue
                if self.config.invalid_vector_policy == InvalidVectorPolicy.ZERO_FILL:
                    fallback = self._zero_record(raw)
                    output[(fallback.namespace, fallback.id)] = fallback

        report.total_output = len(output)
        report.finish()
        return VectorProcessingResult(records=list(output.values()), report=report)

    def _validate_vector(self, vector: Sequence[float], *, record_id: Optional[str] = None) -> None:
        if not vector:
            raise VectorValidationError("Vector cannot be empty")
        if self.config.expected_dimension is not None and len(vector) != self.config.expected_dimension:
            raise VectorDimensionError(
                f"Invalid vector dimension for record {record_id}: expected {self.config.expected_dimension}, got {len(vector)}"
            )
        for index, value in enumerate(vector):
            if math.isnan(value) and not self.config.allow_nan:
                raise VectorValidationError(f"NaN value at dimension {index}")
            if math.isinf(value) and not self.config.allow_inf:
                raise VectorValidationError(f"Infinite value at dimension {index}")

    def _coerce_record(self, raw: Union[VectorRecord, Mapping[str, Any]]) -> VectorRecord:
        if isinstance(raw, VectorRecord):
            return raw
        if not isinstance(raw, Mapping):
            raise VectorValidationError(f"Record must be VectorRecord or mapping, got {type(raw).__name__}")
        vector = raw.get("vector") or raw.get("embedding") or raw.get("values")
        if vector is None:
            raise VectorValidationError("Record does not contain vector/embedding/values")
        record_id = str(raw.get("id") or raw.get("record_id") or self._hash_vector(vector))
        metadata = dict(raw.get("metadata") or {})
        namespace = str(raw.get("namespace") or self.config.default_namespace)
        return VectorRecord(id=record_id, vector=VectorMath.to_float_list(vector), metadata=metadata, namespace=namespace)

    def _zero_record(self, raw: Union[VectorRecord, Mapping[str, Any]]) -> VectorRecord:
        record_id = self._safe_record_id(raw) or str(uuid.uuid4())
        namespace = self.config.default_namespace
        metadata: JsonDict = {"vector_processing_fallback": "zero_fill"}
        if isinstance(raw, VectorRecord):
            namespace = raw.namespace
            metadata.update(raw.metadata)
        elif isinstance(raw, Mapping):
            namespace = str(raw.get("namespace") or namespace)
            metadata.update(dict(raw.get("metadata") or {}))
        dimension = self.config.expected_dimension or 1
        return VectorRecord(id=record_id, vector=[0.0] * dimension, namespace=namespace, metadata=metadata)

    @staticmethod
    def _safe_record_id(raw: Any) -> Optional[str]:
        if isinstance(raw, VectorRecord):
            return raw.id
        if isinstance(raw, Mapping):
            value = raw.get("id") or raw.get("record_id")
            return str(value) if value is not None else None
        return None

    @staticmethod
    def _hash_vector(vector: Any) -> str:
        raw = json.dumps(vector, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def pairwise_similarity_matrix(
        self,
        vectors: Sequence[VectorLike],
        *,
        metric: Optional[SimilarityMetric] = None,
    ) -> List[List[float]]:
        metric = metric or self.config.metric
        processed = [self.process_vector(v) for v in vectors]
        matrix: List[List[float]] = []
        for left in processed:
            row = []
            for right in processed:
                score, _ = VectorMath.score_and_distance(left, right, metric, self.config.zero_epsilon)
                row.append(score)
            matrix.append(row)
        return matrix

    def detect_near_duplicates(
        self,
        records: Sequence[VectorRecord],
        *,
        threshold: float = 0.98,
        metric: SimilarityMetric = SimilarityMetric.COSINE,
    ) -> List[Tuple[str, str, float]]:
        duplicates: List[Tuple[str, str, float]] = []
        for i, left in enumerate(records):
            for right in records[i + 1 :]:
                if left.namespace != right.namespace:
                    continue
                score, _ = VectorMath.score_and_distance(left.vector, right.vector, metric, self.config.zero_epsilon)
                if score >= threshold:
                    duplicates.append((left.id, right.id, score))
        return duplicates


class InMemoryVectorIndex(VectorIndexBackend):
    """Exact in-memory vector index suitable for tests, small workloads and fallback retrieval."""

    def __init__(self, *, processor: Optional[VectorProcessor] = None, metric: SimilarityMetric = SimilarityMetric.COSINE) -> None:
        self.processor = processor or VectorProcessor(VectorProcessorConfig(metric=metric))
        self.metric = metric
        self._records: Dict[Tuple[str, str], VectorRecord] = {}

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        processed = self.processor.process_records(records).records
        for record in processed:
            self._records[(record.namespace, record.id)] = record

    def delete(self, ids: Sequence[str], namespace: str = "default") -> int:
        deleted = 0
        for record_id in ids:
            key = (namespace, str(record_id))
            if key in self._records:
                self._records.pop(key)
                deleted += 1
        return deleted

    def search(
        self,
        query_vector: Sequence[float],
        *,
        top_k: int = 10,
        namespace: str = "default",
        metric: SimilarityMetric = SimilarityMetric.COSINE,
        metadata_filter: Optional[MetadataFilter] = None,
    ) -> List[SearchResult]:
        if top_k <= 0:
            return []
        query = self.processor.process_vector(query_vector)
        heap: List[Tuple[float, SearchResult]] = []

        for (record_namespace, _), record in self._records.items():
            if record_namespace != namespace:
                continue
            if metadata_filter and not metadata_filter(record.metadata):
                continue
            score, distance = VectorMath.score_and_distance(query, record.vector, metric, self.processor.config.zero_epsilon)
            result = SearchResult(
                id=record.id,
                score=score,
                distance=distance,
                metadata=dict(record.metadata),
                namespace=record.namespace,
            )
            heapq.heappush(heap, (score, result))
            if len(heap) > top_k:
                heapq.heappop(heap)

        return [item[1] for item in sorted(heap, key=lambda x: x[0], reverse=True)]

    def count(self, namespace: Optional[str] = None) -> int:
        if namespace is None:
            return len(self._records)
        return sum(1 for record_namespace, _ in self._records if record_namespace == namespace)

    def get(self, record_id: str, namespace: str = "default") -> Optional[VectorRecord]:
        return self._records.get((namespace, record_id))

    def clear(self, namespace: Optional[str] = None) -> None:
        if namespace is None:
            self._records.clear()
            return
        for key in list(self._records.keys()):
            if key[0] == namespace:
                self._records.pop(key, None)

    def stats(self) -> JsonDict:
        namespaces = Counter(namespace for namespace, _ in self._records.keys())
        dimensions = Counter(len(record.vector) for record in self._records.values())
        return {
            "total_records": len(self._records),
            "namespaces": dict(namespaces),
            "dimensions": dict(dimensions),
            "metric": self.metric.value,
        }


class MetadataFilters:
    """Common metadata filter builders for vector retrieval."""

    @staticmethod
    def equals(key: str, value: Any) -> MetadataFilter:
        return lambda metadata: metadata.get(key) == value

    @staticmethod
    def contains(key: str, value: Any) -> MetadataFilter:
        def _filter(metadata: Mapping[str, Any]) -> bool:
            candidate = metadata.get(key)
            if isinstance(candidate, (list, tuple, set)):
                return value in candidate
            if isinstance(candidate, str):
                return str(value) in candidate
            return False

        return _filter

    @staticmethod
    def all_of(*filters: MetadataFilter) -> MetadataFilter:
        return lambda metadata: all(fn(metadata) for fn in filters)

    @staticmethod
    def any_of(*filters: MetadataFilter) -> MetadataFilter:
        return lambda metadata: any(fn(metadata) for fn in filters)


# -----------------------------------------------------------------------------
# Convenience factories
# -----------------------------------------------------------------------------


def build_embedding_processor(dimension: int, *, normalization: NormalizationMode = NormalizationMode.L2) -> VectorProcessor:
    return VectorProcessor(
        VectorProcessorConfig(
            expected_dimension=dimension,
            normalization=normalization,
            invalid_vector_policy=InvalidVectorPolicy.FAIL,
            duplicate_policy=DuplicatePolicy.KEEP_LAST,
            metric=SimilarityMetric.COSINE,
        )
    )


def build_semantic_search_index(dimension: int) -> InMemoryVectorIndex:
    processor = build_embedding_processor(dimension)
    return InMemoryVectorIndex(processor=processor, metric=SimilarityMetric.COSINE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    index = build_semantic_search_index(dimension=3)
    records = [
        VectorRecord(id="doc-1", vector=[1.0, 0.0, 0.0], metadata={"category": "finance"}),
        VectorRecord(id="doc-2", vector=[0.9, 0.1, 0.0], metadata={"category": "finance"}),
        VectorRecord(id="doc-3", vector=[0.0, 1.0, 0.0], metadata={"category": "retail"}),
    ]
    index.upsert(records)

    results = index.search(
        [1.0, 0.0, 0.0],
        top_k=2,
        metadata_filter=MetadataFilters.equals("category", "finance"),
    )

    print(json.dumps(index.stats(), indent=2, ensure_ascii=False))
    print(json.dumps([result.to_dict() for result in results], indent=2, ensure_ascii=False))
