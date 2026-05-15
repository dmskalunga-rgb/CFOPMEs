# ml/anomaly_detection/predictor.py
"""
Enterprise Anomaly Detection Predictor.

Recursos:
- Serving realtime e batch
- Integração opcional com FeatureEngineer
- Validação de payload
- Cache TTL
- Circuit breaker
- Auditoria JSONL
- Métricas operacionais
- Health check
- Responses padronizadas para APIs
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple


try:
    from ml.anomaly_detection.model import (
        AnomalyDecision,
        AnomalyPrediction,
        AnomalySeverity,
        EnterpriseAnomalyDetector,
    )
except Exception:  # pragma: no cover
    from model import (  # type: ignore
        AnomalyDecision,
        AnomalyPrediction,
        AnomalySeverity,
        EnterpriseAnomalyDetector,
    )


class PredictorError(RuntimeError):
    pass


class PredictorStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class AnomalyPredictorConfig:
    service_name: str = "anomaly-predictor"
    environment: str = "production"

    cache_enabled: bool = True
    cache_ttl_seconds: int = 300

    max_batch_size: int = 5_000
    timeout_seconds: float = 5.0

    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: int = 30

    audit_enabled: bool = True
    include_feature_contributions: bool = True
    include_detector_scores: bool = True


@dataclass(frozen=True)
class PredictionRequest:
    request_id: str
    record: Mapping[str, Any] | Sequence[float]
    record_id: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchPredictionRequest:
    request_id: str
    records: Sequence[Mapping[str, Any] | Sequence[float]]
    record_ids: Optional[Sequence[Optional[str]]] = None
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionResponse:
    request_id: str
    prediction_id: str
    record_id: Optional[str]
    anomaly_score: float
    decision: AnomalyDecision
    severity: AnomalySeverity
    threshold: float
    model_name: str
    model_version: str
    latency_ms: float
    cached: bool
    detector_scores: Dict[str, float]
    feature_contributions: Dict[str, float]
    generated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class BatchPredictionResponse:
    request_id: str
    total: int
    succeeded: int
    failed: int
    latency_ms: float
    predictions: List[PredictionResponse]
    errors: List[Dict[str, Any]]
    generated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass
class PredictorMetrics:
    total_requests: int = 0
    total_batch_requests: int = 0
    total_predictions: int = 0
    total_errors: int = 0

    cache_hits: int = 0
    cache_misses: int = 0

    normal_decisions: int = 0
    review_decisions: int = 0
    anomaly_decisions: int = 0

    latency_ms_sum: float = 0.0
    latency_ms_max: float = 0.0

    def snapshot(self) -> Dict[str, float]:
        avg = self.latency_ms_sum / max(self.total_predictions, 1)

        return {
            "total_requests": float(self.total_requests),
            "total_batch_requests": float(self.total_batch_requests),
            "total_predictions": float(self.total_predictions),
            "total_errors": float(self.total_errors),
            "cache_hits": float(self.cache_hits),
            "cache_misses": float(self.cache_misses),
            "normal_decisions": float(self.normal_decisions),
            "review_decisions": float(self.review_decisions),
            "anomaly_decisions": float(self.anomaly_decisions),
            "latency_ms_avg": float(avg),
            "latency_ms_max": float(self.latency_ms_max),
        }


@dataclass
class CacheEntry:
    value: PredictionResponse
    expires_at: float


class TTLPredictionCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: Dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[PredictionResponse]:
        now = time.time()

        with self._lock:
            entry = self._data.get(key)

            if entry is None:
                return None

            if entry.expires_at < now:
                self._data.pop(key, None)
                return None

            return entry.value

    def set(self, key: str, value: PredictionResponse) -> None:
        with self._lock:
            self._data[key] = CacheEntry(
                value=value,
                expires_at=time.time() + self.ttl_seconds,
            )

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_seconds: int) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_at: Optional[float] = None
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            if self.state != CircuitState.OPEN:
                return

            if self.last_failure_at and time.time() - self.last_failure_at >= self.recovery_seconds:
                self.state = CircuitState.HALF_OPEN
                return

            raise PredictorError("Circuit breaker aberto. Serviço temporariamente indisponível.")

    def success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED
            self.last_failure_at = None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.last_failure_at = time.time()

            if self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN


class AuditSink(Protocol):
    def write(self, event: Mapping[str, Any]) -> None:
        ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self._lock = Lock()

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))

    def list_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self.events)


class JsonlAuditSink:
    def __init__(self, path: str | Path = "artifacts/anomaly_detection/predictions/audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


class FeatureEngineerProtocol(Protocol):
    feature_names: Sequence[str]

    def transform(self, records: Sequence[Mapping[str, Any]], *, update_state: bool = True) -> Any:
        ...


class PredictionValidator:
    @staticmethod
    def validate_request(request: PredictionRequest) -> None:
        if not request.request_id:
            raise PredictorError("request_id obrigatório.")

        if request.record is None:
            raise PredictorError("record obrigatório.")

        if isinstance(request.record, Sequence) and not isinstance(request.record, (str, bytes, Mapping)):
            if len(request.record) == 0:
                raise PredictorError("record vetorial vazio.")

    @staticmethod
    def validate_batch(request: BatchPredictionRequest, max_batch_size: int) -> None:
        if not request.request_id:
            raise PredictorError("request_id obrigatório.")

        if not request.records:
            raise PredictorError("records vazio.")

        if len(request.records) > max_batch_size:
            raise PredictorError(
                f"Batch excede limite máximo: {len(request.records)} > {max_batch_size}"
            )

        if request.record_ids is not None and len(request.record_ids) != len(request.records):
            raise PredictorError("record_ids precisa ter o mesmo tamanho de records.")


class EnterpriseAnomalyPredictor:
    def __init__(
        self,
        model: EnterpriseAnomalyDetector,
        *,
        feature_engineer: Optional[FeatureEngineerProtocol] = None,
        config: Optional[AnomalyPredictorConfig] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        self.model = model
        self.feature_engineer = feature_engineer
        self.config = config or AnomalyPredictorConfig()
        self.audit_sink = audit_sink or JsonlAuditSink()

        self.cache = TTLPredictionCache(self.config.cache_ttl_seconds)
        self.circuit = CircuitBreaker(
            self.config.circuit_failure_threshold,
            self.config.circuit_recovery_seconds,
        )

        self.metrics = PredictorMetrics()
        self._metrics_lock = Lock()

    def predict(self, request: PredictionRequest) -> PredictionResponse:
        started = time.perf_counter()

        with self._metrics_lock:
            self.metrics.total_requests += 1

        PredictionValidator.validate_request(request)
        self.circuit.before_call()

        cache_key = self._cache_key(request)

        if self.config.cache_enabled:
            cached = self.cache.get(cache_key)
            if cached is not None:
                with self._metrics_lock:
                    self.metrics.cache_hits += 1
                return cached

            with self._metrics_lock:
                self.metrics.cache_misses += 1

        try:
            row, resolved_record_id = self._prepare_single(request)

            prediction = self.model.predict_one(
                row,
                record_id=resolved_record_id,
                metadata={
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    **request.metadata,
                },
            )

            latency_ms = (time.perf_counter() - started) * 1000

            response = self._to_response(
                request_id=request.request_id,
                prediction=prediction,
                latency_ms=latency_ms,
                cached=False,
            )

            if self.config.cache_enabled:
                self.cache.set(cache_key, response)

            self._record_success(response)
            self._audit_prediction(request, response)
            self.circuit.success()

            return response

        except Exception as exc:
            self.circuit.failure()

            with self._metrics_lock:
                self.metrics.total_errors += 1

            self._audit_error(request, exc)
            raise

    async def predict_async(self, request: PredictionRequest) -> PredictionResponse:
        return await asyncio.to_thread(self.predict, request)

    def predict_batch(self, request: BatchPredictionRequest) -> BatchPredictionResponse:
        started = time.perf_counter()

        with self._metrics_lock:
            self.metrics.total_batch_requests += 1

        PredictionValidator.validate_batch(request, self.config.max_batch_size)

        predictions: List[PredictionResponse] = []
        errors: List[Dict[str, Any]] = []

        for i, record in enumerate(request.records):
            record_id = request.record_ids[i] if request.record_ids else self._extract_record_id(record, i)

            try:
                response = self.predict(
                    PredictionRequest(
                        request_id=f"{request.request_id}:{record_id or i}",
                        record=record,
                        record_id=record_id,
                        tenant_id=request.tenant_id,
                        metadata=request.metadata,
                    )
                )
                predictions.append(response)

            except Exception as exc:
                errors.append(
                    {
                        "record_id": record_id,
                        "index": i,
                        "error": str(exc),
                        "type": exc.__class__.__name__,
                    }
                )

        latency_ms = (time.perf_counter() - started) * 1000

        return BatchPredictionResponse(
            request_id=request.request_id,
            total=len(request.records),
            succeeded=len(predictions),
            failed=len(errors),
            latency_ms=latency_ms,
            predictions=predictions,
            errors=errors,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    async def predict_batch_async(
        self,
        request: BatchPredictionRequest,
        *,
        concurrency: int = 20,
    ) -> BatchPredictionResponse:
        started = time.perf_counter()

        PredictionValidator.validate_batch(request, self.config.max_batch_size)

        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(i: int, record: Mapping[str, Any] | Sequence[float]) -> Tuple[Optional[PredictionResponse], Optional[Dict[str, Any]]]:
            async with semaphore:
                record_id = request.record_ids[i] if request.record_ids else self._extract_record_id(record, i)

                try:
                    response = await self.predict_async(
                        PredictionRequest(
                            request_id=f"{request.request_id}:{record_id or i}",
                            record=record,
                            record_id=record_id,
                            tenant_id=request.tenant_id,
                            metadata=request.metadata,
                        )
                    )
                    return response, None

                except Exception as exc:
                    return None, {
                        "record_id": record_id,
                        "index": i,
                        "error": str(exc),
                        "type": exc.__class__.__name__,
                    }

        results = await asyncio.gather(
            *(run_one(i, record) for i, record in enumerate(request.records))
        )

        predictions = [r for r, e in results if r is not None]
        errors = [e for r, e in results if e is not None]

        latency_ms = (time.perf_counter() - started) * 1000

        return BatchPredictionResponse(
            request_id=request.request_id,
            total=len(request.records),
            succeeded=len(predictions),
            failed=len(errors),
            latency_ms=latency_ms,
            predictions=predictions,
            errors=errors,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def health(self) -> Dict[str, Any]:
        if self.circuit.state == CircuitState.OPEN:
            status = PredictorStatus.UNAVAILABLE
        elif not getattr(self.model, "is_trained", False):
            status = PredictorStatus.DEGRADED
        else:
            status = PredictorStatus.OK

        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            "status": status.value,
            "circuit_state": self.circuit.state.value,
            "model_name": getattr(self.model.config, "model_name", "unknown"),
            "model_version": getattr(self.model.config, "model_version", "unknown"),
            "model_trained": bool(getattr(self.model, "is_trained", False)),
            "feature_engineer_enabled": self.feature_engineer is not None,
            "metrics": self.metrics.snapshot(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def metrics_snapshot(self) -> Dict[str, float]:
        return self.metrics.snapshot()

    def clear_cache(self) -> None:
        self.cache.clear()

    def _prepare_single(self, request: PredictionRequest) -> Tuple[Sequence[float], Optional[str]]:
        record = request.record
        record_id = request.record_id or self._extract_record_id(record, 0)

        if isinstance(record, Mapping):
            if self.feature_engineer is None:
                if "features" in record and isinstance(record["features"], Sequence):
                    return [float(v) for v in record["features"]], record_id

                raise PredictorError(
                    "Record dict recebido, mas feature_engineer não foi configurado "
                    "e o campo 'features' não existe."
                )

            feature_result = self.feature_engineer.transform([record], update_state=True)
            matrix = feature_result.to_matrix()

            if len(matrix) != 1:
                raise PredictorError("FeatureEngineer retornou quantidade inválida de linhas.")

            return matrix[0].tolist(), record_id

        if isinstance(record, Sequence) and not isinstance(record, (str, bytes)):
            return [float(v) for v in record], record_id

        raise PredictorError(f"Tipo de record não suportado: {type(record)!r}")

    def _to_response(
        self,
        *,
        request_id: str,
        prediction: AnomalyPrediction,
        latency_ms: float,
        cached: bool,
    ) -> PredictionResponse:
        detector_scores = prediction.detector_scores if self.config.include_detector_scores else {}
        contributions = (
            prediction.feature_contributions
            if self.config.include_feature_contributions
            else {}
        )

        return PredictionResponse(
            request_id=request_id,
            prediction_id=prediction.prediction_id,
            record_id=prediction.record_id,
            anomaly_score=prediction.anomaly_score,
            decision=prediction.decision,
            severity=prediction.severity,
            threshold=prediction.threshold,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            latency_ms=latency_ms,
            cached=cached,
            detector_scores=detector_scores,
            feature_contributions=contributions,
            generated_at=prediction.generated_at,
            metadata=prediction.metadata,
        )

    def _record_success(self, response: PredictionResponse) -> None:
        with self._metrics_lock:
            self.metrics.total_predictions += 1
            self.metrics.latency_ms_sum += response.latency_ms
            self.metrics.latency_ms_max = max(self.metrics.latency_ms_max, response.latency_ms)

            if response.decision == AnomalyDecision.NORMAL:
                self.metrics.normal_decisions += 1
            elif response.decision == AnomalyDecision.REVIEW:
                self.metrics.review_decisions += 1
            elif response.decision == AnomalyDecision.ANOMALY:
                self.metrics.anomaly_decisions += 1

    def _audit_prediction(self, request: PredictionRequest, response: PredictionResponse) -> None:
        if not self.config.audit_enabled:
            return

        self.audit_sink.write(
            {
                "event_id": str(uuid.uuid4()),
                "event": "anomaly_prediction",
                "request_id": request.request_id,
                "record_id": response.record_id,
                "tenant_id": request.tenant_id,
                "anomaly_score": response.anomaly_score,
                "decision": response.decision.value,
                "severity": response.severity.value,
                "threshold": response.threshold,
                "model_name": response.model_name,
                "model_version": response.model_version,
                "latency_ms": response.latency_ms,
                "cached": response.cached,
                "generated_at": response.generated_at,
            }
        )

    def _audit_error(self, request: PredictionRequest, exc: Exception) -> None:
        if not self.config.audit_enabled:
            return

        self.audit_sink.write(
            {
                "event_id": str(uuid.uuid4()),
                "event": "anomaly_prediction_error",
                "request_id": request.request_id,
                "record_id": request.record_id,
                "tenant_id": request.tenant_id,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @staticmethod
    def _extract_record_id(record: Mapping[str, Any] | Sequence[float], index: int) -> Optional[str]:
        if isinstance(record, Mapping):
            value = record.get("record_id") or record.get("id")
            return str(value) if value is not None else None

        return f"row-{index}"

    @staticmethod
    def _cache_key(request: PredictionRequest) -> str:
        payload = {
            "record": request.record,
            "record_id": request.record_id,
            "tenant_id": request.tenant_id,
        }

        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()


def request_from_dict(payload: Mapping[str, Any]) -> PredictionRequest:
    record = payload.get("record")

    if record is None and "features" in payload:
        record = payload["features"]

    if record is None:
        raise PredictorError("Campo 'record' ou 'features' obrigatório.")

    return PredictionRequest(
        request_id=str(payload.get("request_id") or uuid.uuid4()),
        record=record,
        record_id=payload.get("record_id"),
        tenant_id=payload.get("tenant_id"),
        metadata=dict(payload.get("metadata", {})),
    )


def batch_request_from_dict(payload: Mapping[str, Any]) -> BatchPredictionRequest:
    records = payload.get("records")

    if records is None:
        raise PredictorError("Campo 'records' obrigatório.")

    return BatchPredictionRequest(
        request_id=str(payload.get("request_id") or uuid.uuid4()),
        records=list(records),
        record_ids=payload.get("record_ids"),
        tenant_id=payload.get("tenant_id"),
        metadata=dict(payload.get("metadata", {})),
    )


if __name__ == "__main__":
    import numpy as np

    from ml.anomaly_detection.model import (
        AnomalyModelConfig,
        DetectorType,
        EnterpriseAnomalyDetector,
        generate_synthetic_anomaly_matrix,
    )

    x, y, feature_names = generate_synthetic_anomaly_matrix(samples=500, features=6)

    model = EnterpriseAnomalyDetector(
        AnomalyModelConfig(
            detector_type=DetectorType.ENSEMBLE,
            contamination=0.05,
        )
    )

    model.train(x, feature_names=feature_names)

    predictor = EnterpriseAnomalyPredictor(
        model,
        config=AnomalyPredictorConfig(environment="development"),
        audit_sink=InMemoryAuditSink(),
    )

    request = PredictionRequest(
        request_id="req-001",
        record=x[0].tolist(),
        record_id="rec-001",
        tenant_id="digital-meta",
    )

    response = predictor.predict(request)

    print(response.to_json())
    print(json.dumps(predictor.health(), indent=2, ensure_ascii=False, default=str))
