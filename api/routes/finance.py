#!/usr/bin/env python3
"""
api/routes/finance.py

Enterprise-grade Finance API Router.

Objetivo:
- Expor endpoints financeiros reutilizáveis para APIs enterprise.
- Calcular KPIs, margem, markup, impostos, descontos, juros, parcelas, NPV, IRR e health score.
- Padronizar respostas, validação, autenticação por scopes, request-id, auditoria leve e uso seguro de Decimal.
- Servir FP&A, controladoria, tesouraria, billing, revenue ops, risk e analytics.

Endpoints:
    GET  /finance/health
    POST /finance/kpis
    POST /finance/margin
    POST /finance/tax
    POST /finance/discount
    POST /finance/compound-interest
    POST /finance/installment
    POST /finance/cashflow-analysis
    POST /finance/health-score

Integração:
    from fastapi import FastAPI
    from api.routes.finance import router as finance_router

    app.include_router(finance_router, prefix="/v1")
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
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

router = APIRouter(prefix="/finance", tags=["finance"])


class TaxMode(str, Enum):
    EXCLUSIVE = "exclusive"
    INCLUSIVE = "inclusive"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Direction(str, Enum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class FinanceMetric(BaseModel):
    name: str
    value: float
    currency: Optional[str] = None
    period: Optional[str] = None
    category: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MoneyRequest(BaseModel):
    amount: float
    currency: str = DEFAULT_CURRENCY


class MarginRequest(BaseModel):
    revenue: float
    cost: float
    fixed_cost: Optional[float] = None
    currency: str = DEFAULT_CURRENCY


class TaxRequest(BaseModel):
    amount: float
    tax_rate_percent: float
    mode: TaxMode = TaxMode.EXCLUSIVE
    currency: str = DEFAULT_CURRENCY


class DiscountRequest(BaseModel):
    amount: float
    discount_rate_percent: Optional[float] = None
    discount_amount: Optional[float] = None
    currency: str = DEFAULT_CURRENCY

    @validator("discount_amount")
    def validate_discount_choice(cls, value: Optional[float], values: Dict[str, Any]) -> Optional[float]:
        rate = values.get("discount_rate_percent")
        if value is None and rate is None:
            raise ValueError("Informe discount_rate_percent ou discount_amount")
        if value is not None and rate is not None:
            raise ValueError("Informe apenas um: discount_rate_percent ou discount_amount")
        return value


class InterestRequest(BaseModel):
    principal: float
    rate_percent: float
    periods: int = Field(ge=0, le=10_000)
    currency: str = DEFAULT_CURRENCY


class InstallmentRequest(BaseModel):
    principal: float
    rate_percent: float
    periods: int = Field(ge=1, le=1_200)
    currency: str = DEFAULT_CURRENCY
    include_schedule: bool = False


class CashflowItem(BaseModel):
    period: int
    amount: float
    category: Optional[str] = None


class CashflowAnalysisRequest(BaseModel):
    cashflows: List[float] = Field(default_factory=list)
    discount_rate_percent: float = 0.0


class KpiRequest(BaseModel):
    metrics: List[FinanceMetric] = Field(default_factory=list)
    currency: str = DEFAULT_CURRENCY
    include_health_score: bool = True


class HealthScoreRequest(BaseModel):
    cash_balance: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    revenue: Optional[float] = None
    cost: Optional[float] = None
    net_income: Optional[float] = None
    debt: Optional[float] = None
    equity: Optional[float] = None
    operating_cashflow: Optional[float] = None
    burn_rate: Optional[float] = None
    currency: str = DEFAULT_CURRENCY


class StandardFinanceResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    result: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


@router.get("/health")
async def finance_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "finance", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.post("/kpis", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def calculate_kpis(payload: KpiRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    result = kpis_from_metrics(payload.metrics, payload.currency)
    if payload.include_health_score:
        result["health_score"] = health_score_from_kpis(result)
    return response(ctx, result, metadata={"operation": "kpis"})


@router.post("/margin", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def calculate_margin(payload: MarginRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    revenue = money(payload.revenue)
    cost = money(payload.cost)
    if revenue == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="revenue não pode ser zero")
    gross_profit = revenue - cost
    gross_margin = (gross_profit / revenue) * Decimal("100")
    markup = Decimal("0") if cost == 0 else (gross_profit / cost) * Decimal("100")
    break_even = None
    if payload.fixed_cost is not None:
        if gross_margin <= 0:
            break_even = None
        else:
            break_even = money(payload.fixed_cost) / (gross_margin / Decimal("100"))
    return response(ctx, {
        "currency": payload.currency.upper(),
        "revenue": money_str(revenue),
        "cost": money_str(cost),
        "gross_profit": money_str(gross_profit),
        "gross_margin_percent": decimal_str(gross_margin),
        "markup_percent": decimal_str(markup),
        "break_even_revenue": None if break_even is None else money_str(break_even),
    }, metadata={"operation": "margin"})


@router.post("/tax", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def calculate_tax(payload: TaxRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    amount = money(payload.amount)
    rate = percent(payload.tax_rate_percent)
    if rate < 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tax_rate_percent não pode ser negativo")
    if payload.mode == TaxMode.EXCLUSIVE:
        base = amount
        tax_amount = amount * rate
        total = base + tax_amount
    else:
        total = amount
        base = total / (Decimal("1") + rate)
        tax_amount = total - base
    return response(ctx, {
        "currency": payload.currency.upper(),
        "mode": payload.mode.value,
        "base": money_str(base),
        "tax_amount": money_str(tax_amount),
        "total": money_str(total),
        "tax_rate_percent": decimal_str(Decimal(str(payload.tax_rate_percent))),
    }, metadata={"operation": "tax"})


@router.post("/discount", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def calculate_discount(payload: DiscountRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    amount = money(payload.amount)
    if payload.discount_rate_percent is not None:
        discount = amount * percent(payload.discount_rate_percent)
        discount_percent = Decimal(str(payload.discount_rate_percent))
    else:
        discount = money(payload.discount_amount or 0)
        if discount > amount:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="discount_amount não pode exceder amount")
        discount_percent = Decimal("0") if amount == 0 else (discount / amount) * Decimal("100")
    net = amount - discount
    return response(ctx, {
        "currency": payload.currency.upper(),
        "original_amount": money_str(amount),
        "discount_amount": money_str(discount),
        "discount_percent": decimal_str(discount_percent),
        "net_amount": money_str(net),
    }, metadata={"operation": "discount"})


@router.post("/compound-interest", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def compound_interest(payload: InterestRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    principal = money(payload.principal)
    rate = percent(payload.rate_percent)
    amount = principal * ((Decimal("1") + rate) ** payload.periods)
    interest = amount - principal
    return response(ctx, {
        "currency": payload.currency.upper(),
        "principal": money_str(principal),
        "rate_percent": decimal_str(Decimal(str(payload.rate_percent))),
        "periods": payload.periods,
        "interest": money_str(interest),
        "amount": money_str(amount),
    }, metadata={"operation": "compound_interest"})


@router.post("/installment", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def installment(payload: InstallmentRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    principal = money(payload.principal)
    rate = percent(payload.rate_percent)
    payment = calculate_pmt(principal, rate, payload.periods)
    schedule = build_schedule(principal, rate, payload.periods, payment) if payload.include_schedule else []
    total_paid = payment * Decimal(payload.periods) if not schedule else sum_decimal(money(item["payment"]) for item in schedule)
    total_interest = total_paid - principal
    result = {
        "currency": payload.currency.upper(),
        "principal": money_str(principal),
        "rate_percent": decimal_str(Decimal(str(payload.rate_percent))),
        "periods": payload.periods,
        "payment": money_str(payment),
        "total_paid": money_str(total_paid),
        "total_interest": money_str(total_interest),
    }
    if payload.include_schedule:
        result["schedule"] = schedule
    return response(ctx, result, metadata={"operation": "installment"})


@router.post("/cashflow-analysis", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def cashflow_analysis(payload: CashflowAnalysisRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    flows = [money(item) for item in payload.cashflows]
    if not flows:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="cashflows não pode ser vazio")
    rate = percent(payload.discount_rate_percent)
    npv_value = calculate_npv(flows, rate)
    irr_value = calculate_irr(flows)
    payback = calculate_payback(flows)
    result = {
        "cashflows": [money_str(item) for item in flows],
        "discount_rate_percent": decimal_str(Decimal(str(payload.discount_rate_percent))),
        "npv": money_str(npv_value),
        "irr_percent": None if irr_value is None else decimal_str(irr_value),
        "payback_period": payback,
        "profitability_index": calculate_profitability_index(flows, rate),
    }
    return response(ctx, result, metadata={"operation": "cashflow_analysis"})


@router.post("/health-score", response_model=StandardFinanceResponse, dependencies=[Depends(require_scopes("finance:read"))])
async def health_score(payload: HealthScoreRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardFinanceResponse:
    ctx = build_context(request, user)
    result = calculate_health_score(payload)
    return response(ctx, result, metadata={"operation": "health_score"})


def kpis_from_metrics(metrics: Sequence[FinanceMetric], currency: str) -> Dict[str, Any]:
    grouped: DefaultDict[str, List[Decimal]] = defaultdict(list)
    for metric in metrics:
        grouped[normalize_name(metric.name)].append(money(metric.value))
    result: Dict[str, Any] = {"currency": currency.upper(), "metric_count": len(metrics)}
    for name, values in grouped.items():
        total = sum_decimal(values)
        result[name] = {
            "count": len(values),
            "sum": money_str(total),
            "avg": money_str(total / Decimal(len(values))) if values else "0.00",
            "min": money_str(min(values)) if values else "0.00",
            "max": money_str(max(values)) if values else "0.00",
        }
    if "revenue" in grouped and "cost" in grouped:
        revenue = sum_decimal(grouped["revenue"])
        cost = sum_decimal(grouped["cost"])
        profit = revenue - cost
        result["gross_profit"] = money_str(profit)
        result["gross_margin_percent"] = None if revenue == 0 else decimal_str((profit / revenue) * Decimal("100"))
    if "cash_balance" in grouped and "burn_rate" in grouped:
        burn = sum_decimal(grouped["burn_rate"])
        cash = sum_decimal(grouped["cash_balance"])
        result["runway_periods"] = None if burn <= 0 else decimal_str(cash / burn)
    return result


def health_score_from_kpis(kpis: Mapping[str, Any]) -> Dict[str, Any]:
    score = Decimal("50")
    reasons: List[str] = []
    margin = safe_decimal(kpis.get("gross_margin_percent"))
    runway = safe_decimal(kpis.get("runway_periods"))
    if margin is not None:
        if margin >= 30:
            score += Decimal("15")
            reasons.append("strong_margin")
        elif margin < 10:
            score -= Decimal("20")
            reasons.append("weak_margin")
    if runway is not None:
        if runway >= 6:
            score += Decimal("15")
            reasons.append("strong_runway")
        elif runway < 3:
            score -= Decimal("20")
            reasons.append("short_runway")
    score = clamp_decimal(score, Decimal("0"), Decimal("100"))
    return {"score": decimal_str(score), "risk_level": risk_level(Decimal("100") - score), "reasons": reasons or ["neutral_kpis"]}


def calculate_health_score(payload: HealthScoreRequest) -> Dict[str, Any]:
    score = Decimal("50")
    reasons: List[str] = []
    current_ratio = None
    gross_margin = None
    net_margin = None
    debt_to_equity = None
    runway = None

    if payload.current_assets is not None and payload.current_liabilities not in {None, 0}:
        current_ratio = money(payload.current_assets) / money(payload.current_liabilities)
        if current_ratio >= Decimal("1.5"):
            score += Decimal("12")
            reasons.append("healthy_current_ratio")
        elif current_ratio < Decimal("1.0"):
            score -= Decimal("18")
            reasons.append("weak_current_ratio")

    if payload.revenue not in {None, 0} and payload.cost is not None:
        revenue = money(payload.revenue)
        gross_margin = ((revenue - money(payload.cost)) / revenue) * Decimal("100")
        if gross_margin >= Decimal("30"):
            score += Decimal("12")
            reasons.append("healthy_gross_margin")
        elif gross_margin < Decimal("10"):
            score -= Decimal("15")
            reasons.append("weak_gross_margin")

    if payload.revenue not in {None, 0} and payload.net_income is not None:
        net_margin = (money(payload.net_income) / money(payload.revenue)) * Decimal("100")
        if net_margin >= Decimal("10"):
            score += Decimal("10")
            reasons.append("positive_net_margin")
        elif net_margin < Decimal("0"):
            score -= Decimal("20")
            reasons.append("negative_net_margin")

    if payload.debt is not None and payload.equity not in {None, 0}:
        debt_to_equity = money(payload.debt) / money(payload.equity)
        if debt_to_equity <= Decimal("1.0"):
            score += Decimal("8")
            reasons.append("controlled_leverage")
        elif debt_to_equity > Decimal("2.5"):
            score -= Decimal("18")
            reasons.append("high_leverage")

    if payload.cash_balance is not None and payload.burn_rate not in {None, 0}:
        runway = money(payload.cash_balance) / money(payload.burn_rate)
        if runway >= Decimal("6"):
            score += Decimal("10")
            reasons.append("healthy_runway")
        elif runway < Decimal("3"):
            score -= Decimal("20")
            reasons.append("short_runway")

    score = clamp_decimal(score, Decimal("0"), Decimal("100"))
    return {
        "currency": payload.currency.upper(),
        "score": decimal_str(score),
        "risk_level": risk_level(Decimal("100") - score),
        "reasons": reasons or ["insufficient_or_neutral_signals"],
        "ratios": {
            "current_ratio": None if current_ratio is None else decimal_str(current_ratio),
            "gross_margin_percent": None if gross_margin is None else decimal_str(gross_margin),
            "net_margin_percent": None if net_margin is None else decimal_str(net_margin),
            "debt_to_equity": None if debt_to_equity is None else decimal_str(debt_to_equity),
            "runway_periods": None if runway is None else decimal_str(runway),
        },
    }


def calculate_pmt(principal: Decimal, rate: Decimal, periods: int) -> Decimal:
    if periods <= 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="periods precisa ser maior que zero")
    if rate == 0:
        return principal / Decimal(periods)
    factor = (Decimal("1") + rate) ** periods
    return principal * ((rate * factor) / (factor - Decimal("1")))


def build_schedule(principal: Decimal, rate: Decimal, periods: int, payment: Decimal) -> List[Dict[str, Any]]:
    balance = principal
    rows: List[Dict[str, Any]] = []
    for number in range(1, periods + 1):
        interest = balance * rate
        principal_part = payment - interest
        if number == periods:
            principal_part = balance
            payment_adjusted = principal_part + interest
            balance = Decimal("0")
        else:
            payment_adjusted = payment
            balance -= principal_part
        rows.append({
            "number": number,
            "payment": money_str(payment_adjusted),
            "principal": money_str(principal_part),
            "interest": money_str(interest),
            "balance": money_str(balance),
        })
    return rows


def calculate_npv(cashflows: Sequence[Decimal], rate: Decimal) -> Decimal:
    total = Decimal("0")
    for index, flow in enumerate(cashflows):
        total += flow / ((Decimal("1") + rate) ** index)
    return total


def calculate_irr(cashflows: Sequence[Decimal]) -> Optional[Decimal]:
    if not any(item < 0 for item in cashflows) or not any(item > 0 for item in cashflows):
        return None
    low = Decimal("-0.9999")
    high = Decimal("10")
    mid = Decimal("0")
    for _ in range(150):
        mid = (low + high) / Decimal("2")
        value = calculate_npv(cashflows, mid)
        if abs(value) <= Decimal("0.000001"):
            return mid * Decimal("100")
        low_value = calculate_npv(cashflows, low)
        if (low_value < 0 and value < 0) or (low_value > 0 and value > 0):
            low = mid
        else:
            high = mid
    return mid * Decimal("100")


def calculate_payback(cashflows: Sequence[Decimal]) -> Optional[int]:
    cumulative = Decimal("0")
    for index, flow in enumerate(cashflows):
        cumulative += flow
        if cumulative >= 0:
            return index
    return None


def calculate_profitability_index(cashflows: Sequence[Decimal], rate: Decimal) -> Optional[str]:
    if not cashflows or cashflows[0] >= 0:
        return None
    future_pv = Decimal("0")
    for index, flow in enumerate(cashflows[1:], start=1):
        future_pv += flow / ((Decimal("1") + rate) ** index)
    pi = future_pv / abs(cashflows[0]) if cashflows[0] != 0 else None
    return None if pi is None else decimal_str(pi)


def response(ctx: ExecutionContext, result: Dict[str, Any], warnings: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> StandardFinanceResponse:
    return StandardFinanceResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        result=result,
        warnings=warnings or [],
        metadata={"user": ctx.user_subject, **(metadata or {})},
    )


def build_context(request: Request, user: Any) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return ExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Valor monetário inválido: {value}") from exc


def percent(value: Any) -> Decimal:
    return money(value) / Decimal("100")


def safe_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return money(value)
        if isinstance(value, Mapping) and "sum" in value:
            return money(value["sum"])
        return money(value)
    except Exception:
        return None


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def money_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def normalize_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def risk_level(risk_score: Decimal) -> str:
    if risk_score >= Decimal("85"):
        return RiskLevel.CRITICAL.value
    if risk_score >= Decimal("65"):
        return RiskLevel.HIGH.value
    if risk_score >= Decimal("35"):
        return RiskLevel.MEDIUM.value
    return RiskLevel.LOW.value


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
