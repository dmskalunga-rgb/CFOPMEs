#!/usr/bin/env python3
"""
api/routes/revenue.py

Enterprise-grade Revenue API Router.

Objetivo:
- Expor endpoints HTTP para análise de receita em nível enterprise.
- Calcular MRR, ARR, ARPU, churn, expansion, contraction, NRR, GRR, receita por período,
  cohort simplificado, segmentação por canal/plano/cliente e forecast básico.
- Detectar anomalias de queda de receita, concentração, churn elevado e variação período contra período.
- Aplicar validação Pydantic, autenticação por scopes, request-id, respostas padronizadas e Decimal.

Endpoints:
    GET  /revenue/health
    POST /revenue/analyze
    POST /revenue/kpis
    POST /revenue/segments
    POST /revenue/cohorts
    POST /revenue/anomalies
    POST /revenue/forecast-simple

Integração:
    from fastapi import FastAPI
    from api.routes.revenue import router as revenue_router

    app.include_router(revenue_router, prefix="/v1")
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

router = APIRouter(prefix="/revenue", tags=["revenue"])


class RevenueEventType(str, Enum):
    NEW = "new"
    RENEWAL = "renewal"
    EXPANSION = "expansion"
    CONTRACTION = "contraction"
    CHURN = "churn"
    REFUND = "refund"
    ONE_TIME = "one_time"
    REACTIVATION = "reactivation"


class BillingFrequency(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ONE_TIME = "one_time"


class TimeGrain(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RevenueEventRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"rev_{uuid.uuid4().hex[:16]}")
    customer_id: str
    date: str
    amount: float
    currency: str = DEFAULT_CURRENCY
    event_type: RevenueEventType = RevenueEventType.NEW
    billing_frequency: BillingFrequency = BillingFrequency.MONTHLY
    plan: Optional[str] = None
    product: Optional[str] = None
    channel: Optional[str] = None
    region: Optional[str] = None
    segment: Optional[str] = None
    sales_rep: Optional[str] = None
    invoice_id: Optional[str] = None
    subscription_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RevenuePolicyRequest(BaseModel):
    currency: str = DEFAULT_CURRENCY
    grain: TimeGrain = TimeGrain.MONTHLY
    churn_alert_percent: float = 8.0
    revenue_drop_alert_percent: float = 15.0
    concentration_alert_percent: float = 35.0
    nrr_warning_percent: float = 100.0
    grr_warning_percent: float = 85.0
    anonymize_customer_ids: bool = True


class RevenueAnalyzeRequest(BaseModel):
    events: List[RevenueEventRequest] = Field(default_factory=list)
    policy: RevenuePolicyRequest = Field(default_factory=RevenuePolicyRequest)
    include_segments: bool = True
    include_cohorts: bool = True
    include_anomalies: bool = True

    @validator("events")
    def validate_event_count(cls, value: List[RevenueEventRequest]) -> List[RevenueEventRequest]:
        if len(value) > 100_000:
            raise ValueError("events excede limite de 100.000")
        return value


class SegmentRequest(BaseModel):
    events: List[RevenueEventRequest] = Field(default_factory=list)
    policy: RevenuePolicyRequest = Field(default_factory=RevenuePolicyRequest)
    dimension: str = "channel"
    top_n: int = Field(default=20, ge=1, le=500)


class ForecastRequest(RevenueAnalyzeRequest):
    horizon_periods: int = Field(default=6, ge=1, le=36)
    lookback_periods: int = Field(default=3, ge=1, le=24)
    method: str = Field(default="moving_average", description="moving_average ou last_value")


class RevenuePeriodMetric(BaseModel):
    period: str
    revenue: float
    recurring_revenue: float
    one_time_revenue: float
    new_revenue: float
    expansion_revenue: float
    contraction_revenue: float
    churn_revenue: float
    refund_amount: float
    net_revenue: float
    mrr: float
    arr: float
    active_customers: int
    new_customers: int
    churned_customers: int
    arpu: float
    churn_rate_percent: float
    nrr_percent: Optional[float]
    grr_percent: Optional[float]
    event_count: int


class RevenueSegmentMetric(BaseModel):
    dimension: str
    key: str
    revenue: float
    net_revenue: float
    customers: int
    event_count: int
    share_percent: float
    concentration_warning: bool


class RevenueCohortMetric(BaseModel):
    cohort: str
    customers: int
    initial_revenue: float
    current_revenue: float
    retained_revenue: float
    retention_percent: float
    expansion_revenue: float
    churn_revenue: float


class RevenueAnomaly(BaseModel):
    anomaly_id: str
    severity: RiskLevel
    anomaly_type: str
    period: Optional[str]
    description: str
    impact_amount: float
    recommended_actions: List[str]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RevenueSummary(BaseModel):
    currency: str
    event_count: int
    period_count: int
    customer_count: int
    total_revenue: float
    net_revenue: float
    recurring_revenue: float
    one_time_revenue: float
    mrr: float
    arr: float
    arpu: float
    new_revenue: float
    expansion_revenue: float
    contraction_revenue: float
    churn_revenue: float
    refund_amount: float
    churn_rate_percent: float
    nrr_percent: Optional[float]
    grr_percent: Optional[float]
    top_channel: Optional[str]
    top_plan: Optional[str]
    concentration_warning_count: int


class RevenueForecastPoint(BaseModel):
    period: str
    projected_revenue: float
    projected_net_revenue: float
    projected_mrr: float
    projected_arr: float
    confidence: str


class StandardRevenueResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    result: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class RevenueEvent:
    event_id: str
    customer_id: str
    customer_hash: str
    date_value: date
    period: str
    amount: Decimal
    currency: str
    event_type: RevenueEventType
    billing_frequency: BillingFrequency
    plan: str
    product: str
    channel: str
    region: str
    segment: str
    sales_rep_hash: Optional[str]
    invoice_hash: Optional[str]
    subscription_hash: Optional[str]

    @property
    def mrr_equivalent(self) -> Decimal:
        if self.billing_frequency == BillingFrequency.MONTHLY:
            return self.amount
        if self.billing_frequency == BillingFrequency.QUARTERLY:
            return self.amount / Decimal("3")
        if self.billing_frequency == BillingFrequency.ANNUAL:
            return self.amount / Decimal("12")
        return Decimal("0")

    @property
    def signed_net_revenue(self) -> Decimal:
        if self.event_type in {RevenueEventType.CHURN, RevenueEventType.REFUND, RevenueEventType.CONTRACTION}:
            return -abs(self.amount)
        return self.amount


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


@router.get("/health")
async def revenue_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "revenue", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.post("/analyze", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def analyze_revenue(payload: RevenueAnalyzeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    analysis = calculate_revenue(payload.events, payload.policy)
    result = {
        "summary": analysis["summary"].dict(),
        "periods": [item.dict() for item in analysis["periods"]],
    }
    if payload.include_segments:
        result["segments"] = [item.dict() for item in analysis["segments"]]
    if payload.include_cohorts:
        result["cohorts"] = [item.dict() for item in analysis["cohorts"]]
    if payload.include_anomalies:
        result["anomalies"] = [item.dict() for item in analysis["anomalies"]]
    return response(ctx, result, warnings=analysis["warnings"], metadata={"operation": "analyze"})


@router.post("/kpis", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def revenue_kpis(payload: RevenueAnalyzeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    analysis = calculate_revenue(payload.events, payload.policy)
    return response(ctx, {"summary": analysis["summary"].dict(), "periods": [item.dict() for item in analysis["periods"]]}, warnings=analysis["warnings"], metadata={"operation": "kpis"})


@router.post("/segments", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def revenue_segments(payload: SegmentRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    events = parse_events(payload.events, payload.policy)
    segments = build_segments(events, payload.policy, dimensions=[payload.dimension])[: payload.top_n]
    return response(ctx, {"segments": [item.dict() for item in segments]}, metadata={"operation": "segments", "dimension": payload.dimension})


@router.post("/cohorts", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def revenue_cohorts(payload: RevenueAnalyzeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    events = parse_events(payload.events, payload.policy)
    cohorts = build_cohorts(events)
    return response(ctx, {"cohorts": [item.dict() for item in cohorts]}, metadata={"operation": "cohorts"})


@router.post("/anomalies", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def revenue_anomalies(payload: RevenueAnalyzeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    analysis = calculate_revenue(payload.events, payload.policy)
    anomalies = analysis["anomalies"]
    return response(ctx, {"anomaly_count": len(anomalies), "anomalies": [item.dict() for item in anomalies]}, warnings=["no_anomalies_detected"] if not anomalies else [], metadata={"operation": "anomalies"})


@router.post("/forecast-simple", response_model=StandardRevenueResponse, dependencies=[Depends(require_scopes("revenue:read"))])
async def revenue_forecast(payload: ForecastRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardRevenueResponse:
    ctx = build_context(request, user)
    analysis = calculate_revenue(payload.events, payload.policy)
    forecast = build_forecast(analysis["periods"], payload)
    return response(ctx, {"summary": analysis["summary"].dict(), "forecast": [item.dict() for item in forecast]}, warnings=analysis["warnings"], metadata={"operation": "forecast_simple"})


def calculate_revenue(raw_events: Sequence[RevenueEventRequest], policy: RevenuePolicyRequest) -> Dict[str, Any]:
    events = parse_events(raw_events, policy)
    periods = build_periods(events, policy)
    segments = build_segments(events, policy)
    cohorts = build_cohorts(events)
    summary = build_summary(events, periods, segments, policy)
    anomalies = build_anomalies(periods, segments, summary, policy)
    warnings = build_warnings(events, periods, anomalies)
    return {"summary": summary, "periods": periods, "segments": segments, "cohorts": cohorts, "anomalies": anomalies, "warnings": warnings}


def parse_events(raw_events: Sequence[RevenueEventRequest], policy: RevenuePolicyRequest) -> List[RevenueEvent]:
    parsed: List[RevenueEvent] = []
    errors: List[str] = []
    currency = policy.currency.upper()
    for index, raw in enumerate(raw_events, start=1):
        try:
            if raw.currency.upper() != currency:
                continue
            date_value = parse_date(raw.date)
            customer_hash = hash_identifier(raw.customer_id) if policy.anonymize_customer_ids else raw.customer_id
            parsed.append(
                RevenueEvent(
                    event_id=raw.event_id,
                    customer_id=raw.customer_id,
                    customer_hash=customer_hash,
                    date_value=date_value,
                    period=period_label(date_value, policy.grain),
                    amount=money(raw.amount),
                    currency=raw.currency.upper(),
                    event_type=raw.event_type,
                    billing_frequency=raw.billing_frequency,
                    plan=normalize(raw.plan, "unknown"),
                    product=normalize(raw.product, "unknown"),
                    channel=normalize(raw.channel, "unknown"),
                    region=normalize(raw.region, "unknown"),
                    segment=normalize(raw.segment, "unknown"),
                    sales_rep_hash=hash_identifier(raw.sales_rep) if raw.sales_rep else None,
                    invoice_hash=hash_identifier(raw.invoice_id) if raw.invoice_id else None,
                    subscription_hash=hash_identifier(raw.subscription_id) if raw.subscription_id else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"event={index}: {exc}")
    if errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": "invalid_revenue_events", "errors": errors[:30]})
    return sorted(parsed, key=lambda item: (item.date_value, item.event_id))


def build_periods(events: Sequence[RevenueEvent], policy: RevenuePolicyRequest) -> List[RevenuePeriodMetric]:
    grouped: DefaultDict[str, List[RevenueEvent]] = defaultdict(list)
    for event in events:
        grouped[event.period].append(event)

    active_customers: set[str] = set()
    result: List[RevenuePeriodMetric] = []
    previous_recurring = Decimal("0")

    for period in sorted(grouped):
        rows = grouped[period]
        new_customers = {event.customer_hash for event in rows if event.event_type in {RevenueEventType.NEW, RevenueEventType.REACTIVATION}}
        churned_customers = {event.customer_hash for event in rows if event.event_type == RevenueEventType.CHURN}
        active_customers |= new_customers
        active_customers -= churned_customers

        revenue = sum_decimal(event.amount for event in rows if event.event_type != RevenueEventType.REFUND)
        recurring = sum_decimal(event.mrr_equivalent for event in rows if event.billing_frequency != BillingFrequency.ONE_TIME and event.event_type not in {RevenueEventType.CHURN, RevenueEventType.REFUND})
        one_time = sum_decimal(event.amount for event in rows if event.billing_frequency == BillingFrequency.ONE_TIME or event.event_type == RevenueEventType.ONE_TIME)
        new_revenue = sum_decimal(event.amount for event in rows if event.event_type in {RevenueEventType.NEW, RevenueEventType.REACTIVATION})
        expansion = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.EXPANSION)
        contraction = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.CONTRACTION)
        churn = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.CHURN)
        refund = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.REFUND)
        net = revenue + expansion - contraction - churn - refund
        mrr = recurring + expansion - contraction - churn
        arr = mrr * Decimal("12")
        active_count = len(active_customers)
        arpu = Decimal("0") if active_count == 0 else mrr / Decimal(active_count)
        churn_rate = Decimal("0") if active_count + len(churned_customers) == 0 else Decimal(len(churned_customers)) / Decimal(active_count + len(churned_customers)) * Decimal("100")

        if previous_recurring > 0:
            nrr = ((previous_recurring + expansion - contraction - churn) / previous_recurring) * Decimal("100")
            grr = ((previous_recurring - contraction - churn) / previous_recurring) * Decimal("100")
        else:
            nrr = None
            grr = None

        result.append(
            RevenuePeriodMetric(
                period=period,
                revenue=to_float(revenue),
                recurring_revenue=to_float(recurring),
                one_time_revenue=to_float(one_time),
                new_revenue=to_float(new_revenue),
                expansion_revenue=to_float(expansion),
                contraction_revenue=to_float(contraction),
                churn_revenue=to_float(churn),
                refund_amount=to_float(refund),
                net_revenue=to_float(net),
                mrr=to_float(mrr),
                arr=to_float(arr),
                active_customers=active_count,
                new_customers=len(new_customers),
                churned_customers=len(churned_customers),
                arpu=to_float(arpu),
                churn_rate_percent=to_float(churn_rate),
                nrr_percent=None if nrr is None else to_float(nrr),
                grr_percent=None if grr is None else to_float(grr),
                event_count=len(rows),
            )
        )
        previous_recurring = max(mrr, Decimal("0"))
    return result


def build_segments(events: Sequence[RevenueEvent], policy: RevenuePolicyRequest, dimensions: Optional[Sequence[str]] = None) -> List[RevenueSegmentMetric]:
    dimensions = list(dimensions or ["channel", "plan", "product", "region", "segment"])
    total_net = sum_decimal(event.signed_net_revenue for event in events) or Decimal("1")
    result: List[RevenueSegmentMetric] = []
    for dimension in dimensions:
        grouped: DefaultDict[str, List[RevenueEvent]] = defaultdict(list)
        for event in events:
            key = str(getattr(event, dimension, "unknown") or "unknown")
            grouped[key].append(event)
        for key, rows in grouped.items():
            revenue = sum_decimal(event.amount for event in rows if event.event_type not in {RevenueEventType.CHURN, RevenueEventType.REFUND, RevenueEventType.CONTRACTION})
            net = sum_decimal(event.signed_net_revenue for event in rows)
            share = Decimal("0") if total_net == 0 else (net / abs(total_net)) * Decimal("100")
            result.append(
                RevenueSegmentMetric(
                    dimension=dimension,
                    key=key,
                    revenue=to_float(revenue),
                    net_revenue=to_float(net),
                    customers=len({event.customer_hash for event in rows}),
                    event_count=len(rows),
                    share_percent=to_float(share),
                    concentration_warning=abs(share) >= Decimal(str(policy.concentration_alert_percent)),
                )
            )
    return sorted(result, key=lambda item: abs(item.net_revenue), reverse=True)


def build_cohorts(events: Sequence[RevenueEvent]) -> List[RevenueCohortMetric]:
    first_period_by_customer: Dict[str, str] = {}
    for event in events:
        if event.event_type in {RevenueEventType.NEW, RevenueEventType.REACTIVATION}:
            first_period_by_customer.setdefault(event.customer_hash, event.period)
    grouped: DefaultDict[str, List[RevenueEvent]] = defaultdict(list)
    for event in events:
        cohort = first_period_by_customer.get(event.customer_hash)
        if cohort:
            grouped[cohort].append(event)
    result: List[RevenueCohortMetric] = []
    for cohort, rows in grouped.items():
        customers = {event.customer_hash for event in rows}
        initial = sum_decimal(event.amount for event in rows if event.period == cohort and event.event_type in {RevenueEventType.NEW, RevenueEventType.REACTIVATION})
        expansion = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.EXPANSION)
        churn = sum_decimal(event.amount for event in rows if event.event_type == RevenueEventType.CHURN)
        current = sum_decimal(event.signed_net_revenue for event in rows)
        retained = max(current - expansion, Decimal("0"))
        retention = Decimal("0") if initial == 0 else (retained / initial) * Decimal("100")
        result.append(
            RevenueCohortMetric(
                cohort=cohort,
                customers=len(customers),
                initial_revenue=to_float(initial),
                current_revenue=to_float(current),
                retained_revenue=to_float(retained),
                retention_percent=to_float(retention),
                expansion_revenue=to_float(expansion),
                churn_revenue=to_float(churn),
            )
        )
    return sorted(result, key=lambda item: item.cohort)


def build_summary(events: Sequence[RevenueEvent], periods: Sequence[RevenuePeriodMetric], segments: Sequence[RevenueSegmentMetric], policy: RevenuePolicyRequest) -> RevenueSummary:
    total_revenue = sum_decimal(event.amount for event in events if event.event_type not in {RevenueEventType.REFUND})
    net_revenue = sum_decimal(event.signed_net_revenue for event in events)
    recurring = sum_decimal(event.mrr_equivalent for event in events if event.billing_frequency != BillingFrequency.ONE_TIME and event.event_type not in {RevenueEventType.CHURN, RevenueEventType.REFUND})
    one_time = sum_decimal(event.amount for event in events if event.billing_frequency == BillingFrequency.ONE_TIME or event.event_type == RevenueEventType.ONE_TIME)
    new_revenue = sum_decimal(event.amount for event in events if event.event_type in {RevenueEventType.NEW, RevenueEventType.REACTIVATION})
    expansion = sum_decimal(event.amount for event in events if event.event_type == RevenueEventType.EXPANSION)
    contraction = sum_decimal(event.amount for event in events if event.event_type == RevenueEventType.CONTRACTION)
    churn = sum_decimal(event.amount for event in events if event.event_type == RevenueEventType.CHURN)
    refund = sum_decimal(event.amount for event in events if event.event_type == RevenueEventType.REFUND)
    last_period = periods[-1] if periods else None
    customers = {event.customer_hash for event in events}
    top_channel = next((item.key for item in segments if item.dimension == "channel"), None)
    top_plan = next((item.key for item in segments if item.dimension == "plan"), None)
    return RevenueSummary(
        currency=policy.currency.upper(),
        event_count=len(events),
        period_count=len(periods),
        customer_count=len(customers),
        total_revenue=to_float(total_revenue),
        net_revenue=to_float(net_revenue),
        recurring_revenue=to_float(recurring),
        one_time_revenue=to_float(one_time),
        mrr=last_period.mrr if last_period else 0,
        arr=last_period.arr if last_period else 0,
        arpu=last_period.arpu if last_period else 0,
        new_revenue=to_float(new_revenue),
        expansion_revenue=to_float(expansion),
        contraction_revenue=to_float(contraction),
        churn_revenue=to_float(churn),
        refund_amount=to_float(refund),
        churn_rate_percent=last_period.churn_rate_percent if last_period else 0,
        nrr_percent=last_period.nrr_percent if last_period else None,
        grr_percent=last_period.grr_percent if last_period else None,
        top_channel=top_channel,
        top_plan=top_plan,
        concentration_warning_count=sum(1 for item in segments if item.concentration_warning),
    )


def build_anomalies(periods: Sequence[RevenuePeriodMetric], segments: Sequence[RevenueSegmentMetric], summary: RevenueSummary, policy: RevenuePolicyRequest) -> List[RevenueAnomaly]:
    anomalies: List[RevenueAnomaly] = []
    for previous, current in zip(periods, periods[1:]):
        prev_net = money(previous.net_revenue)
        curr_net = money(current.net_revenue)
        if prev_net > 0:
            drop = ((prev_net - curr_net) / prev_net) * Decimal("100")
            if drop >= Decimal(str(policy.revenue_drop_alert_percent)):
                severity = RiskLevel.HIGH if drop >= Decimal(str(policy.revenue_drop_alert_percent * 2)) else RiskLevel.MEDIUM
                anomalies.append(
                    RevenueAnomaly(
                        anomaly_id=f"rev_anom_{uuid.uuid4().hex[:16]}",
                        severity=severity,
                        anomaly_type="period_revenue_drop",
                        period=current.period,
                        description="Queda relevante de receita líquida versus período anterior.",
                        impact_amount=to_float(prev_net - curr_net),
                        recommended_actions=["review_pipeline_and_billing", "inspect_churn_and_refunds", "analyze_channel_or_plan_drop"],
                        metadata={"drop_percent": decimal_str(drop), "previous_period": previous.period},
                    )
                )
        if current.churn_rate_percent >= policy.churn_alert_percent:
            anomalies.append(
                RevenueAnomaly(
                    anomaly_id=f"rev_anom_{uuid.uuid4().hex[:16]}",
                    severity=RiskLevel.HIGH,
                    anomaly_type="high_churn_rate",
                    period=current.period,
                    description="Churn rate acima da política definida.",
                    impact_amount=current.churn_revenue,
                    recommended_actions=["review_churned_accounts", "trigger_retention_playbook", "inspect_product_or_support_causes"],
                    metadata={"churn_rate_percent": current.churn_rate_percent},
                )
            )
        if current.nrr_percent is not None and current.nrr_percent < policy.nrr_warning_percent:
            anomalies.append(
                RevenueAnomaly(
                    anomaly_id=f"rev_anom_{uuid.uuid4().hex[:16]}",
                    severity=RiskLevel.MEDIUM,
                    anomaly_type="nrr_below_target",
                    period=current.period,
                    description="NRR abaixo do alvo mínimo.",
                    impact_amount=current.churn_revenue + current.contraction_revenue,
                    recommended_actions=["review_expansion_pipeline", "reduce_contraction", "launch_customer_success_intervention"],
                    metadata={"nrr_percent": current.nrr_percent},
                )
            )
        if current.grr_percent is not None and current.grr_percent < policy.grr_warning_percent:
            anomalies.append(
                RevenueAnomaly(
                    anomaly_id=f"rev_anom_{uuid.uuid4().hex[:16]}",
                    severity=RiskLevel.MEDIUM,
                    anomaly_type="grr_below_target",
                    period=current.period,
                    description="GRR abaixo do alvo mínimo.",
                    impact_amount=current.churn_revenue + current.contraction_revenue,
                    recommended_actions=["review_base_retention", "inspect_pricing_or_downgrade_causes"],
                    metadata={"grr_percent": current.grr_percent},
                )
            )
    for segment in segments:
        if segment.concentration_warning:
            anomalies.append(
                RevenueAnomaly(
                    anomaly_id=f"rev_anom_{uuid.uuid4().hex[:16]}",
                    severity=RiskLevel.MEDIUM,
                    anomaly_type="revenue_concentration",
                    period=None,
                    description=f"Concentração relevante de receita em {segment.dimension}={segment.key}.",
                    impact_amount=segment.net_revenue,
                    recommended_actions=["diversify_revenue_mix", "monitor_concentration_risk", "review_dependence_on_segment"],
                    metadata={"dimension": segment.dimension, "key": segment.key, "share_percent": segment.share_percent},
                )
            )
    return anomalies


def build_forecast(periods: Sequence[RevenuePeriodMetric], payload: ForecastRequest) -> List[RevenueForecastPoint]:
    if not periods:
        return []
    lookback = periods[-payload.lookback_periods:]
    if payload.method == "last_value":
        revenue = money(lookback[-1].revenue)
        net = money(lookback[-1].net_revenue)
        mrr = money(lookback[-1].mrr)
    else:
        revenue = mean_decimal([money(item.revenue) for item in lookback])
        net = mean_decimal([money(item.net_revenue) for item in lookback])
        mrr = mean_decimal([money(item.mrr) for item in lookback])
    result: List[RevenueForecastPoint] = []
    for index in range(1, payload.horizon_periods + 1):
        result.append(
            RevenueForecastPoint(
                period=f"forecast+{index}",
                projected_revenue=to_float(revenue),
                projected_net_revenue=to_float(net),
                projected_mrr=to_float(mrr),
                projected_arr=to_float(mrr * Decimal("12")),
                confidence="medium" if len(lookback) >= 3 else "low",
            )
        )
    return result


def build_warnings(events: Sequence[RevenueEvent], periods: Sequence[RevenuePeriodMetric], anomalies: Sequence[RevenueAnomaly]) -> List[str]:
    warnings: List[str] = []
    if not events:
        warnings.append("empty_revenue_events")
    if any(item.anomaly_type == "high_churn_rate" for item in anomalies):
        warnings.append("high_churn_detected")
    if any(item.anomaly_type == "period_revenue_drop" for item in anomalies):
        warnings.append("period_revenue_drop_detected")
    if len(events) > 50_000:
        warnings.append("large_payload_consider_async_processing")
    return warnings


def response(ctx: ExecutionContext, result: Dict[str, Any], warnings: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> StandardRevenueResponse:
    return StandardRevenueResponse(
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


def parse_date(value: str) -> date:
    text = value.strip().replace("Z", "+00:00")
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"data inválida: {value}") from exc


def period_label(value: date, grain: TimeGrain) -> str:
    if grain == TimeGrain.MONTHLY:
        return f"{value.year:04d}-{value.month:02d}"
    if grain == TimeGrain.QUARTERLY:
        quarter = ((value.month - 1) // 3) + 1
        return f"{value.year:04d}-Q{quarter}"
    if grain == TimeGrain.YEARLY:
        return f"{value.year:04d}"
    return f"{value.year:04d}-{value.month:02d}"


def normalize(value: Optional[str], default: str = "unknown") -> str:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower().replace(" ", "_")


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"valor decimal inválido: {value}") from exc


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum_decimal(values) / Decimal(len(values))


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()

