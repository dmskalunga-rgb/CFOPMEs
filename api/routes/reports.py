#!/usr/bin/env python3
"""
api/routes/reports.py

Enterprise-grade Reports API Router.

Objetivo:
- Expor endpoints HTTP para geração, catálogo, histórico e exportação de relatórios.
- Suportar relatórios de finance, cashflow, payroll, fraud, analytics, NLP e documentos.
- Aplicar templates, filtros, agrupamentos, agregações, ordenação, paginação e auditoria leve.
- Retornar JSON ou CSV sem dependências externas obrigatórias.
- Integrar com FastAPI, autenticação por scopes, request-id e respostas padronizadas.

Endpoints:
    GET    /reports/health
    GET    /reports/catalog
    POST   /reports/generate
    GET    /reports/history
    GET    /reports/{report_id}
    GET    /reports/{report_id}/export
    DELETE /reports/{report_id}
    GET    /reports/stats/summary

Integração:
    from fastapi import FastAPI
    from api.routes.reports import router as reports_router

    app.include_router(reports_router, prefix="/v1")

Notas:
- Este router usa repositório em memória para ser plug-and-play.
- Em produção, substitua ReportRepository por banco, data warehouse ou object storage.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
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
MAX_REPORT_ROWS = 100_000
MAX_STORED_REPORTS = 1_000

getcontext().prec = DEFAULT_PRECISION

router = APIRouter(prefix="/reports", tags=["reports"])


class ReportDomain(str, Enum):
    FINANCE = "finance"
    CASHFLOW = "cashflow"
    PAYROLL = "payroll"
    FRAUD = "fraud"
    ANALYTICS = "analytics"
    NLP = "nlp"
    DOCUMENTS = "documents"
    OPERATIONS = "operations"
    CUSTOM = "custom"


class ReportFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class Aggregation(str, Enum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    DISTINCT_COUNT = "distinct_count"
    MEDIAN = "median"
    P95 = "p95"


class ReportStatus(str, Enum):
    GENERATED = "generated"
    FAILED = "failed"
    DELETED = "deleted"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class FilterSpec(BaseModel):
    field: str
    op: str = Field(default="eq", description="eq, ne, gt, gte, lt, lte, in, contains, exists")
    value: Any = None


class MetricSpec(BaseModel):
    name: str
    field: str
    aggregation: Aggregation = Aggregation.SUM
    label: Optional[str] = None


class SortSpec(BaseModel):
    field: str
    direction: SortDirection = SortDirection.DESC


class ReportTemplate(BaseModel):
    template_id: str
    name: str
    domain: ReportDomain
    description: str
    default_metrics: List[MetricSpec] = Field(default_factory=list)
    default_group_by: List[str] = Field(default_factory=list)
    default_filters: List[FilterSpec] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class ReportGenerateRequest(BaseModel):
    title: str = "Custom Report"
    domain: ReportDomain = ReportDomain.CUSTOM
    template_id: Optional[str] = None
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: List[MetricSpec] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    sort: List[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=10_000, ge=1, le=MAX_REPORT_ROWS)
    offset: int = Field(default=0, ge=0)
    include_detail_rows: bool = False
    include_summary: bool = True
    include_facets: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("rows")
    def validate_rows(cls, value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(value) > MAX_REPORT_ROWS:
            raise ValueError(f"rows excede limite de {MAX_REPORT_ROWS}")
        return value


class ReportRecord(BaseModel):
    report_id: str
    title: str
    domain: ReportDomain
    status: ReportStatus
    row_count: int
    generated_by: str
    generated_at: str
    latency_ms: float
    content_hash: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ReportGenerateResponse(BaseModel):
    request_id: str
    status: str
    version: str
    report: ReportRecord
    summary: Dict[str, Any] = Field(default_factory=dict)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    detail_rows: List[Dict[str, Any]] = Field(default_factory=list)
    facets: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class ReportHistoryResponse(BaseModel):
    request_id: str
    status: str
    total: int
    returned: int
    reports: List[ReportRecord]


class ReportStatsResponse(BaseModel):
    status: str
    version: str
    total_reports: int
    generated_reports: int
    failed_reports: int
    deleted_reports: int
    by_domain: Dict[str, int]
    avg_latency_ms: float
    avg_row_count: float


@dataclass
class StoredReport:
    record: ReportRecord
    summary: Dict[str, Any]
    data: List[Dict[str, Any]]
    detail_rows: List[Dict[str, Any]]
    facets: Dict[str, Dict[str, int]]
    audit: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


class ReportRepository:
    def __init__(self) -> None:
        self.reports: Dict[str, StoredReport] = {}

    def save(self, report: StoredReport) -> None:
        if len(self.reports) >= MAX_STORED_REPORTS:
            oldest = sorted(self.reports.values(), key=lambda item: item.record.generated_at)[0]
            self.reports.pop(oldest.record.report_id, None)
        self.reports[report.record.report_id] = report

    def get(self, report_id: str) -> Optional[StoredReport]:
        return self.reports.get(report_id)

    def list(self, domain: Optional[ReportDomain] = None, include_deleted: bool = False) -> List[StoredReport]:
        rows = list(self.reports.values())
        if domain:
            rows = [item for item in rows if item.record.domain == domain]
        if not include_deleted:
            rows = [item for item in rows if item.record.status != ReportStatus.DELETED]
        return sorted(rows, key=lambda item: item.record.generated_at, reverse=True)

    def delete(self, report_id: str, actor: str) -> bool:
        item = self.reports.get(report_id)
        if not item:
            return False
        item.record.status = ReportStatus.DELETED
        item.audit.append({"audit_id": f"aud_{uuid.uuid4().hex[:16]}", "action": "deleted", "actor": actor, "created_at": utc_now_iso()})
        return True


repository = ReportRepository()


TEMPLATES: Dict[str, ReportTemplate] = {
    "finance_summary": ReportTemplate(
        template_id="finance_summary",
        name="Finance Summary",
        domain=ReportDomain.FINANCE,
        description="Resumo financeiro com receita, custo, margem e lucro.",
        default_metrics=[
            MetricSpec(name="revenue", field="revenue", aggregation=Aggregation.SUM),
            MetricSpec(name="cost", field="cost", aggregation=Aggregation.SUM),
            MetricSpec(name="profit", field="profit", aggregation=Aggregation.SUM),
        ],
        default_group_by=["period"],
        tags=["finance", "kpi"],
    ),
    "cashflow_summary": ReportTemplate(
        template_id="cashflow_summary",
        name="Cashflow Summary",
        domain=ReportDomain.CASHFLOW,
        description="Resumo de entradas, saídas e saldo por período.",
        default_metrics=[
            MetricSpec(name="inflow", field="inflow", aggregation=Aggregation.SUM),
            MetricSpec(name="outflow", field="outflow", aggregation=Aggregation.SUM),
            MetricSpec(name="net_cashflow", field="net_cashflow", aggregation=Aggregation.SUM),
        ],
        default_group_by=["period"],
        tags=["cashflow", "treasury"],
    ),
    "payroll_cost": ReportTemplate(
        template_id="payroll_cost",
        name="Payroll Cost",
        domain=ReportDomain.PAYROLL,
        description="Custo de folha por departamento ou centro de custo.",
        default_metrics=[
            MetricSpec(name="gross_pay", field="gross_pay", aggregation=Aggregation.SUM),
            MetricSpec(name="net_pay", field="net_pay", aggregation=Aggregation.SUM),
            MetricSpec(name="total_employer_cost", field="total_employer_cost", aggregation=Aggregation.SUM),
        ],
        default_group_by=["department"],
        tags=["payroll", "cost"],
    ),
    "fraud_risk": ReportTemplate(
        template_id="fraud_risk",
        name="Fraud Risk",
        domain=ReportDomain.FRAUD,
        description="Relatório de decisões antifraude por nível de risco e decisão.",
        default_metrics=[
            MetricSpec(name="events", field="event_id", aggregation=Aggregation.COUNT),
            MetricSpec(name="avg_risk_score", field="risk_score", aggregation=Aggregation.AVG),
            MetricSpec(name="max_risk_score", field="risk_score", aggregation=Aggregation.MAX),
        ],
        default_group_by=["risk_level", "decision"],
        tags=["fraud", "risk"],
    ),
}


@router.get("/health")
async def reports_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "reports", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.get("/catalog", response_model=List[ReportTemplate], dependencies=[Depends(require_scopes("reports:read"))])
async def reports_catalog(domain: Optional[ReportDomain] = None) -> List[ReportTemplate]:
    templates = list(TEMPLATES.values())
    if domain:
        templates = [item for item in templates if item.domain == domain]
    return templates


@router.post("/generate", response_model=ReportGenerateResponse, dependencies=[Depends(require_scopes("reports:write"))])
async def generate_report(payload: ReportGenerateRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> ReportGenerateResponse:
    ctx = build_context(request, user)
    warnings: List[str] = []
    try:
        effective = apply_template(payload)
        filtered_rows = apply_filters(effective.rows, effective.filters)
        metrics = effective.metrics or infer_metrics(filtered_rows)
        data = aggregate_rows(filtered_rows, metrics, effective.group_by)
        data = sort_report_rows(data, effective.sort)
        paged_data = data[effective.offset : effective.offset + effective.limit]
        detail_rows = filtered_rows[effective.offset : effective.offset + effective.limit] if effective.include_detail_rows else []
        summary = build_summary(filtered_rows, metrics, data) if effective.include_summary else {}
        facets_payload = build_facets(filtered_rows) if effective.include_facets else {}
        if not filtered_rows:
            warnings.append("filters_returned_no_rows")
        if len(payload.rows) > 50_000:
            warnings.append("large_report_payload_consider_async_generation")
        content_hash = hash_payload({"summary": summary, "data": paged_data, "detail_rows": detail_rows})
        report_id = f"rpt_{uuid.uuid4().hex[:20]}"
        record = ReportRecord(
            report_id=report_id,
            title=effective.title,
            domain=effective.domain,
            status=ReportStatus.GENERATED,
            row_count=len(filtered_rows),
            generated_by=ctx.user_subject,
            generated_at=utc_now_iso(),
            latency_ms=elapsed_ms(ctx.started_at),
            content_hash=content_hash,
            metadata=sanitize_metadata(effective.metadata),
        )
        stored = StoredReport(record=record, summary=summary, data=paged_data, detail_rows=detail_rows, facets=facets_payload)
        stored.audit.append({"audit_id": f"aud_{uuid.uuid4().hex[:16]}", "action": "generated", "actor": ctx.user_subject, "created_at": utc_now_iso()})
        repository.save(stored)
        return ReportGenerateResponse(
            request_id=ctx.request_id,
            status="success",
            version=ROUTER_VERSION,
            report=record,
            summary=summary,
            data=paged_data,
            detail_rows=detail_rows,
            facets=facets_payload,
            warnings=warnings,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("report_generation_failed")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Falha ao gerar relatório: {exc}") from exc


@router.get("/history", response_model=ReportHistoryResponse, dependencies=[Depends(require_scopes("reports:read"))])
async def reports_history(
    request: Request,
    domain: Optional[ReportDomain] = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> ReportHistoryResponse:
    ctx = build_context(request, None)
    rows = repository.list(domain=domain, include_deleted=include_deleted)
    total = len(rows)
    page = rows[offset : offset + limit]
    return ReportHistoryResponse(
        request_id=ctx.request_id,
        status="success",
        total=total,
        returned=len(page),
        reports=[item.record for item in page],
    )


@router.get("/{report_id}", response_model=ReportGenerateResponse, dependencies=[Depends(require_scopes("reports:read"))])
async def get_report(report_id: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> ReportGenerateResponse:
    ctx = build_context(request, user)
    report = repository.get(report_id)
    if report is None or report.record.status == ReportStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relatório não encontrado")
    report.audit.append({"audit_id": f"aud_{uuid.uuid4().hex[:16]}", "action": "read", "actor": ctx.user_subject, "created_at": utc_now_iso()})
    return ReportGenerateResponse(
        request_id=ctx.request_id,
        status="success",
        version=ROUTER_VERSION,
        report=report.record,
        summary=report.summary,
        data=report.data,
        detail_rows=report.detail_rows,
        facets=report.facets,
    )


@router.get("/{report_id}/export", dependencies=[Depends(require_scopes("reports:read"))])
async def export_report(report_id: str, fmt: ReportFormat = ReportFormat.JSON) -> Response:
    report = repository.get(report_id)
    if report is None or report.record.status == ReportStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relatório não encontrado")
    if fmt == ReportFormat.JSON:
        payload = {
            "report": report.record.dict(),
            "summary": report.summary,
            "data": report.data,
            "detail_rows": report.detail_rows,
            "facets": report.facets,
        }
        return Response(
            content=json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{report_id}.json"'},
        )
    csv_text = report_to_csv(report)
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_id}.csv"'},
    )


@router.delete("/{report_id}", dependencies=[Depends(require_scopes("reports:write"))])
async def delete_report(report_id: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    ctx = build_context(request, user)
    deleted = repository.delete(report_id, ctx.user_subject)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Relatório não encontrado")
    return {"request_id": ctx.request_id, "status": "deleted", "report_id": report_id}


@router.get("/stats/summary", response_model=ReportStatsResponse, dependencies=[Depends(require_scopes("reports:read"))])
async def report_stats() -> ReportStatsResponse:
    reports = [item.record for item in repository.reports.values()]
    latencies = [Decimal(str(item.latency_ms)) for item in reports]
    row_counts = [Decimal(str(item.row_count)) for item in reports]
    return ReportStatsResponse(
        status="success",
        version=ROUTER_VERSION,
        total_reports=len(reports),
        generated_reports=sum(1 for item in reports if item.status == ReportStatus.GENERATED),
        failed_reports=sum(1 for item in reports if item.status == ReportStatus.FAILED),
        deleted_reports=sum(1 for item in reports if item.status == ReportStatus.DELETED),
        by_domain=dict(Counter(item.domain.value for item in reports)),
        avg_latency_ms=to_float(mean_decimal(latencies)),
        avg_row_count=to_float(mean_decimal(row_counts)),
    )


def apply_template(payload: ReportGenerateRequest) -> ReportGenerateRequest:
    if not payload.template_id:
        return payload
    template = TEMPLATES.get(payload.template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Template não encontrado: {payload.template_id}")
    return ReportGenerateRequest(
        title=payload.title or template.name,
        domain=payload.domain if payload.domain != ReportDomain.CUSTOM else template.domain,
        template_id=payload.template_id,
        rows=payload.rows,
        metrics=payload.metrics or template.default_metrics,
        group_by=payload.group_by or template.default_group_by,
        filters=list(template.default_filters) + list(payload.filters),
        sort=payload.sort,
        limit=payload.limit,
        offset=payload.offset,
        include_detail_rows=payload.include_detail_rows,
        include_summary=payload.include_summary,
        include_facets=payload.include_facets,
        metadata={**template.dict(), **payload.metadata},
    )


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
        left = to_decimal(actual)
        right = to_decimal(expected)
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
    if op == "exists":
        return actual is not None
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Operador inválido: {filter_spec.op}")


def aggregate_rows(rows: Sequence[Dict[str, Any]], metrics: Sequence[MetricSpec], group_by: Sequence[str]) -> List[Dict[str, Any]]:
    grouped: DefaultDict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    if group_by:
        for row in rows:
            key = tuple(get_field(row, field) for field in group_by)
            grouped[key].append(row)
    else:
        grouped[tuple()].extend(rows)
    result: List[Dict[str, Any]] = []
    for key_tuple, group in grouped.items():
        item: Dict[str, Any] = {field: key_tuple[index] for index, field in enumerate(group_by)}
        item["record_count"] = len(group)
        for metric in metrics:
            item[metric.name] = aggregate_metric(group, metric)
        result.append(item)
    return result


def aggregate_metric(rows: Sequence[Mapping[str, Any]], metric: MetricSpec) -> Any:
    values = [get_field(row, metric.field) for row in rows]
    clean = [value for value in values if value is not None]
    if metric.aggregation == Aggregation.COUNT:
        return len(rows)
    if metric.aggregation == Aggregation.DISTINCT_COUNT:
        return len({json.dumps(value, sort_keys=True, default=str) for value in clean})
    numeric = [to_decimal(value) for value in clean if is_decimal_like(value)]
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


def infer_metrics(rows: Sequence[Mapping[str, Any]]) -> List[MetricSpec]:
    if not rows:
        return [MetricSpec(name="records", field="__record__", aggregation=Aggregation.COUNT)]
    numeric_fields: List[str] = []
    for row in rows[:100]:
        for key, value in flatten_dict(row).items():
            if is_decimal_like(value) and key not in numeric_fields:
                numeric_fields.append(key)
    if not numeric_fields:
        return [MetricSpec(name="records", field="__record__", aggregation=Aggregation.COUNT)]
    return [MetricSpec(name=field.replace(".", "_"), field=field, aggregation=Aggregation.SUM) for field in numeric_fields[:20]]


def sort_report_rows(rows: Sequence[Dict[str, Any]], sort_specs: Sequence[SortSpec]) -> List[Dict[str, Any]]:
    result = list(rows)
    for spec in reversed(sort_specs):
        result.sort(key=lambda row: get_field(row, spec.field), reverse=spec.direction == SortDirection.DESC)
    return result


def build_summary(rows: Sequence[Dict[str, Any]], metrics: Sequence[MetricSpec], data: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "input_rows": len(rows),
        "output_rows": len(data),
        "metrics": {metric.name: aggregate_metric(rows, metric) for metric in metrics},
        "generated_at": utc_now_iso(),
    }


def build_facets(rows: Sequence[Dict[str, Any]], max_fields: int = 20, max_values: int = 20) -> Dict[str, Dict[str, int]]:
    counters: Dict[str, Counter] = {}
    for row in rows[:10_000]:
        flat = flatten_dict(row)
        for key, value in flat.items():
            if isinstance(value, (dict, list)) or is_decimal_like(value):
                continue
            counters.setdefault(key, Counter())[str(value)] += 1
    selected = sorted(counters.items(), key=lambda item: sum(item[1].values()), reverse=True)[:max_fields]
    return {key: dict(counter.most_common(max_values)) for key, counter in selected}


def report_to_csv(report: StoredReport) -> str:
    rows = report.data if report.data else report.detail_rows
    output = io.StringIO()
    if not rows:
        writer = csv.writer(output)
        writer.writerow(["report_id", "title", "row_count"])
        writer.writerow([report.record.report_id, report.record.title, report.record.row_count])
        return output.getvalue()
    fieldnames: List[str] = []
    for row in rows:
        for key in flatten_dict(row).keys():
            if key not in fieldnames:
                fieldnames.append(key)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        flat = flatten_dict(row)
        writer.writerow({key: flat.get(key) for key in fieldnames})
    return output.getvalue()


def get_field(row: Mapping[str, Any], field: str) -> Any:
    if field == "__record__":
        return 1
    if field in row:
        return row[field]
    current: Any = row
    for part in field.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def flatten_dict(payload: Mapping[str, Any], prefix: str = "") -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(flatten_dict(value, full_key))
        else:
            result[full_key] = value
    return result


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        lower = key_text.lower()
        if any(item in lower for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def is_decimal_like(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        Decimal(str(value).replace(",", "."))
        return True
    except (InvalidOperation, ValueError):
        return False


def to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


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


def to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def hash_payload(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def build_context(request: Request, user: Any) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return ExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
