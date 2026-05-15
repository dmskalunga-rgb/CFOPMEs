#!/usr/bin/env python3
"""
api/routes/payroll.py

Enterprise-grade Payroll API Router.

Objetivo:
- Expor endpoints HTTP para análise e cálculo de folha de pagamento em nível enterprise.
- Calcular salário bruto/líquido simplificado, horas extras, benefícios, descontos, encargos,
  custo total do colaborador, KPIs por período/departamento/centro de custo e alertas.
- Aplicar validação Pydantic, autenticação por scopes, request-id, respostas padronizadas,
  auditoria leve e uso seguro de Decimal.

Endpoints:
    GET  /payroll/health
    POST /payroll/calculate
    POST /payroll/batch-calculate
    POST /payroll/kpis
    POST /payroll/anomalies
    POST /payroll/optimize

Integração:
    from fastapi import FastAPI
    from api.routes.payroll import router as payroll_router

    app.include_router(payroll_router, prefix="/v1")

Notas:
- Regras tributárias/trabalhistas reais variam por país, regime e convenção coletiva.
- Este módulo é uma base técnica enterprise e deve ser parametrizado pelo jurídico/contábil antes de produção.
"""

from __future__ import annotations

import hashlib
import logging
import statistics
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

router = APIRouter(prefix="/payroll", tags=["payroll"])


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


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PayrollComponentType(str, Enum):
    EARNING = "earning"
    DEDUCTION = "deduction"
    BENEFIT = "benefit"
    EMPLOYER_TAX = "employer_tax"


class PayrollComponent(BaseModel):
    name: str
    amount: float
    component_type: PayrollComponentType = PayrollComponentType.EARNING
    taxable: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PayrollEmployeeRequest(BaseModel):
    employee_id: str
    employee_name: Optional[str] = None
    period: str
    currency: str = DEFAULT_CURRENCY
    department: Optional[str] = None
    cost_center: Optional[str] = None
    role: Optional[str] = None
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    pay_frequency: PayFrequency = PayFrequency.MONTHLY
    base_salary: float = Field(ge=0)
    regular_hours: float = Field(default=220, ge=0)
    worked_hours: float = Field(default=220, ge=0)
    overtime_hours: float = Field(default=0, ge=0)
    overtime_multiplier: float = Field(default=1.5, ge=1)
    bonus: float = Field(default=0, ge=0)
    commission: float = Field(default=0, ge=0)
    benefits: List[PayrollComponent] = Field(default_factory=list)
    deductions: List[PayrollComponent] = Field(default_factory=list)
    additional_earnings: List[PayrollComponent] = Field(default_factory=list)
    employer_charges_rate_percent: float = Field(default=28.0, ge=0, le=200)
    employee_tax_rate_percent: float = Field(default=8.0, ge=0, le=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PayrollPolicyRequest(BaseModel):
    currency: str = DEFAULT_CURRENCY
    overtime_alert_hours: float = 20.0
    overtime_cost_alert_percent: float = 15.0
    employer_charges_rate_percent: float = 28.0
    employee_tax_rate_percent: float = 8.0
    max_department_concentration_percent: float = 40.0
    anonymize_employee_ids: bool = True


class PayrollCalculateRequest(BaseModel):
    employee: PayrollEmployeeRequest
    policy: PayrollPolicyRequest = Field(default_factory=PayrollPolicyRequest)


class PayrollBatchRequest(BaseModel):
    employees: List[PayrollEmployeeRequest] = Field(default_factory=list)
    policy: PayrollPolicyRequest = Field(default_factory=PayrollPolicyRequest)

    @validator("employees")
    def validate_size(cls, value: List[PayrollEmployeeRequest]) -> List[PayrollEmployeeRequest]:
        if len(value) > 50_000:
            raise ValueError("batch payroll excede 50.000 colaboradores")
        return value


class PayrollKpiRequest(PayrollBatchRequest):
    group_by: List[str] = Field(default_factory=lambda: ["department"])


class PayrollAnomalyRequest(PayrollBatchRequest):
    compare_previous_period: Optional[List[PayrollEmployeeRequest]] = None
    variance_alert_percent: float = Field(default=20.0, ge=0, le=500)


class PayrollOptimizeRequest(PayrollBatchRequest):
    target_cost_reduction_percent: float = Field(default=5.0, ge=0, le=80)
    protect_base_salary: bool = True
    max_overtime_reduction_percent: float = Field(default=40.0, ge=0, le=100)


class PayrollCalculationResult(BaseModel):
    employee_hash: str
    period: str
    currency: str
    department: Optional[str]
    cost_center: Optional[str]
    gross_pay: float
    base_salary: float
    hourly_rate: float
    overtime_pay: float
    bonus: float
    commission: float
    additional_earnings: float
    taxable_earnings: float
    employee_deductions: float
    employee_tax: float
    benefits_value: float
    net_pay: float
    employer_charges: float
    total_employer_cost: float
    overtime_cost_percent: float
    warnings: List[str]
    breakdown: Dict[str, Any]


class PayrollSummary(BaseModel):
    currency: str
    employee_count: int
    gross_pay_total: float
    net_pay_total: float
    employer_charges_total: float
    benefits_total: float
    deductions_total: float
    total_employer_cost: float
    avg_gross_pay: float
    avg_net_pay: float
    avg_employer_cost: float
    overtime_pay_total: float
    overtime_cost_percent: float
    departments: Dict[str, int]
    cost_centers: Dict[str, int]
    warning_count: int


class PayrollKpiPoint(BaseModel):
    key: Dict[str, Any]
    employee_count: int
    gross_pay_total: float
    net_pay_total: float
    total_employer_cost: float
    overtime_pay_total: float
    benefits_total: float
    avg_gross_pay: float
    avg_employer_cost: float
    concentration_percent: float
    concentration_warning: bool


class PayrollAnomaly(BaseModel):
    anomaly_id: str
    employee_hash: Optional[str]
    severity: RiskLevel
    anomaly_type: str
    description: str
    amount_impact: float
    recommended_actions: List[str]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PayrollOptimizationRecommendation(BaseModel):
    recommendation_id: str
    priority: RiskLevel
    title: str
    description: str
    estimated_savings: float
    actions: List[str]
    affected_count: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StandardPayrollResponse(BaseModel):
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
async def payroll_health() -> Dict[str, Any]:
    return {"status": "ok", "router": "payroll", "version": ROUTER_VERSION, "timestamp": utc_now_iso()}


@router.post("/calculate", response_model=StandardPayrollResponse, dependencies=[Depends(require_scopes("payroll:read"))])
async def calculate_payroll(payload: PayrollCalculateRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardPayrollResponse:
    ctx = build_context(request, user)
    result = calculate_employee(payload.employee, payload.policy)
    return response(ctx, {"calculation": result.dict()}, warnings=result.warnings, metadata={"operation": "calculate"})


@router.post("/batch-calculate", response_model=StandardPayrollResponse, dependencies=[Depends(require_scopes("payroll:read"))])
async def batch_calculate_payroll(payload: PayrollBatchRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardPayrollResponse:
    ctx = build_context(request, user)
    calculations = [calculate_employee(employee, payload.policy) for employee in payload.employees]
    summary = summarize_payroll(calculations, payload.policy.currency)
    warnings = build_global_warnings(calculations)
    return response(
        ctx,
        {"summary": summary.dict(), "calculations": [item.dict() for item in calculations]},
        warnings=warnings,
        metadata={"operation": "batch_calculate"},
    )


@router.post("/kpis", response_model=StandardPayrollResponse, dependencies=[Depends(require_scopes("payroll:read"))])
async def payroll_kpis(payload: PayrollKpiRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardPayrollResponse:
    ctx = build_context(request, user)
    calculations = [calculate_employee(employee, payload.policy) for employee in payload.employees]
    summary = summarize_payroll(calculations, payload.policy.currency)
    kpis = build_kpis(calculations, payload.group_by, payload.policy)
    return response(ctx, {"summary": summary.dict(), "kpis": [item.dict() for item in kpis]}, metadata={"operation": "kpis"})


@router.post("/anomalies", response_model=StandardPayrollResponse, dependencies=[Depends(require_scopes("payroll:read"))])
async def payroll_anomalies(payload: PayrollAnomalyRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardPayrollResponse:
    ctx = build_context(request, user)
    current = [calculate_employee(employee, payload.policy) for employee in payload.employees]
    previous = [calculate_employee(employee, payload.policy) for employee in payload.compare_previous_period or []]
    anomalies = detect_anomalies(current, previous, payload)
    return response(
        ctx,
        {"anomaly_count": len(anomalies), "anomalies": [item.dict() for item in anomalies]},
        warnings=["no_anomalies_detected"] if not anomalies else [],
        metadata={"operation": "anomalies"},
    )


@router.post("/optimize", response_model=StandardPayrollResponse, dependencies=[Depends(require_scopes("payroll:read"))])
async def optimize_payroll(payload: PayrollOptimizeRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> StandardPayrollResponse:
    ctx = build_context(request, user)
    calculations = [calculate_employee(employee, payload.policy) for employee in payload.employees]
    summary = summarize_payroll(calculations, payload.policy.currency)
    recommendations = optimize_payroll_costs(calculations, payload, summary)
    return response(
        ctx,
        {
            "summary": summary.dict(),
            "recommendation_count": len(recommendations),
            "recommendations": [item.dict() for item in recommendations],
        },
        metadata={"operation": "optimize"},
    )


def calculate_employee(employee: PayrollEmployeeRequest, policy: PayrollPolicyRequest) -> PayrollCalculationResult:
    currency = employee.currency.upper() or policy.currency.upper()
    base_salary = money(employee.base_salary)
    regular_hours = Decimal(str(max(employee.regular_hours, 1)))
    worked_hours = Decimal(str(employee.worked_hours))
    overtime_hours = Decimal(str(employee.overtime_hours))
    overtime_multiplier = Decimal(str(employee.overtime_multiplier))
    hourly_rate = base_salary / regular_hours
    overtime_pay = hourly_rate * overtime_multiplier * overtime_hours
    bonus = money(employee.bonus)
    commission = money(employee.commission)
    additional_earnings = sum_components(employee.additional_earnings)
    benefits_value = sum_components(employee.benefits)
    explicit_deductions = sum_components(employee.deductions)

    gross_pay = base_salary + overtime_pay + bonus + commission + additional_earnings
    taxable_earnings = base_salary + overtime_pay + bonus + commission + sum_components([item for item in employee.additional_earnings if item.taxable])
    employee_tax = taxable_earnings * percent(employee.employee_tax_rate_percent if employee.employee_tax_rate_percent is not None else policy.employee_tax_rate_percent)
    employee_deductions = explicit_deductions + employee_tax
    net_pay = gross_pay + benefits_value - employee_deductions
    employer_charges = gross_pay * percent(employee.employer_charges_rate_percent if employee.employer_charges_rate_percent is not None else policy.employer_charges_rate_percent)
    total_employer_cost = gross_pay + benefits_value + employer_charges
    overtime_cost_percent = Decimal("0") if gross_pay == 0 else (overtime_pay / gross_pay) * Decimal("100")

    warnings: List[str] = []
    if float(overtime_hours) > policy.overtime_alert_hours:
        warnings.append("overtime_hours_above_policy")
    if overtime_cost_percent > Decimal(str(policy.overtime_cost_alert_percent)):
        warnings.append("overtime_cost_percent_above_policy")
    if net_pay < 0:
        warnings.append("negative_net_pay")
    if employee.employment_type == EmploymentType.CONTRACTOR and benefits_value > 0:
        warnings.append("contractor_with_benefits_review_policy")

    return PayrollCalculationResult(
        employee_hash=hash_identifier(employee.employee_id) if policy.anonymize_employee_ids else employee.employee_id,
        period=employee.period,
        currency=currency,
        department=employee.department,
        cost_center=employee.cost_center,
        gross_pay=to_float(gross_pay),
        base_salary=to_float(base_salary),
        hourly_rate=to_float(hourly_rate),
        overtime_pay=to_float(overtime_pay),
        bonus=to_float(bonus),
        commission=to_float(commission),
        additional_earnings=to_float(additional_earnings),
        taxable_earnings=to_float(taxable_earnings),
        employee_deductions=to_float(employee_deductions),
        employee_tax=to_float(employee_tax),
        benefits_value=to_float(benefits_value),
        net_pay=to_float(net_pay),
        employer_charges=to_float(employer_charges),
        total_employer_cost=to_float(total_employer_cost),
        overtime_cost_percent=to_float(overtime_cost_percent),
        warnings=warnings,
        breakdown={
            "benefits": [component.dict() for component in employee.benefits],
            "deductions": [component.dict() for component in employee.deductions],
            "additional_earnings": [component.dict() for component in employee.additional_earnings],
            "employment_type": employee.employment_type.value,
            "pay_frequency": employee.pay_frequency.value,
            "worked_hours": employee.worked_hours,
            "regular_hours": employee.regular_hours,
            "overtime_hours": employee.overtime_hours,
        },
    )


def summarize_payroll(calculations: Sequence[PayrollCalculationResult], currency: str) -> PayrollSummary:
    count = len(calculations)
    gross = sum_decimal(money(item.gross_pay) for item in calculations)
    net = sum_decimal(money(item.net_pay) for item in calculations)
    charges = sum_decimal(money(item.employer_charges) for item in calculations)
    benefits = sum_decimal(money(item.benefits_value) for item in calculations)
    deductions = sum_decimal(money(item.employee_deductions) for item in calculations)
    employer_cost = sum_decimal(money(item.total_employer_cost) for item in calculations)
    overtime = sum_decimal(money(item.overtime_pay) for item in calculations)
    overtime_percent = Decimal("0") if gross == 0 else (overtime / gross) * Decimal("100")
    return PayrollSummary(
        currency=currency.upper(),
        employee_count=count,
        gross_pay_total=to_float(gross),
        net_pay_total=to_float(net),
        employer_charges_total=to_float(charges),
        benefits_total=to_float(benefits),
        deductions_total=to_float(deductions),
        total_employer_cost=to_float(employer_cost),
        avg_gross_pay=to_float(gross / Decimal(count)) if count else 0,
        avg_net_pay=to_float(net / Decimal(count)) if count else 0,
        avg_employer_cost=to_float(employer_cost / Decimal(count)) if count else 0,
        overtime_pay_total=to_float(overtime),
        overtime_cost_percent=to_float(overtime_percent),
        departments=dict(Counter(item.department or "unknown" for item in calculations)),
        cost_centers=dict(Counter(item.cost_center or "unknown" for item in calculations)),
        warning_count=sum(len(item.warnings) for item in calculations),
    )


def build_kpis(calculations: Sequence[PayrollCalculationResult], group_by: Sequence[str], policy: PayrollPolicyRequest) -> List[PayrollKpiPoint]:
    total_cost = sum_decimal(money(item.total_employer_cost) for item in calculations) or Decimal("1")
    grouped: DefaultDict[Tuple[Any, ...], List[PayrollCalculationResult]] = defaultdict(list)
    for item in calculations:
        key = tuple(getattr(item, field, None) for field in group_by)
        grouped[key].append(item)

    result: List[PayrollKpiPoint] = []
    for key_tuple, rows in grouped.items():
        gross = sum_decimal(money(item.gross_pay) for item in rows)
        net = sum_decimal(money(item.net_pay) for item in rows)
        employer_cost = sum_decimal(money(item.total_employer_cost) for item in rows)
        overtime = sum_decimal(money(item.overtime_pay) for item in rows)
        benefits = sum_decimal(money(item.benefits_value) for item in rows)
        concentration = (employer_cost / total_cost) * Decimal("100")
        result.append(
            PayrollKpiPoint(
                key={field: key_tuple[index] for index, field in enumerate(group_by)},
                employee_count=len(rows),
                gross_pay_total=to_float(gross),
                net_pay_total=to_float(net),
                total_employer_cost=to_float(employer_cost),
                overtime_pay_total=to_float(overtime),
                benefits_total=to_float(benefits),
                avg_gross_pay=to_float(gross / Decimal(len(rows))),
                avg_employer_cost=to_float(employer_cost / Decimal(len(rows))),
                concentration_percent=to_float(concentration),
                concentration_warning=concentration >= Decimal(str(policy.max_department_concentration_percent)),
            )
        )
    return sorted(result, key=lambda item: item.total_employer_cost, reverse=True)


def detect_anomalies(
    current: Sequence[PayrollCalculationResult],
    previous: Sequence[PayrollCalculationResult],
    payload: PayrollAnomalyRequest,
) -> List[PayrollAnomaly]:
    anomalies: List[PayrollAnomaly] = []
    avg_cost = mean_decimal([money(item.total_employer_cost) for item in current])
    std_cost = std_decimal([money(item.total_employer_cost) for item in current])
    previous_by_employee = {item.employee_hash: item for item in previous}

    for item in current:
        cost = money(item.total_employer_cost)
        if std_cost > 0 and cost > avg_cost + (std_cost * Decimal("3")):
            anomalies.append(
                PayrollAnomaly(
                    anomaly_id=f"pay_anom_{uuid.uuid4().hex[:16]}",
                    employee_hash=item.employee_hash,
                    severity=RiskLevel.HIGH,
                    anomaly_type="outlier_total_employer_cost",
                    description="Custo total do colaborador está acima de 3 desvios-padrão da média.",
                    amount_impact=to_float(cost - avg_cost),
                    recommended_actions=["review_payroll_components", "validate_bonus_overtime_and_benefits"],
                    metadata={"avg_cost": money_str(avg_cost), "std_cost": money_str(std_cost)},
                )
            )
        if item.overtime_cost_percent >= payload.policy.overtime_cost_alert_percent:
            anomalies.append(
                PayrollAnomaly(
                    anomaly_id=f"pay_anom_{uuid.uuid4().hex[:16]}",
                    employee_hash=item.employee_hash,
                    severity=RiskLevel.MEDIUM,
                    anomaly_type="high_overtime_cost",
                    description="Percentual de custo de horas extras acima da política.",
                    amount_impact=item.overtime_pay,
                    recommended_actions=["review_shift_planning", "redistribute_workload", "approve_overtime_exception"],
                    metadata={"overtime_cost_percent": item.overtime_cost_percent},
                )
            )
        previous_item = previous_by_employee.get(item.employee_hash)
        if previous_item:
            previous_cost = money(previous_item.total_employer_cost)
            if previous_cost > 0:
                variance = ((cost - previous_cost) / previous_cost) * Decimal("100")
                if abs(variance) >= Decimal(str(payload.variance_alert_percent)):
                    severity = RiskLevel.HIGH if abs(variance) >= Decimal(str(payload.variance_alert_percent * 2)) else RiskLevel.MEDIUM
                    anomalies.append(
                        PayrollAnomaly(
                            anomaly_id=f"pay_anom_{uuid.uuid4().hex[:16]}",
                            employee_hash=item.employee_hash,
                            severity=severity,
                            anomaly_type="period_over_period_variance",
                            description="Variação relevante no custo total versus período anterior.",
                            amount_impact=to_float(cost - previous_cost),
                            recommended_actions=["compare_period_components", "validate_salary_or_benefit_changes"],
                            metadata={"variance_percent": decimal_str(variance)},
                        )
                    )
    return anomalies


def optimize_payroll_costs(
    calculations: Sequence[PayrollCalculationResult],
    payload: PayrollOptimizeRequest,
    summary: PayrollSummary,
) -> List[PayrollOptimizationRecommendation]:
    recommendations: List[PayrollOptimizationRecommendation] = []
    total_cost = money(summary.total_employer_cost)
    target_savings = total_cost * percent(payload.target_cost_reduction_percent)
    overtime_total = money(summary.overtime_pay_total)
    benefits_total = money(summary.benefits_total)

    if overtime_total > 0:
        possible_savings = overtime_total * percent(payload.max_overtime_reduction_percent)
        priority = RiskLevel.HIGH if possible_savings >= target_savings * Decimal("0.5") else RiskLevel.MEDIUM
        affected = sum(1 for item in calculations if item.overtime_pay > 0)
        recommendations.append(
            PayrollOptimizationRecommendation(
                recommendation_id=f"pay_opt_{uuid.uuid4().hex[:16]}",
                priority=priority,
                title="Reduzir custo de horas extras",
                description="Rebalancear escalas, redistribuir carga ou contratar cobertura parcial para reduzir horas extras.",
                estimated_savings=to_float(possible_savings),
                actions=["analyze_overtime_by_department", "rebalance_shifts", "set_overtime_approval_workflow"],
                affected_count=affected,
                metadata={"overtime_total": money_str(overtime_total)},
            )
        )

    if benefits_total > 0 and not payload.protect_base_salary:
        possible_savings = benefits_total * Decimal("0.05")
        recommendations.append(
            PayrollOptimizationRecommendation(
                recommendation_id=f"pay_opt_{uuid.uuid4().hex[:16]}",
                priority=RiskLevel.LOW,
                title="Revisar pacote de benefícios",
                description="Auditar benefícios não utilizados e renegociar fornecedores sem reduzir salário-base.",
                estimated_savings=to_float(possible_savings),
                actions=["audit_benefit_utilization", "renegotiate_vendors", "remove_duplicate_benefits"],
                affected_count=len(calculations),
                metadata={"benefits_total": money_str(benefits_total)},
            )
        )

    department_costs: DefaultDict[str, Decimal] = defaultdict(Decimal)
    for item in calculations:
        department_costs[item.department or "unknown"] += money(item.total_employer_cost)
    for department, cost in department_costs.items():
        concentration = Decimal("0") if total_cost == 0 else (cost / total_cost) * Decimal("100")
        if concentration >= Decimal(str(payload.policy.max_department_concentration_percent)):
            recommendations.append(
                PayrollOptimizationRecommendation(
                    recommendation_id=f"pay_opt_{uuid.uuid4().hex[:16]}",
                    priority=RiskLevel.MEDIUM,
                    title=f"Revisar concentração de custo em {department}",
                    description="Departamento concentra parcela relevante do custo total de folha.",
                    estimated_savings=to_float(cost * Decimal("0.03")),
                    actions=["review_headcount_plan", "validate_budget_allocation", "compare_productivity_metrics"],
                    affected_count=sum(1 for item in calculations if (item.department or "unknown") == department),
                    metadata={"department": department, "concentration_percent": decimal_str(concentration)},
                )
            )
    return sorted(recommendations, key=lambda item: item.estimated_savings, reverse=True)


def build_global_warnings(calculations: Sequence[PayrollCalculationResult]) -> List[str]:
    warnings: List[str] = []
    if not calculations:
        warnings.append("empty_payroll_batch")
    if any("negative_net_pay" in item.warnings for item in calculations):
        warnings.append("negative_net_pay_detected")
    if any("overtime_cost_percent_above_policy" in item.warnings for item in calculations):
        warnings.append("overtime_cost_policy_breach_detected")
    return warnings


def sum_components(components: Sequence[PayrollComponent]) -> Decimal:
    return sum_decimal(money(component.amount) for component in components)


def response(ctx: ExecutionContext, result: Dict[str, Any], warnings: Optional[List[str]] = None, metadata: Optional[Dict[str, Any]] = None) -> StandardPayrollResponse:
    return StandardPayrollResponse(
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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Valor decimal inválido: {value}") from exc


def percent(value: Any) -> Decimal:
    return money(value) / Decimal("100")


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum_decimal(values) / Decimal(len(values))


def std_decimal(values: Sequence[Decimal]) -> Decimal:
    if len(values) < 2:
        return Decimal("0")
    avg = mean_decimal(values)
    variance = sum((item - avg) ** 2 for item in values) / Decimal(len(values) - 1)
    return Decimal(str(float(variance) ** 0.5))


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def money_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
