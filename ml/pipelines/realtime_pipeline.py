"""
ml/pipelines/realtime_pipeline.py

Enterprise-grade realtime ML pipeline.

Responsabilidades:
- Receber requests de inferência online
- Validar payload, schema e limites
- Aplicar pré-processamento de baixa latência
- Executar inferência realtime com timeout/cache/circuit breaker
- Normalizar resposta de predição
- Aplicar política de decisão
- Emitir auditoria, métricas e health check
- Padronizar erros e rastreabilidade
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence

try:
    from ml.utils.realtime_serving import (
        PredictionRequest,
        PredictionResponse,
        RealtimeServingConfig,
        RealtimeServingEngine,
    )
    from ml.utils.preprocessing import SchemaRule, validate_schema
except ImportError:  # pragma: no cover
    from ..utils.realtime_serving import (
        PredictionRequest,
        PredictionResponse,
        RealtimeServingConfig,
        RealtimeServingEngine,
    )
    from ..utils.preprocessing import SchemaRule, validate_schema

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("pandas is required for realtime_pipeline.py") from exc


logger = logging.getLogger(__name__)


class RealtimePipelineError(Exception):
    """Erro base do pipeline realtime."""


class RealtimePipelineValidationError(RealtimePipelineError):
    """Erro de validação do request realtime."""


class RealtimePipelineStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"


class ModelProtocol(Protocol):
    def predict(self, data: Any) -> Any:
        ...


@dataclass(frozen=True)
class RealtimeRequest:
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]]
    request_id: str | None = None
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionPolicy:
    approve_below: float = 0.50
    review_from: float = 0.50
    challenge_from: float = 0.75
    block_from: float = 0.90

    def decide(self, probability: float | None, fallback: str | None = None) -> str | None:
        if probability is None:
            return fallback

        if probability >= self.block_from:
            return "block"

        if probability >= self.challenge_from:
            return "challenge"

        if probability >= self.review_from:
            return "review"

        return "approve"


@dataclass(frozen=True)
class RealtimePipelineConfig:
    pipeline_name: str = "realtime_pipeline"
    environment: str = "dev"
    model_name: str = "model"
    model_version: str = "unknown"
    max_features: int = 500
    max_batch_size: int = 128
    require_tenant_id: bool = False
    require_user_id: bool = False
    enable_audit: bool = True
    enable_metrics: bool = True
    serving: RealtimeServingConfig = field(default_factory=RealtimeServingConfig)
    decision_policy: DecisionPolicy = field(default_factory=DecisionPolicy)
    schema_rules: tuple[SchemaRule, ...] = ()


@dataclass
class RealtimePipelineMetrics:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0
    total_duration_ms: int = 0
    last_duration_ms: int = 0

    def record_success(self, duration_ms: int) -> None:
        self.total_requests += 1
        self.successful_requests += 1
        self.total_duration_ms += duration_ms
        self.last_duration_ms = duration_ms

    def record_failed(self, duration_ms: int) -> None:
        self.total_requests += 1
        self.failed_requests += 1
        self.total_duration_ms += duration_ms
        self.last_duration_ms = duration_ms

    def record_rejected(self, duration_ms: int) -> None:
        self.total_requests += 1
        self.rejected_requests += 1
        self.total_duration_ms += duration_ms
        self.last_duration_ms = duration_ms

    @property
    def avg_duration_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_duration_ms / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "rejected_requests": self.rejected_requests,
            "total_duration_ms": self.total_duration_ms,
            "last_duration_ms": self.last_duration_ms,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
        }


@dataclass(frozen=True)
class RealtimePipelineResponse:
    request_id: str
    correlation_id: str | None
    status: str
    pipeline_name: str
    environment: str
    model_name: str
    model_version: str
    prediction: Any | None
    probability: float | None
    decision: str | None
    duration_ms: int
    cached: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "status": self.status,
            "pipeline_name": self.pipeline_name,
            "environment": self.environment,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prediction": self.prediction,
            "probability": self.probability,
            "decision": self.decision,
            "duration_ms": self.duration_ms,
            "cached": self.cached,
            "metadata": dict(self.metadata),
            "error": dict(self.error) if self.error else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_request_id() -> str:
    return str(uuid.uuid4())


def elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def normalize_realtime_request(
    payload: RealtimeRequest | Mapping[str, Any],
) -> RealtimeRequest:
    if isinstance(payload, RealtimeRequest):
        return payload

    if not isinstance(payload, Mapping):
        raise RealtimePipelineValidationError("Payload precisa ser um mapping/dict.")

    features = payload.get("features")

    if features is None:
        raise RealtimePipelineValidationError("Campo obrigatório ausente: features.")

    return RealtimeRequest(
        features=features,  # type: ignore[arg-type]
        request_id=payload.get("request_id"),  # type: ignore[arg-type]
        tenant_id=payload.get("tenant_id"),  # type: ignore[arg-type]
        user_id=payload.get("user_id"),  # type: ignore[arg-type]
        session_id=payload.get("session_id"),  # type: ignore[arg-type]
        correlation_id=payload.get("correlation_id"),  # type: ignore[arg-type]
        metadata=payload.get("metadata") or {},  # type: ignore[arg-type]
    )


def normalize_features_to_dataframe(
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    if isinstance(features, Mapping):
        return pd.DataFrame([dict(features)])

    items = list(features)

    if not all(isinstance(item, Mapping) for item in items):
        raise RealtimePipelineValidationError(
            "features em lote precisa conter apenas objetos/mappings."
        )

    return pd.DataFrame([dict(item) for item in items])


def validate_request(
    request: RealtimeRequest,
    config: RealtimePipelineConfig,
) -> None:
    if config.require_tenant_id and not request.tenant_id:
        raise RealtimePipelineValidationError("tenant_id é obrigatório.")

    if config.require_user_id and not request.user_id:
        raise RealtimePipelineValidationError("user_id é obrigatório.")

    df = normalize_features_to_dataframe(request.features)

    if df.empty:
        raise RealtimePipelineValidationError("features não pode estar vazio.")

    if len(df) > config.max_batch_size:
        raise RealtimePipelineValidationError(
            f"Batch excede max_batch_size={config.max_batch_size}."
        )

    if df.shape[1] > config.max_features:
        raise RealtimePipelineValidationError(
            f"Número de features excede max_features={config.max_features}."
        )

    if df.columns.duplicated().any():
        duplicated = df.columns[df.columns.duplicated()].tolist()
        raise RealtimePipelineValidationError(f"Features duplicadas: {duplicated}")

    if config.schema_rules:
        validate_schema(df, config.schema_rules)


class RealtimePipeline:
    def __init__(
        self,
        model: ModelProtocol | Callable[[Any], Any],
        *,
        config: RealtimePipelineConfig | None = None,
        preprocessor: Callable[[Any], Any] | None = None,
        postprocessor: Callable[[PredictionResponse], PredictionResponse] | None = None,
    ) -> None:
        self.config = config or RealtimePipelineConfig()
        serving_config = RealtimeServingConfig(
            timeout_seconds=self.config.serving.timeout_seconds,
            max_batch_size=self.config.max_batch_size,
            enable_cache=self.config.serving.enable_cache,
            cache_ttl_seconds=self.config.serving.cache_ttl_seconds,
            prediction_field=self.config.serving.prediction_field,
            probability_field=self.config.serving.probability_field,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            retry_policy=self.config.serving.retry_policy,
            circuit_breaker=self.config.serving.circuit_breaker,
        )

        self.engine = RealtimeServingEngine(
            model,
            config=serving_config,
            preprocessor=preprocessor,
            postprocessor=postprocessor,
        )
        self.metrics = RealtimePipelineMetrics()

    def predict(
        self,
        payload: RealtimeRequest | Mapping[str, Any],
    ) -> RealtimePipelineResponse:
        started_at = time.perf_counter()
        request = normalize_realtime_request(payload)
        request_id = request.request_id or make_request_id()

        try:
            validate_request(request, self.config)

            self._audit(
                "info",
                "realtime_pipeline.request_received",
                {
                    "request_id": request_id,
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "correlation_id": request.correlation_id,
                },
            )

            serving_response = self.engine.predict(
                PredictionRequest(
                    features=request.features,
                    request_id=request_id,
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    metadata={
                        **dict(request.metadata),
                        "session_id": request.session_id,
                        "correlation_id": request.correlation_id,
                    },
                )
            )

            decision = self.config.decision_policy.decide(
                serving_response.probability,
                fallback=serving_response.decision,
            )

            duration = elapsed_ms(started_at)

            response = RealtimePipelineResponse(
                request_id=request_id,
                correlation_id=request.correlation_id,
                status=RealtimePipelineStatus.SUCCESS.value,
                pipeline_name=self.config.pipeline_name,
                environment=self.config.environment,
                model_name=self.config.model_name,
                model_version=self.config.model_version,
                prediction=serving_response.prediction,
                probability=serving_response.probability,
                decision=decision,
                duration_ms=duration,
                cached=serving_response.cached,
                metadata={
                    **dict(serving_response.metadata),
                    "served_at": utc_now_iso(),
                },
            )

            self.metrics.record_success(duration)

            self._metric(
                "realtime_pipeline.success",
                1,
                {
                    "model_name": self.config.model_name,
                    "model_version": self.config.model_version,
                    "decision": decision or "none",
                },
            )

            self._audit(
                "info",
                "realtime_pipeline.request_success",
                {
                    "request_id": request_id,
                    "duration_ms": duration,
                    "decision": decision,
                    "cached": serving_response.cached,
                },
            )

            return response

        except RealtimePipelineValidationError as exc:
            duration = elapsed_ms(started_at)
            self.metrics.record_rejected(duration)

            self._audit(
                "warn",
                "realtime_pipeline.request_rejected",
                {
                    "request_id": request_id,
                    "error": str(exc),
                    "duration_ms": duration,
                },
            )

            return RealtimePipelineResponse(
                request_id=request_id,
                correlation_id=request.correlation_id,
                status=RealtimePipelineStatus.REJECTED.value,
                pipeline_name=self.config.pipeline_name,
                environment=self.config.environment,
                model_name=self.config.model_name,
                model_version=self.config.model_version,
                prediction=None,
                probability=None,
                decision=None,
                duration_ms=duration,
                cached=False,
                metadata={
                    "served_at": utc_now_iso(),
                },
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )

        except Exception as exc:
            duration = elapsed_ms(started_at)
            self.metrics.record_failed(duration)

            logger.exception(
                "realtime_pipeline.request_failed",
                extra={
                    "request_id": request_id,
                    "duration_ms": duration,
                },
            )

            self._metric(
                "realtime_pipeline.failed",
                1,
                {
                    "model_name": self.config.model_name,
                    "model_version": self.config.model_version,
                    "error_type": type(exc).__name__,
                },
            )

            return RealtimePipelineResponse(
                request_id=request_id,
                correlation_id=request.correlation_id,
                status=RealtimePipelineStatus.FAILED.value,
                pipeline_name=self.config.pipeline_name,
                environment=self.config.environment,
                model_name=self.config.model_name,
                model_version=self.config.model_version,
                prediction=None,
                probability=None,
                decision=None,
                duration_ms=duration,
                cached=False,
                metadata={
                    "served_at": utc_now_iso(),
                },
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )

    def health(self) -> dict[str, Any]:
        engine_health = self.engine.health()

        status = "ok"
        if engine_health.get("status") != "ok":
            status = "degraded"

        return {
            "status": status,
            "pipeline_name": self.config.pipeline_name,
            "environment": self.config.environment,
            "model_name": self.config.model_name,
            "model_version": self.config.model_version,
            "timestamp": utc_now_iso(),
            "metrics": self.metrics.to_dict(),
            "serving": engine_health,
        }

    def _audit(
        self,
        level: str,
        event: str,
        data: Mapping[str, Any],
    ) -> None:
        if not self.config.enable_audit:
            return

        log_fn = {
            "debug": logger.debug,
            "info": logger.info,
            "warn": logger.warning,
            "warning": logger.warning,
            "error": logger.error,
        }.get(level, logger.info)

        log_fn(
            event,
            extra={
                "event": event,
                "timestamp": utc_now_iso(),
                **dict(data),
            },
        )

    def _metric(
        self,
        name: str,
        value: float,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        if not self.config.enable_metrics:
            return

        logger.info(
            "metric",
            extra={
                "metric_name": name,
                "metric_value": value,
                "tags": dict(tags or {}),
                "timestamp": utc_now_iso(),
            },
        )


def run_realtime_pipeline(
    model: ModelProtocol | Callable[[Any], Any],
    features: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    config: RealtimePipelineConfig | None = None,
    preprocessor: Callable[[Any], Any] | None = None,
    postprocessor: Callable[[PredictionResponse], PredictionResponse] | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> RealtimePipelineResponse:
    pipeline = RealtimePipeline(
        model,
        config=config,
        preprocessor=preprocessor,
        postprocessor=postprocessor,
    )

    return pipeline.predict(
        RealtimeRequest(
            features=features,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )
    )


__all__ = [
    "DecisionPolicy",
    "ModelProtocol",
    "RealtimePipeline",
    "RealtimePipelineConfig",
    "RealtimePipelineError",
    "RealtimePipelineMetrics",
    "RealtimePipelineResponse",
    "RealtimePipelineStatus",
    "RealtimePipelineValidationError",
    "RealtimeRequest",
    "elapsed_ms",
    "make_request_id",
    "normalize_features_to_dataframe",
    "normalize_realtime_request",
    "run_realtime_pipeline",
    "utc_now_iso",
    "validate_request",
]