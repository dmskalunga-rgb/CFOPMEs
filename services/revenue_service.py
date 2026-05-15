"""
kwanza-ai-core/services/revenue_service.py

Enterprise-grade revenue service layer.

Purpose
-------
Centralize revenue operations for SaaS, retail, services, subscriptions and
transactional businesses: invoicing, revenue recognition, deferred revenue,
adjustments, refunds, tax-like charges, revenue KPIs and reporting snapshots.

Design goals
------------
- Multi-tenant and currency-aware.
- Deterministic, auditable financial calculations.
- Idempotent invoice/revenue operations.
- Configurable recognition policies without hardcoded jurisdiction rules.
- Async-first repository/payment abstractions.
- Supports one-time, recurring, usage-based and milestone revenue.
- MRR/ARR, churn, expansion/contraction and cohort-friendly events.
- Production hooks: metrics, audit, structured logs and safe validation.

Important
---------
This module is a configurable revenue engine. It does not encode official tax,
accounting or jurisdiction-specific rules by default. Production systems should
supply reviewed policy configuration and integrate with the company ledger/ERP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)
getcontext().prec = 28

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]
Money = Decimal


# =============================================================================
# Exceptions
# =============================================================================


class RevenueServiceError(RuntimeError):
    """Base exception for revenue service failures."""


class RevenueValidationError(RevenueServiceError):
    """Raised when revenue input data is invalid."""


class RevenuePolicyError(RevenueServiceError):
    """Raised when revenue policy configuration is invalid."""


class RevenueConflictError(RevenueServiceError):
    """Raised when an operation conflicts with current revenue state."""


# =============================================================================
# Enums
# =============================================================================


class RevenueType(str, Enum):
    ONE_TIME = "one_time"
    SUBSCRIPTION = "subscription"
    USAGE = "usage"
    MILESTONE = "milestone"
    SERVICE = "service"
    RETAIL = "retail"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class RevenueRecognitionMethod(str, Enum):
    IMMEDIATE = "immediate"
    STRAIGHT_LINE = "straight_line"
    USAGE_BASED = "usage_based"
    MILESTONE_BASED = "milestone_based"
    MANUAL = "manual"


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"
    VOID = "void"
    WRITTEN_OFF = "written_off"


class RevenueEventStatus(str, Enum):
    PENDING = "pending"
    RECOGNIZED = "recognized"
    DEFERRED = "deferred"
    REVERSED = "reversed"
    CANCELLED = "cancelled"


class BillingCadence(str, Enum):
    NONE = "none"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"
    CUSTOM = "custom"


class PaymentStatus(str, Enum):
    NOT_DUE = "not_due"
    DUE = "due"
    PARTIAL = "partial"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class RevenueLineType(str, Enum):
    PRODUCT = "product"
    SERVICE = "service"
    SUBSCRIPTION = "subscription"
    USAGE = "usage"
    DISCOUNT = "discount"
    TAX = "tax"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


# =============================================================================
# Data models
# =============================================================================


@dataclass(frozen=True)
class RevenuePeriod:
    start_date: date
    end_date: date

    def validate(self) -> None:
        if self.end_date < self.start_date:
            raise RevenueValidationError("period.end_date cannot be before period.start_date.")

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def key(self) -> str:
        return f"{self.start_date.isoformat()}:{self.end_date.isoformat()}"


@dataclass(frozen=True)
class CustomerProfile:
    customer_id: str
    tenant_id: str
    name: str
    currency: str = "AOA"
    email: Optional[str] = None
    tax_id: Optional[str] = None
    segment: Optional[str] = None
    country: Optional[str] = None
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RevenueLine:
    code: str
    description: str
    quantity: Decimal
    unit_price: Money
    line_type: RevenueLineType = RevenueLineType.PRODUCT
    discount_amount: Money = Decimal("0")
    tax_rate: Decimal = Decimal("0")
    recognition_method: RevenueRecognitionMethod = RevenueRecognitionMethod.IMMEDIATE
    service_period: Optional[RevenuePeriod] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def subtotal(self) -> Money:
        return self.quantity * self.unit_price

    @property
    def net_amount(self) -> Money:
        return max(Decimal("0"), self.subtotal - self.discount_amount)

    @property
    def tax_amount(self) -> Money:
        return max(Decimal("0"), self.net_amount * self.tax_rate)

    @property
    def total_amount(self) -> Money:
        if self.line_type == RevenueLineType.DISCOUNT:
            return -abs(self.net_amount)
        if self.line_type in {RevenueLineType.REFUND, RevenueLineType.ADJUSTMENT} and self.unit_price < 0:
            return self.net_amount.copy_negate()
        return self.net_amount + self.tax_amount


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    tenant_id: str
    customer_id: str
    invoice_number: str
    issue_date: date
    due_date: date
    currency: str
    lines: Sequence[RevenueLine]
    status: InvoiceStatus = InvoiceStatus.DRAFT
    paid_amount: Money = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def subtotal(self) -> Money:
        return sum((line.net_amount for line in self.lines if line.line_type != RevenueLineType.TAX), Decimal("0"))

    @property
    def tax_total(self) -> Money:
        return sum((line.tax_amount for line in self.lines), Decimal("0"))

    @property
    def total(self) -> Money:
        return sum((line.total_amount for line in self.lines), Decimal("0"))

    @property
    def balance_due(self) -> Money:
        return max(Decimal("0"), self.total - self.paid_amount)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["issue_date"] = self.issue_date.isoformat()
        payload["due_date"] = self.due_date.isoformat()
        payload["status"] = self.status.value
        payload["paid_amount"] = str(self.paid_amount)
        payload["subtotal"] = str(self.subtotal)
        payload["tax_total"] = str(self.tax_total)
        payload["total"] = str(self.total)
        payload["balance_due"] = str(self.balance_due)
        for line in payload["lines"]:
            line["quantity"] = str(line["quantity"])
            line["unit_price"] = str(line["unit_price"])
            line["discount_amount"] = str(line["discount_amount"])
            line["tax_rate"] = str(line["tax_rate"])
            line["line_type"] = line["line_type"].value if hasattr(line["line_type"], "value") else line["line_type"]
            line["recognition_method"] = line["recognition_method"].value if hasattr(line["recognition_method"], "value") else line["recognition_method"]
            if line.get("service_period"):
                line["service_period"] = {
                    "start_date": line["service_period"].start_date.isoformat(),
                    "end_date": line["service_period"].end_date.isoformat(),
                } if hasattr(line["service_period"], "start_date") else line["service_period"]
        return payload


@dataclass(frozen=True)
class RevenueEvent:
    event_id: str
    tenant_id: str
    customer_id: str
    invoice_id: Optional[str]
    line_code: str
    revenue_type: RevenueType
    recognition_method: RevenueRecognitionMethod
    event_date: date
    amount: Money
    currency: str
    status: RevenueEventStatus = RevenueEventStatus.PENDING
    recognized_period: Optional[RevenuePeriod] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["event_date"] = self.event_date.isoformat()
        payload["amount"] = str(self.amount)
        payload["revenue_type"] = self.revenue_type.value
        payload["recognition_method"] = self.recognition_method.value
        payload["status"] = self.status.value
        if self.recognized_period:
            payload["recognized_period"] = {
                "start_date": self.recognized_period.start_date.isoformat(),
                "end_date": self.recognized_period.end_date.isoformat(),
            }
        return payload


@dataclass(frozen=True)
class SubscriptionContract:
    contract_id: str
    tenant_id: str
    customer_id: str
    product_code: str
    start_date: date
    end_date: Optional[date]
    mrr: Money
    currency: str = "AOA"
    cadence: BillingCadence = BillingCadence.MONTHLY
    status: str = "active"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RevenuePolicyConfig:
    policy_id: str = "default"
    currency: str = "AOA"
    default_payment_terms_days: int = 15
    default_tax_rate: Decimal = Decimal("0")
    rounding_quantum: Decimal = Decimal("0.01")
    allow_negative_invoice_total: bool = False
    recognize_tax_as_revenue: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise RevenuePolicyError("policy_id is required.")
        if self.default_payment_terms_days < 0:
            raise RevenuePolicyError("default_payment_terms_days cannot be negative.")
        if self.default_tax_rate < 0:
            raise RevenuePolicyError("default_tax_rate cannot be negative.")
        if self.rounding_quantum <= 0:
            raise RevenuePolicyError("rounding_quantum must be positive.")


@dataclass(frozen=True)
class CreateInvoiceRequest:
    tenant_id: str
    customer_id: str
    lines: Sequence[RevenueLine]
    issue_date: date = field(default_factory=lambda: datetime.now(UTC).date())
    due_date: Optional[date] = None
    invoice_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: Optional[str] = None
    currency: Optional[str] = None
    policy_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentRecord:
    payment_id: str
    tenant_id: str
    invoice_id: str
    amount: Money
    currency: str
    paid_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reference: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RevenueSummary:
    tenant_id: str
    period: RevenuePeriod
    currency: str
    recognized_revenue: Money
    deferred_revenue: Money
    invoiced_revenue: Money
    collected_cash: Money
    tax_total: Money
    refunds: Money
    adjustments: Money
    mrr: Money
    arr: Money
    active_customers: int
    paying_customers: int
    ar_balance: Money
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["period"] = {
            "start_date": self.period.start_date.isoformat(),
            "end_date": self.period.end_date.isoformat(),
        }
        payload["created_at"] = self.created_at.isoformat()
        for key in [
            "recognized_revenue",
            "deferred_revenue",
            "invoiced_revenue",
            "collected_cash",
            "tax_total",
            "refunds",
            "adjustments",
            "mrr",
            "arr",
            "ar_balance",
        ]:
            payload[key] = str(payload[key])
        return payload


@dataclass(frozen=True)
class RevenueServiceConfig:
    default_currency: str = "AOA"
    audit_enabled: bool = True
    idempotency_ttl_seconds: int = 86_400
    max_invoice_lines: int = 1_000
    fail_fast: bool = False
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.idempotency_ttl_seconds <= 0:
            raise RevenueValidationError("idempotency_ttl_seconds must be positive.")
        if self.max_invoice_lines <= 0:
            raise RevenueValidationError("max_invoice_lines must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class RevenueRepository(Protocol):
    async def get_customer(self, tenant_id: str, customer_id: str) -> Optional[CustomerProfile]: ...

    async def get_policy(self, tenant_id: str, policy_id: Optional[str]) -> RevenuePolicyConfig: ...

    async def save_invoice(self, invoice: Invoice) -> None: ...

    async def get_invoice(self, tenant_id: str, invoice_id: str) -> Optional[Invoice]: ...

    async def update_invoice_status(self, tenant_id: str, invoice_id: str, status: InvoiceStatus, paid_amount: Optional[Money] = None) -> None: ...

    async def save_revenue_events(self, events: Sequence[RevenueEvent]) -> None: ...

    async def list_revenue_events(self, tenant_id: str, period: RevenuePeriod) -> Sequence[RevenueEvent]: ...

    async def list_invoices(self, tenant_id: str, period: RevenuePeriod) -> Sequence[Invoice]: ...

    async def save_payment(self, payment: PaymentRecord) -> None: ...

    async def list_payments(self, tenant_id: str, period: RevenuePeriod) -> Sequence[PaymentRecord]: ...

    async def list_subscriptions(self, tenant_id: str, as_of: date) -> Sequence[SubscriptionContract]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# No-op and in-memory dependencies
# =============================================================================


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


class InMemoryRevenueRepository:
    def __init__(self, default_policy: Optional[RevenuePolicyConfig] = None) -> None:
        self.default_policy = default_policy or RevenuePolicyConfig()
        self._customers: Dict[Tuple[str, str], CustomerProfile] = {}
        self._invoices: Dict[Tuple[str, str], Invoice] = {}
        self._events: List[RevenueEvent] = []
        self._payments: List[PaymentRecord] = []
        self._subscriptions: Dict[Tuple[str, str], SubscriptionContract] = {}

    def add_customer(self, customer: CustomerProfile) -> None:
        self._customers[(customer.tenant_id, customer.customer_id)] = customer

    def add_subscription(self, contract: SubscriptionContract) -> None:
        self._subscriptions[(contract.tenant_id, contract.contract_id)] = contract

    async def get_customer(self, tenant_id: str, customer_id: str) -> Optional[CustomerProfile]:
        return self._customers.get((tenant_id, customer_id))

    async def get_policy(self, tenant_id: str, policy_id: Optional[str]) -> RevenuePolicyConfig:
        return self.default_policy

    async def save_invoice(self, invoice: Invoice) -> None:
        self._invoices[(invoice.tenant_id, invoice.invoice_id)] = invoice

    async def get_invoice(self, tenant_id: str, invoice_id: str) -> Optional[Invoice]:
        return self._invoices.get((tenant_id, invoice_id))

    async def update_invoice_status(self, tenant_id: str, invoice_id: str, status: InvoiceStatus, paid_amount: Optional[Money] = None) -> None:
        invoice = self._invoices.get((tenant_id, invoice_id))
        if not invoice:
            raise RevenueValidationError(f"Invoice not found: {invoice_id}")
        self._invoices[(tenant_id, invoice_id)] = Invoice(
            invoice_id=invoice.invoice_id,
            tenant_id=invoice.tenant_id,
            customer_id=invoice.customer_id,
            invoice_number=invoice.invoice_number,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
            currency=invoice.currency,
            lines=invoice.lines,
            status=status,
            paid_amount=paid_amount if paid_amount is not None else invoice.paid_amount,
            metadata=invoice.metadata,
        )

    async def save_revenue_events(self, events: Sequence[RevenueEvent]) -> None:
        self._events.extend(events)

    async def list_revenue_events(self, tenant_id: str, period: RevenuePeriod) -> Sequence[RevenueEvent]:
        return [e for e in self._events if e.tenant_id == tenant_id and period.start_date <= e.event_date <= period.end_date]

    async def list_invoices(self, tenant_id: str, period: RevenuePeriod) -> Sequence[Invoice]:
        return [i for (tid, _), i in self._invoices.items() if tid == tenant_id and period.start_date <= i.issue_date <= period.end_date]

    async def save_payment(self, payment: PaymentRecord) -> None:
        self._payments.append(payment)

    async def list_payments(self, tenant_id: str, period: RevenuePeriod) -> Sequence[PaymentRecord]:
        return [p for p in self._payments if p.tenant_id == tenant_id and period.start_date <= p.paid_at.date() <= period.end_date]

    async def list_subscriptions(self, tenant_id: str, as_of: date) -> Sequence[SubscriptionContract]:
        return [
            s for (tid, _), s in self._subscriptions.items()
            if tid == tenant_id and s.start_date <= as_of and (s.end_date is None or s.end_date >= as_of) and s.status == "active"
        ]


class AsyncIdempotencyStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        now = time.monotonic()
        async with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _money(value: Any, field_name: str = "amount") -> Money:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RevenueValidationError(f"Invalid money value for {field_name}: {value!r}") from exc


def _round_money(value: Money, quantum: Decimal = Decimal("0.01")) -> Money:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _date_from_any(value: Any, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RevenueValidationError(f"Invalid date for {field_name}: {value!r}") from exc
    raise RevenueValidationError(f"Invalid date for {field_name}: {value!r}")


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _add_months(value: date, months: int) -> date:
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# =============================================================================
# Revenue recognition engine
# =============================================================================


class RevenueRecognitionEngine:
    def __init__(self, policy: RevenuePolicyConfig) -> None:
        self.policy = policy
        self.policy.validate()

    def build_events_for_invoice(self, invoice: Invoice) -> Sequence[RevenueEvent]:
        events: List[RevenueEvent] = []
        for line in invoice.lines:
            if line.line_type == RevenueLineType.TAX and not self.policy.recognize_tax_as_revenue:
                continue
            if line.line_type == RevenueLineType.DISCOUNT:
                continue
            revenue_type = self._revenue_type_from_line(line)
            amount = _round_money(line.net_amount, self.policy.rounding_quantum)
            if line.line_type == RevenueLineType.REFUND:
                amount = -abs(amount)

            if line.recognition_method == RevenueRecognitionMethod.IMMEDIATE or not line.service_period:
                events.append(self._event(invoice, line, revenue_type, invoice.issue_date, amount, RevenueEventStatus.RECOGNIZED, None))
            elif line.recognition_method == RevenueRecognitionMethod.STRAIGHT_LINE:
                events.extend(self._straight_line_events(invoice, line, revenue_type, amount))
            else:
                events.append(self._event(invoice, line, revenue_type, invoice.issue_date, amount, RevenueEventStatus.DEFERRED, line.service_period))
        return tuple(events)

    def _straight_line_events(self, invoice: Invoice, line: RevenueLine, revenue_type: RevenueType, amount: Money) -> Sequence[RevenueEvent]:
        assert line.service_period is not None
        period = line.service_period
        months: List[date] = []
        cursor = _month_start(period.start_date)
        while cursor <= period.end_date:
            months.append(cursor)
            cursor = _add_months(cursor, 1)
        if not months:
            return tuple()
        monthly_amount = _round_money(amount / Decimal(len(months)), self.policy.rounding_quantum)
        events = []
        allocated = Decimal("0")
        for idx, month in enumerate(months):
            event_amount = monthly_amount if idx < len(months) - 1 else amount - allocated
            allocated += event_amount
            event_period = RevenuePeriod(start_date=month, end_date=_add_months(month, 1) - timedelta(days=1))
            events.append(self._event(invoice, line, revenue_type, month, event_amount, RevenueEventStatus.RECOGNIZED, event_period))
        return tuple(events)

    def _event(
        self,
        invoice: Invoice,
        line: RevenueLine,
        revenue_type: RevenueType,
        event_date: date,
        amount: Money,
        status: RevenueEventStatus,
        recognized_period: Optional[RevenuePeriod],
    ) -> RevenueEvent:
        return RevenueEvent(
            event_id=str(uuid.uuid4()),
            tenant_id=invoice.tenant_id,
            customer_id=invoice.customer_id,
            invoice_id=invoice.invoice_id,
            line_code=line.code,
            revenue_type=revenue_type,
            recognition_method=line.recognition_method,
            event_date=event_date,
            amount=_round_money(amount, self.policy.rounding_quantum),
            currency=invoice.currency,
            status=status,
            recognized_period=recognized_period,
            metadata={"invoice_number": invoice.invoice_number, "line_type": line.line_type.value},
        )

    def _revenue_type_from_line(self, line: RevenueLine) -> RevenueType:
        mapping = {
            RevenueLineType.PRODUCT: RevenueType.RETAIL,
            RevenueLineType.SERVICE: RevenueType.SERVICE,
            RevenueLineType.SUBSCRIPTION: RevenueType.SUBSCRIPTION,
            RevenueLineType.USAGE: RevenueType.USAGE,
            RevenueLineType.REFUND: RevenueType.REFUND,
            RevenueLineType.ADJUSTMENT: RevenueType.ADJUSTMENT,
        }
        return mapping.get(line.line_type, RevenueType.ONE_TIME)


# =============================================================================
# Main service
# =============================================================================


class RevenueService:
    def __init__(
        self,
        repository: RevenueRepository,
        config: Optional[RevenueServiceConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        idempotency_store: Optional[AsyncIdempotencyStore] = None,
    ) -> None:
        self.config = config or RevenueServiceConfig()
        self.config.validate()
        self.repository = repository
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.idempotency_store = idempotency_store or AsyncIdempotencyStore(self.config.idempotency_ttl_seconds)

    async def create_invoice(self, request: CreateInvoiceRequest) -> Invoice:
        started = time.perf_counter()
        self._validate_invoice_request(request)
        tags = {"tenant_id": request.tenant_id}
        self.metrics.increment("revenue.invoice.create.started", tags=tags)

        key = self._idempotency_key("invoice", request.idempotency_key, {
            "tenant_id": request.tenant_id,
            "customer_id": request.customer_id,
            "lines": [asdict(line) for line in request.lines],
            "issue_date": request.issue_date,
        })
        cached = await self.idempotency_store.get(key)
        if cached is not None:
            self.metrics.increment("revenue.invoice.idempotency_hit", tags=tags)
            return cached

        policy = await self.repository.get_policy(request.tenant_id, request.policy_id)
        policy.validate()
        customer = await self.repository.get_customer(request.tenant_id, request.customer_id)
        if not customer:
            raise RevenueValidationError(f"Customer not found: {request.customer_id}")

        currency = request.currency or customer.currency or policy.currency or self.config.default_currency
        due_date = request.due_date or (request.issue_date + timedelta(days=policy.default_payment_terms_days))
        invoice_number = request.invoice_number or self._generate_invoice_number(request.tenant_id, request.issue_date)
        normalized_lines = tuple(self._normalize_line(line, policy) for line in request.lines)

        invoice = Invoice(
            invoice_id=request.invoice_id,
            tenant_id=request.tenant_id,
            customer_id=request.customer_id,
            invoice_number=invoice_number,
            issue_date=request.issue_date,
            due_date=due_date,
            currency=currency,
            lines=normalized_lines,
            status=InvoiceStatus.ISSUED,
            paid_amount=Decimal("0"),
            metadata={**dict(request.metadata), "policy_id": policy.policy_id},
        )

        if invoice.total < 0 and not policy.allow_negative_invoice_total:
            raise RevenueValidationError("Invoice total cannot be negative under current policy.")

        engine = RevenueRecognitionEngine(policy)
        events = engine.build_events_for_invoice(invoice)
        await self.repository.save_invoice(invoice)
        await self.repository.save_revenue_events(events)
        await self.idempotency_store.set(key, invoice)

        self.metrics.increment("revenue.invoice.created", tags=tags)
        self.metrics.gauge("revenue.invoice.total", float(invoice.total), tags=tags)
        self.metrics.timing("revenue.invoice.create_ms", (time.perf_counter() - started) * 1000, tags=tags)
        await self._audit("revenue.invoice.created", {
            "tenant_id": invoice.tenant_id,
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "customer_hash": _hash_value(invoice.customer_id, self.config.privacy_hash_salt),
            "total": str(invoice.total),
            "currency": invoice.currency,
            "event_count": len(events),
        })
        return invoice

    async def record_payment(self, payment: PaymentRecord) -> Invoice:
        self._validate_payment(payment)
        invoice = await self.repository.get_invoice(payment.tenant_id, payment.invoice_id)
        if not invoice:
            raise RevenueValidationError(f"Invoice not found: {payment.invoice_id}")
        if invoice.status in {InvoiceStatus.VOID, InvoiceStatus.WRITTEN_OFF}:
            raise RevenueConflictError(f"Cannot record payment for invoice status {invoice.status.value}.")
        if payment.currency != invoice.currency:
            raise RevenueValidationError("Payment currency does not match invoice currency.")

        new_paid = invoice.paid_amount + payment.amount
        if new_paid <= 0:
            status = InvoiceStatus.ISSUED
        elif new_paid < invoice.total:
            status = InvoiceStatus.PARTIALLY_PAID
        else:
            status = InvoiceStatus.PAID
            new_paid = invoice.total

        await self.repository.save_payment(payment)
        await self.repository.update_invoice_status(payment.tenant_id, payment.invoice_id, status, new_paid)
        updated = await self.repository.get_invoice(payment.tenant_id, payment.invoice_id)
        assert updated is not None

        self.metrics.increment("revenue.payment.recorded", tags={"tenant_id": payment.tenant_id, "status": status.value})
        await self._audit("revenue.payment.recorded", {
            "tenant_id": payment.tenant_id,
            "invoice_id": payment.invoice_id,
            "payment_id": payment.payment_id,
            "amount": str(payment.amount),
            "status": status.value,
            "customer_hash": _hash_value(invoice.customer_id, self.config.privacy_hash_salt),
        })
        return updated

    async def void_invoice(self, tenant_id: str, invoice_id: str, actor_id: Optional[str] = None, reason: Optional[str] = None) -> None:
        invoice = await self.repository.get_invoice(tenant_id, invoice_id)
        if not invoice:
            raise RevenueValidationError(f"Invoice not found: {invoice_id}")
        if invoice.status == InvoiceStatus.PAID:
            raise RevenueConflictError("Paid invoices cannot be voided; create a refund/credit adjustment instead.")
        await self.repository.update_invoice_status(tenant_id, invoice_id, InvoiceStatus.VOID)
        reversal_events = [
            RevenueEvent(
                event_id=str(uuid.uuid4()),
                tenant_id=event.tenant_id,
                customer_id=event.customer_id,
                invoice_id=event.invoice_id,
                line_code=event.line_code,
                revenue_type=RevenueType.ADJUSTMENT,
                recognition_method=RevenueRecognitionMethod.MANUAL,
                event_date=_utc_now().date(),
                amount=-event.amount,
                currency=event.currency,
                status=RevenueEventStatus.REVERSED,
                recognized_period=event.recognized_period,
                metadata={"reason": reason, "voided_by_hash": _hash_value(actor_id, self.config.privacy_hash_salt)},
            )
            for event in await self.repository.list_revenue_events(tenant_id, RevenuePeriod(date.min, date.max))
            if event.invoice_id == invoice_id and event.status != RevenueEventStatus.REVERSED
        ]
        await self.repository.save_revenue_events(reversal_events)
        await self._audit("revenue.invoice.voided", {
            "tenant_id": tenant_id,
            "invoice_id": invoice_id,
            "actor_hash": _hash_value(actor_id, self.config.privacy_hash_salt),
            "reason": reason,
            "reversal_events": len(reversal_events),
        })

    async def summarize_revenue(self, tenant_id: str, period: RevenuePeriod, policy_id: Optional[str] = None) -> RevenueSummary:
        period.validate()
        policy = await self.repository.get_policy(tenant_id, policy_id)
        events, invoices, payments, subscriptions = await asyncio.gather(
            self.repository.list_revenue_events(tenant_id, period),
            self.repository.list_invoices(tenant_id, period),
            self.repository.list_payments(tenant_id, period),
            self.repository.list_subscriptions(tenant_id, period.end_date),
        )

        recognized = sum((e.amount for e in events if e.status in {RevenueEventStatus.RECOGNIZED, RevenueEventStatus.REVERSED}), Decimal("0"))
        deferred = sum((e.amount for e in events if e.status == RevenueEventStatus.DEFERRED), Decimal("0"))
        invoiced = sum((i.total for i in invoices if i.status != InvoiceStatus.VOID), Decimal("0"))
        collected = sum((p.amount for p in payments), Decimal("0"))
        tax_total = sum((i.tax_total for i in invoices if i.status != InvoiceStatus.VOID), Decimal("0"))
        refunds = abs(sum((e.amount for e in events if e.revenue_type == RevenueType.REFUND), Decimal("0")))
        adjustments = sum((e.amount for e in events if e.revenue_type == RevenueType.ADJUSTMENT), Decimal("0"))
        mrr = sum((s.mrr for s in subscriptions), Decimal("0"))
        arr = mrr * Decimal("12")
        active_customers = len({s.customer_id for s in subscriptions})
        paying_customers = len({i.customer_id for i in invoices if i.status in {InvoiceStatus.PAID, InvoiceStatus.PARTIALLY_PAID}})
        ar_balance = sum((i.balance_due for i in invoices if i.status not in {InvoiceStatus.PAID, InvoiceStatus.VOID, InvoiceStatus.WRITTEN_OFF}), Decimal("0"))

        summary = RevenueSummary(
            tenant_id=tenant_id,
            period=period,
            currency=policy.currency,
            recognized_revenue=_round_money(recognized, policy.rounding_quantum),
            deferred_revenue=_round_money(deferred, policy.rounding_quantum),
            invoiced_revenue=_round_money(invoiced, policy.rounding_quantum),
            collected_cash=_round_money(collected, policy.rounding_quantum),
            tax_total=_round_money(tax_total, policy.rounding_quantum),
            refunds=_round_money(refunds, policy.rounding_quantum),
            adjustments=_round_money(adjustments, policy.rounding_quantum),
            mrr=_round_money(mrr, policy.rounding_quantum),
            arr=_round_money(arr, policy.rounding_quantum),
            active_customers=active_customers,
            paying_customers=paying_customers,
            ar_balance=_round_money(ar_balance, policy.rounding_quantum),
            created_at=_utc_now(),
            metadata={
                "invoice_count": len(invoices),
                "event_count": len(events),
                "payment_count": len(payments),
                "subscription_count": len(subscriptions),
                "policy_id": policy.policy_id,
            },
        )
        self.metrics.gauge("revenue.recognized", float(summary.recognized_revenue), tags={"tenant_id": tenant_id})
        self.metrics.gauge("revenue.mrr", float(summary.mrr), tags={"tenant_id": tenant_id})
        await self._audit("revenue.summary.generated", {
            "tenant_id": tenant_id,
            "period": period.key,
            "recognized_revenue": str(summary.recognized_revenue),
            "mrr": str(summary.mrr),
            "arr": str(summary.arr),
            "ar_balance": str(summary.ar_balance),
        })
        return summary

    def _normalize_line(self, line: RevenueLine, policy: RevenuePolicyConfig) -> RevenueLine:
        if line.quantity <= 0:
            raise RevenueValidationError(f"Line {line.code} quantity must be positive.")
        if line.unit_price < 0 and line.line_type not in {RevenueLineType.REFUND, RevenueLineType.ADJUSTMENT, RevenueLineType.DISCOUNT}:
            raise RevenueValidationError(f"Line {line.code} unit_price cannot be negative for {line.line_type.value}.")
        tax_rate = line.tax_rate if line.tax_rate != 0 else policy.default_tax_rate
        if tax_rate < 0:
            raise RevenueValidationError(f"Line {line.code} tax_rate cannot be negative.")
        if line.service_period:
            line.service_period.validate()
        return RevenueLine(
            code=line.code,
            description=line.description,
            quantity=line.quantity,
            unit_price=_round_money(line.unit_price, policy.rounding_quantum),
            line_type=line.line_type,
            discount_amount=_round_money(line.discount_amount, policy.rounding_quantum),
            tax_rate=tax_rate,
            recognition_method=line.recognition_method,
            service_period=line.service_period,
            metadata=line.metadata,
        )

    def _validate_invoice_request(self, request: CreateInvoiceRequest) -> None:
        if not request.tenant_id:
            raise RevenueValidationError("tenant_id is required.")
        if not request.customer_id:
            raise RevenueValidationError("customer_id is required.")
        if not request.lines:
            raise RevenueValidationError("invoice lines cannot be empty.")
        if len(request.lines) > self.config.max_invoice_lines:
            raise RevenueValidationError(f"invoice line count exceeds max_invoice_lines={self.config.max_invoice_lines}.")
        if request.due_date and request.due_date < request.issue_date:
            raise RevenueValidationError("due_date cannot be before issue_date.")

    def _validate_payment(self, payment: PaymentRecord) -> None:
        if not payment.tenant_id or not payment.invoice_id or not payment.payment_id:
            raise RevenueValidationError("payment_id, tenant_id and invoice_id are required.")
        if payment.amount <= 0:
            raise RevenueValidationError("payment amount must be positive.")

    def _generate_invoice_number(self, tenant_id: str, issue_date: date) -> str:
        tenant_part = hashlib.sha1(tenant_id.encode("utf-8")).hexdigest()[:6].upper()
        return f"INV-{tenant_part}-{issue_date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    def _idempotency_key(self, namespace: str, provided: Optional[str], payload: Mapping[str, Any]) -> str:
        if provided:
            return f"revenue:{namespace}:{provided}"
        return f"revenue:{namespace}:{_stable_hash(payload)}"

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write revenue audit event", extra={"event_name": event_name})

    @classmethod
    def invoice_request_from_payload(cls, payload: Mapping[str, Any]) -> CreateInvoiceRequest:
        lines = []
        for item in payload.get("lines", []):
            service_period = None
            if item.get("service_period"):
                service_period = RevenuePeriod(
                    start_date=_date_from_any(item["service_period"].get("start_date"), "service_period.start_date"),
                    end_date=_date_from_any(item["service_period"].get("end_date"), "service_period.end_date"),
                )
            lines.append(
                RevenueLine(
                    code=str(item["code"]),
                    description=str(item.get("description") or item["code"]),
                    quantity=Decimal(str(item.get("quantity", "1"))),
                    unit_price=Decimal(str(item.get("unit_price", "0"))),
                    line_type=RevenueLineType(item.get("line_type", RevenueLineType.PRODUCT.value)),
                    discount_amount=Decimal(str(item.get("discount_amount", "0"))),
                    tax_rate=Decimal(str(item.get("tax_rate", "0"))),
                    recognition_method=RevenueRecognitionMethod(item.get("recognition_method", RevenueRecognitionMethod.IMMEDIATE.value)),
                    service_period=service_period,
                    metadata=item.get("metadata") or {},
                )
            )
        return CreateInvoiceRequest(
            tenant_id=str(payload["tenant_id"]),
            customer_id=str(payload["customer_id"]),
            lines=tuple(lines),
            issue_date=_date_from_any(payload.get("issue_date", datetime.now(UTC).date()), "issue_date"),
            due_date=_date_from_any(payload["due_date"], "due_date") if payload.get("due_date") else None,
            invoice_id=str(payload.get("invoice_id") or uuid.uuid4()),
            invoice_number=payload.get("invoice_number"),
            currency=payload.get("currency"),
            policy_id=payload.get("policy_id"),
            idempotency_key=payload.get("idempotency_key"),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_revenue_service(
    repository: Optional[RevenueRepository] = None,
    config: Optional[RevenueServiceConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> RevenueService:
    return RevenueService(
        repository=repository or InMemoryRevenueRepository(),
        config=config,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    policy = RevenuePolicyConfig(
        policy_id="ao-demo-revenue",
        currency="AOA",
        default_payment_terms_days=15,
        default_tax_rate=Decimal("0.14"),
        recognize_tax_as_revenue=False,
    )
    repo = InMemoryRevenueRepository(default_policy=policy)
    repo.add_customer(CustomerProfile(customer_id="CUST-001", tenant_id="tenant-ao", name="Cliente Demo", currency="AOA"))
    repo.add_subscription(
        SubscriptionContract(
            contract_id="SUB-001",
            tenant_id="tenant-ao",
            customer_id="CUST-001",
            product_code="KWANZA-AI-PRO",
            start_date=date(2026, 5, 1),
            end_date=None,
            mrr=Decimal("150000"),
            currency="AOA",
        )
    )
    service = build_revenue_service(
        repository=repo,
        config=RevenueServiceConfig(default_currency="AOA", privacy_hash_salt="local-dev-salt"),
    )

    invoice = await service.create_invoice(
        CreateInvoiceRequest(
            tenant_id="tenant-ao",
            customer_id="CUST-001",
            issue_date=date(2026, 5, 14),
            lines=(
                RevenueLine(
                    code="KWANZA-AI-PRO",
                    description="Kwanza AI Pro subscription",
                    quantity=Decimal("1"),
                    unit_price=Decimal("150000"),
                    line_type=RevenueLineType.SUBSCRIPTION,
                    recognition_method=RevenueRecognitionMethod.STRAIGHT_LINE,
                    service_period=RevenuePeriod(date(2026, 5, 1), date(2026, 5, 31)),
                ),
                RevenueLine(
                    code="SETUP",
                    description="Implementation setup",
                    quantity=Decimal("1"),
                    unit_price=Decimal("50000"),
                    line_type=RevenueLineType.SERVICE,
                    recognition_method=RevenueRecognitionMethod.IMMEDIATE,
                ),
            ),
            idempotency_key="demo-invoice-001",
        )
    )
    print(json.dumps(invoice.to_dict(), indent=2, ensure_ascii=False, default=str))

    await service.record_payment(
        PaymentRecord(
            payment_id="PAY-001",
            tenant_id="tenant-ao",
            invoice_id=invoice.invoice_id,
            amount=invoice.total,
            currency="AOA",
            reference="bank-transfer-demo",
        )
    )
    summary = await service.summarize_revenue("tenant-ao", RevenuePeriod(date(2026, 5, 1), date(2026, 5, 31)))
    print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
