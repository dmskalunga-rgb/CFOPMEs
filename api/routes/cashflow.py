#!/usr/bin/env python3
"""
api/routes/cashflow.py

Enterprise-grade Cashflow API Router.

Objetivo:
- Expor endpoints HTTP para análise de fluxo de caixa.
- Calcular entradas, saídas, saldo inicial/final, net cashflow, burn rate, runway e gaps.
- Agregar por período, categoria, conta, centro de custo, departamento e contraparte.
- Aplicar validação Pydantic, autenticação por scopes, request-id, respostas padronizadas e auditoria leve.

Endpoints:
    GET  /cashflow/health
    POST /cashflow/analyze
    POST /cashflow/periods
    POST /cashflow/gaps
    POST /cashflow/dimensions
    POST /cashflow/forecast-simple

Integração:
    from fastapi import FastAPI
    from api.routes.cashflow import router as cashflow_router

    app.include_router(cashflow_router, prefix="/v1")
"""

from __future__ import annotations

import hashlib
import logging
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, validator

try:
    from api.auth.dependencies import CurrentUser, get_current_user, require_scopes
except Exception:  # pragma: no cover
    CurrentUser = Any  # type: ignore

    async def get_current_user() -> Any:  # type: ignore
        return {"subject": "auth-unavailable"}

    def require_scopes(*_: str, **__: Any) -> Any:  # type: ignore
        async def dependency() -> Any:
            return None

        return dependency


LOGGER = logging.getLogger(__name__)
ROUTER_VERSION = "1.0.0"
DEFAULT_CURRENCY = "BRL"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION

router = APIRouter(prefix="/cashflow", tags=["cashflow"])


class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class CashDirection(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class CashflowStatus(str, Enum):
    CONFIRMED = "confirmed"
    POSTED = "posted"
    SETTLED = "settled"
    PENDING = "pending"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CashflowEntryRequest(BaseModel):
    cashflow_id: str = Field(default_factory=lambda: f"cf_{uuid.uuid4().hex[:16]}")
    date: str
    amount: float
    currency: str = DEFAULT_CURRENCY
    direction: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    counterparty: Optional[str] = None
    account: Optional[str] = None
    cost_center: Optional[str] = None
    department: Optional[str] = None
    description: Optional[str] = None
    status: str = CashflowStatus.CONFIRMED.value
    reference_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CashflowAnalyzeRequest(BaseModel):
    entries: List[CashflowEntryRequest] = Field(default_factory=list)
    opening_balance: float = 0.0
    currency: str = DEFAULT_CURRENCY
    frequency: Frequency = Frequency.MONTHLY
    include_pending: bool = True
    ignore_cancelled: bool = True
    minimum_balance_floor: float = 0.0
    warning_balance_floor: float = 10_000.0
    concentration_warning_percent: float = 35.0


class ForecastRequest(CashflowAnalyzeRequest):
    horizon_periods: int = Field(default=6, ge=1, le=60)
    method: str = Field(default="moving_average", description="moving_average ou last_value")
    lookback_periods: int = Field(default=3, ge=1, le=24)


class PeriodMetric(BaseModel):
    period: str
    opening_balance: float
    inflow: float
    outflow: float
    net_cashflow: float
    closing_balance: float
    burn_rate: float
    runway_periods: Optional[float]
    transaction_count: int
    minimum_balance_breach: bool
    warning_balance_breach: bool


class DimensionMetric(BaseModel):
    dimension: str
    key: str
    inflow: float
    outflow: float
    net_cashflow: float
    transaction_count: int
    share_of_total_inflow_percent: float
    share_of_total_outflow_percent: float
    concentration_warning: bool


class CashflowGap(BaseModel):
    period: str
    balance: float
    floor: float
    gap_amount: float
    severity: str
    recommended_actions: List[str]


class CashflowSummary(BaseModel):
    currency: str
    record_count: int
    period_count: int
    opening_balance: float
    closing_balance: float
    total_inflow: float
    total_outflow: float
    net_cashflow: float
    avg_period_inflow: float
    avg_period_outflow: float
    avg_period_net_cashflow: float
    min_closing_balance: float
    max_closing_balance: float
    negative_cashflow_periods: int
    balance_breach_periods: int
    estimated_runway_periods: Optional[float]
    top_inflow_category: Optional[str]
    top_outflow_category: Optional[str]
    concentration_warning_count: int


class CashflowAnalyzeResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    summary: CashflowSummary
    periods: List[PeriodMetric]
    dimensions: List[DimensionMetric]
    gaps: List[CashflowGap]
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ForecastPoint(BaseModel):
    period: str
    projected_inflow: float
    projected_outflow: float
    projected_net_cashflow: float
    projected_closing_balance: float
    confidence: str


class CashflowForecastResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    base_summary: CashflowSummary
    forecast: List[ForecastPoint]
    warnings: List[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Entry:
    cashflow_id: str
    date_value: date
    amount: Decimal
    currency: str
    direction: CashDirection
    type: str
    category: str
    subcategory: str
    counterparty_hash: Optional[str]
    account_hash: Optional[str]
    cost_center: str
    department: str
    status: str

    @property
    def inflow(self) -> Decimal:
        return self.amount if self.direction == CashDirection.INFLOW else Decimal("0")

    @property
    def outflow(self) -> Decimal:
        return self.amount if self.direction == CashDirection.OUTFLOW else Decimal("0")

    @property
    def signed_amount(self) -> Decimal:
        return self.inflow - self.outflow


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


@router.get("/health")
async def cashflow_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "router": "cashflow",
        "version": ROUTER_VERSION,
        "timestamp": utc_now_iso(),
    }


@router.post("/analyze", response_model=CashflowAnalyzeResponse, dependencies=[Depends(require_scopes("cashflow:read"))])
async def analyze_cashflow(payload: CashflowAnalyzeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> CashflowAnalyzeResponse:
    ctx = build_context(request, user)
    result = calculate_cashflow(payload)
    return CashflowAnalyzeResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        summary=result["summary"],
        periods=result["periods"],
        dimensions=result["dimensions"],
        gaps=result["gaps"],
        warnings=result["warnings"],
        metadata={"user": ctx.user_subject, "operation": "analyze"},
    )


@router.post("/periods", response_model=List[PeriodMetric], dependencies=[Depends(require_scopes("cashflow:read"))])
async def cashflow_periods(payload: CashflowAnalyzeRequest) -> List[PeriodMetric]:
    return calculate_cashflow(payload)["periods"]


@router.post("/gaps", response_model=List[CashflowGap], dependencies=[Depends(require_scopes("cashflow:read"))])
async def cashflow_gaps(payload: CashflowAnalyzeRequest) -> List[CashflowGap]:
    return calculate_cashflow(payload)["gaps"]


@router.post("/dimensions", response_model=List[DimensionMetric], dependencies=[Depends(require_scopes("cashflow:read"))])
async def cashflow_dimensions(payload: CashflowAnalyzeRequest) -> List[DimensionMetric]:
    return calculate_cashflow(payload)["dimensions"]


@router.post("/forecast-simple", response_model=CashflowForecastResponse, dependencies=[Depends(require_scopes("cashflow:read"))])
async def forecast_cashflow(payload: ForecastRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> CashflowForecastResponse:
    ctx = build_context(request, user)
    result = calculate_cashflow(payload)
    forecast = simple_forecast(payload, result["periods"], result["summary"].closing_balance)
    return CashflowForecastResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        base_summary=result["summary"],
        forecast=forecast,
        warnings=result["warnings"],
    )


def calculate_cashflow(payload: CashflowAnalyzeRequest) -> Dict[str, Any]:
    entries = parse_entries(payload)
    if not entries:
        empty_summary = CashflowSummary(
            currency=payload.currency.upper(), record_count=0, period_count=0,
            opening_balance=payload.opening_balance, closing_balance=payload.opening_balance,
            total_inflow=0, total_outflow=0, net_cashflow=0,
            avg_period_inflow=0, avg_period_outflow=0, avg_period_net_cashflow=0,
            min_closing_balance=payload.opening_balance, max_closing_balance=payload.opening_balance,
            negative_cashflow_periods=0, balance_breach_periods=0,
            estimated_runway_periods=None, top_inflow_category=None, top_outflow_category=None,
            concentration_warning_count=0,
        )
        return {"summary": empty_summary, "periods": [], "dimensions": [], "gaps": [], "warnings": ["empty_entries"]}

    periods = build_period_metrics(entries, payload)
    dimensions = build_dimension_metrics(entries, payload)
    gaps = build_gaps(periods, payload)
    summary = build_summary(entries, periods, dimensions, payload)
    warnings = build_warnings(entries, periods, payload)
    return {"summary": summary, "periods": periods, "dimensions": dimensions, "gaps": gaps, "warnings": warnings}


def parse_entries(payload: CashflowAnalyzeRequest) -> List[Entry]:
    entries: List[Entry] = []
    errors: List[str] = []
    wanted_currency = payload.currency.upper()
    for index, raw in enumerate(payload.entries, start=1):
        try:
            status_value = (raw.status or "unknown").lower()
            if payload.ignore_cancelled and status_value in {"cancelled", "canceled", "cancelado"}:
                continue
            if not payload.include_pending and status_value in {"pending", "pendente"}:
                continue
            if raw.currency.upper() != wanted_currency:
                continue
            amount = Decimal(str(raw.amount)).copy_abs()
            entries.append(
                Entry(
                    cashflow_id=raw.cashflow_id,
                    date_value=parse_date(raw.date),
                    amount=amount,
                    currency=raw.currency.upper(),
                    direction=parse_direction(raw.direction, Decimal(str(raw.amount))),
                    type=normalize(raw.type, "unknown"),
                    category=normalize(raw.category, "unknown"),
                    subcategory=normalize(raw.subcategory, "unknown"),
                    counterparty_hash=hash_identifier(raw.counterparty) if raw.counterparty else None,
                    account_hash=hash_identifier(raw.account) if raw.account else None,
                    cost_center=normalize(raw.cost_center, "unknown"),
                    department=normalize(raw.department, "unknown"),
                    status=status_value,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"entry={index}: {exc}")
    if errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "invalid_cashflow_entries", "errors": errors[:30]})
    return sorted(entries, key=lambda item: (item.date_value, item.cashflow_id))


def build_period_metrics(entries: Sequence[Entry], payload: CashflowAnalyzeRequest) -> List[PeriodMetric]:
    grouped: DefaultDict[str, List[Entry]] = defaultdict(list)
    for entry in entries:
        grouped[period_label(entry.date_value, payload.frequency)].append(entry)

    balance = Decimal(str(payload.opening_balance))
    result: List[PeriodMetric] = []
    for period in sorted(grouped):
        rows = grouped[period]
        inflow = sum_decimal(row.inflow for row in rows)
        outflow = sum_decimal(row.outflow for row in rows)
        net = inflow - outflow
        closing = balance + net
        burn = outflow - inflow if outflow > inflow else Decimal("0")
        runway = None if burn <= 0 else closing / burn
        result.append(
            PeriodMetric(
                period=period,
                opening_balance=to_float(balance),
                inflow=to_float(inflow),
                outflow=to_float(outflow),
                net_cashflow=to_float(net),
                closing_balance=to_float(closing),
                burn_rate=to_float(burn),
                runway_periods=None if runway is None else to_float(runway),
                transaction_count=len(rows),
                minimum_balance_breach=closing < Decimal(str(payload.minimum_balance_floor)),
                warning_balance_breach=closing < Decimal(str(payload.warning_balance_floor)),
            )
        )
        balance = closing
    return result


def build_dimension_metrics(entries: Sequence[Entry], payload: CashflowAnalyzeRequest) -> List[DimensionMetric]:
    dimensions = ["type", "category", "subcategory", "counterparty_hash", "account_hash", "cost_center", "department", "status"]
    total_inflow = sum_decimal(entry.inflow for entry in entries) or Decimal("1")
    total_outflow = sum_decimal(entry.outflow for entry in entries) or Decimal("1")
    threshold = Decimal(str(payload.concentration_warning_percent))
    result: List[DimensionMetric] = []
    for dimension in dimensions:
        grouped: DefaultDict[str, List[Entry]] = defaultdict(list)
        for entry in entries:
            key = str(getattr(entry, dimension) or "unknown")
            grouped[key].append(entry)
        for key, rows in grouped.items():
            inflow = sum_decimal(row.inflow for row in rows)
            outflow = sum_decimal(row.outflow for row in rows)
            inflow_share = (inflow / total_inflow) * Decimal("100")
            outflow_share = (outflow / total_outflow) * Decimal("100")
            result.append(
                DimensionMetric(
                    dimension=dimension,
                    key=key,
                    inflow=to_float(inflow),
                    outflow=to_float(outflow),
                    net_cashflow=to_float(inflow - outflow),
                    transaction_count=len(rows),
                    share_of_total_inflow_percent=to_float(inflow_share),
                    share_of_total_outflow_percent=to_float(outflow_share),
                    concentration_warning=max(inflow_share, outflow_share) >= threshold,
                )
            )
    return sorted(result, key=lambda item: (item.dimension, -(item.inflow + item.outflow)))


def build_gaps(periods: Sequence[PeriodMetric], payload: CashflowAnalyzeRequest) -> List[CashflowGap]:
    gaps: List[CashflowGap] = []
    minimum = Decimal(str(payload.minimum_balance_floor))
    warning = Decimal(str(payload.warning_balance_floor))
    for period in periods:
        closing = Decimal(str(period.closing_balance))
        if closing < minimum:
            gap = minimum - closing
            severity = "critical" if closing < 0 else "high"
            actions = ["secure_short_term_funding", "accelerate_receivables", "delay_discretionary_outflows", "review_cash_control_tower"]
            gaps.append(CashflowGap(period=period.period, balance=period.closing_balance, floor=to_float(minimum), gap_amount=to_float(gap), severity=severity, recommended_actions=actions))
        elif closing < warning:
            gap = warning - closing
            actions = ["monitor_cash_daily", "review_payment_calendar", "prioritize_collections"]
            gaps.append(CashflowGap(period=period.period, balance=period.closing_balance, floor=to_float(warning), gap_amount=to_float(gap), severity="medium", recommended_actions=actions))
    return gaps


def build_summary(entries: Sequence[Entry], periods: Sequence[PeriodMetric], dimensions: Sequence[DimensionMetric], payload: CashflowAnalyzeRequest) -> CashflowSummary:
    opening = Decimal(str(payload.opening_balance))
    total_inflow = sum_decimal(entry.inflow for entry in entries)
    total_outflow = sum_decimal(entry.outflow for entry in entries)
    net = total_inflow - total_outflow
    closing = opening + net
    period_inflows = [Decimal(str(item.inflow)) for item in periods]
    period_outflows = [Decimal(str(item.outflow)) for item in periods]
    period_nets = [Decimal(str(item.net_cashflow)) for item in periods]
    closing_balances = [Decimal(str(item.closing_balance)) for item in periods] or [opening]
    categories = [item for item in dimensions if item.dimension == "category"]
    top_inflow = max(categories, key=lambda item: item.inflow).key if categories else None
    top_outflow = max(categories, key=lambda item: item.outflow).key if categories else None
    recent_burn = [Decimal(str(item.burn_rate)) for item in periods[-3:] if item.burn_rate > 0]
    avg_burn = mean_decimal(recent_burn)
    runway = None if avg_burn <= 0 else closing / avg_burn
    return CashflowSummary(
        currency=payload.currency.upper(),
        record_count=len(entries),
        period_count=len(periods),
        opening_balance=to_float(opening),
        closing_balance=to_float(closing),
        total_inflow=to_float(total_inflow),
        total_outflow=to_float(total_outflow),
        net_cashflow=to_float(net),
        avg_period_inflow=to_float(mean_decimal(period_inflows)),
        avg_period_outflow=to_float(mean_decimal(period_outflows)),
        avg_period_net_cashflow=to_float(mean_decimal(period_nets)),
        min_closing_balance=to_float(min(closing_balances)),
        max_closing_balance=to_float(max(closing_balances)),
        negative_cashflow_periods=sum(1 for item in periods if item.net_cashflow < 0),
        balance_breach_periods=sum(1 for item in periods if item.minimum_balance_breach or item.warning_balance_breach),
        estimated_runway_periods=None if runway is None else to_float(runway),
        top_inflow_category=top_inflow,
        top_outflow_category=top_outflow,
        concentration_warning_count=sum(1 for item in dimensions if item.concentration_warning),
    )


def simple_forecast(payload: ForecastRequest, periods: Sequence[PeriodMetric], starting_balance: float) -> List[ForecastPoint]:
    if not periods:
        return []
    lookback = periods[-payload.lookback_periods:]
    if payload.method == "last_value":
        inflow = Decimal(str(lookback[-1].inflow))
        outflow = Decimal(str(lookback[-1].outflow))
    else:
        inflow = mean_decimal([Decimal(str(item.inflow)) for item in lookback])
        outflow = mean_decimal([Decimal(str(item.outflow)) for item in lookback])
    balance = Decimal(str(starting_balance))
    result: List[ForecastPoint] = []
    last_period = periods[-1].period
    for index in range(1, payload.horizon_periods + 1):
        net = inflow - outflow
        balance += net
        result.append(
            ForecastPoint(
                period=f"forecast+{index}",
                projected_inflow=to_float(inflow),
                projected_outflow=to_float(outflow),
                projected_net_cashflow=to_float(net),
                projected_closing_balance=to_float(balance),
                confidence="medium" if len(lookback) >= 3 else "low",
            )
        )
    return result


def build_warnings(entries: Sequence[Entry], periods: Sequence[PeriodMetric], payload: CashflowAnalyzeRequest) -> List[str]:
    warnings: List[str] = []
    if not entries:
        warnings.append("empty_entries")
    if len(entries) > 100_000:
        warnings.append("large_payload_consider_async_processing")
    if any(item.minimum_balance_breach for item in periods):
        warnings.append("minimum_balance_breach_detected")
    if any(item.warning_balance_breach for item in periods):
        warnings.append("warning_balance_breach_detected")
    return warnings


def build_context(request: Request, user: Any) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return ExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def parse_date(value: str) -> date:
    text = value.strip().replace("Z", "+00:00")
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"data inválida: {value}") from exc


def parse_direction(value: Optional[str], amount: Decimal) -> CashDirection:
    if value:
        text = value.strip().lower()
        if text in {"inflow", "credit", "entrada", "receita", "credito", "crédito"}:
            return CashDirection.INFLOW
        if text in {"outflow", "debit", "saida", "saída", "despesa", "debito", "débito"}:
            return CashDirection.OUTFLOW
    return CashDirection.INFLOW if amount >= 0 else CashDirection.OUTFLOW


def period_label(value: date, frequency: Frequency) -> str:
    if frequency == Frequency.DAILY:
        return value.isoformat()
    if frequency == Frequency.WEEKLY:
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if frequency == Frequency.MONTHLY:
        return f"{value.year:04d}-{value.month:02d}"
    if frequency == Frequency.QUARTERLY:
        quarter = ((value.month - 1) // 3) + 1
        return f"{value.year:04d}-Q{quarter}"
    if frequency == Frequency.YEARLY:
        return f"{value.year:04d}"
    return value.isoformat()


def normalize(value: Optional[str], default: str = "unknown") -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower().replace(" ", "_")


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum_decimal(values) / Decimal(len(values))


def to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
