"""
kwanza-ai-core/services/payroll_service.py

Enterprise-grade payroll service layer.

Purpose
-------
Centralize payroll processing for employees, contractors, teams and branches:
gross salary, allowances, benefits, deductions, tax-like rules, net salary,
payslip generation, approvals, audit trails and payment-export preparation.

Design goals
------------
- Multi-tenant and currency-aware payroll engine.
- Deterministic, auditable calculations with explainable line items.
- Configurable earnings/deductions/taxes without hardcoding jurisdiction rules.
- Idempotent payroll runs to prevent duplicate processing.
- Async-first repository/payment abstractions.
- Batch payroll processing with partial failure reporting.
- Production hooks: metrics, audit, structured logs and safe fallbacks.

Important
---------
This module provides a configurable payroll calculation engine. It does not
pretend to encode official tax/labor law for any country by default. Production
systems must supply validated statutory rules through PayrollPolicyConfig or a
jurisdiction-specific policy adapter reviewed by qualified professionals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)
getcontext().prec = 28

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]
Money = Decimal


# =============================================================================
# Exceptions
# =============================================================================


class PayrollServiceError(RuntimeError):
    """Base exception for payroll service failures."""


class PayrollValidationError(PayrollServiceError):
    """Raised when payroll input data is invalid."""


class PayrollPolicyError(PayrollServiceError):
    """Raised when payroll policy configuration is invalid."""


class PayrollConflictError(PayrollServiceError):
    """Raised when an idempotency or state conflict occurs."""


class PayrollDependencyError(PayrollServiceError):
    """Raised when a required dependency fails."""


# =============================================================================
# Enums
# =============================================================================


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACTOR = "contractor"
    INTERN = "intern"
    TEMPORARY = "temporary"


class PayFrequency(str, Enum):
    MONTHLY = "monthly"
    BIWEEKLY = "biweekly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"


class PayrollRunStatus(str, Enum):
    DRAFT = "draft"
    CALCULATED = "calculated"
    APPROVED = "approved"
    EXPORTED = "exported"
    PAID = "paid"
    CANCELLED = "cancelled"
    FAILED = "failed"


class PayrollLineType(str, Enum):
    EARNING = "earning"
    ALLOWANCE = "allowance"
    BENEFIT = "benefit"
    DEDUCTION = "deduction"
    TAX = "tax"
    EMPLOYER_CONTRIBUTION = "employer_contribution"
    ADJUSTMENT = "adjustment"


class PayrollLineTaxability(str, Enum):
    TAXABLE = "taxable"
    NON_TAXABLE = "non_taxable"
    PRE_TAX_DEDUCTION = "pre_tax_deduction"
    POST_TAX_DEDUCTION = "post_tax_deduction"


class PaymentMethod(str, Enum):
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    CHECK = "check"
    INTERNAL_WALLET = "internal_wallet"


class ApprovalAction(str, Enum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"


# =============================================================================
# Data models
# =============================================================================


@dataclass(frozen=True)
class PayrollPeriod:
    start_date: date
    end_date: date
    pay_date: date

    def validate(self) -> None:
        if self.end_date < self.start_date:
            raise PayrollValidationError("period.end_date cannot be before period.start_date.")
        if self.pay_date < self.start_date:
            raise PayrollValidationError("period.pay_date cannot be before period.start_date.")

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def key(self) -> str:
        return f"{self.start_date.isoformat()}:{self.end_date.isoformat()}:{self.pay_date.isoformat()}"


@dataclass(frozen=True)
class BankAccount:
    bank_name: Optional[str] = None
    account_holder: Optional[str] = None
    account_number: Optional[str] = None
    iban: Optional[str] = None
    swift: Optional[str] = None
    branch_code: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmployeeProfile:
    employee_id: str
    tenant_id: str
    full_name: str
    base_salary: Money
    currency: str = "AOA"
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    pay_frequency: PayFrequency = PayFrequency.MONTHLY
    department: Optional[str] = None
    cost_center: Optional[str] = None
    position: Optional[str] = None
    hire_date: Optional[date] = None
    termination_date: Optional[date] = None
    tax_id: Optional[str] = None
    payment_method: PaymentMethod = PaymentMethod.BANK_TRANSFER
    bank_account: Optional[BankAccount] = None
    standard_hours_per_period: Decimal = Decimal("173.33")
    hourly_rate: Optional[Money] = None
    is_active: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeEntrySummary:
    regular_hours: Decimal = Decimal("0")
    overtime_hours: Decimal = Decimal("0")
    absence_hours: Decimal = Decimal("0")
    leave_paid_hours: Decimal = Decimal("0")
    holiday_hours: Decimal = Decimal("0")
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PayrollInputLine:
    code: str
    description: str
    amount: Money
    line_type: PayrollLineType
    taxability: PayrollLineTaxability = PayrollLineTaxability.TAXABLE
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PayrollPolicyBracket:
    lower_bound: Money
    upper_bound: Optional[Money]
    rate: Decimal
    fixed_amount: Money = Decimal("0")

    def validate(self) -> None:
        if self.lower_bound < 0:
            raise PayrollPolicyError("Bracket lower_bound cannot be negative.")
        if self.upper_bound is not None and self.upper_bound <= self.lower_bound:
            raise PayrollPolicyError("Bracket upper_bound must be greater than lower_bound.")
        if self.rate < 0:
            raise PayrollPolicyError("Bracket rate cannot be negative.")


@dataclass(frozen=True)
class PayrollPolicyConfig:
    policy_id: str = "default"
    currency: str = "AOA"
    overtime_multiplier: Decimal = Decimal("1.5")
    absence_deduction_enabled: bool = True
    taxable_benefits_enabled: bool = True
    employee_tax_brackets: Sequence[PayrollPolicyBracket] = field(default_factory=tuple)
    employee_social_security_rate: Decimal = Decimal("0")
    employer_social_security_rate: Decimal = Decimal("0")
    employee_social_security_cap: Optional[Money] = None
    employer_social_security_cap: Optional[Money] = None
    minimum_net_pay: Optional[Money] = None
    rounding_quantum: Decimal = Decimal("0.01")
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise PayrollPolicyError("policy_id is required.")
        if self.overtime_multiplier < 0:
            raise PayrollPolicyError("overtime_multiplier cannot be negative.")
        if self.employee_social_security_rate < 0 or self.employer_social_security_rate < 0:
            raise PayrollPolicyError("Social security rates cannot be negative.")
        if self.rounding_quantum <= 0:
            raise PayrollPolicyError("rounding_quantum must be positive.")
        for bracket in self.employee_tax_brackets:
            bracket.validate()


@dataclass(frozen=True)
class PayrollCalculationRequest:
    tenant_id: str
    period: PayrollPeriod
    employee_ids: Optional[Sequence[str]] = None
    payroll_run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    requested_by: Optional[str] = None
    idempotency_key: Optional[str] = None
    policy_id: Optional[str] = None
    dry_run: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PayrollLineItem:
    code: str
    description: str
    amount: Money
    line_type: PayrollLineType
    taxability: PayrollLineTaxability
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    source: str = "system"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def signed_amount(self) -> Money:
        if self.line_type in {PayrollLineType.DEDUCTION, PayrollLineType.TAX}:
            return -self.amount
        return self.amount


@dataclass(frozen=True)
class PayrollEmployeeResult:
    tenant_id: str
    payroll_run_id: str
    employee_id: str
    employee_name: str
    currency: str
    gross_pay: Money
    taxable_income: Money
    total_deductions: Money
    total_taxes: Money
    employer_contributions: Money
    net_pay: Money
    line_items: Sequence[PayrollLineItem]
    warnings: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        for money_key in [
            "gross_pay",
            "taxable_income",
            "total_deductions",
            "total_taxes",
            "employer_contributions",
            "net_pay",
        ]:
            payload[money_key] = str(payload[money_key])
        for line in payload["line_items"]:
            line["amount"] = str(line["amount"])
            line["line_type"] = line["line_type"].value if hasattr(line["line_type"], "value") else line["line_type"]
            line["taxability"] = line["taxability"].value if hasattr(line["taxability"], "value") else line["taxability"]
            if line.get("quantity") is not None:
                line["quantity"] = str(line["quantity"])
            if line.get("rate") is not None:
                line["rate"] = str(line["rate"])
        return payload


@dataclass(frozen=True)
class PayrollRunResult:
    payroll_run_id: str
    tenant_id: str
    period: PayrollPeriod
    status: PayrollRunStatus
    currency: str
    employees: Sequence[PayrollEmployeeResult]
    total_gross_pay: Money
    total_net_pay: Money
    total_taxes: Money
    total_deductions: Money
    total_employer_contributions: Money
    succeeded: int
    failed: int
    warnings: Sequence[str]
    processing_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["period"] = {
            "start_date": self.period.start_date.isoformat(),
            "end_date": self.period.end_date.isoformat(),
            "pay_date": self.period.pay_date.isoformat(),
        }
        payload["created_at"] = self.created_at.isoformat()
        for money_key in [
            "total_gross_pay",
            "total_net_pay",
            "total_taxes",
            "total_deductions",
            "total_employer_contributions",
        ]:
            payload[money_key] = str(payload[money_key])
        payload["employees"] = [employee.to_dict() for employee in self.employees]
        return payload


@dataclass(frozen=True)
class Payslip:
    payslip_id: str
    payroll_run_id: str
    tenant_id: str
    employee_id: str
    employee_name: str
    period: PayrollPeriod
    currency: str
    gross_pay: Money
    net_pay: Money
    line_items: Sequence[PayrollLineItem]
    issued_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "payslip_id": self.payslip_id,
            "payroll_run_id": self.payroll_run_id,
            "tenant_id": self.tenant_id,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "period": {
                "start_date": self.period.start_date.isoformat(),
                "end_date": self.period.end_date.isoformat(),
                "pay_date": self.period.pay_date.isoformat(),
            },
            "currency": self.currency,
            "gross_pay": str(self.gross_pay),
            "net_pay": str(self.net_pay),
            "line_items": [
                {
                    "code": line.code,
                    "description": line.description,
                    "amount": str(line.amount),
                    "line_type": line.line_type.value,
                    "taxability": line.taxability.value,
                    "source": line.source,
                }
                for line in self.line_items
            ],
            "issued_at": self.issued_at.isoformat(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PaymentInstruction:
    instruction_id: str
    payroll_run_id: str
    tenant_id: str
    employee_id: str
    employee_name: str
    amount: Money
    currency: str
    method: PaymentMethod
    bank_account: Optional[BankAccount]
    reference: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["amount"] = str(self.amount)
        payload["method"] = self.method.value
        return payload


@dataclass(frozen=True)
class PayrollServiceConfig:
    default_currency: str = "AOA"
    fail_fast: bool = False
    max_employees_per_run: int = 10_000
    audit_enabled: bool = True
    idempotency_ttl_seconds: int = 86_400
    privacy_hash_salt: str = "change-me-in-production"
    allow_negative_net_pay: bool = False

    def validate(self) -> None:
        if self.max_employees_per_run <= 0:
            raise PayrollValidationError("max_employees_per_run must be positive.")
        if self.idempotency_ttl_seconds <= 0:
            raise PayrollValidationError("idempotency_ttl_seconds must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class PayrollRepository(Protocol):
    async def list_employees(self, tenant_id: str, employee_ids: Optional[Sequence[str]] = None) -> Sequence[EmployeeProfile]: ...

    async def get_time_summary(self, tenant_id: str, employee_id: str, period: PayrollPeriod) -> Optional[TimeEntrySummary]: ...

    async def get_input_lines(self, tenant_id: str, employee_id: str, period: PayrollPeriod) -> Sequence[PayrollInputLine]: ...

    async def get_policy(self, tenant_id: str, policy_id: Optional[str]) -> PayrollPolicyConfig: ...

    async def save_payroll_run(self, result: PayrollRunResult) -> None: ...

    async def get_payroll_run(self, tenant_id: str, payroll_run_id: str) -> Optional[PayrollRunResult]: ...

    async def update_payroll_status(self, tenant_id: str, payroll_run_id: str, status: PayrollRunStatus, actor_id: Optional[str]) -> None: ...


class PaymentExporter(Protocol):
    async def export_payment_instructions(self, instructions: Sequence[PaymentInstruction]) -> Mapping[str, Any]: ...


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


class InMemoryPaymentExporter:
    async def export_payment_instructions(self, instructions: Sequence[PaymentInstruction]) -> Mapping[str, Any]:
        return {
            "export_id": str(uuid.uuid4()),
            "count": len(instructions),
            "total_amount": str(sum((i.amount for i in instructions), Decimal("0"))),
            "created_at": _utc_now().isoformat(),
        }


class InMemoryPayrollRepository:
    def __init__(self, default_policy: Optional[PayrollPolicyConfig] = None) -> None:
        self._employees: Dict[Tuple[str, str], EmployeeProfile] = {}
        self._time: Dict[Tuple[str, str, str], TimeEntrySummary] = {}
        self._input_lines: Dict[Tuple[str, str, str], List[PayrollInputLine]] = {}
        self._runs: Dict[Tuple[str, str], PayrollRunResult] = {}
        self._statuses: Dict[Tuple[str, str], PayrollRunStatus] = {}
        self.default_policy = default_policy or PayrollPolicyConfig()

    def add_employee(self, employee: EmployeeProfile) -> None:
        self._employees[(employee.tenant_id, employee.employee_id)] = employee

    def set_time_summary(self, tenant_id: str, employee_id: str, period: PayrollPeriod, summary: TimeEntrySummary) -> None:
        self._time[(tenant_id, employee_id, period.key)] = summary

    def add_input_line(self, tenant_id: str, employee_id: str, period: PayrollPeriod, line: PayrollInputLine) -> None:
        self._input_lines.setdefault((tenant_id, employee_id, period.key), []).append(line)

    async def list_employees(self, tenant_id: str, employee_ids: Optional[Sequence[str]] = None) -> Sequence[EmployeeProfile]:
        allowed = set(employee_ids or [])
        rows = [employee for (tid, _), employee in self._employees.items() if tid == tenant_id]
        if allowed:
            rows = [employee for employee in rows if employee.employee_id in allowed]
        return sorted(rows, key=lambda e: e.employee_id)

    async def get_time_summary(self, tenant_id: str, employee_id: str, period: PayrollPeriod) -> Optional[TimeEntrySummary]:
        return self._time.get((tenant_id, employee_id, period.key))

    async def get_input_lines(self, tenant_id: str, employee_id: str, period: PayrollPeriod) -> Sequence[PayrollInputLine]:
        return list(self._input_lines.get((tenant_id, employee_id, period.key), []))

    async def get_policy(self, tenant_id: str, policy_id: Optional[str]) -> PayrollPolicyConfig:
        return self.default_policy

    async def save_payroll_run(self, result: PayrollRunResult) -> None:
        self._runs[(result.tenant_id, result.payroll_run_id)] = result
        self._statuses[(result.tenant_id, result.payroll_run_id)] = result.status

    async def get_payroll_run(self, tenant_id: str, payroll_run_id: str) -> Optional[PayrollRunResult]:
        return self._runs.get((tenant_id, payroll_run_id))

    async def update_payroll_status(self, tenant_id: str, payroll_run_id: str, status: PayrollRunStatus, actor_id: Optional[str]) -> None:
        key = (tenant_id, payroll_run_id)
        if key not in self._runs:
            raise PayrollValidationError(f"Payroll run not found: {payroll_run_id}")
        current = self._runs[key]
        updated = PayrollRunResult(
            payroll_run_id=current.payroll_run_id,
            tenant_id=current.tenant_id,
            period=current.period,
            status=status,
            currency=current.currency,
            employees=current.employees,
            total_gross_pay=current.total_gross_pay,
            total_net_pay=current.total_net_pay,
            total_taxes=current.total_taxes,
            total_deductions=current.total_deductions,
            total_employer_contributions=current.total_employer_contributions,
            succeeded=current.succeeded,
            failed=current.failed,
            warnings=current.warnings,
            processing_ms=current.processing_ms,
            created_at=current.created_at,
            metadata={**dict(current.metadata), "last_status_actor": actor_id, "last_status_at": _utc_now().isoformat()},
        )
        self._runs[key] = updated
        self._statuses[key] = status


# =============================================================================
# Idempotency cache
# =============================================================================


class AsyncIdempotencyStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
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
        raise PayrollValidationError(f"Invalid money value for {field_name}: {value!r}") from exc


def _decimal(value: Any, field_name: str = "value") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PayrollValidationError(f"Invalid decimal value for {field_name}: {value!r}") from exc


def _round_money(value: Money, quantum: Decimal = Decimal("0.01")) -> Money:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _stable_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _date_from_any(value: Any, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PayrollValidationError(f"Invalid date for {field_name}: {value!r}") from exc
    raise PayrollValidationError(f"Invalid date for {field_name}: {value!r}")


# =============================================================================
# Payroll calculator
# =============================================================================


class PayrollCalculator:
    def __init__(self, policy: PayrollPolicyConfig, allow_negative_net_pay: bool = False) -> None:
        self.policy = policy
        self.policy.validate()
        self.allow_negative_net_pay = allow_negative_net_pay

    def calculate_employee(
        self,
        tenant_id: str,
        payroll_run_id: str,
        employee: EmployeeProfile,
        period: PayrollPeriod,
        time_summary: Optional[TimeEntrySummary],
        input_lines: Sequence[PayrollInputLine],
    ) -> PayrollEmployeeResult:
        self._validate_employee(employee, tenant_id)
        warnings: List[str] = []
        line_items: List[PayrollLineItem] = []
        time_summary = time_summary or TimeEntrySummary()

        base_line = self._base_pay_line(employee, period, time_summary)
        line_items.append(base_line)

        overtime_line = self._overtime_line(employee, time_summary)
        if overtime_line:
            line_items.append(overtime_line)

        absence_line = self._absence_line(employee, time_summary)
        if absence_line:
            line_items.append(absence_line)

        for input_line in input_lines:
            if input_line.amount < 0:
                warnings.append(f"Input line {input_line.code} has negative amount and was converted to absolute value.")
            line_items.append(
                PayrollLineItem(
                    code=input_line.code,
                    description=input_line.description,
                    amount=_round_money(abs(input_line.amount), self.policy.rounding_quantum),
                    line_type=input_line.line_type,
                    taxability=input_line.taxability,
                    quantity=input_line.quantity,
                    rate=input_line.rate,
                    source="input",
                    metadata=input_line.metadata,
                )
            )

        gross_pay = self._sum_lines(
            line_items,
            include_types={PayrollLineType.EARNING, PayrollLineType.ALLOWANCE, PayrollLineType.BENEFIT, PayrollLineType.ADJUSTMENT},
            exclude_taxability={PayrollLineTaxability.PRE_TAX_DEDUCTION, PayrollLineTaxability.POST_TAX_DEDUCTION},
        )

        pre_tax_deductions = self._sum_lines(line_items, include_taxability={PayrollLineTaxability.PRE_TAX_DEDUCTION})
        taxable_income = self._calculate_taxable_income(line_items, gross_pay, pre_tax_deductions)

        employee_social_security = self._social_security_amount(
            taxable_income,
            self.policy.employee_social_security_rate,
            self.policy.employee_social_security_cap,
        )
        if employee_social_security > 0:
            line_items.append(
                PayrollLineItem(
                    code="EMPLOYEE_SOCIAL_SECURITY",
                    description="Employee social security contribution",
                    amount=employee_social_security,
                    line_type=PayrollLineType.DEDUCTION,
                    taxability=PayrollLineTaxability.PRE_TAX_DEDUCTION,
                    source="policy",
                    metadata={"rate": str(self.policy.employee_social_security_rate)},
                )
            )
            taxable_income = max(Decimal("0"), taxable_income - employee_social_security)

        income_tax = self._progressive_tax(taxable_income)
        if income_tax > 0:
            line_items.append(
                PayrollLineItem(
                    code="EMPLOYEE_INCOME_TAX",
                    description="Employee income tax / withholding",
                    amount=income_tax,
                    line_type=PayrollLineType.TAX,
                    taxability=PayrollLineTaxability.POST_TAX_DEDUCTION,
                    source="policy",
                    metadata={"policy_id": self.policy.policy_id},
                )
            )

        employer_social_security = self._social_security_amount(
            taxable_income,
            self.policy.employer_social_security_rate,
            self.policy.employer_social_security_cap,
        )
        if employer_social_security > 0:
            line_items.append(
                PayrollLineItem(
                    code="EMPLOYER_SOCIAL_SECURITY",
                    description="Employer social security contribution",
                    amount=employer_social_security,
                    line_type=PayrollLineType.EMPLOYER_CONTRIBUTION,
                    taxability=PayrollLineTaxability.NON_TAXABLE,
                    source="policy",
                    metadata={"rate": str(self.policy.employer_social_security_rate)},
                )
            )

        total_deductions = self._sum_lines(line_items, include_types={PayrollLineType.DEDUCTION})
        total_taxes = self._sum_lines(line_items, include_types={PayrollLineType.TAX})
        employer_contributions = self._sum_lines(line_items, include_types={PayrollLineType.EMPLOYER_CONTRIBUTION})
        net_pay = gross_pay - total_deductions - total_taxes

        if self.policy.minimum_net_pay is not None and net_pay < self.policy.minimum_net_pay:
            warnings.append(f"Net pay below configured minimum_net_pay={self.policy.minimum_net_pay}.")

        if net_pay < 0 and not self.allow_negative_net_pay:
            warnings.append("Net pay was negative and has been clamped to zero.")
            net_pay = Decimal("0")

        line_items = [self._rounded_line(line) for line in line_items]

        return PayrollEmployeeResult(
            tenant_id=tenant_id,
            payroll_run_id=payroll_run_id,
            employee_id=employee.employee_id,
            employee_name=employee.full_name,
            currency=employee.currency,
            gross_pay=_round_money(gross_pay, self.policy.rounding_quantum),
            taxable_income=_round_money(taxable_income, self.policy.rounding_quantum),
            total_deductions=_round_money(total_deductions, self.policy.rounding_quantum),
            total_taxes=_round_money(total_taxes, self.policy.rounding_quantum),
            employer_contributions=_round_money(employer_contributions, self.policy.rounding_quantum),
            net_pay=_round_money(net_pay, self.policy.rounding_quantum),
            line_items=line_items,
            warnings=tuple(warnings),
            metadata={
                "department": employee.department,
                "cost_center": employee.cost_center,
                "employment_type": employee.employment_type.value,
                "pay_frequency": employee.pay_frequency.value,
                "policy_id": self.policy.policy_id,
            },
        )

    def _base_pay_line(self, employee: EmployeeProfile, period: PayrollPeriod, time_summary: TimeEntrySummary) -> PayrollLineItem:
        if employee.pay_frequency == PayFrequency.HOURLY:
            hourly_rate = employee.hourly_rate or self._hourly_rate(employee)
            amount = hourly_rate * time_summary.regular_hours
            quantity = time_summary.regular_hours
            rate = hourly_rate
            description = "Regular hourly pay"
        else:
            amount = employee.base_salary
            quantity = None
            rate = None
            description = "Base salary"

        return PayrollLineItem(
            code="BASE_PAY",
            description=description,
            amount=_round_money(amount, self.policy.rounding_quantum),
            line_type=PayrollLineType.EARNING,
            taxability=PayrollLineTaxability.TAXABLE,
            quantity=quantity,
            rate=rate,
            source="contract",
        )

    def _overtime_line(self, employee: EmployeeProfile, time_summary: TimeEntrySummary) -> Optional[PayrollLineItem]:
        if time_summary.overtime_hours <= 0:
            return None
        hourly_rate = self._hourly_rate(employee)
        rate = hourly_rate * self.policy.overtime_multiplier
        amount = rate * time_summary.overtime_hours
        return PayrollLineItem(
            code="OVERTIME_PAY",
            description="Overtime pay",
            amount=_round_money(amount, self.policy.rounding_quantum),
            line_type=PayrollLineType.EARNING,
            taxability=PayrollLineTaxability.TAXABLE,
            quantity=time_summary.overtime_hours,
            rate=_round_money(rate, self.policy.rounding_quantum),
            source="time_tracking",
            metadata={"multiplier": str(self.policy.overtime_multiplier)},
        )

    def _absence_line(self, employee: EmployeeProfile, time_summary: TimeEntrySummary) -> Optional[PayrollLineItem]:
        if not self.policy.absence_deduction_enabled or time_summary.absence_hours <= 0:
            return None
        hourly_rate = self._hourly_rate(employee)
        amount = hourly_rate * time_summary.absence_hours
        return PayrollLineItem(
            code="ABSENCE_DEDUCTION",
            description="Unpaid absence deduction",
            amount=_round_money(amount, self.policy.rounding_quantum),
            line_type=PayrollLineType.DEDUCTION,
            taxability=PayrollLineTaxability.PRE_TAX_DEDUCTION,
            quantity=time_summary.absence_hours,
            rate=_round_money(hourly_rate, self.policy.rounding_quantum),
            source="time_tracking",
        )

    def _hourly_rate(self, employee: EmployeeProfile) -> Money:
        if employee.hourly_rate is not None:
            return employee.hourly_rate
        if employee.standard_hours_per_period <= 0:
            raise PayrollValidationError(f"Employee {employee.employee_id} has invalid standard_hours_per_period.")
        return employee.base_salary / employee.standard_hours_per_period

    def _calculate_taxable_income(self, line_items: Sequence[PayrollLineItem], gross_pay: Money, pre_tax_deductions: Money) -> Money:
        non_taxable = self._sum_lines(line_items, include_taxability={PayrollLineTaxability.NON_TAXABLE})
        taxable_benefits = Decimal("0")
        if self.policy.taxable_benefits_enabled:
            taxable_benefits = self._sum_lines(
                line_items,
                include_types={PayrollLineType.BENEFIT},
                include_taxability={PayrollLineTaxability.TAXABLE},
            )
        taxable_income = gross_pay - non_taxable - pre_tax_deductions + taxable_benefits
        return max(Decimal("0"), taxable_income)

    def _social_security_amount(self, base: Money, rate: Decimal, cap: Optional[Money]) -> Money:
        if rate <= 0 or base <= 0:
            return Decimal("0")
        contribution_base = min(base, cap) if cap is not None else base
        return _round_money(contribution_base * rate, self.policy.rounding_quantum)

    def _progressive_tax(self, taxable_income: Money) -> Money:
        if taxable_income <= 0 or not self.policy.employee_tax_brackets:
            return Decimal("0")
        total = Decimal("0")
        for bracket in self.policy.employee_tax_brackets:
            if taxable_income <= bracket.lower_bound:
                continue
            upper = bracket.upper_bound if bracket.upper_bound is not None else taxable_income
            amount_in_bracket = min(taxable_income, upper) - bracket.lower_bound
            if amount_in_bracket > 0:
                total += bracket.fixed_amount + (amount_in_bracket * bracket.rate)
        return _round_money(max(Decimal("0"), total), self.policy.rounding_quantum)

    def _sum_lines(
        self,
        line_items: Sequence[PayrollLineItem],
        include_types: Optional[set[PayrollLineType]] = None,
        exclude_types: Optional[set[PayrollLineType]] = None,
        include_taxability: Optional[set[PayrollLineTaxability]] = None,
        exclude_taxability: Optional[set[PayrollLineTaxability]] = None,
    ) -> Money:
        total = Decimal("0")
        for line in line_items:
            if include_types is not None and line.line_type not in include_types:
                continue
            if exclude_types is not None and line.line_type in exclude_types:
                continue
            if include_taxability is not None and line.taxability not in include_taxability:
                continue
            if exclude_taxability is not None and line.taxability in exclude_taxability:
                continue
            total += line.amount
        return _round_money(total, self.policy.rounding_quantum)

    def _rounded_line(self, line: PayrollLineItem) -> PayrollLineItem:
        return PayrollLineItem(
            code=line.code,
            description=line.description,
            amount=_round_money(line.amount, self.policy.rounding_quantum),
            line_type=line.line_type,
            taxability=line.taxability,
            quantity=line.quantity,
            rate=_round_money(line.rate, self.policy.rounding_quantum) if line.rate is not None else None,
            source=line.source,
            metadata=line.metadata,
        )

    def _validate_employee(self, employee: EmployeeProfile, tenant_id: str) -> None:
        if employee.tenant_id != tenant_id:
            raise PayrollValidationError("Employee tenant_id does not match payroll request tenant_id.")
        if not employee.employee_id:
            raise PayrollValidationError("employee_id is required.")
        if not employee.full_name:
            raise PayrollValidationError(f"Employee {employee.employee_id} full_name is required.")
        if employee.base_salary < 0:
            raise PayrollValidationError(f"Employee {employee.employee_id} base_salary cannot be negative.")
        if employee.currency != self.policy.currency:
            logger.warning(
                "Employee currency differs from policy currency",
                extra={"employee_id": employee.employee_id, "employee_currency": employee.currency, "policy_currency": self.policy.currency},
            )


# =============================================================================
# Main service
# =============================================================================


class PayrollService:
    def __init__(
        self,
        repository: PayrollRepository,
        config: Optional[PayrollServiceConfig] = None,
        payment_exporter: Optional[PaymentExporter] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        idempotency_store: Optional[AsyncIdempotencyStore] = None,
    ) -> None:
        self.config = config or PayrollServiceConfig()
        self.config.validate()
        self.repository = repository
        self.payment_exporter = payment_exporter or InMemoryPaymentExporter()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.idempotency_store = idempotency_store or AsyncIdempotencyStore(self.config.idempotency_ttl_seconds)

    async def calculate_payroll(self, request: PayrollCalculationRequest) -> PayrollRunResult:
        started = time.perf_counter()
        self._validate_request(request)
        tags = {"tenant_id": request.tenant_id, "dry_run": str(request.dry_run).lower()}
        self.metrics.increment("payroll.run.started", tags=tags)

        idempotency_key = self._idempotency_key(request)
        existing = await self.idempotency_store.get(idempotency_key)
        if existing is not None:
            self.metrics.increment("payroll.run.idempotency_hit", tags=tags)
            return existing

        try:
            policy = await self.repository.get_policy(request.tenant_id, request.policy_id)
            policy.validate()
            employees = await self.repository.list_employees(request.tenant_id, request.employee_ids)
            self._validate_employee_count(employees)

            calculator = PayrollCalculator(policy, allow_negative_net_pay=self.config.allow_negative_net_pay)
            results: List[PayrollEmployeeResult] = []
            warnings: List[str] = []
            failed = 0

            for employee in employees:
                try:
                    if not self._employee_in_period(employee, request.period):
                        warnings.append(f"Employee {employee.employee_id} skipped: inactive outside payroll period.")
                        continue
                    time_summary, input_lines = await asyncio.gather(
                        self.repository.get_time_summary(request.tenant_id, employee.employee_id, request.period),
                        self.repository.get_input_lines(request.tenant_id, employee.employee_id, request.period),
                    )
                    employee_result = calculator.calculate_employee(
                        tenant_id=request.tenant_id,
                        payroll_run_id=request.payroll_run_id,
                        employee=employee,
                        period=request.period,
                        time_summary=time_summary,
                        input_lines=input_lines,
                    )
                    results.append(employee_result)
                    warnings.extend(f"{employee.employee_id}: {warning}" for warning in employee_result.warnings)
                except Exception as exc:
                    failed += 1
                    self.metrics.increment("payroll.employee.failed", tags={**tags, "error": exc.__class__.__name__})
                    logger.exception("Employee payroll calculation failed", extra={"employee_id": employee.employee_id})
                    if self.config.fail_fast:
                        raise
                    warnings.append(f"Employee {employee.employee_id} failed: {exc.__class__.__name__}: {exc}")

            result = self._build_run_result(
                request=request,
                policy=policy,
                employees=results,
                failed=failed,
                warnings=warnings,
                started=started,
            )

            if not request.dry_run:
                await self.repository.save_payroll_run(result)
                await self.idempotency_store.set(idempotency_key, result)

            self.metrics.increment("payroll.run.completed", tags={**tags, "status": result.status.value})
            self.metrics.gauge("payroll.total_net_pay", float(result.total_net_pay), tags=tags)
            self.metrics.timing("payroll.run.processing_ms", result.processing_ms, tags=tags)
            await self._audit_run("payroll.run.completed", request, result)
            return result
        except Exception as exc:
            self.metrics.increment("payroll.run.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Payroll run failed", extra={"payroll_run_id": request.payroll_run_id})
            failure = PayrollRunResult(
                payroll_run_id=request.payroll_run_id,
                tenant_id=request.tenant_id,
                period=request.period,
                status=PayrollRunStatus.FAILED,
                currency=self.config.default_currency,
                employees=tuple(),
                total_gross_pay=Decimal("0"),
                total_net_pay=Decimal("0"),
                total_taxes=Decimal("0"),
                total_deductions=Decimal("0"),
                total_employer_contributions=Decimal("0"),
                succeeded=0,
                failed=0,
                warnings=(f"Payroll run failed: {exc.__class__.__name__}: {exc}",),
                processing_ms=round((time.perf_counter() - started) * 1000, 4),
                created_at=_utc_now(),
                metadata={"error": exc.__class__.__name__, "dry_run": request.dry_run},
            )
            await self._audit_run("payroll.run.failed", request, failure)
            raise

    async def approve_payroll_run(self, tenant_id: str, payroll_run_id: str, actor_id: Optional[str]) -> None:
        await self._transition_status(tenant_id, payroll_run_id, PayrollRunStatus.APPROVED, actor_id, "payroll.run.approved")

    async def cancel_payroll_run(self, tenant_id: str, payroll_run_id: str, actor_id: Optional[str]) -> None:
        await self._transition_status(tenant_id, payroll_run_id, PayrollRunStatus.CANCELLED, actor_id, "payroll.run.cancelled")

    async def generate_payslips(self, tenant_id: str, payroll_run_id: str) -> Sequence[Payslip]:
        run = await self.repository.get_payroll_run(tenant_id, payroll_run_id)
        if not run:
            raise PayrollValidationError(f"Payroll run not found: {payroll_run_id}")
        payslips = [
            Payslip(
                payslip_id=str(uuid.uuid4()),
                payroll_run_id=run.payroll_run_id,
                tenant_id=run.tenant_id,
                employee_id=employee.employee_id,
                employee_name=employee.employee_name,
                period=run.period,
                currency=employee.currency,
                gross_pay=employee.gross_pay,
                net_pay=employee.net_pay,
                line_items=employee.line_items,
                issued_at=_utc_now(),
                metadata={"employee_hash": _hash_value(employee.employee_id, self.config.privacy_hash_salt)},
            )
            for employee in run.employees
        ]
        self.metrics.increment("payroll.payslips.generated", len(payslips), tags={"tenant_id": tenant_id})
        await self._audit_generic(
            "payroll.payslips.generated",
            {"tenant_id": tenant_id, "payroll_run_id": payroll_run_id, "count": len(payslips)},
        )
        return payslips

    async def build_payment_instructions(self, tenant_id: str, payroll_run_id: str) -> Sequence[PaymentInstruction]:
        run = await self.repository.get_payroll_run(tenant_id, payroll_run_id)
        if not run:
            raise PayrollValidationError(f"Payroll run not found: {payroll_run_id}")
        if run.status not in {PayrollRunStatus.APPROVED, PayrollRunStatus.EXPORTED, PayrollRunStatus.PAID}:
            raise PayrollConflictError(f"Payroll run must be approved before payment export. Current status: {run.status.value}")

        employee_profiles = await self.repository.list_employees(tenant_id, [employee.employee_id for employee in run.employees])
        profile_by_id = {profile.employee_id: profile for profile in employee_profiles}

        instructions: List[PaymentInstruction] = []
        for employee in run.employees:
            profile = profile_by_id.get(employee.employee_id)
            instructions.append(
                PaymentInstruction(
                    instruction_id=str(uuid.uuid4()),
                    payroll_run_id=run.payroll_run_id,
                    tenant_id=run.tenant_id,
                    employee_id=employee.employee_id,
                    employee_name=employee.employee_name,
                    amount=employee.net_pay,
                    currency=employee.currency,
                    method=profile.payment_method if profile else PaymentMethod.BANK_TRANSFER,
                    bank_account=profile.bank_account if profile else None,
                    reference=f"PAYROLL-{run.period.pay_date.isoformat()}-{employee.employee_id}",
                    metadata={"employee_hash": _hash_value(employee.employee_id, self.config.privacy_hash_salt)},
                )
            )
        return instructions

    async def export_payments(self, tenant_id: str, payroll_run_id: str, actor_id: Optional[str] = None) -> Mapping[str, Any]:
        instructions = await self.build_payment_instructions(tenant_id, payroll_run_id)
        export_result = await self.payment_exporter.export_payment_instructions(instructions)
        await self.repository.update_payroll_status(tenant_id, payroll_run_id, PayrollRunStatus.EXPORTED, actor_id)
        self.metrics.increment("payroll.payments.exported", tags={"tenant_id": tenant_id})
        await self._audit_generic(
            "payroll.payments.exported",
            {
                "tenant_id": tenant_id,
                "payroll_run_id": payroll_run_id,
                "count": len(instructions),
                "export_result": dict(export_result),
                "actor_hash": _hash_value(actor_id, self.config.privacy_hash_salt),
            },
        )
        return export_result

    async def mark_paid(self, tenant_id: str, payroll_run_id: str, actor_id: Optional[str] = None) -> None:
        await self._transition_status(tenant_id, payroll_run_id, PayrollRunStatus.PAID, actor_id, "payroll.run.paid")

    async def _transition_status(
        self,
        tenant_id: str,
        payroll_run_id: str,
        status: PayrollRunStatus,
        actor_id: Optional[str],
        event_name: str,
    ) -> None:
        run = await self.repository.get_payroll_run(tenant_id, payroll_run_id)
        if not run:
            raise PayrollValidationError(f"Payroll run not found: {payroll_run_id}")
        self._validate_status_transition(run.status, status)
        await self.repository.update_payroll_status(tenant_id, payroll_run_id, status, actor_id)
        self.metrics.increment(event_name.replace(".", "_"), tags={"tenant_id": tenant_id})
        await self._audit_generic(
            event_name,
            {
                "tenant_id": tenant_id,
                "payroll_run_id": payroll_run_id,
                "from_status": run.status.value,
                "to_status": status.value,
                "actor_hash": _hash_value(actor_id, self.config.privacy_hash_salt),
            },
        )

    def _validate_status_transition(self, current: PayrollRunStatus, target: PayrollRunStatus) -> None:
        allowed = {
            PayrollRunStatus.CALCULATED: {PayrollRunStatus.APPROVED, PayrollRunStatus.CANCELLED},
            PayrollRunStatus.APPROVED: {PayrollRunStatus.EXPORTED, PayrollRunStatus.PAID, PayrollRunStatus.CANCELLED},
            PayrollRunStatus.EXPORTED: {PayrollRunStatus.PAID, PayrollRunStatus.CANCELLED},
            PayrollRunStatus.DRAFT: {PayrollRunStatus.CALCULATED, PayrollRunStatus.CANCELLED},
        }
        if target not in allowed.get(current, set()):
            raise PayrollConflictError(f"Invalid payroll status transition: {current.value} -> {target.value}")

    def _build_run_result(
        self,
        request: PayrollCalculationRequest,
        policy: PayrollPolicyConfig,
        employees: Sequence[PayrollEmployeeResult],
        failed: int,
        warnings: Sequence[str],
        started: float,
    ) -> PayrollRunResult:
        total_gross = sum((employee.gross_pay for employee in employees), Decimal("0"))
        total_net = sum((employee.net_pay for employee in employees), Decimal("0"))
        total_taxes = sum((employee.total_taxes for employee in employees), Decimal("0"))
        total_deductions = sum((employee.total_deductions for employee in employees), Decimal("0"))
        total_employer_contributions = sum((employee.employer_contributions for employee in employees), Decimal("0"))
        return PayrollRunResult(
            payroll_run_id=request.payroll_run_id,
            tenant_id=request.tenant_id,
            period=request.period,
            status=PayrollRunStatus.CALCULATED,
            currency=policy.currency,
            employees=tuple(employees),
            total_gross_pay=_round_money(total_gross, policy.rounding_quantum),
            total_net_pay=_round_money(total_net, policy.rounding_quantum),
            total_taxes=_round_money(total_taxes, policy.rounding_quantum),
            total_deductions=_round_money(total_deductions, policy.rounding_quantum),
            total_employer_contributions=_round_money(total_employer_contributions, policy.rounding_quantum),
            succeeded=len(employees),
            failed=failed,
            warnings=tuple(warnings),
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={
                "dry_run": request.dry_run,
                "policy_id": policy.policy_id,
                "requested_by_hash": _hash_value(request.requested_by, self.config.privacy_hash_salt),
                "request_hash": _stable_hash(
                    {
                        "tenant_id": request.tenant_id,
                        "period": request.period.key,
                        "employee_ids": list(request.employee_ids or []),
                        "policy_id": request.policy_id,
                    }
                ),
            },
        )

    def _validate_request(self, request: PayrollCalculationRequest) -> None:
        if not request.tenant_id:
            raise PayrollValidationError("tenant_id is required.")
        if not request.payroll_run_id:
            raise PayrollValidationError("payroll_run_id is required.")
        request.period.validate()
        if request.employee_ids is not None and len(set(request.employee_ids)) != len(request.employee_ids):
            raise PayrollValidationError("employee_ids contains duplicates.")

    def _validate_employee_count(self, employees: Sequence[EmployeeProfile]) -> None:
        if len(employees) > self.config.max_employees_per_run:
            raise PayrollValidationError(
                f"Employee count {len(employees)} exceeds max_employees_per_run={self.config.max_employees_per_run}."
            )

    def _employee_in_period(self, employee: EmployeeProfile, period: PayrollPeriod) -> bool:
        if not employee.is_active and not employee.termination_date:
            return False
        if employee.hire_date and employee.hire_date > period.end_date:
            return False
        if employee.termination_date and employee.termination_date < period.start_date:
            return False
        return True

    def _idempotency_key(self, request: PayrollCalculationRequest) -> str:
        if request.idempotency_key:
            return f"payroll:{request.tenant_id}:{request.idempotency_key}"
        payload = {
            "tenant_id": request.tenant_id,
            "period": request.period.key,
            "employee_ids": sorted(list(request.employee_ids or [])),
            "policy_id": request.policy_id,
            "dry_run": request.dry_run,
        }
        return f"payroll:{_stable_hash(payload)}"

    async def _audit_run(self, event_name: str, request: PayrollCalculationRequest, result: PayrollRunResult) -> None:
        if not self.config.audit_enabled:
            return
        await self._audit_generic(
            event_name,
            {
                "tenant_id": result.tenant_id,
                "payroll_run_id": result.payroll_run_id,
                "status": result.status.value,
                "period": result.period.key,
                "employee_count": len(result.employees),
                "succeeded": result.succeeded,
                "failed": result.failed,
                "total_gross_pay": str(result.total_gross_pay),
                "total_net_pay": str(result.total_net_pay),
                "total_taxes": str(result.total_taxes),
                "processing_ms": result.processing_ms,
                "dry_run": request.dry_run,
                "requested_by_hash": _hash_value(request.requested_by, self.config.privacy_hash_salt),
                "created_at": result.created_at.isoformat(),
            },
        )

    async def _audit_generic(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write payroll audit event", extra={"event_name": event_name})

    @classmethod
    def request_from_payload(cls, payload: Mapping[str, Any]) -> PayrollCalculationRequest:
        period_payload = payload.get("period") or {}
        period = PayrollPeriod(
            start_date=_date_from_any(period_payload.get("start_date"), "period.start_date"),
            end_date=_date_from_any(period_payload.get("end_date"), "period.end_date"),
            pay_date=_date_from_any(period_payload.get("pay_date"), "period.pay_date"),
        )
        return PayrollCalculationRequest(
            tenant_id=str(payload["tenant_id"]),
            period=period,
            employee_ids=tuple(payload.get("employee_ids") or ()) or None,
            payroll_run_id=str(payload.get("payroll_run_id") or uuid.uuid4()),
            requested_by=payload.get("requested_by"),
            idempotency_key=payload.get("idempotency_key"),
            policy_id=payload.get("policy_id"),
            dry_run=bool(payload.get("dry_run", False)),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_payroll_service(
    repository: Optional[PayrollRepository] = None,
    config: Optional[PayrollServiceConfig] = None,
    payment_exporter: Optional[PaymentExporter] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> PayrollService:
    return PayrollService(
        repository=repository or InMemoryPayrollRepository(),
        config=config,
        payment_exporter=payment_exporter,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)

    policy = PayrollPolicyConfig(
        policy_id="ao-demo-policy",
        currency="AOA",
        overtime_multiplier=Decimal("1.5"),
        employee_social_security_rate=Decimal("0.03"),
        employer_social_security_rate=Decimal("0.08"),
        employee_tax_brackets=(
            PayrollPolicyBracket(lower_bound=Decimal("0"), upper_bound=Decimal("100000"), rate=Decimal("0.00")),
            PayrollPolicyBracket(lower_bound=Decimal("100000"), upper_bound=Decimal("300000"), rate=Decimal("0.10")),
            PayrollPolicyBracket(lower_bound=Decimal("300000"), upper_bound=None, rate=Decimal("0.17")),
        ),
    )
    repo = InMemoryPayrollRepository(default_policy=policy)
    employee = EmployeeProfile(
        employee_id="EMP-001",
        tenant_id="tenant-ao",
        full_name="Maria Fernandes",
        base_salary=Decimal("450000"),
        currency="AOA",
        department="Finance",
        cost_center="FIN-001",
        standard_hours_per_period=Decimal("173.33"),
        bank_account=BankAccount(bank_name="Banco Demo", account_holder="Maria Fernandes", account_number="000123456"),
    )
    repo.add_employee(employee)

    period = PayrollPeriod(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
        pay_date=date(2026, 5, 31),
    )
    repo.set_time_summary(
        "tenant-ao",
        "EMP-001",
        period,
        TimeEntrySummary(regular_hours=Decimal("173.33"), overtime_hours=Decimal("8"), absence_hours=Decimal("0")),
    )
    repo.add_input_line(
        "tenant-ao",
        "EMP-001",
        period,
        PayrollInputLine(
            code="MEAL_ALLOWANCE",
            description="Meal allowance",
            amount=Decimal("35000"),
            line_type=PayrollLineType.ALLOWANCE,
            taxability=PayrollLineTaxability.NON_TAXABLE,
        ),
    )
    repo.add_input_line(
        "tenant-ao",
        "EMP-001",
        period,
        PayrollInputLine(
            code="HEALTH_PLAN",
            description="Health plan employee share",
            amount=Decimal("12000"),
            line_type=PayrollLineType.DEDUCTION,
            taxability=PayrollLineTaxability.POST_TAX_DEDUCTION,
        ),
    )

    service = build_payroll_service(
        repository=repo,
        config=PayrollServiceConfig(default_currency="AOA", privacy_hash_salt="local-dev-salt"),
    )
    result = await service.calculate_payroll(
        PayrollCalculationRequest(
            tenant_id="tenant-ao",
            period=period,
            requested_by="admin-1",
            idempotency_key="may-2026-main-run",
        )
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))

    await service.approve_payroll_run("tenant-ao", result.payroll_run_id, actor_id="admin-1")
    export_result = await service.export_payments("tenant-ao", result.payroll_run_id, actor_id="admin-1")
    print(json.dumps(export_result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
