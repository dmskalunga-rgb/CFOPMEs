"""
kwanza-ai-core/services/prediction_service.py

Enterprise-grade prediction service layer.

Purpose
-------
Provide a unified prediction orchestration layer for business and ML use cases:
forecasting, scoring, regression, classification, risk estimation, demand
prediction, cashflow projections and scenario simulations.

Design goals
------------
- Async-first and framework-agnostic service API.
- Model/provider abstraction with local deterministic fallback.
- Single, batch and time-series prediction requests.
- Ensemble support with weighted aggregation.
- Confidence intervals and uncertainty metadata.
- Input validation, normalization and feature hashing.
- Idempotency/cache for repeated prediction workloads.
- Metrics, audit and structured logs.
- Tenant-aware, privacy-conscious outputs.

This module is intentionally self-contained and can be wired into FastAPI,
workers, orchestration pipelines, Kafka consumers, batch jobs or internal APIs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]
FeatureMap = Mapping[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class PredictionServiceError(RuntimeError):
    """Base exception for prediction service failures."""


class PredictionValidationError(PredictionServiceError):
    """Raised when a prediction request is invalid."""


class PredictionProviderError(PredictionServiceError):
    """Raised when a prediction provider fails."""


class PredictionTimeoutError(PredictionProviderError):
    """Raised when a prediction provider times out."""


class PredictionModelNotFoundError(PredictionServiceError):
    """Raised when a model/provider cannot be resolved."""


# =============================================================================
# Enums and data models
# =============================================================================


class PredictionTask(str, Enum):
    REGRESSION = "regression"
    CLASSIFICATION = "classification"
    FORECASTING = "forecasting"
    RISK_SCORE = "risk_score"
    ANOMALY_SCORE = "anomaly_score"
    CASHFLOW_FORECAST = "cashflow_forecast"
    DEMAND_FORECAST = "demand_forecast"
    SCENARIO_SIMULATION = "scenario_simulation"
    GENERIC = "generic"


class PredictionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FALLBACK = "fallback"
    FAILED = "failed"


class AggregationStrategy(str, Enum):
    SINGLE = "single"
    MEAN = "mean"
    MEDIAN = "median"
    WEIGHTED_MEAN = "weighted_mean"
    VOTE = "vote"
    MAX_CONFIDENCE = "max_confidence"


class PredictionOutputType(str, Enum):
    VALUE = "value"
    LABEL = "label"
    SCORE = "score"
    TIMESERIES = "timeseries"
    DISTRIBUTION = "distribution"
    STRUCTURED = "structured"


class ForecastFrequency(str, Enum):
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


@dataclass(frozen=True)
class TimeSeriesPoint:
    timestamp: datetime
    value: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastPoint:
    timestamp: datetime
    yhat: float
    yhat_lower: Optional[float] = None
    yhat_upper: Optional[float] = None
    confidence: Optional[float] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionInterval:
    lower: float
    upper: float
    confidence_level: float = 0.95
    method: str = "heuristic"


@dataclass(frozen=True)
class PredictionExplanation:
    method: str
    top_features: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    evidence: Sequence[str] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDescriptor:
    name: str
    version: str = "1.0.0"
    provider: str = "local"
    task: PredictionTask = PredictionTask.GENERIC
    tenant_id: Optional[str] = None
    weight: float = 1.0
    timeout_ms: int = 2_500
    is_default: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        tenant = self.tenant_id or "global"
        return f"{tenant}:{self.name}:{self.version}:{self.provider}:{self.task.value}"


@dataclass(frozen=True)
class PredictionRequest:
    features: FeatureMap
    task: PredictionTask = PredictionTask.GENERIC
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    output_type: PredictionOutputType = PredictionOutputType.STRUCTURED
    aggregation_strategy: AggregationStrategy = AggregationStrategy.SINGLE
    explain: bool = True
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    confidence_level: float = 0.95
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchPredictionRequest:
    rows: Sequence[FeatureMap]
    task: PredictionTask = PredictionTask.GENERIC
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    output_type: PredictionOutputType = PredictionOutputType.STRUCTURED
    aggregation_strategy: AggregationStrategy = AggregationStrategy.SINGLE
    explain: bool = False
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    confidence_level: float = 0.95
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastRequest:
    history: Sequence[TimeSeriesPoint]
    horizon: int
    frequency: ForecastFrequency = ForecastFrequency.DAILY
    task: PredictionTask = PredictionTask.FORECASTING
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    exogenous_features: Mapping[str, Any] = field(default_factory=dict)
    aggregation_strategy: AggregationStrategy = AggregationStrategy.SINGLE
    explain: bool = True
    use_cache: bool = True
    timeout_ms: Optional[int] = None
    confidence_level: float = 0.95
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionResult:
    request_id: str
    status: PredictionStatus
    task: PredictionTask
    output_type: PredictionOutputType
    prediction: Any
    confidence: Optional[float]
    interval: Optional[PredictionInterval]
    model: Optional[ModelDescriptor]
    explanation: Optional[PredictionExplanation]
    cached: bool
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["task"] = self.task.value
        payload["output_type"] = self.output_type.value
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(frozen=True)
class BatchPredictionResult:
    request_id: str
    status: PredictionStatus
    task: PredictionTask
    results: Sequence[PredictionResult]
    total: int
    succeeded: int
    failed: int
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "task": self.task.value,
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ForecastResult:
    request_id: str
    status: PredictionStatus
    task: PredictionTask
    forecast: Sequence[ForecastPoint]
    model: Optional[ModelDescriptor]
    explanation: Optional[PredictionExplanation]
    cached: bool
    latency_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["task"] = self.task.value
        payload["created_at"] = self.created_at.isoformat()
        for point in payload["forecast"]:
            point["timestamp"] = point["timestamp"].isoformat() if isinstance(point["timestamp"], datetime) else point["timestamp"]
        return payload


@dataclass(frozen=True)
class PredictionServiceConfig:
    default_timeout_ms: int = 2_500
    retries: int = 2
    retry_base_delay_ms: int = 80
    retry_jitter_ms: int = 50
    max_batch_size: int = 2_000
    max_forecast_horizon: int = 730
    min_history_points: int = 3
    cache_ttl_seconds: int = 300
    cache_max_size: int = 100_000
    fail_open: bool = True
    audit_enabled: bool = True
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.default_timeout_ms <= 0:
            raise PredictionValidationError("default_timeout_ms must be positive.")
        if self.retries < 0:
            raise PredictionValidationError("retries cannot be negative.")
        if self.max_batch_size <= 0:
            raise PredictionValidationError("max_batch_size must be positive.")
        if self.max_forecast_horizon <= 0:
            raise PredictionValidationError("max_forecast_horizon must be positive.")
        if self.min_history_points <= 0:
            raise PredictionValidationError("min_history_points must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class PredictionProvider(Protocol):
    descriptor: ModelDescriptor

    async def predict(self, features: FeatureMap) -> PredictionResult: ...

    async def predict_batch(self, rows: Sequence[FeatureMap]) -> Sequence[PredictionResult]: ...

    async def forecast(self, request: ForecastRequest) -> ForecastResult: ...


class PredictionRegistry(Protocol):
    async def resolve(
        self,
        task: PredictionTask,
        tenant_id: Optional[str],
        model_name: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> Sequence[PredictionProvider]: ...


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
    def __init__(self, ttl_seconds: int = 300, max_size: int = 100_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        if self.ttl_seconds <= 0:
            return None
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        if self.ttl_seconds <= 0:
            return
        async with self._lock:
            if len(self._items) >= self.max_size:
                self._items.pop(next(iter(self._items)), None)
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _next_timestamp(last: datetime, frequency: ForecastFrequency, step: int) -> datetime:
    if frequency == ForecastFrequency.HOURLY:
        return last + timedelta(hours=step)
    if frequency == ForecastFrequency.DAILY:
        return last + timedelta(days=step)
    if frequency == ForecastFrequency.WEEKLY:
        return last + timedelta(weeks=step)
    if frequency == ForecastFrequency.MONTHLY:
        return last + timedelta(days=30 * step)
    if frequency == ForecastFrequency.QUARTERLY:
        return last + timedelta(days=91 * step)
    if frequency == ForecastFrequency.YEARLY:
        return last + timedelta(days=365 * step)
    return last + timedelta(days=step)


def _numeric_features(features: FeatureMap) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, value in features.items():
        if isinstance(value, bool):
            out[str(key)] = 1.0 if value else 0.0
        elif isinstance(value, (int, float, Decimal)):
            out[str(key)] = _safe_float(value)
        elif isinstance(value, str):
            try:
                out[str(key)] = float(value)
            except ValueError:
                continue
    return out


# =============================================================================
# Local provider and registry
# =============================================================================


class LocalHeuristicPredictionProvider:
    """Deterministic local provider for development, tests and fail-open fallback."""

    def __init__(self, descriptor: Optional[ModelDescriptor] = None) -> None:
        self.descriptor = descriptor or ModelDescriptor(
            name="local-heuristic-predictor",
            version="1.0.0",
            provider="local",
            task=PredictionTask.GENERIC,
            is_default=True,
        )

    async def predict(self, features: FeatureMap) -> PredictionResult:
        started = time.perf_counter()
        numeric = _numeric_features(features)
        values = list(numeric.values())
        if not values:
            prediction: Any = None
            confidence = 0.35
            interval = None
            warnings = ("No numeric features were available for heuristic prediction.",)
        else:
            mean_value = statistics.mean(values)
            std = statistics.pstdev(values) if len(values) > 1 else abs(mean_value) * 0.1
            prediction = mean_value
            confidence = _clamp(0.55 + min(len(values), 20) / 100)
            interval = PredictionInterval(
                lower=mean_value - 1.96 * std,
                upper=mean_value + 1.96 * std,
                confidence_level=0.95,
                method="heuristic_feature_dispersion",
            )
            warnings = tuple()

        top_features = sorted(
            ({"feature": key, "value": value, "importance": abs(value)} for key, value in numeric.items()),
            key=lambda x: x["importance"],
            reverse=True,
        )[:10]

        return PredictionResult(
            request_id=str(uuid.uuid4()),
            status=PredictionStatus.SUCCESS,
            task=self.descriptor.task,
            output_type=PredictionOutputType.STRUCTURED,
            prediction=prediction,
            confidence=confidence,
            interval=interval,
            model=self.descriptor,
            explanation=PredictionExplanation(
                method="local_heuristic_numeric_average",
                top_features=top_features,
                warnings=warnings,
                metadata={"numeric_feature_count": len(numeric)},
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"provider": "local"},
        )

    async def predict_batch(self, rows: Sequence[FeatureMap]) -> Sequence[PredictionResult]:
        return [await self.predict(row) for row in rows]

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        started = time.perf_counter()
        values = [point.value for point in sorted(request.history, key=lambda p: p.timestamp)]
        last_timestamp = max(point.timestamp for point in request.history)
        if len(values) >= 2:
            recent_deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
            trend = statistics.mean(recent_deltas[-min(7, len(recent_deltas)) :])
        else:
            trend = 0.0
        residual_std = statistics.pstdev(values) if len(values) > 1 else abs(values[-1]) * 0.1
        last_value = values[-1]
        z = 1.96 if request.confidence_level >= 0.95 else 1.64

        points: List[ForecastPoint] = []
        for step in range(1, request.horizon + 1):
            yhat = last_value + trend * step
            uncertainty = z * residual_std * math.sqrt(step)
            points.append(
                ForecastPoint(
                    timestamp=_next_timestamp(last_timestamp, request.frequency, step),
                    yhat=round(yhat, 6),
                    yhat_lower=round(yhat - uncertainty, 6),
                    yhat_upper=round(yhat + uncertainty, 6),
                    confidence=_clamp(request.confidence_level),
                    metadata={"step": step, "trend": trend},
                )
            )

        return ForecastResult(
            request_id=request.request_id,
            status=PredictionStatus.SUCCESS,
            task=request.task,
            forecast=tuple(points),
            model=self.descriptor,
            explanation=PredictionExplanation(
                method="local_trend_extrapolation",
                evidence=[f"history_points={len(values)}", f"trend={round(trend, 6)}"],
                metadata={"residual_std": residual_std, "frequency": request.frequency.value},
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"provider": "local"},
        )


class InMemoryPredictionRegistry:
    def __init__(self) -> None:
        self._providers: Dict[str, PredictionProvider] = {}

    def register(self, provider: PredictionProvider) -> None:
        self._providers[provider.descriptor.key] = provider

    async def resolve(
        self,
        task: PredictionTask,
        tenant_id: Optional[str],
        model_name: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> Sequence[PredictionProvider]:
        candidates = []
        for provider in self._providers.values():
            descriptor = provider.descriptor
            if descriptor.task not in {task, PredictionTask.GENERIC}:
                continue
            if descriptor.tenant_id not in {tenant_id, None}:
                continue
            if model_name and descriptor.name != model_name:
                continue
            if model_version and descriptor.version != model_version:
                continue
            candidates.append(provider)

        if not candidates:
            raise PredictionModelNotFoundError(f"No prediction provider found for task={task.value}, model={model_name!r}.")

        tenant_specific = [p for p in candidates if p.descriptor.tenant_id == tenant_id]
        pool = tenant_specific or candidates
        defaults = [p for p in pool if p.descriptor.is_default]
        return tuple(defaults or sorted(pool, key=lambda p: p.descriptor.weight, reverse=True))


# =============================================================================
# Main service
# =============================================================================


class PredictionService:
    def __init__(
        self,
        registry: PredictionRegistry,
        config: Optional[PredictionServiceConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[AsyncTTLCache] = None,
    ) -> None:
        self.config = config or PredictionServiceConfig()
        self.config.validate()
        self.registry = registry
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.cache = cache or AsyncTTLCache(self.config.cache_ttl_seconds, self.config.cache_max_size)

    async def predict(self, request: PredictionRequest) -> PredictionResult:
        started = time.perf_counter()
        self._validate_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value}
        self.metrics.increment("prediction.request.started", tags=tags)

        cache_key = self._cache_key("predict", request)
        if request.use_cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                self.metrics.increment("prediction.cache.hit", tags=tags)
                return self._mark_cached(cached, started)
        self.metrics.increment("prediction.cache.miss", tags=tags)

        try:
            providers = await self.registry.resolve(request.task, request.tenant_id, request.model_name, request.model_version)
            result = await self._predict_with_providers(request, providers, started)
            if request.use_cache:
                await self.cache.set(cache_key, result)
            self.metrics.increment("prediction.request.completed", tags={**tags, "status": result.status.value})
            self.metrics.timing("prediction.latency_ms", result.latency_ms, tags=tags)
            await self._audit_prediction("prediction.request.completed", request, result)
            return result
        except Exception as exc:
            self.metrics.increment("prediction.request.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Prediction request failed", extra={"request_id": request.request_id})
            if not self.config.fail_open:
                raise
            result = self._fallback_prediction_result(request, started, exc)
            await self._audit_prediction("prediction.request.failed", request, result)
            return result

    async def predict_batch(self, request: BatchPredictionRequest) -> BatchPredictionResult:
        started = time.perf_counter()
        self._validate_batch_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value}
        self.metrics.increment("prediction.batch.started", tags=tags)

        results: List[PredictionResult] = []
        failed = 0
        try:
            providers = await self.registry.resolve(request.task, request.tenant_id, request.model_name, request.model_version)
            primary = providers[0]
            timeout_ms = request.timeout_ms or primary.descriptor.timeout_ms or self.config.default_timeout_ms
            raw_results = await self._call_with_retry(lambda: primary.predict_batch(request.rows), timeout_ms)
            for idx, item in enumerate(raw_results):
                results.append(
                    PredictionResult(
                        request_id=f"{request.request_id}:{idx}",
                        status=item.status,
                        task=request.task,
                        output_type=request.output_type,
                        prediction=item.prediction,
                        confidence=item.confidence,
                        interval=item.interval,
                        model=item.model,
                        explanation=item.explanation if request.explain else None,
                        cached=False,
                        latency_ms=item.latency_ms,
                        created_at=item.created_at,
                        metadata={**dict(item.metadata), "batch_request_id": request.request_id, "row_index": idx},
                    )
                )
            failed = max(0, len(request.rows) - len(results))
            status = PredictionStatus.SUCCESS if failed == 0 else PredictionStatus.PARTIAL
        except Exception as exc:
            logger.exception("Batch prediction failed", extra={"request_id": request.request_id})
            self.metrics.increment("prediction.batch.failed", tags={**tags, "error": exc.__class__.__name__})
            if not self.config.fail_open:
                raise
            results = [
                self._fallback_prediction_result(
                    PredictionRequest(
                        features=row,
                        task=request.task,
                        model_name=request.model_name,
                        model_version=request.model_version,
                        tenant_id=request.tenant_id,
                        request_id=f"{request.request_id}:{idx}",
                        output_type=request.output_type,
                        explain=request.explain,
                        use_cache=False,
                        timeout_ms=request.timeout_ms,
                        confidence_level=request.confidence_level,
                        metadata={**dict(request.metadata), "row_index": idx},
                    ),
                    started,
                    exc,
                )
                for idx, row in enumerate(request.rows)
            ]
            failed = len(results)
            status = PredictionStatus.FALLBACK

        batch = BatchPredictionResult(
            request_id=request.request_id,
            status=status,
            task=request.task,
            results=tuple(results),
            total=len(request.rows),
            succeeded=len(results) - failed,
            failed=failed,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"mean_item_latency_ms": self._mean_latency(results)},
        )
        self.metrics.increment("prediction.batch.completed", tags={**tags, "status": batch.status.value})
        self.metrics.timing("prediction.batch.latency_ms", batch.latency_ms, tags=tags)
        await self._audit_batch("prediction.batch.completed", request, batch)
        return batch

    async def forecast(self, request: ForecastRequest) -> ForecastResult:
        started = time.perf_counter()
        self._validate_forecast_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "task": request.task.value, "frequency": request.frequency.value}
        self.metrics.increment("prediction.forecast.started", tags=tags)

        cache_key = self._cache_key("forecast", request)
        if request.use_cache:
            cached = await self.cache.get(cache_key)
            if cached is not None:
                self.metrics.increment("prediction.cache.hit", tags=tags)
                return self._mark_forecast_cached(cached, started)
        self.metrics.increment("prediction.cache.miss", tags=tags)

        try:
            providers = await self.registry.resolve(request.task, request.tenant_id, request.model_name, request.model_version)
            result = await self._forecast_with_providers(request, providers, started)
            if request.use_cache:
                await self.cache.set(cache_key, result)
            self.metrics.increment("prediction.forecast.completed", tags={**tags, "status": result.status.value})
            self.metrics.timing("prediction.forecast.latency_ms", result.latency_ms, tags=tags)
            await self._audit_forecast("prediction.forecast.completed", request, result)
            return result
        except Exception as exc:
            self.metrics.increment("prediction.forecast.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Forecast request failed", extra={"request_id": request.request_id})
            if not self.config.fail_open:
                raise
            result = self._fallback_forecast_result(request, started, exc)
            await self._audit_forecast("prediction.forecast.failed", request, result)
            return result

    async def _predict_with_providers(
        self,
        request: PredictionRequest,
        providers: Sequence[PredictionProvider],
        started: float,
    ) -> PredictionResult:
        selected = providers if request.aggregation_strategy != AggregationStrategy.SINGLE else providers[:1]
        provider_results: List[PredictionResult] = []
        for provider in selected:
            timeout_ms = request.timeout_ms or provider.descriptor.timeout_ms or self.config.default_timeout_ms
            result = await self._call_with_retry(lambda p=provider: p.predict(request.features), timeout_ms)
            provider_results.append(result)

        if len(provider_results) == 1:
            item = provider_results[0]
            return PredictionResult(
                request_id=request.request_id,
                status=item.status,
                task=request.task,
                output_type=request.output_type,
                prediction=item.prediction,
                confidence=item.confidence,
                interval=item.interval,
                model=item.model,
                explanation=item.explanation if request.explain else None,
                cached=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={**dict(item.metadata), "feature_hash": _hash_payload(request.features)},
            )

        return self._aggregate_predictions(request, provider_results, started)

    async def _forecast_with_providers(
        self,
        request: ForecastRequest,
        providers: Sequence[PredictionProvider],
        started: float,
    ) -> ForecastResult:
        selected = providers if request.aggregation_strategy != AggregationStrategy.SINGLE else providers[:1]
        results: List[ForecastResult] = []
        for provider in selected:
            timeout_ms = request.timeout_ms or provider.descriptor.timeout_ms or self.config.default_timeout_ms
            results.append(await self._call_with_retry(lambda p=provider: p.forecast(request), timeout_ms))

        if len(results) == 1:
            item = results[0]
            return ForecastResult(
                request_id=request.request_id,
                status=item.status,
                task=request.task,
                forecast=item.forecast,
                model=item.model,
                explanation=item.explanation if request.explain else None,
                cached=False,
                latency_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={**dict(item.metadata), "history_hash": self._history_hash(request.history)},
            )

        return self._aggregate_forecasts(request, results, started)

    def _aggregate_predictions(
        self,
        request: PredictionRequest,
        results: Sequence[PredictionResult],
        started: float,
    ) -> PredictionResult:
        numeric = [_safe_float(r.prediction, default=math.nan) for r in results]
        numeric = [v for v in numeric if not math.isnan(v)]
        labels = [str(r.prediction) for r in results if r.prediction is not None]
        weights = [max((r.model.weight if r.model else 1.0), 0.0) for r in results]

        prediction: Any
        if request.aggregation_strategy == AggregationStrategy.MEDIAN and numeric:
            prediction = statistics.median(numeric)
        elif request.aggregation_strategy == AggregationStrategy.WEIGHTED_MEAN and numeric:
            total_weight = sum(weights[: len(numeric)]) or 1.0
            prediction = sum(v * w for v, w in zip(numeric, weights)) / total_weight
        elif request.aggregation_strategy == AggregationStrategy.VOTE and labels:
            prediction = max(set(labels), key=labels.count)
        elif request.aggregation_strategy == AggregationStrategy.MAX_CONFIDENCE:
            best = max(results, key=lambda r: r.confidence or 0.0)
            prediction = best.prediction
        elif numeric:
            prediction = statistics.mean(numeric)
        else:
            prediction = labels[0] if labels else None

        confidence_values = [r.confidence for r in results if r.confidence is not None]
        confidence = statistics.mean(confidence_values) if confidence_values else None
        interval = None
        if numeric:
            std = statistics.pstdev(numeric) if len(numeric) > 1 else abs(numeric[0]) * 0.05
            center = _safe_float(prediction)
            interval = PredictionInterval(center - 1.96 * std, center + 1.96 * std, request.confidence_level, "ensemble_dispersion")

        return PredictionResult(
            request_id=request.request_id,
            status=PredictionStatus.SUCCESS,
            task=request.task,
            output_type=request.output_type,
            prediction=prediction,
            confidence=None if confidence is None else round(_clamp(confidence), 4),
            interval=interval,
            model=None,
            explanation=PredictionExplanation(
                method=f"ensemble_{request.aggregation_strategy.value}",
                evidence=[f"models={len(results)}"],
                metadata={"model_keys": [r.model.key for r in results if r.model]},
            ) if request.explain else None,
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"ensemble": True, "feature_hash": _hash_payload(request.features)},
        )

    def _aggregate_forecasts(self, request: ForecastRequest, results: Sequence[ForecastResult], started: float) -> ForecastResult:
        by_step: Dict[int, List[ForecastPoint]] = defaultdict(list)
        for result in results:
            for idx, point in enumerate(result.forecast):
                by_step[idx].append(point)

        forecast: List[ForecastPoint] = []
        for idx in sorted(by_step):
            points = by_step[idx]
            yhats = [p.yhat for p in points]
            yhat = statistics.median(yhats) if request.aggregation_strategy == AggregationStrategy.MEDIAN else statistics.mean(yhats)
            lowers = [p.yhat_lower for p in points if p.yhat_lower is not None]
            uppers = [p.yhat_upper for p in points if p.yhat_upper is not None]
            confidences = [p.confidence for p in points if p.confidence is not None]
            forecast.append(
                ForecastPoint(
                    timestamp=points[0].timestamp,
                    yhat=round(yhat, 6),
                    yhat_lower=round(min(lowers), 6) if lowers else None,
                    yhat_upper=round(max(uppers), 6) if uppers else None,
                    confidence=round(statistics.mean(confidences), 4) if confidences else None,
                    metadata={"ensemble_models": len(points)},
                )
            )

        return ForecastResult(
            request_id=request.request_id,
            status=PredictionStatus.SUCCESS,
            task=request.task,
            forecast=tuple(forecast),
            model=None,
            explanation=PredictionExplanation(
                method=f"forecast_ensemble_{request.aggregation_strategy.value}",
                evidence=[f"models={len(results)}", f"horizon={request.horizon}"],
                metadata={"model_keys": [r.model.key for r in results if r.model]},
            ) if request.explain else None,
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"ensemble": True, "history_hash": self._history_hash(request.history)},
        )

    async def _call_with_retry(self, call: Callable[[], Any], timeout_ms: int) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(self.config.retries + 1):
            try:
                result = call()
                if asyncio.iscoroutine(result):
                    return await asyncio.wait_for(result, timeout=timeout_ms / 1000)
                return result
            except asyncio.TimeoutError as exc:
                last_exc = PredictionTimeoutError(f"Prediction provider timed out after {timeout_ms}ms")
            except Exception as exc:
                last_exc = exc
            if attempt < self.config.retries:
                await asyncio.sleep((self.config.retry_base_delay_ms * (2**attempt) + self.config.retry_jitter_ms) / 1000)
        assert last_exc is not None
        raise last_exc

    def _validate_request(self, request: PredictionRequest) -> None:
        if not isinstance(request.features, Mapping):
            raise PredictionValidationError("features must be a mapping/object.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise PredictionValidationError("timeout_ms must be positive.")
        if not 0 < request.confidence_level < 1:
            raise PredictionValidationError("confidence_level must be between 0 and 1.")

    def _validate_batch_request(self, request: BatchPredictionRequest) -> None:
        if not request.rows:
            raise PredictionValidationError("rows cannot be empty.")
        if len(request.rows) > self.config.max_batch_size:
            raise PredictionValidationError(f"batch size exceeds max_batch_size={self.config.max_batch_size}.")
        for idx, row in enumerate(request.rows):
            if not isinstance(row, Mapping):
                raise PredictionValidationError(f"rows[{idx}] must be a mapping/object.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise PredictionValidationError("timeout_ms must be positive.")

    def _validate_forecast_request(self, request: ForecastRequest) -> None:
        if len(request.history) < self.config.min_history_points:
            raise PredictionValidationError(f"history must contain at least {self.config.min_history_points} points.")
        if request.horizon <= 0:
            raise PredictionValidationError("horizon must be positive.")
        if request.horizon > self.config.max_forecast_horizon:
            raise PredictionValidationError(f"horizon exceeds max_forecast_horizon={self.config.max_forecast_horizon}.")
        if request.timeout_ms is not None and request.timeout_ms <= 0:
            raise PredictionValidationError("timeout_ms must be positive.")
        if not 0 < request.confidence_level < 1:
            raise PredictionValidationError("confidence_level must be between 0 and 1.")

    def _cache_key(self, operation: str, request: Any) -> str:
        return f"prediction:{operation}:{_hash_payload(asdict(request))}"

    def _history_hash(self, history: Sequence[TimeSeriesPoint]) -> str:
        return _hash_payload([{"timestamp": p.timestamp.isoformat(), "value": p.value} for p in history])

    def _mark_cached(self, result: PredictionResult, started: float) -> PredictionResult:
        return PredictionResult(
            request_id=result.request_id,
            status=result.status,
            task=result.task,
            output_type=result.output_type,
            prediction=result.prediction,
            confidence=result.confidence,
            interval=result.interval,
            model=result.model,
            explanation=result.explanation,
            cached=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={**dict(result.metadata), "cache_returned_at": _utc_now().isoformat()},
        )

    def _mark_forecast_cached(self, result: ForecastResult, started: float) -> ForecastResult:
        return ForecastResult(
            request_id=result.request_id,
            status=result.status,
            task=result.task,
            forecast=result.forecast,
            model=result.model,
            explanation=result.explanation,
            cached=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={**dict(result.metadata), "cache_returned_at": _utc_now().isoformat()},
        )

    def _fallback_prediction_result(self, request: PredictionRequest, started: float, exc: Exception) -> PredictionResult:
        numeric = _numeric_features(request.features)
        prediction = statistics.mean(numeric.values()) if numeric else None
        return PredictionResult(
            request_id=request.request_id,
            status=PredictionStatus.FALLBACK,
            task=request.task,
            output_type=request.output_type,
            prediction=prediction,
            confidence=0.35 if prediction is not None else 0.0,
            interval=None,
            model=None,
            explanation=PredictionExplanation(
                method="service_fallback",
                warnings=[f"Fallback activated due to {exc.__class__.__name__}"],
                metadata={"numeric_feature_count": len(numeric)},
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"fallback": True, "error": exc.__class__.__name__, "message": str(exc)},
        )

    def _fallback_forecast_result(self, request: ForecastRequest, started: float, exc: Exception) -> ForecastResult:
        history = sorted(request.history, key=lambda p: p.timestamp)
        last = history[-1]
        points = [
            ForecastPoint(
                timestamp=_next_timestamp(last.timestamp, request.frequency, step),
                yhat=last.value,
                yhat_lower=None,
                yhat_upper=None,
                confidence=0.25,
                metadata={"fallback": "last_observation_carried_forward", "step": step},
            )
            for step in range(1, request.horizon + 1)
        ]
        return ForecastResult(
            request_id=request.request_id,
            status=PredictionStatus.FALLBACK,
            task=request.task,
            forecast=tuple(points),
            model=None,
            explanation=PredictionExplanation(
                method="last_observation_carried_forward",
                warnings=[f"Fallback activated due to {exc.__class__.__name__}"],
            ),
            cached=False,
            latency_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"fallback": True, "error": exc.__class__.__name__, "message": str(exc)},
        )

    def _mean_latency(self, results: Sequence[PredictionResult]) -> float:
        if not results:
            return 0.0
        return round(statistics.mean(r.latency_ms for r in results), 4)

    async def _audit_prediction(self, event_name: str, request: PredictionRequest, result: PredictionResult) -> None:
        if not self.config.audit_enabled:
            return
        await self._audit_generic(
            event_name,
            {
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "task": request.task.value,
                "status": result.status.value,
                "model": result.model.key if result.model else None,
                "confidence": result.confidence,
                "cached": result.cached,
                "latency_ms": result.latency_ms,
                "feature_hash": _hash_payload(request.features),
                "created_at": result.created_at.isoformat(),
            },
        )

    async def _audit_batch(self, event_name: str, request: BatchPredictionRequest, result: BatchPredictionResult) -> None:
        if not self.config.audit_enabled:
            return
        await self._audit_generic(
            event_name,
            {
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "task": request.task.value,
                "status": result.status.value,
                "total": result.total,
                "succeeded": result.succeeded,
                "failed": result.failed,
                "latency_ms": result.latency_ms,
                "created_at": result.created_at.isoformat(),
            },
        )

    async def _audit_forecast(self, event_name: str, request: ForecastRequest, result: ForecastResult) -> None:
        if not self.config.audit_enabled:
            return
        await self._audit_generic(
            event_name,
            {
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "task": request.task.value,
                "status": result.status.value,
                "horizon": request.horizon,
                "frequency": request.frequency.value,
                "model": result.model.key if result.model else None,
                "cached": result.cached,
                "latency_ms": result.latency_ms,
                "history_hash": self._history_hash(request.history),
                "created_at": result.created_at.isoformat(),
            },
        )

    async def _audit_generic(self, event_name: str, payload: Mapping[str, Any]) -> None:
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write prediction audit event", extra={"event_name": event_name})

    @classmethod
    def request_from_payload(cls, payload: Mapping[str, Any]) -> PredictionRequest:
        return PredictionRequest(
            features=payload.get("features") or {},
            task=PredictionTask(payload.get("task", PredictionTask.GENERIC.value)),
            model_name=payload.get("model_name"),
            model_version=payload.get("model_version"),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            output_type=PredictionOutputType(payload.get("output_type", PredictionOutputType.STRUCTURED.value)),
            aggregation_strategy=AggregationStrategy(payload.get("aggregation_strategy", AggregationStrategy.SINGLE.value)),
            explain=bool(payload.get("explain", True)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            confidence_level=float(payload.get("confidence_level", 0.95)),
            metadata=payload.get("metadata") or {},
        )

    @classmethod
    def forecast_request_from_payload(cls, payload: Mapping[str, Any]) -> ForecastRequest:
        history = [
            TimeSeriesPoint(
                timestamp=_parse_datetime(item.get("timestamp")),
                value=_safe_float(item.get("value")),
                metadata=item.get("metadata") or {},
            )
            for item in payload.get("history", [])
        ]
        return ForecastRequest(
            history=tuple(history),
            horizon=int(payload["horizon"]),
            frequency=ForecastFrequency(payload.get("frequency", ForecastFrequency.DAILY.value)),
            task=PredictionTask(payload.get("task", PredictionTask.FORECASTING.value)),
            model_name=payload.get("model_name"),
            model_version=payload.get("model_version"),
            tenant_id=payload.get("tenant_id"),
            request_id=str(payload.get("request_id") or uuid.uuid4()),
            exogenous_features=payload.get("exogenous_features") or {},
            aggregation_strategy=AggregationStrategy(payload.get("aggregation_strategy", AggregationStrategy.SINGLE.value)),
            explain=bool(payload.get("explain", True)),
            use_cache=bool(payload.get("use_cache", True)),
            timeout_ms=payload.get("timeout_ms"),
            confidence_level=float(payload.get("confidence_level", 0.95)),
            metadata=payload.get("metadata") or {},
        )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise PredictionValidationError(f"Invalid timestamp: {value!r}")


# =============================================================================
# Factory
# =============================================================================


def build_prediction_service(
    registry: Optional[InMemoryPredictionRegistry] = None,
    config: Optional[PredictionServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> PredictionService:
    reg = registry or InMemoryPredictionRegistry()
    if registry is None:
        reg.register(
            LocalHeuristicPredictionProvider(
                ModelDescriptor(
                    name="local-heuristic-predictor",
                    version="1.0.0",
                    provider="local",
                    task=PredictionTask.GENERIC,
                    is_default=True,
                )
            )
        )
        reg.register(
            LocalHeuristicPredictionProvider(
                ModelDescriptor(
                    name="local-forecast-predictor",
                    version="1.0.0",
                    provider="local",
                    task=PredictionTask.FORECASTING,
                    is_default=True,
                )
            )
        )
        reg.register(
            LocalHeuristicPredictionProvider(
                ModelDescriptor(
                    name="local-cashflow-predictor",
                    version="1.0.0",
                    provider="local",
                    task=PredictionTask.CASHFLOW_FORECAST,
                    is_default=True,
                )
            )
        )
    return PredictionService(registry=reg, config=config, metrics=metrics, audit_sink=audit_sink)


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    service = build_prediction_service(config=PredictionServiceConfig(privacy_hash_salt="local-dev-salt"))

    result = await service.predict(
        PredictionRequest(
            tenant_id="tenant-ao",
            task=PredictionTask.GENERIC,
            features={"revenue": 1_250_000, "expenses": 820_000, "customers": 320, "risk_flag": False},
            explain=True,
        )
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

    now = datetime(2026, 5, 1, tzinfo=UTC)
    history = tuple(
        TimeSeriesPoint(timestamp=now + timedelta(days=i), value=1000 + i * 35 + (i % 3) * 20)
        for i in range(14)
    )
    forecast = await service.forecast(
        ForecastRequest(
            tenant_id="tenant-ao",
            task=PredictionTask.FORECASTING,
            history=history,
            horizon=7,
            frequency=ForecastFrequency.DAILY,
        )
    )
    print(json.dumps(forecast.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
