#!/usr/bin/env python3
"""
api/routes/analytics.py

Enterprise-grade Analytics API Router.

Objetivo:
- Expor endpoints analíticos padronizados para KPIs, séries temporais, segmentações e insights.
- Servir dados para dashboards, BI, FP&A, risco, operações e produtos.
- Aplicar padrões enterprise: validação Pydantic, request-id, auth/scopes, tratamento de erros,
  paginação, filtros, agregações, auditoria leve e respostas consistentes.

Endpoints:
    GET  /analytics/health
    POST /analytics/kpis
    POST /analytics/timeseries
    POST /analytics/segments
    POST /analytics/insights
    POST /analytics/query

Integração:
    from fastapi import FastAPI
    from api.routes.analytics import router as analytics_router

    app = FastAPI()
    app.include_router(analytics_router, prefix="/v1")

Notas:
- Este router é dependency-light e pode rodar sem banco, usando payloads enviados na request.
- Em produção, substitua AnalyticsRepository por implementação SQL/Data Warehouse.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION

router = APIRouter(prefix="/analytics", tags=["analytics"])


class Aggregation(str, Enum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    MEDIAN = "median"
    P95 = "p95"


class TimeGrain(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class InsightSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AnalyticsRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:16]}")
    timestamp: Optional[str] = None
    entity_id: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    dimensions: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MetricSpec(BaseModel):
    name: str
    field: str = "metric_value"
    aggregation: Aggregation = Aggregation.SUM
    label: Optional[str] = None


class FilterSpec(BaseModel):
    field: str
    op: str = Field(default="eq", description="eq, ne, gt, gte, lt, lte, in, contains")
    value: Any


class SortSpec(BaseModel):
    field: str
    direction: SortDirection = SortDirection.DESC


class AnalyticsRequest(BaseModel):
    records: List[AnalyticsRecord] = Field(default_factory=list)
    metrics: List[MetricSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    sort: List[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)
    include_totals: bool = True


class TimeSeriesRequest(AnalyticsRequest):
    timestamp_field: str = "timestamp"
    grain: TimeGrain = TimeGrain.MONTHLY
    fill_missing: bool = False


class SegmentRequest(AnalyticsRequest):
    segment_field: str
    metric: MetricSpec = Field(default_factory=lambda: MetricSpec(name="value", field="metric_value", aggregation=Aggregation.SUM))
    top_n: int = Field(default=20, ge=1, le=1000)
    include_other: bool = True


class InsightRequest(AnalyticsRequest):
    baseline_records: List[AnalyticsRecord] = Field(default_factory=list)
    sensitivity: float = Field(default=1.5, ge=0.1, le=10)
    min_abs_delta: float = 0
    min_percent_delta: float = 10


class QueryRequest(BaseModel):
    records: List[AnalyticsRecord] = Field(default_factory=list)
    query_name: str = "custom_query"
    metrics: List[MetricSpec] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    grain: Optional[TimeGrain] = None
    limit: int = Field(default=100, ge=1, le=10_000)


class AnalyticsPoint(BaseModel):
    key: Dict[str, Any] = Field(default_factory=dict)
    values: Dict[str, Any] = Field(default_factory=dict)
    record_count: int


class AnalyticsResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    total_records: int
    returned_records: int
    data: List[AnalyticsPoint]
    totals: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InsightItem(BaseModel):
    insight_id: str
    severity: InsightSeverity
    title: str
    description: str
    metric: str
    current_value: Optional[float] = None
    baseline_value: Optional[float] = None
    delta: Optional[float] = None
    delta_percent: Optional[float] = None
    recommended_actions: List[str] = Field(default_factory=list)
    dimensions: Dict[str, Any] = Field(default_factory=dict)


class InsightResponse(BaseModel):
    request_id: str
    status: str
    version: str
    latency_ms: float
    insight_count: int
    insights: List[InsightItem]
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class AnalyticsExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


class AnalyticsRepository:
    """Repository placeholder. In production, replace with SQL/Warehouse adapter."""

    def normalize_records(self, records: Sequence[AnalyticsRecord]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for record in records:
            row = record.dict()
            for key, value in record.dimensions.items():
                row[f"dimensions.{key}"] = value
                row[key] = value
            for key, value in record.metadata.items():
                row[f"metadata.{key}"] = value
            normalized.append(row)
        return normalized


repository = AnalyticsRepository()


@router.get("/health")
async def analytics_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "router": "analytics",
        "version": ROUTER_VERSION,
        "timestamp": utc_now_iso(),
    }


@router.post("/kpis", response_model=AnalyticsResponse, dependencies=[Depends(require_scopes("analytics:read"))])
async def calculate_kpis(request: AnalyticsRequest, http_request: Request, user: CurrentUser = Depends(get_current_user)) -> AnalyticsResponse:
    ctx = build_context(http_request, user)
    rows = repository.normalize_records(request.records)
    rows = apply_filters(rows, request.filters)
    metrics = request.metrics or [MetricSpec(name="value", field="metric_value", aggregation=Aggregation.SUM)]
    data = aggregate_rows(rows, metrics, request.group_by)
    data = sort_points(data, request.sort)
    total_count = len(data)
    paged = data[request.offset : request.offset + request.limit]
    totals = calculate_totals(rows, metrics) if request.include_totals else {}
    return AnalyticsResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        total_records=total_count,
        returned_records=len(paged),
        data=[point_to_model(item) for item in paged],
        totals=totals,
        warnings=build_warnings(request.records, rows),
        metadata={"user": ctx.user_subject, "operation": "kpis"},
    )


@router.post("/timeseries", response_model=AnalyticsResponse, dependencies=[Depends(require_scopes("analytics:read"))])
async def calculate_timeseries(request: TimeSeriesRequest, http_request: Request, user: CurrentUser = Depends(get_current_user)) -> AnalyticsResponse:
    ctx = build_context(http_request, user)
    rows = repository.normalize_records(request.records)
    rows = apply_filters(rows, request.filters)
    metrics = request.metrics or [MetricSpec(name="value", field="metric_value", aggregation=Aggregation.SUM)]

    for row in rows:
        row["__period"] = period_label(row.get(request.timestamp_field), request.grain)

    group_by = ["__period"] + [field for field in request.group_by if field != "__period"]
    data = aggregate_rows(rows, metrics, group_by)
    data = sorted(data, key=lambda item: str(item["key"].get("__period", "")))
    total_count = len(data)
    paged = data[request.offset : request.offset + request.limit]

    return AnalyticsResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        total_records=total_count,
        returned_records=len(paged),
        data=[point_to_model(item) for item in paged],
        totals=calculate_totals(rows, metrics) if request.include_totals else {},
        warnings=build_warnings(request.records, rows),
        metadata={"user": ctx.user_subject, "operation": "timeseries", "grain": request.grain.value},
    )


@router.post("/segments", response_model=AnalyticsResponse, dependencies=[Depends(require_scopes("analytics:read"))])
async def calculate_segments(request: SegmentRequest, http_request: Request, user: CurrentUser = Depends(get_current_user)) -> AnalyticsResponse:
    ctx = build_context(http_request, user)
    rows = repository.normalize_records(request.records)
    rows = apply_filters(rows, request.filters)
    data = aggregate_rows(rows, [request.metric], [request.segment_field])
    data = sorted(data, key=lambda item: decimal_from_any(item["values"].get(request.metric.name, 0)), reverse=True)

    top = data[: request.top_n]
    if request.include_other and len(data) > request.top_n:
        other_rows = data[request.top_n :]
        other_value = sum(decimal_from_any(item["values"].get(request.metric.name, 0)) for item in other_rows)
        other_count = sum(int(item["record_count"]) for item in other_rows)
        top.append({"key": {request.segment_field: "other"}, "values": {request.metric.name: decimal_to_number(other_value)}, "record_count": other_count})

    return AnalyticsResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        total_records=len(data),
        returned_records=len(top),
        data=[point_to_model(item) for item in top],
        totals=calculate_totals(rows, [request.metric]),
        warnings=build_warnings(request.records, rows),
        metadata={"user": ctx.user_subject, "operation": "segments", "segment_field": request.segment_field},
    )


@router.post("/insights", response_model=InsightResponse, dependencies=[Depends(require_scopes("analytics:read"))])
async def generate_insights(request: InsightRequest, http_request: Request, user: CurrentUser = Depends(get_current_user)) -> InsightResponse:
    ctx = build_context(http_request, user)
    current_rows = apply_filters(repository.normalize_records(request.records), request.filters)
    baseline_rows = apply_filters(repository.normalize_records(request.baseline_records), request.filters) if request.baseline_records else []
    metrics = request.metrics or [MetricSpec(name="value", field="metric_value", aggregation=Aggregation.SUM)]
    insights = build_insights(current_rows, baseline_rows, metrics, request)
    return InsightResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        insight_count=len(insights),
        insights=insights,
        metadata={"user": ctx.user_subject, "operation": "insights"},
    )


@router.post("/query", response_model=AnalyticsResponse, dependencies=[Depends(require_scopes("analytics:read"))])
async def analytics_query(request: QueryRequest, http_request: Request, user: CurrentUser = Depends(get_current_user)) -> AnalyticsResponse:
    ctx = build_context(http_request, user)
    rows = apply_filters(repository.normalize_records(request.records), request.filters)
    metrics = request.metrics or [MetricSpec(name="value", field="metric_value", aggregation=Aggregation.SUM)]
    group_by = list(request.group_by)
    if request.grain:
        for row in rows:
            row["__period"] = period_label(row.get("timestamp"), request.grain)
        group_by = ["__period"] + group_by
    data = aggregate_rows(rows, metrics, group_by)
    data = data[: request.limit]
    return AnalyticsResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        latency_ms=elapsed_ms(ctx.started_at),
        total_records=len(data),
        returned_records=len(data),
        data=[point_to_model(item) for item in data],
        totals=calculate_totals(rows, metrics),
        warnings=build_warnings(request.records, rows),
        metadata={"user": ctx.user_subject, "operation": request.query_name},
    )


def build_context(request: Request, user: Any) -> AnalyticsExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return AnalyticsExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def apply_filters(rows: Sequence[Dict[str, Any]], filters: Sequence[FilterSpec]) -> List[Dict[str, Any]]:
    result = list(rows)
    for filter_spec in filters:
        result = [row for row in result if match_filter(row, filter_spec)]
    return result


def match_filter(row: Mapping[str, Any], filter_spec: FilterSpec) -> bool:
    actual = get_field(row, filter_spec.field)
    expected = filter_spec.value
    op = filter_spec.op.lower()
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if op in {"gt", "gte", "lt", "lte"}:
        left = decimal_from_any(actual)
        right = decimal_from_any(expected)
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        return left <= right
    if op == "in":
        return actual in expected if isinstance(expected, list) else actual == expected
    if op == "contains":
        return str(expected).lower() in str(actual).lower()
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Operador de filtro inválido: {filter_spec.op}")


def aggregate_rows(rows: Sequence[Dict[str, Any]], metrics: Sequence[MetricSpec], group_by: Sequence[str]) -> List[Dict[str, Any]]:
    grouped: DefaultDict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    if group_by:
        for row in rows:
            key = tuple(get_field(row, field) for field in group_by)
            grouped[key].append(row)
    else:
        grouped[tuple()].extend(rows)

    output: List[Dict[str, Any]] = []
    for key_tuple, group in grouped.items():
        key_payload = {field: key_tuple[index] for index, field in enumerate(group_by)}
        values = {metric.name: aggregate_metric(group, metric) for metric in metrics}
        output.append({"key": key_payload, "values": values, "record_count": len(group)})
    return output


def aggregate_metric(rows: Sequence[Mapping[str, Any]], metric: MetricSpec) -> Any:
    values = [get_field(row, metric.field) for row in rows]
    clean_values = [value for value in values if value is not None]
    if metric.aggregation == Aggregation.COUNT:
        return len(rows)
    if metric.aggregation == Aggregation.DISTINCT_COUNT:
        return len({json.dumps(value, sort_keys=True, default=str) for value in clean_values})
    numeric = [decimal_from_any(value) for value in clean_values if is_decimal_like(value)]
    if not numeric:
        return 0
    if metric.aggregation == Aggregation.SUM:
        return decimal_to_number(sum(numeric, Decimal("0")))
    if metric.aggregation == Aggregation.AVG:
        return decimal_to_number(sum(numeric, Decimal("0")) / Decimal(len(numeric)))
    if metric.aggregation == Aggregation.MIN:
        return decimal_to_number(min(numeric))
    if metric.aggregation == Aggregation.MAX:
        return decimal_to_number(max(numeric))
    if metric.aggregation == Aggregation.MEDIAN:
        return decimal_to_number(median_decimal(numeric))
    if metric.aggregation == Aggregation.P95:
        return decimal_to_number(percentile_decimal(numeric, 95))
    return 0


def calculate_totals(rows: Sequence[Dict[str, Any]], metrics: Sequence[MetricSpec]) -> Dict[str, Any]:
    return {metric.name: aggregate_metric(rows, metric) for metric in metrics}


def sort_points(points: Sequence[Dict[str, Any]], sort_specs: Sequence[SortSpec]) -> List[Dict[str, Any]]:
    result = list(points)
    for spec in reversed(sort_specs):
        reverse = spec.direction == SortDirection.DESC
        result.sort(key=lambda item: sort_value(item, spec.field), reverse=reverse)
    return result


def sort_value(point: Mapping[str, Any], field: str) -> Any:
    if field.startswith("key."):
        return point.get("key", {}).get(field[4:])
    if field.startswith("values."):
        return point.get("values", {}).get(field[7:])
    return point.get(field)


def build_insights(
    current_rows: Sequence[Dict[str, Any]],
    baseline_rows: Sequence[Dict[str, Any]],
    metrics: Sequence[MetricSpec],
    request: InsightRequest,
) -> List[InsightItem]:
    insights: List[InsightItem] = []
    current_totals = calculate_totals(current_rows, metrics)
    baseline_totals = calculate_totals(baseline_rows, metrics) if baseline_rows else {}

    for metric in metrics:
        current = decimal_from_any(current_totals.get(metric.name, 0))
        baseline = decimal_from_any(baseline_totals.get(metric.name, 0)) if baseline_totals else None
        if baseline is None:
            if current == 0:
                continue
            insights.append(
                InsightItem(
                    insight_id=f"ins_{uuid.uuid4().hex[:16]}",
                    severity=InsightSeverity.INFO,
                    title=f"Métrica {metric.name} calculada",
                    description=f"Valor atual de {metric.name}: {decimal_to_number(current)}.",
                    metric=metric.name,
                    current_value=float(current),
                    recommended_actions=["monitor_metric_trend"],
                )
            )
            continue

        delta = current - baseline
        delta_percent = Decimal("0") if baseline == 0 else (delta / abs(baseline)) * Decimal("100")
        if abs(delta) < Decimal(str(request.min_abs_delta)) and abs(delta_percent) < Decimal(str(request.min_percent_delta)):
            continue
        severity = severity_from_delta(delta_percent, request.sensitivity)
        direction = "aumentou" if delta > 0 else "caiu"
        insights.append(
            InsightItem(
                insight_id=f"ins_{uuid.uuid4().hex[:16]}",
                severity=severity,
                title=f"{metric.name} {direction}",
                description=f"{metric.name} {direction} {decimal_str(delta_percent)}% versus baseline.",
                metric=metric.name,
                current_value=float(current),
                baseline_value=float(baseline),
                delta=float(delta),
                delta_percent=float(delta_percent),
                recommended_actions=recommend_actions(metric.name, delta_percent),
            )
        )
    return sorted(insights, key=lambda item: severity_rank(item.severity), reverse=True)


def point_to_model(point: Mapping[str, Any]) -> AnalyticsPoint:
    return AnalyticsPoint(key=dict(point.get("key", {})), values=dict(point.get("values", {})), record_count=int(point.get("record_count", 0)))


def get_field(row: Mapping[str, Any], field: str) -> Any:
    if field in row:
        return row[field]
    current: Any = row
    for part in field.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def period_label(value: Any, grain: TimeGrain) -> str:
    parsed = parse_date(value)
    if parsed is None:
        return "unknown"
    if grain == TimeGrain.DAILY:
        return parsed.isoformat()
    if grain == TimeGrain.WEEKLY:
        year, week, _ = parsed.isocalendar()
        return f"{year}-W{week:02d}"
    if grain == TimeGrain.MONTHLY:
        return f"{parsed.year:04d}-{parsed.month:02d}"
    if grain == TimeGrain.QUARTERLY:
        quarter = ((parsed.month - 1) // 3) + 1
        return f"{parsed.year:04d}-Q{quarter}"
    if grain == TimeGrain.YEARLY:
        return f"{parsed.year:04d}"
    return "unknown"


def parse_date(value: Any) -> Optional[date]:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def is_decimal_like(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        Decimal(str(value).replace(",", "."))
        return True
    except (InvalidOperation, ValueError):
        return False


def decimal_from_any(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def percentile_decimal(values: Sequence[Decimal], percent: int) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = Decimal(len(ordered) - 1) * Decimal(percent) / Decimal("100")
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - Decimal(lower)
    return ordered[lower] * (Decimal("1") - weight) + ordered[upper] * weight


def decimal_to_number(value: Decimal) -> Any:
    quantized = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return int(quantized)
    return float(quantized)


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def build_warnings(original_records: Sequence[AnalyticsRecord], filtered_rows: Sequence[Dict[str, Any]]) -> List[str]:
    warnings: List[str] = []
    if not original_records:
        warnings.append("empty_input_records")
    elif not filtered_rows:
        warnings.append("filters_returned_no_records")
    if len(original_records) > 100_000:
        warnings.append("large_payload_consider_async_or_warehouse_query")
    return warnings


def severity_from_delta(delta_percent: Decimal, sensitivity: float) -> InsightSeverity:
    value = abs(delta_percent)
    factor = Decimal(str(sensitivity))
    if value >= Decimal("75") / factor:
        return InsightSeverity.CRITICAL
    if value >= Decimal("40") / factor:
        return InsightSeverity.HIGH
    if value >= Decimal("20") / factor:
        return InsightSeverity.MEDIUM
    if value >= Decimal("10") / factor:
        return InsightSeverity.LOW
    return InsightSeverity.INFO


def severity_rank(severity: InsightSeverity) -> int:
    return {
        InsightSeverity.INFO: 0,
        InsightSeverity.LOW: 1,
        InsightSeverity.MEDIUM: 2,
        InsightSeverity.HIGH: 3,
        InsightSeverity.CRITICAL: 4,
    }[severity]


def recommend_actions(metric_name: str, delta_percent: Decimal) -> List[str]:
    name = metric_name.lower()
    negative = delta_percent < 0
    if "revenue" in name and negative:
        return ["review_revenue_pipeline", "inspect_customer_or_channel_drop", "validate_billing_data"]
    if "cost" in name and not negative:
        return ["review_cost_drivers", "compare_budget_variance", "prioritize_savings_actions"]
    if "cash" in name and negative:
        return ["review_cash_position", "accelerate_collections", "prioritize_payments"]
    if "margin" in name and negative:
        return ["review_pricing", "analyze_cost_structure", "prepare_margin_recovery_plan"]
    return ["monitor_trend", "review_underlying_records"]


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
