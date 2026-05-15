#!/usr/bin/env python3
"""
api/schemas/requests.py

Enterprise-grade request schemas for API layer.

Objetivo:
- Centralizar contratos de entrada reutilizáveis para routers e serviços.
- Padronizar paginação, ordenação, filtros, períodos, batch, auditoria, metadados,
  payloads financeiros, risco, fraude, UEBA, NLP, documentos e relatórios.
- Fornecer validações comuns para datas, campos, valores monetários, percentuais e limites.
- Evitar duplicação entre api/routes/* e manter compatibilidade com Pydantic.

Uso:
    from api.schemas.requests import PaginationRequest, DateRangeRequest, FraudEventRequest

Notas:
- Este módulo é intencionalmente dependency-light.
- Valores monetários entram como float/string por compatibilidade HTTP, mas podem ser convertidos
  para Decimal via helpers no service layer.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, root_validator, validator


REQUEST_SCHEMAS_VERSION = "1.0.0"
DEFAULT_CURRENCY = "BRL"
MAX_BATCH_SIZE = 50_000
MAX_TEXT_LENGTH = 200_000
MAX_ROWS = 100_000
DEFAULT_TIMEZONE = timezone.utc


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    EXISTS = "exists"
    BETWEEN = "between"


class TimeGrain(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"
    XLSX = "xlsx"


class Currency(str, Enum):
    BRL = "BRL"
    USD = "USD"
    EUR = "EUR"


class Direction(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    INFLOW = "inflow"
    OUTFLOW = "outflow"
    UNKNOWN = "unknown"


class RiskDomain(str, Enum):
    FRAUD = "fraud"
    UEBA = "ueba"
    CREDIT = "credit"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"
    COMPLIANCE = "compliance"


class RequestMetadata(BaseModel):
    source: Optional[str] = None
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    custom: Dict[str, Any] = Field(default_factory=dict)


class BaseRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:16]}")
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)


class PaginationRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=10_000)
    offset: int = Field(default=0, ge=0)


class SortRequest(BaseModel):
    field: str
    direction: SortDirection = SortDirection.DESC

    @validator("field")
    def validate_field(cls, value: str) -> str:
        return validate_field_name(value)


class FilterRequest(BaseModel):
    field: str
    operator: FilterOperator = FilterOperator.EQ
    value: Any = None

    @validator("field")
    def validate_field(cls, value: str) -> str:
        return validate_field_name(value)

    @root_validator
    def validate_filter_value(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        operator = values.get("operator")
        value = values.get("value")
        if operator in {FilterOperator.IN, FilterOperator.NOT_IN, FilterOperator.BETWEEN} and not isinstance(value, list):
            raise ValueError(f"operator {operator} requer value como lista")
        if operator == FilterOperator.BETWEEN and isinstance(value, list) and len(value) != 2:
            raise ValueError("operator between requer lista com 2 valores")
        return values


class QueryRequest(BaseRequest):
    filters: List[FilterRequest] = Field(default_factory=list)
    sort: List[SortRequest] = Field(default_factory=list)
    pagination: PaginationRequest = Field(default_factory=PaginationRequest)
    include_deleted: bool = False


class DateRangeRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    timezone: str = "UTC"

    @root_validator
    def validate_date_range(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        start = parse_date_optional(values.get("start_date"))
        end = parse_date_optional(values.get("end_date"))
        if start and end and start > end:
            raise ValueError("start_date não pode ser maior que end_date")
        return values


class PeriodRequest(DateRangeRequest):
    grain: TimeGrain = TimeGrain.MONTHLY
    period: Optional[str] = None


class BatchRequest(BaseRequest):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    fail_fast: bool = False
    dry_run: bool = False

    @validator("items")
    def validate_batch_size(cls, value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"batch excede limite de {MAX_BATCH_SIZE}")
        return value


class ExportRequest(QueryRequest):
    format: OutputFormat = OutputFormat.JSON
    include_header: bool = True
    filename: Optional[str] = None


class MoneyRequest(BaseRequest):
    amount: Union[float, str]
    currency: Currency = Currency.BRL

    @validator("amount")
    def validate_amount(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "amount")
        return value


class MetricRequest(BaseModel):
    name: str
    value: Union[float, str]
    currency: Optional[Currency] = None
    period: Optional[str] = None
    category: Optional[str] = None
    dimensions: Dict[str, Any] = Field(default_factory=dict)

    @validator("name")
    def validate_name(cls, value: str) -> str:
        return validate_slug_like(value, "name")

    @validator("value")
    def validate_value(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "value")
        return value


class MetricsRequest(BaseRequest):
    metrics: List[MetricRequest] = Field(default_factory=list)
    period: Optional[PeriodRequest] = None


class AnalyticsRequest(QueryRequest):
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    metrics: List[str] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)

    @validator("rows")
    def validate_rows(cls, value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if len(value) > MAX_ROWS:
            raise ValueError(f"rows excede limite de {MAX_ROWS}")
        return value


class InferenceRequest(BaseRequest):
    entity_id: Optional[str] = None
    operation: str = "predict"
    payload: Dict[str, Any] = Field(default_factory=dict)

    @validator("operation")
    def validate_operation(cls, value: str) -> str:
        return validate_slug_like(value, "operation")


class InferenceBatchRequest(BaseRequest):
    requests: List[InferenceRequest] = Field(default_factory=list)
    handler: Optional[str] = None
    fail_fast: bool = False

    @validator("requests")
    def validate_requests_size(cls, value: List[InferenceRequest]) -> List[InferenceRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"requests excede limite de {MAX_BATCH_SIZE}")
        return value


class FinancialMetricRequest(BaseRequest):
    entity_id: str
    metric_name: str
    metric_value: Union[float, str]
    baseline_value: Optional[Union[float, str]] = None
    target_value: Optional[Union[float, str]] = None
    budget_value: Optional[Union[float, str]] = None
    currency: Currency = Currency.BRL
    period: Optional[str] = None
    category: Optional[str] = None

    @validator("metric_name")
    def validate_metric_name(cls, value: str) -> str:
        return validate_slug_like(value, "metric_name")

    @validator("metric_value", "baseline_value", "target_value", "budget_value")
    def validate_optional_decimal(cls, value: Optional[Union[float, str]]) -> Optional[Union[float, str]]:
        if value is not None:
            validate_decimal_like(value, "value")
        return value


class CashflowEntryRequest(BaseModel):
    entry_id: str = Field(default_factory=lambda: f"cf_{uuid.uuid4().hex[:16]}")
    date: str
    amount: Union[float, str]
    currency: Currency = Currency.BRL
    direction: Direction = Direction.UNKNOWN
    category: Optional[str] = None
    subcategory: Optional[str] = None
    counterparty: Optional[str] = None
    account: Optional[str] = None
    cost_center: Optional[str] = None
    department: Optional[str] = None
    status: str = "confirmed"
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("date")
    def validate_date(cls, value: str) -> str:
        parse_date_required(value)
        return value

    @validator("amount")
    def validate_amount(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "amount")
        return value


class CashflowRequest(BaseRequest):
    entries: List[CashflowEntryRequest] = Field(default_factory=list)
    opening_balance: Union[float, str] = 0.0
    currency: Currency = Currency.BRL
    period: PeriodRequest = Field(default_factory=PeriodRequest)

    @validator("entries")
    def validate_entries_size(cls, value: List[CashflowEntryRequest]) -> List[CashflowEntryRequest]:
        if len(value) > MAX_ROWS:
            raise ValueError(f"entries excede limite de {MAX_ROWS}")
        return value


class FraudEventRequest(BaseRequest):
    event_id: str = Field(default_factory=lambda: f"frd_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    event_type: str = "transaction"
    amount: Union[float, str] = 0.0
    currency: Currency = Currency.BRL
    direction: Direction = Direction.DEBIT
    channel: Optional[str] = None
    ip_address: Optional[str] = None
    device_id: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    counterparty: Optional[str] = None
    merchant_id: Optional[str] = None
    reference_id: Optional[str] = None
    status: Optional[str] = None
    success: Optional[bool] = None

    @validator("timestamp")
    def validate_timestamp(cls, value: str) -> str:
        parse_datetime_required(value)
        return value

    @validator("amount")
    def validate_amount(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "amount")
        return value


class FraudBatchRequest(BaseRequest):
    events: List[FraudEventRequest] = Field(default_factory=list)
    update_state: bool = True

    @validator("events")
    def validate_events_size(cls, value: List[FraudEventRequest]) -> List[FraudEventRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"events excede limite de {MAX_BATCH_SIZE}")
        return value


class UebaEventRequest(BaseRequest):
    event_id: str = Field(default_factory=lambda: f"ueba_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    entity_type: str = "user"
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    event_type: str = "activity"
    action: str = "unknown"
    resource: Optional[str] = None
    outcome: str = "success"
    ip_address: Optional[str] = None
    device_id: Optional[str] = None
    user_agent: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    session_id: Optional[str] = None
    amount: Optional[Union[float, str]] = None
    sensitivity: str = "normal"

    @validator("timestamp")
    def validate_timestamp(cls, value: str) -> str:
        parse_datetime_required(value)
        return value

    @validator("amount")
    def validate_optional_amount(cls, value: Optional[Union[float, str]]) -> Optional[Union[float, str]]:
        if value is not None:
            validate_decimal_like(value, "amount")
        return value


class UebaBatchRequest(BaseRequest):
    events: List[UebaEventRequest] = Field(default_factory=list)
    update_state: bool = True

    @validator("events")
    def validate_events_size(cls, value: List[UebaEventRequest]) -> List[UebaEventRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"events excede limite de {MAX_BATCH_SIZE}")
        return value


class TextRequest(BaseRequest):
    text: str
    language: str = "auto"

    @validator("text")
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text não pode ser vazio")
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"text excede limite de {MAX_TEXT_LENGTH}")
        return value


class BatchTextRequest(BaseRequest):
    items: List[TextRequest] = Field(default_factory=list)

    @validator("items")
    def validate_items_size(cls, value: List[TextRequest]) -> List[TextRequest]:
        if len(value) > 1_000:
            raise ValueError("items excede limite de 1000")
        return value


class DocumentIngestRequest(BaseRequest):
    document_id: Optional[str] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    text: Optional[str] = None
    content_base64: Optional[str] = None
    title: Optional[str] = None
    source: Optional[str] = None
    owner: Optional[str] = None
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    @root_validator
    def validate_content(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        text = values.get("text")
        content_base64 = values.get("content_base64")
        if not text and not content_base64:
            raise ValueError("Informe text ou content_base64")
        if text and len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"text excede limite de {MAX_TEXT_LENGTH}")
        return values


class ReportGenerateRequest(AnalyticsRequest):
    title: str = "Custom Report"
    domain: str = "custom"
    template_id: Optional[str] = None
    format: OutputFormat = OutputFormat.JSON
    include_detail_rows: bool = False
    include_summary: bool = True
    include_facets: bool = True


class PayrollEmployeeRequest(BaseModel):
    employee_id: str
    period: str
    base_salary: Union[float, str]
    currency: Currency = Currency.BRL
    department: Optional[str] = None
    cost_center: Optional[str] = None
    role: Optional[str] = None
    worked_hours: float = Field(default=220, ge=0)
    overtime_hours: float = Field(default=0, ge=0)
    bonus: Union[float, str] = 0.0
    commission: Union[float, str] = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("base_salary", "bonus", "commission")
    def validate_money_fields(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "money")
        return value


class PayrollRequest(BaseRequest):
    employees: List[PayrollEmployeeRequest] = Field(default_factory=list)

    @validator("employees")
    def validate_employees_size(cls, value: List[PayrollEmployeeRequest]) -> List[PayrollEmployeeRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"employees excede limite de {MAX_BATCH_SIZE}")
        return value


class RevenueEventRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"rev_{uuid.uuid4().hex[:16]}")
    customer_id: str
    date: str
    amount: Union[float, str]
    currency: Currency = Currency.BRL
    event_type: str = "new"
    billing_frequency: str = "monthly"
    plan: Optional[str] = None
    product: Optional[str] = None
    channel: Optional[str] = None
    region: Optional[str] = None
    segment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @validator("date")
    def validate_date(cls, value: str) -> str:
        parse_date_required(value)
        return value

    @validator("amount")
    def validate_amount(cls, value: Union[float, str]) -> Union[float, str]:
        validate_decimal_like(value, "amount")
        return value


class RevenueRequest(BaseRequest):
    events: List[RevenueEventRequest] = Field(default_factory=list)
    period: PeriodRequest = Field(default_factory=PeriodRequest)

    @validator("events")
    def validate_events_size(cls, value: List[RevenueEventRequest]) -> List[RevenueEventRequest]:
        if len(value) > MAX_BATCH_SIZE:
            raise ValueError(f"events excede limite de {MAX_BATCH_SIZE}")
        return value


class AuthTokenRequest(BaseRequest):
    subject: str
    scopes: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    ttl_seconds: Optional[int] = Field(default=None, ge=60, le=86_400)


class LoginRequest(BaseRequest):
    username: str
    password: str
    tenant_id: Optional[str] = None


class RefreshTokenRequest(BaseRequest):
    refresh_token: str


def validate_field_name(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("field não pode ser vazio")
    value = value.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", value):
        raise ValueError(f"field inválido: {value}")
    return value


def validate_slug_like(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} não pode ser vazio")
    clean = value.strip().lower().replace(" ", "_")
    if not re.match(r"^[a-z0-9_:-]+$", clean):
        raise ValueError(f"{field_name} inválido: {value}")
    return clean


def validate_decimal_like(value: Any, field_name: str) -> None:
    try:
        float(str(value).replace(",", "."))
    except Exception as exc:
        raise ValueError(f"{field_name} precisa ser numérico") from exc


def parse_date_required(value: str) -> date:
    parsed = parse_date_optional(value)
    if parsed is None:
        raise ValueError(f"data inválida: {value}")
    return parsed


def parse_date_optional(value: Optional[str]) -> Optional[date]:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text).date()
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"data inválida: {value}") from exc


def parse_datetime_required(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"datetime inválido: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed


__all__ = [
    "REQUEST_SCHEMAS_VERSION",
    "DEFAULT_CURRENCY",
    "MAX_BATCH_SIZE",
    "MAX_TEXT_LENGTH",
    "MAX_ROWS",
    "SortDirection",
    "FilterOperator",
    "TimeGrain",
    "OutputFormat",
    "Currency",
    "Direction",
    "RiskDomain",
    "RequestMetadata",
    "BaseRequest",
    "PaginationRequest",
    "SortRequest",
    "FilterRequest",
    "QueryRequest",
    "DateRangeRequest",
    "PeriodRequest",
    "BatchRequest",
    "ExportRequest",
    "MoneyRequest",
    "MetricRequest",
    "MetricsRequest",
    "AnalyticsRequest",
    "InferenceRequest",
    "InferenceBatchRequest",
    "FinancialMetricRequest",
    "CashflowEntryRequest",
    "CashflowRequest",
    "FraudEventRequest",
    "FraudBatchRequest",
    "UebaEventRequest",
    "UebaBatchRequest",
    "TextRequest",
    "BatchTextRequest",
    "DocumentIngestRequest",
    "ReportGenerateRequest",
    "PayrollEmployeeRequest",
    "PayrollRequest",
    "RevenueEventRequest",
    "RevenueRequest",
    "AuthTokenRequest",
    "LoginRequest",
    "RefreshTokenRequest",
    "validate_field_name",
    "validate_slug_like",
    "validate_decimal_like",
    "parse_date_required",
    "parse_date_optional",
    "parse_datetime_required",
]
