# kwanza-ai-core/services/cashflow_service.py
"""
Enterprise Cashflow Service.

Responsável por:
- registrar transações de fluxo de caixa
- consultar séries históricas
- executar previsão de caixa
- cache TTL
- auditoria
- métricas operacionais
- validação de entrada
- integração com módulos ml/forecasting
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence


try:
    from ml.forecasting.cashflow_predictor import (
        CashflowDirection,
        CashflowForecastReport,
        CashflowPredictorConfig,
        CashflowTransaction,
        EnterpriseCashflowPredictor,
    )
except Exception:  # pragma: no cover
    CashflowDirection = None
    CashflowForecastReport = Any
    CashflowPredictorConfig = Any
    CashflowTransaction = Any
    EnterpriseCashflowPredictor = Any


class CashflowServiceError(RuntimeError):
    pass


class ServiceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class TransactionDirection(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


@dataclass(frozen=True)
class CashflowServiceConfig:
    service_name: str = "cashflow-service"
    environment: str = "production"
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    audit_enabled: bool = True
    artifact_dir: str = "artifacts/cashflow_service"
    default_currency: str = "BRL"
    default_horizon: int = 30
    min_forecast_transactions: int = 60


@dataclass(frozen=True)
class CashflowEntry:
    transaction_id: str
    transaction_date: str
    amount: float
    direction: TransactionDirection
    currency: str = "BRL"
    account_id: Optional[str] = None
    category: Optional[str] = None
    cost_center: Optional[str] = None
    description: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.transaction_id:
            raise CashflowServiceError("transaction_id obrigatório.")
        if not self.transaction_date:
            raise CashflowServiceError("transaction_date obrigatório.")
        if self.amount < 0:
            raise CashflowServiceError("amount não pode ser negativo.")
        if not self.currency:
            raise CashflowServiceError("currency obrigatório.")


@dataclass(frozen=True)
class ForecastRequest:
    request_id: str
    tenant_id: Optional[str] = None
    account_id: Optional[str] = None
    category: Optional[str] = None
    cost_center: Optional[str] = None
    horizon: Optional[int] = None
    opening_balance: Optional[float] = None
    include_backtest: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForecastResponse:
    request_id: str
    forecast_id: str
    status: str
    cached: bool
    latency_ms: float
    report: Dict[str, Any]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent, default=str)


@dataclass
class ServiceMetrics:
    total_entries_added: int = 0
    total_forecasts: int = 0
    total_errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    latency_ms_sum: float = 0.0
    latency_ms_max: float = 0.0

    def snapshot(self) -> Dict[str, float]:
        avg = self.latency_ms_sum / max(self.total_forecasts, 1)
        return {
            "total_entries_added": float(self.total_entries_added),
            "total_forecasts": float(self.total_forecasts),
            "total_errors": float(self.total_errors),
            "cache_hits": float(self.cache_hits),
            "cache_misses": float(self.cache_misses),
            "latency_ms_avg": float(avg),
            "latency_ms_max": float(self.latency_ms_max),
        }


class CashflowRepository(Protocol):
    def add(self, entry: CashflowEntry) -> None:
        ...

    def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        account_id: Optional[str] = None,
        category: Optional[str] = None,
        cost_center: Optional[str] = None,
    ) -> List[CashflowEntry]:
        ...


class InMemoryCashflowRepository:
    def __init__(self) -> None:
        self._items: Dict[str, CashflowEntry] = {}
        self._lock = Lock()

    def add(self, entry: CashflowEntry) -> None:
        with self._lock:
            self._items[entry.transaction_id] = entry

    def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        account_id: Optional[str] = None,
        category: Optional[str] = None,
        cost_center: Optional[str] = None,
    ) -> List[CashflowEntry]:
        with self._lock:
            items = list(self._items.values())

        if tenant_id:
            items = [x for x in items if x.tenant_id == tenant_id]
        if account_id:
            items = [x for x in items if x.account_id == account_id]
        if category:
            items = [x for x in items if x.category == category]
        if cost_center:
            items = [x for x in items if x.cost_center == cost_center]

        return sorted(items, key=lambda x: x.transaction_date)


class JsonlCashflowRepository:
    def __init__(self, path: str | Path = "artifacts/cashflow_service/transactions.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def add(self, entry: CashflowEntry) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")

    def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        account_id: Optional[str] = None,
        category: Optional[str] = None,
        cost_center: Optional[str] = None,
    ) -> List[CashflowEntry]:
        if not self.path.exists():
            return []

        items: List[CashflowEntry] = []

        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue

                raw = json.loads(line)
                items.append(
                    CashflowEntry(
                        transaction_id=raw["transaction_id"],
                        transaction_date=raw["transaction_date"],
                        amount=float(raw["amount"]),
                        direction=TransactionDirection(raw["direction"]),
                        currency=raw.get("currency", "BRL"),
                        account_id=raw.get("account_id"),
                        category=raw.get("category"),
                        cost_center=raw.get("cost_center"),
                        description=raw.get("description"),
                        tenant_id=raw.get("tenant_id"),
                        metadata=dict(raw.get("metadata", {})),
                    )
                )

        if tenant_id:
            items = [x for x in items if x.tenant_id == tenant_id]
        if account_id:
            items = [x for x in items if x.account_id == account_id]
        if category:
            items = [x for x in items if x.category == category]
        if cost_center:
            items = [x for x in items if x.cost_center == cost_center]

        return sorted(items, key=lambda x: x.transaction_date)


@dataclass
class CacheEntry:
    value: ForecastResponse
    expires_at: float


class TTLCache:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: Dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, key: str) -> Optional[ForecastResponse]:
        now = time.time()

        with self._lock:
            item = self._data.get(key)

            if item is None:
                return None

            if item.expires_at < now:
                self._data.pop(key, None)
                return None

            return item.value

    def set(self, key: str, value: ForecastResponse) -> None:
        with self._lock:
            self._data[key] = CacheEntry(
                value=value,
                expires_at=time.time() + self.ttl_seconds,
            )

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class AuditSink:
    def __init__(self, path: str | Path = "artifacts/cashflow_service/audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(dict(event), ensure_ascii=False, default=str) + "\n")


class CashflowMapper:
    @staticmethod
    def to_ml_transaction(entry: CashflowEntry) -> Any:
        if CashflowTransaction is Any or CashflowDirection is None:
            raise CashflowServiceError("Módulo ml.forecasting.cashflow_predictor indisponível.")

        return CashflowTransaction(
            transaction_id=entry.transaction_id,
            transaction_date=entry.transaction_date,
            amount=entry.amount,
            direction=CashflowDirection.INFLOW
            if entry.direction == TransactionDirection.INFLOW
            else CashflowDirection.OUTFLOW,
            currency=entry.currency,
            account_id=entry.account_id,
            category=entry.category,
            cost_center=entry.cost_center,
            description=entry.description,
            metadata=entry.metadata,
        )


class CashflowService:
    def __init__(
        self,
        config: Optional[CashflowServiceConfig] = None,
        repository: Optional[CashflowRepository] = None,
        predictor: Optional[Any] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        self.config = config or CashflowServiceConfig()
        self.repository = repository or JsonlCashflowRepository(
            Path(self.config.artifact_dir) / "transactions.jsonl"
        )
        self.predictor = predictor
        self.audit_sink = audit_sink or AuditSink(Path(self.config.artifact_dir) / "audit.jsonl")
        self.cache = TTLCache(self.config.cache_ttl_seconds)
        self.metrics = ServiceMetrics()
        self._lock = Lock()

    def add_entry(self, entry: CashflowEntry) -> CashflowEntry:
        try:
            entry.validate()
            self.repository.add(entry)

            with self._lock:
                self.metrics.total_entries_added += 1

            self.cache.clear()

            self._audit(
                "cashflow_entry_added",
                {
                    "transaction_id": entry.transaction_id,
                    "tenant_id": entry.tenant_id,
                    "amount": entry.amount,
                    "direction": entry.direction.value,
                },
            )

            return entry

        except Exception as exc:
            self._error(exc)
            raise

    def add_entries(self, entries: Sequence[CashflowEntry]) -> List[CashflowEntry]:
        return [self.add_entry(entry) for entry in entries]

    def list_entries(
        self,
        *,
        tenant_id: Optional[str] = None,
        account_id: Optional[str] = None,
        category: Optional[str] = None,
        cost_center: Optional[str] = None,
    ) -> List[CashflowEntry]:
        return self.repository.list(
            tenant_id=tenant_id,
            account_id=account_id,
            category=category,
            cost_center=cost_center,
        )

    def forecast(self, request: ForecastRequest) -> ForecastResponse:
        started = time.perf_counter()
        cache_key = self._cache_key(request)

        try:
            if self.config.cache_enabled:
                cached = self.cache.get(cache_key)
                if cached:
                    with self._lock:
                        self.metrics.cache_hits += 1
                    return ForecastResponse(
                        request_id=request.request_id,
                        forecast_id=cached.forecast_id,
                        status=cached.status,
                        cached=True,
                        latency_ms=(time.perf_counter() - started) * 1000,
                        report=cached.report,
                    )

                with self._lock:
                    self.metrics.cache_misses += 1

            entries = self.list_entries(
                tenant_id=request.tenant_id,
                account_id=request.account_id,
                category=request.category,
                cost_center=request.cost_center,
            )

            if len(entries) < self.config.min_forecast_transactions:
                raise CashflowServiceError(
                    f"Histórico insuficiente para previsão: "
                    f"{len(entries)} < {self.config.min_forecast_transactions}"
                )

            predictor = self._get_predictor()
            transactions = [CashflowMapper.to_ml_transaction(entry) for entry in entries]

            predictor.train(
                transactions,
                opening_balance=request.opening_balance,
                metadata={
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    **request.metadata,
                },
            )

            report = predictor.forecast(
                horizon=request.horizon or self.config.default_horizon,
                opening_balance=request.opening_balance,
                include_backtest=request.include_backtest,
                metadata={
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    "account_id": request.account_id,
                    "category": request.category,
                    "cost_center": request.cost_center,
                    **request.metadata,
                },
            )

            latency_ms = (time.perf_counter() - started) * 1000

            response = ForecastResponse(
                request_id=request.request_id,
                forecast_id=report.forecast_id,
                status="success",
                cached=False,
                latency_ms=latency_ms,
                report=report.to_dict(),
            )

            if self.config.cache_enabled:
                self.cache.set(cache_key, response)

            with self._lock:
                self.metrics.total_forecasts += 1
                self.metrics.latency_ms_sum += latency_ms
                self.metrics.latency_ms_max = max(self.metrics.latency_ms_max, latency_ms)

            self._audit(
                "cashflow_forecast_generated",
                {
                    "request_id": request.request_id,
                    "forecast_id": response.forecast_id,
                    "tenant_id": request.tenant_id,
                    "entries": len(entries),
                    "horizon": request.horizon or self.config.default_horizon,
                    "latency_ms": latency_ms,
                },
            )

            return response

        except Exception as exc:
            self._error(exc)
            self._audit(
                "cashflow_forecast_error",
                {
                    "request_id": request.request_id,
                    "tenant_id": request.tenant_id,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
            )
            raise

    def health(self) -> Dict[str, Any]:
        status = ServiceStatus.OK

        if EnterpriseCashflowPredictor is Any:
            status = ServiceStatus.DEGRADED

        return {
            "service": self.config.service_name,
            "environment": self.config.environment,
            "status": status.value,
            "cache_enabled": self.config.cache_enabled,
            "metrics": self.metrics.snapshot(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def metrics_snapshot(self) -> Dict[str, float]:
        return self.metrics.snapshot()

    def _get_predictor(self) -> Any:
        if self.predictor is not None:
            return self.predictor

        if EnterpriseCashflowPredictor is Any:
            raise CashflowServiceError("EnterpriseCashflowPredictor indisponível.")

        self.predictor = EnterpriseCashflowPredictor(
            CashflowPredictorConfig(
                horizon=self.config.default_horizon,
            )
        )
        return self.predictor

    def _audit(self, event: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return

        self.audit_sink.write(
            {
                "event_id": str(uuid.uuid4()),
                "event": event,
                "service": self.config.service_name,
                "environment": self.config.environment,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **dict(payload),
            }
        )

    def _error(self, exc: Exception) -> None:
        with self._lock:
            self.metrics.total_errors += 1

    @staticmethod
    def _cache_key(request: ForecastRequest) -> str:
        payload = asdict(request)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def entry_from_dict(data: Mapping[str, Any]) -> CashflowEntry:
    return CashflowEntry(
        transaction_id=str(data.get("transaction_id") or data.get("id") or uuid.uuid4()),
        transaction_date=str(data["transaction_date"]),
        amount=float(data["amount"]),
        direction=TransactionDirection(data["direction"]),
        currency=str(data.get("currency", "BRL")),
        account_id=data.get("account_id"),
        category=data.get("category"),
        cost_center=data.get("cost_center"),
        description=data.get("description"),
        tenant_id=data.get("tenant_id"),
        metadata=dict(data.get("metadata", {})),
    )


def forecast_request_from_dict(data: Mapping[str, Any]) -> ForecastRequest:
    return ForecastRequest(
        request_id=str(data.get("request_id") or uuid.uuid4()),
        tenant_id=data.get("tenant_id"),
        account_id=data.get("account_id"),
        category=data.get("category"),
        cost_center=data.get("cost_center"),
        horizon=int(data["horizon"]) if data.get("horizon") is not None else None,
        opening_balance=float(data["opening_balance"]) if data.get("opening_balance") is not None else None,
        include_backtest=bool(data.get("include_backtest", False)),
        metadata=dict(data.get("metadata", {})),
    )


if __name__ == "__main__":
    from datetime import timedelta

    service = CashflowService(
        config=CashflowServiceConfig(
            environment="development",
            min_forecast_transactions=60,
            default_horizon=15,
        ),
        repository=InMemoryCashflowRepository(),
    )

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    for i in range(120):
        day = (start + timedelta(days=i)).date().isoformat()

        service.add_entry(
            CashflowEntry(
                transaction_id=f"in-{i}",
                transaction_date=day,
                amount=5000 + (i % 7) * 120,
                direction=TransactionDirection.INFLOW,
                tenant_id="digital-meta",
                category="sales",
            )
        )

        service.add_entry(
            CashflowEntry(
                transaction_id=f"out-{i}",
                transaction_date=day,
                amount=3000 + (i % 5) * 90,
                direction=TransactionDirection.OUTFLOW,
                tenant_id="digital-meta",
                category="operations",
            )
        )

    response = service.forecast(
        ForecastRequest(
            request_id="req-001",
            tenant_id="digital-meta",
            horizon=15,
            opening_balance=100_000,
        )
    )

    print(response.to_json())
    print(json.dumps(service.health(), indent=2, ensure_ascii=False))