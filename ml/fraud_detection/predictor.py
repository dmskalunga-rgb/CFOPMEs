# ml/fraud_detection/predictor.py
"""
Enterprise Fraud Predictor.

Camada de serving para o modelo de fraude.

Recursos:
- predição realtime
- predição batch
- validação de payload
- cache TTL opcional
- circuit breaker
- métricas operacionais
- auditoria de decisões
- reason codes
- integração com APIs
- serialização JSON
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
from threading import Lock
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


try:
    from ml.fraud_detection.model import (
        EnterpriseFraudDetectionModel,
        FraudDecision,
        FraudPrediction,
        FraudRiskLevel,
        Transaction,
        UserProfile,
    )
except Exception:  # pragma: no cover
    from model import (  # type: ignore
        EnterpriseFraudDetectionModel,
        FraudDecision,
        FraudPrediction,
        FraudRiskLevel,
        Transaction,
        UserProfile,
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
class PredictorConfig:
    service_name: str = "fraud-predictor"
    environment: str = "production"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    max_batch_size: int = 2_000
    timeout_seconds: float = 3.0
    circuit_failure_threshold: int = 5
    circuit_recovery_seconds: int = 30
    audit_enabled: bool = True
    include_raw_signals: bool = True


@dataclass(frozen=True)
class PredictionRequest:
    request_id: str
    transaction: Transaction
    profile: Optional[UserProfile] = None
    tenant_id: Optional[str] = None
    user_context: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchPredictionRequest:
    request_id: str
    transactions: Sequence[Transaction]
    profiles: Dict[str, UserProfile] = field(default_factory=dict)
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredictionResponse:
    request_id: str
    prediction_id: str
    transaction_id: str
    risk_score: float
    risk_level: FraudRiskLevel
    decision: FraudDecision
    model_name: str
    model_version: str
    latency_ms: float
    cached: bool
    generated_at: str
    signals: List[Dict[str, Any]]
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
    blocked_decisions: int = 0
    review_decisions: int = 0
    approved_decisions: int = 0
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
            "blocked_decisions": float(self.blocked_decisions),
            "review_decisions": float(self.review_decisions),
            "approved_decisions": float(self.approved_decisions),
            "latency_ms_avg": float(avg),
            "latency_ms_max": float(self.latency_ms_max),
        }


@dataclass
class CacheEntry:
    value: PredictionResponse
    expires_at: float


class TTLPredictionCache:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: Dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[PredictionResponse]:
        now = time.time()

        with self._lock:
            entry = self._data.get(key)

            if not entry:
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
    def __init__(
        self,
        failure_threshold: int,
        recovery_seconds: int,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_at: Optional[float] = None
        self._lock = Lock()

    def before_call(self) -> None:
        with self._lock:
            if self.state == CircuitState.OPEN:
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


class PredictionAuditSink(Protocol):
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
    def __init__(self, path: str = "artifacts/fraud_predictions/audit.jsonl") -> None:
        from pathlib import Path

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


class PredictionValidator:
    @staticmethod
    def validate_request(request: PredictionRequest) -> None:
        tx = request.transaction

        if not request.request_id:
            raise PredictorError("request_id obrigatório.")

        if not tx.transaction_id:
            raise PredictorError("transaction_id obrigatório.")

        if not tx.user_id:
            raise PredictorError("user_id obrigatório.")

        if tx.amount < 0:
            raise PredictorError("amount não pode ser negativo.")

        if not tx.currency:
            raise PredictorError("currency obrigatório.")

    @staticmethod
    def validate_batch(request: BatchPredictionRequest, max_batch_size: int) -> None:
        if not request.request_id:
            raise PredictorError("request_id obrigatório.")

        if not request.transactions:
            raise PredictorError("Batch vazio.")

        if len(request.transactions) > max_batch_size:
            raise PredictorError(
                f"Batch excede limite máximo: {len(request.transactions)} > {max_batch_size}"
            )


class FraudPredictor:
    def __init__(
        self,
        model: EnterpriseFraudDetectionModel,
        config: Optional[PredictorConfig] = None,
        audit_sink: Optional[PredictionAuditSink] = None,
    ) -> None:
        self.model = model
        self.config = config or PredictorConfig()
        self.cache = TTLPredictionCache(self.config.cache_ttl_seconds)
        self.circuit = CircuitBreaker(
            self.config.circuit_failure_threshold,
            self.config.circuit_recovery_seconds,
        )
        self.audit_sink = audit_sink or JsonlAuditSink()
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
            if cached:
                with self._metrics_lock:
                    self.metrics.cache_hits += 1
                return cached

            with self._metrics_lock:
                self.metrics.cache_misses += 1

        try:
            prediction = self.model.predict_one(
                request.transaction,
                request.profile,
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
            self._audit(request, response)
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

        for tx in request.transactions:
            try:
                response = self.predict(
                    PredictionRequest(
                        request_id=f"{request.request_id}:{tx.transaction_id}",
                        transaction=tx,
                        profile=request.profiles.get(tx.user_id),
                        tenant_id=request.tenant_id,
                        metadata=request.metadata,
                    )
                )
                predictions.append(response)

            except Exception as exc:
                errors.append(
                    {
                        "transaction_id": tx.transaction_id,
                        "error": str(exc),
                        "type": exc.__class__.__name__,
                    }
                )

        latency_ms = (time.perf_counter() - started) * 1000

        return BatchPredictionResponse(
            request_id=request.request_id,
            total=len(request.transactions),
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
        concurrency: int = 20,
    ) -> BatchPredictionResponse:
        started = time.perf_counter()

        PredictionValidator.validate_batch(request, self.config.max_batch_size)

        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(tx: Transaction) -> Tuple[Optional[PredictionResponse], Optional[Dict[str, Any]]]:
            async with semaphore:
                try:
                    response = await self.predict_async(
                        PredictionRequest(
                            request_id=f"{request.request_id}:{tx.transaction_id}",
                            transaction=tx,
                            profile=request.profiles.get(tx.user_id),
                            tenant_id=request.tenant_id,
                            metadata=request.metadata,
                        )
                    )
                    return response, None
                except Exception as exc:
                    return None, {
                        "transaction_id": tx.transaction_id,
                        "error": str(exc),
                        "type": exc.__class__.__name__,
                    }

        results = await asyncio.gather(*(run_one(tx) for tx in request.transactions))

        predictions = [r for r, e in results if r is not None]
        errors = [e for r, e in results if e is not None]

        latency_ms = (time.perf_counter() - started) * 1000

        return BatchPredictionResponse(
            request_id=request.request_id,
            total=len(request.transactions),
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
            "model_name": self.model.config.model_name,
            "model_version": self.model.config.model_version,
            "model_trained": bool(getattr(self.model, "is_trained", False)),
            "metrics": self.metrics.snapshot(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def metrics_snapshot(self) -> Dict[str, float]:
        return self.metrics.snapshot()

    def _to_response(
        self,
        *,
        request_id: str,
        prediction: FraudPrediction,
        latency_ms: float,
        cached: bool,
    ) -> PredictionResponse:
        signals = []

        if self.config.include_raw_signals:
            signals = [asdict(signal) for signal in prediction.signals]
        else:
            signals = [
                {
                    "signal_type": signal.signal_type.value,
                    "score": signal.score,
                    "reason": signal.reason,
                }
                for signal in prediction.signals
            ]

        return PredictionResponse(
            request_id=request_id,
            prediction_id=prediction.prediction_id,
            transaction_id=prediction.transaction_id,
            risk_score=prediction.risk_score,
            risk_level=prediction.risk_level,
            decision=prediction.decision,
            model_name=prediction.model_name,
            model_version=prediction.model_version,
            latency_ms=latency_ms,
            cached=cached,
            signals=signals,
            generated_at=prediction.generated_at,
            metadata=prediction.metadata,
        )

    def _record_success(self, response: PredictionResponse) -> None:
        with self._metrics_lock:
            self.metrics.total_predictions += 1
            self.metrics.latency_ms_sum += response.latency_ms
            self.metrics.latency_ms_max = max(self.metrics.latency_ms_max, response.latency_ms)

            if response.decision == FraudDecision.BLOCK:
                self.metrics.blocked_decisions += 1
            elif response.decision == FraudDecision.REVIEW:
                self.metrics.review_decisions += 1
            elif response.decision == FraudDecision.APPROVE:
                self.metrics.approved_decisions += 1

    def _audit(self, request: PredictionRequest, response: PredictionResponse) -> None:
        if not self.config.audit_enabled:
            return

        self.audit_sink.write(
            {
                "event_id": str(uuid.uuid4()),
                "event": "fraud_prediction",
                "request_id": request.request_id,
                "transaction_id": request.transaction.transaction_id,
                "user_id": request.transaction.user_id,
                "tenant_id": request.tenant_id,
                "risk_score": response.risk_score,
                "risk_level": response.risk_level.value,
                "decision": response.decision.value,
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
                "event": "fraud_prediction_error",
                "request_id": request.request_id,
                "transaction_id": request.transaction.transaction_id,
                "user_id": request.transaction.user_id,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @staticmethod
    def _cache_key(request: PredictionRequest) -> str:
        payload = {
            "transaction": asdict(request.transaction),
            "profile": asdict(request.profile) if request.profile else None,
            "tenant_id": request.tenant_id,
        }

        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def request_from_dict(payload: Mapping[str, Any]) -> PredictionRequest:
    tx_raw = payload.get("transaction")
    if not isinstance(tx_raw, Mapping):
        raise PredictorError("Campo transaction obrigatório.")

    transaction = Transaction(
        transaction_id=str(tx_raw["transaction_id"]),
        user_id=str(tx_raw["user_id"]),
        amount=float(tx_raw["amount"]),
        currency=str(tx_raw["currency"]),
        merchant_id=tx_raw.get("merchant_id"),
        merchant_category=tx_raw.get("merchant_category"),
        country=tx_raw.get("country"),
        device_id=tx_raw.get("device_id"),
        ip_address=tx_raw.get("ip_address"),
        timestamp=tx_raw.get("timestamp", datetime.now(timezone.utc).isoformat()),
        metadata=dict(tx_raw.get("metadata", {})),
    )

    profile = None
    profile_raw = payload.get("profile")

    if isinstance(profile_raw, Mapping):
        profile = UserProfile(
            user_id=str(profile_raw.get("user_id", transaction.user_id)),
            avg_amount=float(profile_raw.get("avg_amount", 0.0)),
            std_amount=float(profile_raw.get("std_amount", 0.0)),
            transaction_count_24h=int(profile_raw.get("transaction_count_24h", 0)),
            transaction_amount_24h=float(profile_raw.get("transaction_amount_24h", 0.0)),
            known_devices=list(profile_raw.get("known_devices", [])),
            known_countries=list(profile_raw.get("known_countries", [])),
            chargeback_count=int(profile_raw.get("chargeback_count", 0)),
            account_age_days=int(profile_raw.get("account_age_days", 0)),
            metadata=dict(profile_raw.get("metadata", {})),
        )

    return PredictionRequest(
        request_id=str(payload.get("request_id") or uuid.uuid4()),
        transaction=transaction,
        profile=profile,
        tenant_id=payload.get("tenant_id"),
        user_context=payload.get("user_context"),
        metadata=dict(payload.get("metadata", {})),
    )


if __name__ == "__main__":
    from ml.fraud_detection.model import FraudFeatureExtractor

    model = EnterpriseFraudDetectionModel(
        feature_extractor=FraudFeatureExtractor(
            risky_merchants={"risky": 0.25},
            risky_countries={"XX": 0.25},
        )
    )

    predictor = FraudPredictor(
        model=model,
        config=PredictorConfig(environment="development"),
        audit_sink=InMemoryAuditSink(),
    )

    request = request_from_dict(
        {
            "request_id": "req-001",
            "tenant_id": "digital-meta",
            "transaction": {
                "transaction_id": "tx-live-001",
                "user_id": "user-1",
                "amount": 15_000,
                "currency": "BRL",
                "merchant_id": "risky",
                "country": "XX",
                "device_id": "new-device",
            },
            "profile": {
                "user_id": "user-1",
                "avg_amount": 250,
                "std_amount": 100,
                "transaction_count_24h": 10,
                "transaction_amount_24h": 35_000,
                "known_devices": ["old-device"],
                "known_countries": ["BR"],
                "chargeback_count": 1,
                "account_age_days": 120,
            },
        }
    )

    response = predictor.predict(request)

    print(response.to_json())
    print(json.dumps(predictor.health(), indent=2, ensure_ascii=False))