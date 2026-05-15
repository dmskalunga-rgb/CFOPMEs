"""
kwanza-ai-core/services/inference_service.py

Enterprise-grade inference orchestration service.

Purpose
-------
Centralize model inference for online APIs, batch pipelines, fraud scoring,
classification, anomaly detection, recommendations, forecasting and document AI.

Key capabilities
----------------
- Async-first model serving abstraction.
- Single and batch inference.
- Model registry with version/channel routing.
- Input validation and normalization.
- Prediction caching with TTL.
- Timeout, retry, circuit breaker and fallback behavior.
- Shadow/canary routing hooks.
- Explainability payload support.
- Metrics, audit and structured decision metadata.
- Framework-agnostic: can be used with FastAPI, Celery, Kafka, Airflow, Prefect,
  Supabase functions, internal services or CLI jobs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import (
    Any,
    Awaitable,
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
FeatureMap = Mapping[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class InferenceServiceError(RuntimeError):
    """Base exception for inference service failures."""


class InferenceValidationError(InferenceServiceError):
    """Raised when an inference request is invalid."""


class ModelNotFoundError(InferenceServiceError):
    """Raised when a requested model cannot be resolved."""


class ModelUnavailableError(InferenceServiceError):
    """Raised when a model exists but cannot currently serve traffic."""


class ModelTimeoutError(InferenceServiceError):
    """Raised when a model inference call exceeds timeout."""


class CircuitBreakerOpenError(InferenceServiceError):
    """Raised when a model circuit breaker is open."""


# =============================================================================
# Enums and data models
# =============================================================================


class InferenceTask(str, Enum):
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    ANOMALY_DETECTION = "anomaly_detection"
    FRAUD_DETECTION = "fraud_detection"
    FORECASTING = "forecasting"
    RANKING = "ranking"
    RECOMMENDATION = "recommendation"
    EMBEDDING = "embedding"
    DOCUMENT_ROUTING = "document_routing"
    OCR = "ocr"
    GENERIC = "generic"


class ModelStatus(str, Enum):
    ACTIVE = "active"
    WARMING = "warming"
    DEGRADED = "degraded"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class RoutingStrategy(str, Enum):
    LATEST = "latest"
    STABLE = "stable"
    CANARY = "canary"
    SHADOW = "shadow"
    PINNED_VERSION = "pinned_version"


class PredictionFormat(str, Enum):
    RAW = "raw"
    SCORE = "score"
    LABEL = "label"
    PROBABILITIES = "probabilities"
    EMBEDDING = "embedding"
    STRUCTURED = "structured"


@dataclass(frozen=True)
class ModelReference:
    name: str
    version: str
    tenant_id: Optional[str] = None
    stage: str = "production"

    @property
    def key(self) -> str:
        tenant = self.tenant_id or "global"
        return f"{tenant}:{self.name}:{self.version}:{self.stage}"


@dataclass(frozen=True)
class ModelMetadata:
    reference: ModelReference
    task: InferenceTask
    status: ModelStatus = ModelStatus.ACTIVE
    framework: str = "unknown"
    owner: Optional[str] = None
    description: Optional[str] = None
    input_schema: Optional[Mapping[str, Any]] = None
    output_schema: Optional[Mapping[str, Any]] = None
    labels: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_default: bool = False
    priority: int = 100
    canary_weight: float = 0.0
    timeout_ms: int = 2_500
    max_batch_size: int = 256
    cache_ttl_seconds: int = 0


@dataclass(frozen=True)
class InferenceRequest:
    model_name: str
    features: FeatureMap
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_version: Optional[str] = None
    task: InferenceTask = InferenceTask.GENERIC
    routing_strategy: RoutingStrategy = RoutingStrategy.STABLE
    prediction_format: PredictionFormat = PredictionFormat.STRUCTURED
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    entity_id: Optional[str] = None
    explain: bool = False
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchInferenceRequest:
    model_name: str
    rows: Sequence[FeatureMap]
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    model_version: Optional[str] = None
    task: InferenceTask = InferenceTask.GENERIC
    routing_strategy: RoutingStrategy = RoutingStrategy.STABLE
    prediction_format: PredictionFormat = PredictionFormat.STRUCTURED
    explain: bool = False
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InferenceExplanation:
    method: str
    top_features: Sequence[Mapping[str, Any]] = field(default_factory=list)
    global_context: Mapping[str, Any] = field(default_factory=dict)
    warnings: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class InferenceResult:
    request_id: str
    model: ModelReference
    task: InferenceTask
    prediction: Any
    prediction_format: PredictionFormat
    confidence: Optional[float]
    probabilities: Optional[Mapping[str, float]]
    explanation: Optional[InferenceExplanation]
    cached: bool
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["task"] = self.task.value
        payload["prediction_format"] = self.prediction_format.value
        return payload


@dataclass(frozen=True)
class BatchInferenceResult:
    request_id: str
    model: ModelReference
    task: InferenceTask
    results: Sequence[InferenceResult]
    total: int
    succeeded: int
    failed: int
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["task"] = self.task.value
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


@dataclass(frozen=True)
class InferenceServiceConfig:
    default_timeout_ms: int = 2_500
    max_batch_size: int = 1_000
    retries: int = 2
    retry_base_delay_ms: int = 80
    retry_jitter_ms: int = 50
    fail_open: bool = False
    default_cache_ttl_seconds: int = 120
    cache_max_size: int = 50_000
    audit_enabled: bool = True
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: int = 45
    shadow_enabled: bool = False
    canary_random_seed: Optional[int] = None
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.default_timeout_ms <= 0:
            raise InferenceValidationError("default_timeout_ms must be positive.")
        if self.max_batch_size <= 0:
            raise InferenceValidationError("max_batch_size must be positive.")
        if self.retries < 0:
            raise InferenceValidationError("retries cannot be negative.")
        if self.circuit_failure_threshold <= 0:
            raise InferenceValidationError("circuit_failure_threshold must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class ModelAdapter(Protocol):
    async def predict(self, features: FeatureMap) -> Any: ...

    async def predict_batch(self, rows: Sequence[FeatureMap]) -> Sequence[Any]: ...

    async def explain(self, features: FeatureMap, prediction: Any) -> Optional[InferenceExplanation]: ...


class ModelRegistry(Protocol):
    async def resolve(
        self,
        model_name: str,
        tenant_id: Optional[str],
        task: InferenceTask,
        routing_strategy: RoutingStrategy,
        model_version: Optional[str] = None,
    ) -> Tuple[ModelMetadata, ModelAdapter]: ...

    async def list_models(self, tenant_id: Optional[str] = None) -> Sequence[ModelMetadata]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


class InputValidator(Protocol):
    async def validate(self, features: FeatureMap, schema: Optional[Mapping[str, Any]]) -> FeatureMap: ...


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


class BasicInputValidator:
    """
    Lightweight schema validator.

    Supported optional schema format:
    {
        "required": ["amount", "country"],
        "types": {"amount": "number", "country": "string", "is_new": "boolean"},
        "defaults": {"currency": "AOA"}
    }
    """

    async def validate(self, features: FeatureMap, schema: Optional[Mapping[str, Any]]) -> FeatureMap:
        if not isinstance(features, Mapping):
            raise InferenceValidationError("features must be a mapping/object.")

        normalized: JsonDict = dict(features)
        if not schema:
            return normalized

        defaults = schema.get("defaults") or {}
        for key, value in defaults.items():
            normalized.setdefault(key, value)

        required = schema.get("required") or []
        missing = [field_name for field_name in required if field_name not in normalized or normalized[field_name] is None]
        if missing:
            raise InferenceValidationError(f"Missing required feature(s): {', '.join(missing)}")

        type_map = schema.get("types") or {}
        for field_name, expected in type_map.items():
            if field_name not in normalized or normalized[field_name] is None:
                continue
            normalized[field_name] = self._coerce(field_name, normalized[field_name], str(expected).lower())

        return normalized

    def _coerce(self, field_name: str, value: Any, expected: str) -> Any:
        try:
            if expected in {"number", "float"}:
                coerced = float(value)
                if math.isnan(coerced) or math.isinf(coerced):
                    raise ValueError("number cannot be NaN or infinite")
                return coerced
            if expected in {"integer", "int"}:
                return int(value)
            if expected in {"string", "str"}:
                return str(value)
            if expected in {"boolean", "bool"}:
                if isinstance(value, bool):
                    return value
                if str(value).lower() in {"true", "1", "yes", "y"}:
                    return True
                if str(value).lower() in {"false", "0", "no", "n"}:
                    return False
                raise ValueError("invalid boolean")
            if expected in {"object", "dict"}:
                if not isinstance(value, Mapping):
                    raise ValueError("expected object")
                return dict(value)
            if expected in {"array", "list"}:
                if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                    raise ValueError("expected array")
                return list(value)
        except Exception as exc:
            raise InferenceValidationError(
                f"Invalid feature type for '{field_name}': expected {expected}, got {value!r}"
            ) from exc
        return value


# =============================================================================
# Cache
# =============================================================================


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int, max_size: int = 50_000) -> None:
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
# Circuit breaker
# =============================================================================


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: Optional[float] = None


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: int) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._states: MutableMapping[str, CircuitState] = defaultdict(CircuitState)
        self._lock = asyncio.Lock()

    async def before_call(self, key: str) -> None:
        async with self._lock:
            state = self._states[key]
            if state.opened_at is None:
                return
            if time.monotonic() - state.opened_at >= self.recovery_seconds:
                state.failures = 0
                state.opened_at = None
                return
            raise CircuitBreakerOpenError(f"Circuit breaker is open for model {key}")

    async def record_success(self, key: str) -> None:
        async with self._lock:
            self._states[key] = CircuitState()

    async def record_failure(self, key: str) -> None:
        async with self._lock:
            state = self._states[key]
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_at = time.monotonic()


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"), ensure_ascii=False)


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _extract_prediction_fields(raw: Any) -> Tuple[Any, Optional[float], Optional[Mapping[str, float]]]:
    if isinstance(raw, Mapping):
        prediction = raw.get("prediction", raw.get("label", raw.get("score", raw)))
        confidence = _safe_float(raw.get("confidence"))
        probabilities = raw.get("probabilities")
        if isinstance(probabilities, Mapping):
            probabilities = {str(k): float(v) for k, v in probabilities.items() if _safe_float(v) is not None}
        else:
            probabilities = None
        return prediction, confidence, probabilities

    if isinstance(raw, (float, int)):
        score = float(raw)
        confidence = max(score, 1.0 - score) if 0 <= score <= 1 else None
        return raw, confidence, None

    return raw, None, None


def _mean_latency(results: Sequence[InferenceResult]) -> float:
    if not results:
        return 0.0
    return statistics.mean(result.latency_ms for result in results)


# =============================================================================
# In-memory model registry
# =============================================================================


class InMemoryModelRegistry:
    def __init__(self, random_seed: Optional[int] = None) -> None:
        self._models: Dict[str, Tuple[ModelMetadata, ModelAdapter]] = {}
        self._random = random.Random(random_seed)

    def register(self, metadata: ModelMetadata, adapter: ModelAdapter) -> None:
        self._models[metadata.reference.key] = (metadata, adapter)

    async def list_models(self, tenant_id: Optional[str] = None) -> Sequence[ModelMetadata]:
        rows = [metadata for metadata, _ in self._models.values()]
        if tenant_id:
            rows = [m for m in rows if m.reference.tenant_id in {tenant_id, None}]
        return sorted(rows, key=lambda m: (m.reference.name, m.priority, m.reference.version))

    async def resolve(
        self,
        model_name: str,
        tenant_id: Optional[str],
        task: InferenceTask,
        routing_strategy: RoutingStrategy,
        model_version: Optional[str] = None,
    ) -> Tuple[ModelMetadata, ModelAdapter]:
        candidates = [
            pair
            for pair in self._models.values()
            if pair[0].reference.name == model_name
            and pair[0].task in {task, InferenceTask.GENERIC}
            and pair[0].status in {ModelStatus.ACTIVE, ModelStatus.DEGRADED, ModelStatus.WARMING}
            and pair[0].reference.tenant_id in {tenant_id, None}
        ]

        if not candidates:
            raise ModelNotFoundError(f"No active model found for '{model_name}' and task '{task.value}'.")

        if model_version:
            pinned = [pair for pair in candidates if pair[0].reference.version == model_version]
            if not pinned:
                raise ModelNotFoundError(f"Model '{model_name}' version '{model_version}' was not found.")
            return sorted(pinned, key=lambda pair: pair[0].priority)[0]

        tenant_specific = [pair for pair in candidates if pair[0].reference.tenant_id == tenant_id]
        pool = tenant_specific or candidates

        if routing_strategy == RoutingStrategy.CANARY:
            canaries = [pair for pair in pool if pair[0].canary_weight > 0]
            if canaries:
                total_weight = sum(pair[0].canary_weight for pair in canaries)
                roll = self._random.random() * total_weight
                upto = 0.0
                for pair in canaries:
                    upto += pair[0].canary_weight
                    if roll <= upto:
                        return pair

        if routing_strategy == RoutingStrategy.LATEST:
            return sorted(pool, key=lambda pair: pair[0].reference.version, reverse=True)[0]

        defaults = [pair for pair in pool if pair[0].is_default]
        if defaults:
            return sorted(defaults, key=lambda pair: pair[0].priority)[0]

        return sorted(pool, key=lambda pair: pair[0].priority)[0]


# =============================================================================
# Example adapters
# =============================================================================


class CallableModelAdapter:
    """Wraps sync or async Python callables into the ModelAdapter protocol."""

    def __init__(
        self,
        predict_fn: Callable[[FeatureMap], Any] | Callable[[FeatureMap], Awaitable[Any]],
        batch_fn: Optional[Callable[[Sequence[FeatureMap]], Sequence[Any] | Awaitable[Sequence[Any]]]] = None,
        explain_fn: Optional[
            Callable[[FeatureMap, Any], Optional[InferenceExplanation] | Awaitable[Optional[InferenceExplanation]]]
        ] = None,
    ) -> None:
        self.predict_fn = predict_fn
        self.batch_fn = batch_fn
        self.explain_fn = explain_fn

    async def predict(self, features: FeatureMap) -> Any:
        result = self.predict_fn(features)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def predict_batch(self, rows: Sequence[FeatureMap]) -> Sequence[Any]:
        if self.batch_fn:
            result = self.batch_fn(rows)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return [await self.predict(row) for row in rows]

    async def explain(self, features: FeatureMap, prediction: Any) -> Optional[InferenceExplanation]:
        if not self.explain_fn:
            return None
        result = self.explain_fn(features, prediction)
        if asyncio.iscoroutine(result):
            return await result
        return result


class EchoModelAdapter:
    """Development adapter that returns deterministic scores from numeric features."""

    async def predict(self, features: FeatureMap) -> Any:
        numeric_values = [float(v) for v in features.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        score = sum(numeric_values) / (len(numeric_values) or 1)
        score = 1 / (1 + math.exp(-score / 100))
        return {
            "prediction": "positive" if score >= 0.5 else "negative",
            "score": score,
            "confidence": max(score, 1 - score),
            "probabilities": {"negative": 1 - score, "positive": score},
        }

    async def predict_batch(self, rows: Sequence[FeatureMap]) -> Sequence[Any]:
        return [await self.predict(row) for row in rows]

    async def explain(self, features: FeatureMap, prediction: Any) -> Optional[InferenceExplanation]:
        numeric = [
            {"feature": str(key), "value": value, "importance": abs(float(value))}
            for key, value in features.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        numeric.sort(key=lambda item: item["importance"], reverse=True)
        return InferenceExplanation(method="heuristic_feature_magnitude", top_features=numeric[:10])


# =============================================================================
# Main service
# =============================================================================


class InferenceService:
    def __init__(
        self,
        registry: ModelRegistry,
        config: Optional[InferenceServiceConfig] = None,
        validator: Optional[InputValidator] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[AsyncTTLCache] = None,
    ) -> None:
        self.config = config or InferenceServiceConfig()
        self.config.validate()
        self.registry = registry
        self.validator = validator or BasicInputValidator()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.cache = cache or AsyncTTLCache(
            ttl_seconds=self.config.default_cache_ttl_seconds,
            max_size=self.config.cache_max_size,
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.config.circuit_failure_threshold,
            recovery_seconds=self.config.circuit_recovery_seconds,
        )

    async def predict(self, request: InferenceRequest) -> InferenceResult:
        started = time.perf_counter()
        self._validate_request(request)

        metadata, adapter = await self.registry.resolve(
            model_name=request.model_name,
            tenant_id=request.tenant_id,
            task=request.task,
            routing_strategy=request.routing_strategy,
            model_version=request.model_version,
        )

        tags = self._metric_tags(request, metadata)
        self.metrics.increment("inference.request.started", tags=tags)

        cache_key = self._cache_key(request, metadata)
        if request.use_cache and metadata.cache_ttl_seconds >= 0:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                self.metrics.increment("inference.cache.hit", tags=tags)
                return self._mark_cached(cached, started)

        self.metrics.increment("inference.cache.miss", tags=tags)

        try:
            result = await self._predict_uncached(request, metadata, adapter, started)
            ttl = metadata.cache_ttl_seconds or self.config.default_cache_ttl_seconds
            if request.use_cache and ttl > 0:
                await self.cache.set(cache_key, result, ttl_seconds=ttl)
            await self._audit_result("inference.request.completed", request, result)
            self.metrics.increment("inference.request.completed", tags={**tags, "status": "success"})
            self.metrics.timing("inference.latency_ms", result.latency_ms, tags=tags)
            return result
        except Exception as exc:
            self.metrics.increment("inference.request.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Inference request failed", extra={"request_id": request.request_id, "model": metadata.reference.key})
            result = await self._handle_failure(request, metadata, exc, started)
            await self._audit_result("inference.request.failed", request, result)
            return result

    async def predict_batch(self, request: BatchInferenceRequest) -> BatchInferenceResult:
        started = time.perf_counter()
        self._validate_batch_request(request)

        metadata, adapter = await self.registry.resolve(
            model_name=request.model_name,
            tenant_id=request.tenant_id,
            task=request.task,
            routing_strategy=request.routing_strategy,
            model_version=request.model_version,
        )

        if len(request.rows) > min(self.config.max_batch_size, metadata.max_batch_size):
            raise InferenceValidationError(
                f"Batch size {len(request.rows)} exceeds max allowed {min(self.config.max_batch_size, metadata.max_batch_size)}."
            )

        tags = {
            "tenant_id": request.tenant_id or "global",
            "model_name": metadata.reference.name,
            "model_version": metadata.reference.version,
            "task": request.task.value,
        }
        self.metrics.increment("inference.batch.started", tags=tags)

        validated_rows = [await self.validator.validate(row, metadata.input_schema) for row in request.rows]
        timeout_ms = request.timeout_ms or metadata.timeout_ms or self.config.default_timeout_ms

        await self.circuit_breaker.before_call(metadata.reference.key)

        try:
            raw_predictions = await self._call_with_retry(
                key=metadata.reference.key,
                call=lambda: adapter.predict_batch(validated_rows),
                timeout_ms=timeout_ms,
            )
            if len(raw_predictions) != len(validated_rows):
                raise InferenceServiceError("Model returned a different number of predictions than requested rows.")

            results: List[InferenceResult] = []
            for idx, raw_prediction in enumerate(raw_predictions):
                row_started = time.perf_counter()
                prediction, confidence, probabilities = _extract_prediction_fields(raw_prediction)
                explanation = None
                if request.explain:
                    explanation = await adapter.explain(validated_rows[idx], raw_prediction)
                results.append(
                    InferenceResult(
                        request_id=f"{request.request_id}:{idx}",
                        model=metadata.reference,
                        task=request.task,
                        prediction=prediction,
                        prediction_format=request.prediction_format,
                        confidence=confidence,
                        probabilities=probabilities,
                        explanation=explanation,
                        cached=False,
                        latency_ms=round((time.perf_counter() - row_started) * 1000, 4),
                        created_at=_utc_now(),
                        metadata={
                            "batch_request_id": request.request_id,
                            "row_index": idx,
                            "raw_prediction_type": raw_prediction.__class__.__name__,
                        },
                    )
                )

            await self.circuit_breaker.record_success(metadata.reference.key)
            latency_ms = round((time.perf_counter() - started) * 1000, 4)
            result = BatchInferenceResult(
                request_id=request.request_id,
                model=metadata.reference,
                task=request.task,
                results=results,
                total=len(results),
                succeeded=len(results),
                failed=0,
                latency_ms=latency_ms,
                created_at=_utc_now(),
                metadata={"mean_row_latency_ms": round(_mean_latency(results), 4)},
            )
            self.metrics.increment("inference.batch.completed", tags={**tags, "status": "success"})
            self.metrics.timing("inference.batch.latency_ms", latency_ms, tags=tags)
            await self._audit_batch_result("inference.batch.completed", request, result)
            return result
        except Exception as exc:
            await self.circuit_breaker.record_failure(metadata.reference.key)
            self.metrics.increment("inference.batch.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Batch inference failed", extra={"request_id": request.request_id, "model": metadata.reference.key})
            if not self.config.fail_open:
                raise
            fallback_results = [
                self._fallback_result(
                    request_id=f"{request.request_id}:{idx}",
                    metadata=metadata,
                    task=request.task,
                    started=started,
                    exc=exc,
                )
                for idx, _ in enumerate(request.rows)
            ]
            result = BatchInferenceResult(
                request_id=request.request_id,
                model=metadata.reference,
                task=request.task,
                results=fallback_results,
                total=len(fallback_results),
                succeeded=0,
                failed=len(fallback_results),
                latency_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={"fallback": True, "error": exc.__class__.__name__},
            )
            await self._audit_batch_result("inference.batch.failed", request, result)
            return result

    async def _predict_uncached(
        self,
        request: InferenceRequest,
        metadata: ModelMetadata,
        adapter: ModelAdapter,
        started: float,
    ) -> InferenceResult:
        features = await self.validator.validate(request.features, metadata.input_schema)
        timeout_ms = request.timeout_ms or metadata.timeout_ms or self.config.default_timeout_ms

        await self.circuit_breaker.before_call(metadata.reference.key)
        raw_prediction = await self._call_with_retry(
            key=metadata.reference.key,
            call=lambda: adapter.predict(features),
            timeout_ms=timeout_ms,
        )
        await self.circuit_breaker.record_success(metadata.reference.key)

        prediction, confidence, probabilities = _extract_prediction_fields(raw_prediction)
        explanation = await adapter.explain(features, raw_prediction) if request.explain else None

        return InferenceResult(
            request_id=request.request_id,
            model=metadata.reference,
            task=request.task,
            prediction=prediction,
            prediction_format=request.prediction_format,
            confidence=confidence,
            probabilities=probabilities,
            explanation=explanation,
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={
                "trace_id": request.trace_id,
                "routing_strategy": request.routing_strategy.value,
                "model_status": metadata.status.value,
                "raw_prediction_type": raw_prediction.__class__.__name__,
                "input_hash": _hash_payload(features),
                "user_hash": _hash_value(request.user_id, self.config.privacy_hash_salt),
                "entity_hash": _hash_value(request.entity_id, self.config.privacy_hash_salt),
            },
        )

    async def _call_with_retry(
        self,
        key: str,
        call: Callable[[], Awaitable[Any]],
        timeout_ms: int,
    ) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.retries + 1):
            try:
                return await asyncio.wait_for(call(), timeout=timeout_ms / 1000)
            except asyncio.TimeoutError as exc:
                last_exc = ModelTimeoutError(f"Model call timed out after {timeout_ms}ms")
            except CircuitBreakerOpenError:
                raise
            except Exception as exc:
                last_exc = exc

            if attempt < self.config.retries:
                delay_ms = self.config.retry_base_delay_ms * (2**attempt) + random.randint(0, self.config.retry_jitter_ms)
                await asyncio.sleep(delay_ms / 1000)

        await self.circuit_breaker.record_failure(key)
        assert last_exc is not None
        raise last_exc

    async def _handle_failure(
        self,
        request: InferenceRequest,
        metadata: ModelMetadata,
        exc: Exception,
        started: float,
    ) -> InferenceResult:
        try:
            await self.circuit_breaker.record_failure(metadata.reference.key)
        except Exception:
            logger.debug("Unable to update circuit breaker failure state", exc_info=True)

        if not self.config.fail_open:
            raise exc

        return self._fallback_result(
            request_id=request.request_id,
            metadata=metadata,
            task=request.task,
            started=started,
            exc=exc,
        )

    def _fallback_result(
        self,
        request_id: str,
        metadata: ModelMetadata,
        task: InferenceTask,
        started: float,
        exc: Exception,
    ) -> InferenceResult:
        return InferenceResult(
            request_id=request_id,
            model=metadata.reference,
            task=task,
            prediction=None,
            prediction_format=PredictionFormat.STRUCTURED,
            confidence=0.0,
            probabilities=None,
            explanation=InferenceExplanation(
                method="service_fallback",
                warnings=[f"Inference fallback activated due to {exc.__class__.__name__}"],
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"fallback": True, "error": exc.__class__.__name__, "message": str(exc)},
        )

    def _cache_key(self, request: InferenceRequest, metadata: ModelMetadata) -> str:
        payload = {
            "model": metadata.reference.key,
            "features": request.features,
            "task": request.task.value,
            "format": request.prediction_format.value,
            "explain": request.explain,
        }
        return f"inference:{_hash_payload(payload)}"

    def _mark_cached(self, result: InferenceResult, started: float) -> InferenceResult:
        return InferenceResult(
            request_id=result.request_id,
            model=result.model,
            task=result.task,
            prediction=result.prediction,
            prediction_format=result.prediction_format,
            confidence=result.confidence,
            probabilities=result.probabilities,
            explanation=result.explanation,
            cached=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={**dict(result.metadata), "cache_returned_at": _utc_now().isoformat()},
        )

    def _validate_request(self, request: InferenceRequest) -> None:
        if not request.model_name:
            raise InferenceValidationError("model_name is required.")
        if not isinstance(request.features, Mapping):
            raise InferenceValidationError("features must be an object/mapping.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise InferenceValidationError("timeout_ms must be positive.")

    def _validate_batch_request(self, request: BatchInferenceRequest) -> None:
        if not request.model_name:
            raise InferenceValidationError("model_name is required.")
        if not isinstance(request.rows, Sequence) or isinstance(request.rows, (str, bytes)):
            raise InferenceValidationError("rows must be a sequence of feature mappings.")
        if not request.rows:
            raise InferenceValidationError("rows cannot be empty.")
        for idx, row in enumerate(request.rows):
            if not isinstance(row, Mapping):
                raise InferenceValidationError(f"rows[{idx}] must be a mapping/object.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise InferenceValidationError("timeout_ms must be positive.")

    def _metric_tags(self, request: InferenceRequest, metadata: ModelMetadata) -> Dict[str, str]:
        return {
            "tenant_id": request.tenant_id or "global",
            "model_name": metadata.reference.name,
            "model_version": metadata.reference.version,
            "task": request.task.value,
            "routing": request.routing_strategy.value,
        }

    async def _audit_result(self, event_name: str, request: InferenceRequest, result: InferenceResult) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(
                event_name,
                {
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                    "tenant_id": request.tenant_id,
                    "model_name": result.model.name,
                    "model_version": result.model.version,
                    "task": result.task.value,
                    "cached": result.cached,
                    "latency_ms": result.latency_ms,
                    "confidence": result.confidence,
                    "prediction_format": result.prediction_format.value,
                    "user_hash": _hash_value(request.user_id, self.config.privacy_hash_salt),
                    "entity_hash": _hash_value(request.entity_id, self.config.privacy_hash_salt),
                    "created_at": result.created_at.isoformat(),
                    "fallback": bool(result.metadata.get("fallback")),
                },
            )
        except Exception:
            logger.exception("Failed to write inference audit event", extra={"request_id": request.request_id})

    async def _audit_batch_result(self, event_name: str, request: BatchInferenceRequest, result: BatchInferenceResult) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(
                event_name,
                {
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    "model_name": result.model.name,
                    "model_version": result.model.version,
                    "task": result.task.value,
                    "total": result.total,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "latency_ms": result.latency_ms,
                    "created_at": result.created_at.isoformat(),
                    "fallback": bool(result.metadata.get("fallback")),
                },
            )
        except Exception:
            logger.exception("Failed to write batch inference audit event", extra={"request_id": request.request_id})

    @classmethod
    def request_from_payload(cls, payload: Mapping[str, Any]) -> InferenceRequest:
        return InferenceRequest(
            model_name=str(payload["model_name"]),
            model_version=payload.get("model_version"),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            features=payload.get("features") or {},
            task=InferenceTask(payload.get("task", InferenceTask.GENERIC.value)),
            routing_strategy=RoutingStrategy(payload.get("routing_strategy", RoutingStrategy.STABLE.value)),
            prediction_format=PredictionFormat(payload.get("prediction_format", PredictionFormat.STRUCTURED.value)),
            trace_id=payload.get("trace_id"),
            user_id=payload.get("user_id"),
            entity_id=payload.get("entity_id"),
            explain=bool(payload.get("explain", False)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            metadata=payload.get("metadata") or {},
        )

    @classmethod
    def batch_request_from_payload(cls, payload: Mapping[str, Any]) -> BatchInferenceRequest:
        return BatchInferenceRequest(
            model_name=str(payload["model_name"]),
            model_version=payload.get("model_version"),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            rows=payload.get("rows") or [],
            task=InferenceTask(payload.get("task", InferenceTask.GENERIC.value)),
            routing_strategy=RoutingStrategy(payload.get("routing_strategy", RoutingStrategy.STABLE.value)),
            prediction_format=PredictionFormat(payload.get("prediction_format", PredictionFormat.STRUCTURED.value)),
            explain=bool(payload.get("explain", False)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_inference_service(
    registry: Optional[InMemoryModelRegistry] = None,
    config: Optional[InferenceServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> InferenceService:
    cfg = config or InferenceServiceConfig()
    reg = registry or InMemoryModelRegistry(random_seed=cfg.canary_random_seed)

    if registry is None:
        metadata = ModelMetadata(
            reference=ModelReference(name="echo-model", version="1.0.0", stage="production"),
            task=InferenceTask.GENERIC,
            framework="python",
            description="Default development echo model.",
            input_schema={"required": [], "types": {}},
            is_default=True,
            cache_ttl_seconds=cfg.default_cache_ttl_seconds,
        )
        reg.register(metadata, EchoModelAdapter())

    return InferenceService(
        registry=reg,
        config=cfg,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)

    registry = InMemoryModelRegistry(random_seed=42)
    registry.register(
        ModelMetadata(
            reference=ModelReference(name="risk-score", version="1.0.0", tenant_id="tenant-ao"),
            task=InferenceTask.FRAUD_DETECTION,
            framework="callable",
            is_default=True,
            input_schema={
                "required": ["amount", "recent_count"],
                "types": {"amount": "number", "recent_count": "integer", "is_new_user": "boolean"},
                "defaults": {"is_new_user": False},
            },
            cache_ttl_seconds=30,
        ),
        CallableModelAdapter(
            predict_fn=lambda features: {
                "prediction": "review" if features["amount"] > 500_000 or features["recent_count"] > 5 else "approve",
                "confidence": 0.91,
                "probabilities": {"approve": 0.22, "review": 0.78}
                if features["amount"] > 500_000
                else {"approve": 0.89, "review": 0.11},
            },
            explain_fn=lambda features, prediction: InferenceExplanation(
                method="business_heuristic",
                top_features=[
                    {"feature": "amount", "value": features.get("amount"), "importance": 0.72},
                    {"feature": "recent_count", "value": features.get("recent_count"), "importance": 0.41},
                ],
            ),
        ),
    )

    service = build_inference_service(registry=registry)
    request = InferenceRequest(
        model_name="risk-score",
        tenant_id="tenant-ao",
        task=InferenceTask.FRAUD_DETECTION,
        features={"amount": "750000", "recent_count": 8},
        explain=True,
        user_id="user-123",
    )

    result = await service.predict(request)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

    batch = BatchInferenceRequest(
        model_name="risk-score",
        tenant_id="tenant-ao",
        task=InferenceTask.FRAUD_DETECTION,
        rows=[{"amount": 10000, "recent_count": 1}, {"amount": 900000, "recent_count": 9}],
        explain=True,
    )
    batch_result = await service.predict_batch(batch)
    print(json.dumps(batch_result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
