"""
ml/utils/realtime_serving.py

Enterprise-grade realtime serving utilities for ML inference.

Features:
- Low-latency single/bulk inference
- Model adapter abstraction
- Request/response schemas
- Input validation
- Timeout-aware execution
- Retry support
- Circuit breaker
- In-memory prediction cache
- Structured observability
- Confidence/probability normalization
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

logger = logging.getLogger(__name__)


class RealtimeServingError(Exception):
    """Base realtime serving error."""


class PredictionTimeoutError(RealtimeServingError):
    """Raised when prediction exceeds timeout."""


class CircuitBreakerOpenError(RealtimeServingError):
    """Raised when circuit breaker is open."""


class PredictionValidationError(RealtimeServingError):
    """Raised when model output is invalid."""


class RiskDecision(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    CHALLENGE = "challenge"
    BLOCK = "block"


class ModelAdapter(Protocol):
    def predict(self, data: Any) -> Any:
        ...


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 2
    initial_delay_seconds: float = 0.05
    backoff_factor: float = 2.0
    max_delay_seconds: float = 0.5


@dataclass(frozen=True)
class CircuitBreakerConfig:
    enabled: bool = True
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class RealtimeServingConfig:
    timeout_seconds: float = 2.0
    max_batch_size: int = 128
    enable_cache: bool = True
    cache_ttl_seconds: float = 60.0
    prediction_field: str = "prediction"
    probability_field: str = "probability"
    model_name: str = "model"
    model_version: str = "unknown"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


@dataclass(frozen=True)
class PredictionRequest:
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]]
    request_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionResponse:
    request_id: str
    model_name: str
    model_version: str
    prediction: Any
    probability: float | None
    decision: str | None
    duration_ms: int
    cached: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prediction": self.prediction,
            "probability": self.probability,
            "decision": self.decision,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
            "metadata": dict(self.metadata),
        }


@dataclass
class CacheEntry:
    value: PredictionResponse
    expires_at: float


@dataclass
class CircuitBreakerState:
    failures: int = 0
    opened_until: float = 0.0


class PredictionCache:
    def __init__(self) -> None:
        self._items: dict[str, CacheEntry] = {}

    def get(self, key: str) -> PredictionResponse | None:
        item = self._items.get(key)

        if item is None:
            return None

        if item.expires_at <= time.time():
            self._items.pop(key, None)
            return None

        return item.value

    def set(self, key: str, value: PredictionResponse, ttl_seconds: float) -> None:
        self._items[key] = CacheEntry(
            value=value,
            expires_at=time.time() + ttl_seconds,
        )

    def clear(self) -> None:
        self._items.clear()

    def size(self) -> int:
        return len(self._items)


def make_request_id() -> str:
    return str(uuid.uuid4())


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_features(
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    max_batch_size: int,
) -> tuple[list[Mapping[str, Any]], bool]:
    if isinstance(features, Mapping):
        return [features], False

    batch = list(features)

    if len(batch) > max_batch_size:
        raise PredictionValidationError(
            f"Batch size {len(batch)} exceeds max_batch_size={max_batch_size}."
        )

    if not all(isinstance(item, Mapping) for item in batch):
        raise PredictionValidationError("All batch items must be mappings.")

    return batch, True


def normalize_model_output(
    output: Any,
    *,
    prediction_field: str,
    probability_field: str,
) -> tuple[Any, float | None, str | None, Mapping[str, Any]]:
    if isinstance(output, Mapping):
        prediction = output.get(prediction_field, output.get("label", output.get("class")))
        probability = output.get(probability_field, output.get("confidence"))
        decision = output.get("decision")

        metadata = {
            key: value
            for key, value in output.items()
            if key not in {prediction_field, probability_field, "confidence", "decision"}
        }

        if probability is not None:
            probability = float(probability)

        return prediction, probability, str(decision) if decision is not None else None, metadata

    if isinstance(output, tuple) and len(output) >= 2:
        prediction = output[0]
        probability = float(output[1]) if output[1] is not None else None
        return prediction, probability, None, {}

    return output, None, None, {}


def derive_decision(probability: float | None) -> str | None:
    if probability is None:
        return None

    if probability >= 0.90:
        return RiskDecision.BLOCK.value
    if probability >= 0.75:
        return RiskDecision.CHALLENGE.value
    if probability >= 0.50:
        return RiskDecision.REVIEW.value
    return RiskDecision.APPROVE.value


def call_with_timeout(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)

        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as exc:
            future.cancel()
            raise PredictionTimeoutError(
                f"Prediction exceeded timeout_seconds={timeout_seconds}."
            ) from exc


def call_with_retry(
    fn: Callable[[], Any],
    retry_policy: RetryPolicy,
) -> Any:
    delay = retry_policy.initial_delay_seconds
    last_error: Exception | None = None

    for attempt in range(1, retry_policy.attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc

            if attempt >= retry_policy.attempts:
                break

            logger.warning(
                "realtime_serving.retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": retry_policy.attempts,
                    "error": str(exc),
                    "delay_seconds": delay,
                },
            )

            time.sleep(delay)
            delay = min(delay * retry_policy.backoff_factor, retry_policy.max_delay_seconds)

    raise RealtimeServingError(f"Prediction failed after retries: {last_error}") from last_error


class RealtimeServingEngine:
    def __init__(
        self,
        model: ModelAdapter | Callable[[Any], Any],
        *,
        config: RealtimeServingConfig | None = None,
        preprocessor: Callable[[Any], Any] | None = None,
        postprocessor: Callable[[PredictionResponse], PredictionResponse] | None = None,
    ) -> None:
        self.model = model
        self.config = config or RealtimeServingConfig()
        self.preprocessor = preprocessor
        self.postprocessor = postprocessor
        self.cache = PredictionCache()
        self.circuit_state = CircuitBreakerState()

    def _predict_raw(self, payload: Any) -> Any:
        if callable(self.model) and not hasattr(self.model, "predict"):
            return self.model(payload)

        return self.model.predict(payload)  # type: ignore[union-attr]

    def _check_circuit(self) -> None:
        cfg = self.config.circuit_breaker

        if not cfg.enabled:
            return

        if self.circuit_state.opened_until > time.time():
            raise CircuitBreakerOpenError(
                "Circuit breaker is open. Model serving temporarily unavailable."
            )

    def _record_success(self) -> None:
        self.circuit_state.failures = 0
        self.circuit_state.opened_until = 0.0

    def _record_failure(self) -> None:
        cfg = self.config.circuit_breaker

        if not cfg.enabled:
            return

        self.circuit_state.failures += 1

        if self.circuit_state.failures >= cfg.failure_threshold:
            self.circuit_state.opened_until = time.time() + cfg.recovery_timeout_seconds

    def predict(self, request: PredictionRequest) -> PredictionResponse:
        started_at = time.time()
        request_id = request.request_id or make_request_id()

        features, is_batch = normalize_features(
            request.features,
            max_batch_size=self.config.max_batch_size,
        )

        cache_key = stable_hash(
            {
                "model": self.config.model_name,
                "version": self.config.model_version,
                "features": features,
            }
        )

        if self.config.enable_cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return PredictionResponse(
                    request_id=request_id,
                    model_name=cached.model_name,
                    model_version=cached.model_version,
                    prediction=cached.prediction,
                    probability=cached.probability,
                    decision=cached.decision,
                    duration_ms=int((time.time() - started_at) * 1000),
                    cached=True,
                    metadata=cached.metadata,
                )

        self._check_circuit()

        try:
            model_input = features if is_batch else features[0]

            if self.preprocessor:
                model_input = self.preprocessor(model_input)

            raw_output = call_with_retry(
                lambda: call_with_timeout(
                    lambda: self._predict_raw(model_input),
                    timeout_seconds=self.config.timeout_seconds,
                ),
                self.config.retry_policy,
            )

            prediction, probability, decision, metadata = normalize_model_output(
                raw_output,
                prediction_field=self.config.prediction_field,
                probability_field=self.config.probability_field,
            )

            if decision is None:
                decision = derive_decision(probability)

            response = PredictionResponse(
                request_id=request_id,
                model_name=self.config.model_name,
                model_version=self.config.model_version,
                prediction=prediction,
                probability=probability,
                decision=decision,
                duration_ms=int((time.time() - started_at) * 1000),
                cached=False,
                metadata={
                    **metadata,
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "input_type": "batch" if is_batch else "single",
                    "items": len(features),
                    **dict(request.metadata),
                },
            )

            if self.postprocessor:
                response = self.postprocessor(response)

            if self.config.enable_cache:
                self.cache.set(cache_key, response, self.config.cache_ttl_seconds)

            self._record_success()

            logger.info(
                "realtime_serving.success",
                extra={
                    "request_id": request_id,
                    "model_name": self.config.model_name,
                    "model_version": self.config.model_version,
                    "duration_ms": response.duration_ms,
                    "cached": response.cached,
                    "decision": response.decision,
                },
            )

            return response

        except Exception:
            self._record_failure()

            logger.exception(
                "realtime_serving.failed",
                extra={
                    "request_id": request_id,
                    "model_name": self.config.model_name,
                    "model_version": self.config.model_version,
                },
            )

            raise

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok"
            if self.circuit_state.opened_until <= time.time()
            else "degraded",
            "model_name": self.config.model_name,
            "model_version": self.config.model_version,
            "cache_size": self.cache.size(),
            "circuit_breaker": {
                "failures": self.circuit_state.failures,
                "opened_until": self.circuit_state.opened_until,
            },
        }


def realtime_predict(
    model: ModelAdapter | Callable[[Any], Any],
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    config: RealtimeServingConfig | None = None,
    preprocessor: Callable[[Any], Any] | None = None,
    postprocessor: Callable[[PredictionResponse], PredictionResponse] | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> PredictionResponse:
    engine = RealtimeServingEngine(
        model,
        config=config,
        preprocessor=preprocessor,
        postprocessor=postprocessor,
    )

    return engine.predict(
        PredictionRequest(
            features=features,
            tenant_id=tenant_id,
            user_id=user_id,
            metadata=metadata or {},
        )
    )


__all__ = [
    "CircuitBreakerConfig",
    "CircuitBreakerOpenError",
    "CircuitBreakerState",
    "ModelAdapter",
    "PredictionCache",
    "PredictionRequest",
    "PredictionResponse",
    "PredictionTimeoutError",
    "PredictionValidationError",
    "RealtimeServingConfig",
    "RealtimeServingEngine",
    "RealtimeServingError",
    "RetryPolicy",
    "RiskDecision",
    "call_with_retry",
    "call_with_timeout",
    "derive_decision",
    "make_request_id",
    "normalize_features",
    "normalize_model_output",
    "realtime_predict",
    "stable_hash",
]