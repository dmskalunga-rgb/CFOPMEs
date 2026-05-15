#!/usr/bin/env python3
"""
ai_engine/financial_alert.py

Enterprise-grade Financial Alert Engine.

Objetivo:
- Converter métricas financeiras em alertas acionáveis para tesouraria, FP&A, controladoria e risco.
- Detectar violações de liquidez, fluxo de caixa, receita, custos, margem, runway, orçamento e concentração.
- Aplicar severidade, decisão recomendada, deduplicação, cooldown, roteamento e explicabilidade.
- Processar CSV/JSON em batch e exportar JSON/CSV.

Exemplos:
    python ai_engine/financial_alert.py evaluate \
        --input data/financial_metrics.csv \
        --output reports/finance/financial_alerts.json \
        --format json

    python ai_engine/financial_alert.py evaluate \
        --input data/financial_metrics.csv \
        --output reports/finance/financial_alerts.csv \
        --format csv \
        --cooldown-minutes 60 \
        --min-cash-balance 10000 \
        --min-gross-margin-percent 20

Formato esperado CSV/JSON:
    metric_id,entity_id,period,timestamp,metric_name,metric_value,baseline_value,target_value,budget_value,currency,domain,category,status,metadata

Campos mínimos:
    metric_id: string
    entity_id: string
    metric_name: string
    metric_value: número

Campos opcionais:
    period: string. Ex: 2026-01
    timestamp: ISO datetime
    baseline_value: número
    target_value: número
    budget_value: número
    currency: BRL|USD|...
    domain: cashflow|liquidity|revenue|cost|margin|payroll|risk|finance
    category: string
    status: open|closed|resolved|suppressed
    metadata: JSON string opcional

Métricas reconhecidas automaticamente:
    cash_balance, closing_balance, liquidity_gap, runway_periods, burn_rate,
    net_cashflow, revenue, revenue_growth_percent, gross_margin_percent,
    net_margin_percent, cost, cost_variance_percent, budget_variance_percent,
    ar_overdue, debt_to_equity, current_ratio, coverage_ratio, payroll_cost
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import math
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


APP_NAME = "financial_alert"
ENGINE_VERSION = "1.0.0"
DEFAULT_CURRENCY = "BRL"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    FIRED = "fired"
    SUPPRESSED = "suppressed"
    DEDUPED = "deduped"
    COOLDOWN = "cooldown"
    BELOW_THRESHOLD = "below_threshold"


class FinancialDecision(str, Enum):
    IGNORE = "ignore"
    MONITOR = "monitor"
    REVIEW = "review"
    ESCALATE = "escalate"
    INCIDENT = "incident"


class AlertRoute(str, Enum):
    NONE = "none"
    TREASURY = "treasury"
    FPNA = "fpna"
    CONTROLLERSHIP = "controllership"
    RISK = "risk"
    PAYROLL = "payroll"
    EXECUTIVE = "executive"


@dataclass(frozen=True)
class FinancialAlertPolicy:
    currency: str = DEFAULT_CURRENCY
    min_cash_balance: Decimal = Decimal("10000")
    min_current_ratio: Decimal = Decimal("1.20")
    min_coverage_ratio: Decimal = Decimal("1.10")
    min_runway_periods: Decimal = Decimal("3")
    max_liquidity_gap: Decimal = Decimal("0")
    min_gross_margin_percent: Decimal = Decimal("20")
    min_net_margin_percent: Decimal = Decimal("5")
    max_budget_variance_percent: Decimal = Decimal("10")
    max_cost_variance_percent: Decimal = Decimal("12")
    max_negative_revenue_growth_percent: Decimal = Decimal("-10")
    max_debt_to_equity: Decimal = Decimal("2.50")
    max_ar_overdue: Decimal = Decimal("50000")
    payroll_cost_variance_percent: Decimal = Decimal("8")
    medium_score_threshold: Decimal = Decimal("35")
    high_score_threshold: Decimal = Decimal("65")
    critical_score_threshold: Decimal = Decimal("85")
    cooldown_minutes: int = 60
    dedupe_window_minutes: int = 240
    suppress_closed_metrics: bool = True
    hash_entity_ids: bool = True


@dataclass(frozen=True)
class FinancialMetric:
    metric_id: str
    entity_id: str
    entity_id_hash: str
    period: Optional[str]
    timestamp: datetime
    metric_name: str
    metric_value: Decimal
    baseline_value: Optional[Decimal]
    target_value: Optional[Decimal]
    budget_value: Optional[Decimal]
    currency: str
    domain: Optional[str]
    category: Optional[str]
    status: Optional[str]
    metadata: Dict[str, Any]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class AlertEvaluation:
    score: Decimal
    severity: Severity
    reasons: List[str]
    threshold_used: Optional[Decimal]
    variance_percent: Optional[Decimal]
    gap_amount: Optional[Decimal]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": decimal_str(self.score),
            "severity": self.severity.value,
            "reasons": self.reasons,
            "threshold_used": None if self.threshold_used is None else decimal_str(self.threshold_used),
            "variance_percent": None if self.variance_percent is None else decimal_str(self.variance_percent),
            "gap_amount": None if self.gap_amount is None else money_str(self.gap_amount),
        }


@dataclass(frozen=True)
class FinancialAlert:
    alert_id: str
    metric_id: str
    entity_id_hash: str
    period: Optional[str]
    timestamp: str
    status: str
    decision: str
    severity: str
    route: str
    metric_name: str
    metric_value: Decimal
    currency: str
    score: Decimal
    reasons: List[str]
    recommended_actions: List[str]
    dedupe_key: str
    correlation_key: str
    evaluation: AlertEvaluation
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "metric_id": self.metric_id,
            "entity_id_hash": self.entity_id_hash,
            "period": self.period,
            "timestamp": self.timestamp,
            "status": self.status,
            "decision": self.decision,
            "severity": self.severity,
            "route": self.route,
            "metric_name": self.metric_name,
            "metric_value": decimal_str(self.metric_value),
            "currency": self.currency,
            "score": decimal_str(self.score),
            "reasons": self.reasons,
            "recommended_actions": self.recommended_actions,
            "dedupe_key": self.dedupe_key,
            "correlation_key": self.correlation_key,
            "evaluation": self.evaluation.to_dict(),
            "payload": self.payload,
        }


@dataclass(frozen=True)
class FinancialAlertSummary:
    total_metrics: int
    total_alerts: int
    fired: int
    suppressed: int
    deduped: int
    cooldown: int
    below_threshold: int
    critical: int
    high: int
    medium: int
    low: int
    routes: Dict[str, int]
    decisions: Dict[str, int]
    domains: Dict[str, int]
    top_metrics: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class FinancialAlertError(Exception):
    """Base exception for financial alert engine."""


class InputValidationError(FinancialAlertError):
    """Raised when input data is invalid."""


class FileLoader:
    @staticmethod
    def load(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            raise InputValidationError(f"Arquivo não encontrado: {path}")
        if not path.is_file():
            raise InputValidationError(f"Caminho não é arquivo: {path}")
        if path.suffix.lower() == ".csv":
            return FileLoader._load_csv(path)
        if path.suffix.lower() == ".json":
            return FileLoader._load_json(path)
        raise InputValidationError("Formato não suportado. Use .csv ou .json")

    @staticmethod
    def _load_csv(path: Path) -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]

    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("metrics"), list):
            return payload["metrics"]
        raise InputValidationError("JSON inválido. Esperado lista ou objeto com chave 'metrics'.")


class FinancialMetricParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]], policy: FinancialAlertPolicy) -> List[FinancialMetric]:
        metrics: List[FinancialMetric] = []
        errors: List[str] = []
        for index, row in enumerate(rows, start=1):
            try:
                metrics.append(FinancialMetricParser.parse(row, policy))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"linha={index}: {exc}")
        if errors:
            preview = "\n".join(errors[:30])
            extra = "" if len(errors) <= 30 else f"\n... e mais {len(errors) - 30} erro(s)."
            raise InputValidationError(f"Falha ao validar métricas financeiras:\n{preview}{extra}")
        return sorted(metrics, key=lambda item: item.timestamp)

    @staticmethod
    def parse(row: Dict[str, Any], policy: FinancialAlertPolicy) -> FinancialMetric:
        metric_id = required_str(row, "metric_id")
        entity_id = required_str(row, "entity_id")
        metric_name = normalize_metric_name(required_str(row, "metric_name"))
        timestamp = parse_datetime(optional_str(row, "timestamp") or datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
        entity_hash = hash_identifier(entity_id) if policy.hash_entity_ids else entity_id
        return FinancialMetric(
            metric_id=metric_id,
            entity_id=entity_id,
            entity_id_hash=entity_hash,
            period=optional_str(row, "period"),
            timestamp=timestamp,
            metric_name=metric_name,
            metric_value=to_decimal(row.get("metric_value")),
            baseline_value=optional_decimal(row, "baseline_value"),
            target_value=optional_decimal(row, "target_value"),
            budget_value=optional_decimal(row, "budget_value"),
            currency=(optional_str(row, "currency") or policy.currency).upper(),
            domain=normalize_lower(optional_str(row, "domain")),
            category=normalize_lower(optional_str(row, "category")),
            status=normalize_lower(optional_str(row, "status")),
            metadata=parse_metadata(optional_str(row, "metadata")),
            raw=row,
        )


class FinancialAlertEngine:
    def __init__(self, policy: FinancialAlertPolicy) -> None:
        self.policy = policy
        self.last_alert_by_key: Dict[str, datetime] = {}
        self.seen_dedupe_keys: Dict[str, datetime] = {}
        self.logger = logging.getLogger(f"{APP_NAME}.{self.__class__.__name__}")

    def evaluate(self, metrics: Sequence[FinancialMetric]) -> Tuple[FinancialAlertSummary, List[FinancialAlert]]:
        if not metrics:
            raise InputValidationError("Nenhuma métrica válida para avaliação")
        alerts: List[FinancialAlert] = []
        for metric in sorted(metrics, key=lambda item: item.timestamp):
            alert = self.evaluate_one(metric)
            alerts.append(alert)
            self._update_state(metric, alert)
        return self._summary(metrics, alerts), alerts

    def evaluate_one(self, metric: FinancialMetric) -> FinancialAlert:
        evaluation = self._evaluate_metric(metric)
        status = AlertStatus.FIRED
        decision = self._decision(evaluation.severity)
        route = self._route(metric, evaluation.severity)
        reasons = list(evaluation.reasons)
        dedupe_key = self._dedupe_key(metric)
        cooldown_key = self._cooldown_key(metric)

        if self._should_suppress(metric):
            status = AlertStatus.SUPPRESSED
            decision = FinancialDecision.IGNORE
            route = AlertRoute.NONE
            reasons.append("suppressed_by_policy")
        elif evaluation.severity in {Severity.INFO, Severity.LOW} and evaluation.score < self.policy.medium_score_threshold:
            status = AlertStatus.BELOW_THRESHOLD
            decision = FinancialDecision.IGNORE
            route = AlertRoute.NONE
            reasons.append("below_alert_threshold")
        elif self._is_duplicate(metric, dedupe_key):
            status = AlertStatus.DEDUPED
            decision = FinancialDecision.MONITOR
            reasons.append("deduplicated_within_window")
        elif self._is_in_cooldown(metric, cooldown_key):
            status = AlertStatus.COOLDOWN
            decision = FinancialDecision.MONITOR
            reasons.append("cooldown_active")

        payload = self._payload(metric, evaluation, status, decision, route)
        return FinancialAlert(
            alert_id=self._alert_id(metric, dedupe_key),
            metric_id=metric.metric_id,
            entity_id_hash=metric.entity_id_hash,
            period=metric.period,
            timestamp=metric.timestamp.isoformat(),
            status=status.value,
            decision=decision.value,
            severity=evaluation.severity.value,
            route=route.value,
            metric_name=metric.metric_name,
            metric_value=metric.metric_value,
            currency=metric.currency,
            score=evaluation.score,
            reasons=unique_ordered(reasons),
            recommended_actions=self._actions(metric, evaluation.severity, decision, status),
            dedupe_key=dedupe_key,
            correlation_key=self._correlation_key(metric),
            evaluation=evaluation,
            payload=payload,
        )

    def _evaluate_metric(self, metric: FinancialMetric) -> AlertEvaluation:
        name = metric.metric_name
        value = metric.metric_value
        reasons: List[str] = []
        threshold: Optional[Decimal] = None
        variance = self._variance(metric)
        gap: Optional[Decimal] = None
        score = Decimal("0")

        if name in {"cash_balance", "closing_balance"}:
            threshold = self.policy.min_cash_balance
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("cash_balance_below_minimum")
        elif name == "liquidity_gap":
            threshold = self.policy.max_liquidity_gap
            if value > threshold:
                gap = value - threshold
                score = self._score_positive_gap(gap, max_decimal(abs(value), Decimal("1")))
                reasons.append("liquidity_gap_above_limit")
        elif name == "runway_periods":
            threshold = self.policy.min_runway_periods
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("runway_below_minimum")
        elif name == "current_ratio":
            threshold = self.policy.min_current_ratio
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("current_ratio_below_minimum")
        elif name in {"coverage_ratio", "liquidity_coverage_ratio"}:
            threshold = self.policy.min_coverage_ratio
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("coverage_ratio_below_minimum")
        elif name == "gross_margin_percent":
            threshold = self.policy.min_gross_margin_percent
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("gross_margin_below_minimum")
        elif name == "net_margin_percent":
            threshold = self.policy.min_net_margin_percent
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(gap, threshold)
                reasons.append("net_margin_below_minimum")
        elif name in {"budget_variance_percent", "cost_variance_percent"}:
            threshold = self.policy.max_budget_variance_percent if name == "budget_variance_percent" else self.policy.max_cost_variance_percent
            if abs(value) > threshold:
                gap = abs(value) - threshold
                score = self._score_positive_gap(gap, threshold)
                reasons.append(f"{name}_above_limit")
        elif name == "revenue_growth_percent":
            threshold = self.policy.max_negative_revenue_growth_percent
            if value < threshold:
                gap = threshold - value
                score = self._score_gap(abs(gap), abs(threshold) if threshold != 0 else Decimal("1"))
                reasons.append("revenue_growth_negative_beyond_limit")
        elif name == "debt_to_equity":
            threshold = self.policy.max_debt_to_equity
            if value > threshold:
                gap = value - threshold
                score = self._score_positive_gap(gap, threshold)
                reasons.append("debt_to_equity_above_limit")
        elif name == "ar_overdue":
            threshold = self.policy.max_ar_overdue
            if value > threshold:
                gap = value - threshold
                score = self._score_positive_gap(gap, threshold)
                reasons.append("accounts_receivable_overdue_above_limit")
        elif name == "payroll_cost" and variance is not None:
            threshold = self.policy.payroll_cost_variance_percent
            if abs(variance) > threshold:
                gap = abs(variance) - threshold
                score = self._score_positive_gap(gap, threshold)
                reasons.append("payroll_cost_variance_above_limit")
        elif variance is not None and abs(variance) > self.policy.max_budget_variance_percent:
            threshold = self.policy.max_budget_variance_percent
            gap = abs(variance) - threshold
            score = self._score_positive_gap(gap, threshold)
            reasons.append("generic_variance_above_limit")

        if variance is not None:
            if metric.target_value is not None:
                reasons.append("target_comparison_available")
            if metric.budget_value is not None:
                reasons.append("budget_comparison_available")
            if metric.baseline_value is not None:
                reasons.append("baseline_comparison_available")

        if not reasons:
            reasons.append("metric_within_policy")

        severity = self._severity(score, metric)
        return AlertEvaluation(
            score=clamp_decimal(score, Decimal("0"), Decimal("100")),
            severity=severity,
            reasons=reasons,
            threshold_used=threshold,
            variance_percent=variance,
            gap_amount=gap,
        )

    def _variance(self, metric: FinancialMetric) -> Optional[Decimal]:
        comparator = metric.target_value if metric.target_value is not None else metric.budget_value
        comparator = comparator if comparator is not None else metric.baseline_value
        if comparator is None or comparator == 0:
            return None
        return ((metric.metric_value - comparator) / abs(comparator)) * Decimal("100")

    @staticmethod
    def _score_gap(gap: Decimal, threshold: Decimal) -> Decimal:
        denominator = max_decimal(abs(threshold), Decimal("1"))
        return clamp_decimal((gap / denominator) * Decimal("100"), Decimal("0"), Decimal("100"))

    @staticmethod
    def _score_positive_gap(gap: Decimal, denominator: Decimal) -> Decimal:
        denominator = max_decimal(abs(denominator), Decimal("1"))
        return clamp_decimal((gap / denominator) * Decimal("100"), Decimal("0"), Decimal("100"))

    def _severity(self, score: Decimal, metric: FinancialMetric) -> Severity:
        name = metric.metric_name
        value = metric.metric_value
        if score >= self.policy.critical_score_threshold:
            return Severity.CRITICAL
        if score >= self.policy.high_score_threshold:
            return Severity.HIGH
        if score >= self.policy.medium_score_threshold:
            return Severity.MEDIUM
        if name in {"cash_balance", "closing_balance"} and value < 0:
            return Severity.CRITICAL
        if name == "liquidity_gap" and value > 0:
            return Severity.HIGH
        if name == "runway_periods" and value < Decimal("1"):
            return Severity.CRITICAL
        if score > 0:
            return Severity.LOW
        return Severity.INFO

    @staticmethod
    def _decision(severity: Severity) -> FinancialDecision:
        if severity == Severity.CRITICAL:
            return FinancialDecision.INCIDENT
        if severity == Severity.HIGH:
            return FinancialDecision.ESCALATE
        if severity == Severity.MEDIUM:
            return FinancialDecision.REVIEW
        if severity == Severity.LOW:
            return FinancialDecision.MONITOR
        return FinancialDecision.IGNORE

    @staticmethod
    def _route(metric: FinancialMetric, severity: Severity) -> AlertRoute:
        domain = metric.domain or ""
        name = metric.metric_name
        if severity == Severity.CRITICAL:
            if name in {"cash_balance", "closing_balance", "liquidity_gap", "runway_periods"}:
                return AlertRoute.TREASURY
            return AlertRoute.EXECUTIVE
        if domain in {"cashflow", "liquidity", "treasury"} or name in {"cash_balance", "closing_balance", "runway_periods", "liquidity_gap"}:
            return AlertRoute.TREASURY
        if domain in {"revenue", "cost", "margin", "finance"}:
            return AlertRoute.FPNA
        if domain in {"risk", "credit", "compliance"}:
            return AlertRoute.RISK
        if domain == "payroll" or name == "payroll_cost":
            return AlertRoute.PAYROLL
        return AlertRoute.CONTROLLERSHIP

    def _actions(self, metric: FinancialMetric, severity: Severity, decision: FinancialDecision, status: AlertStatus) -> List[str]:
        if status in {AlertStatus.SUPPRESSED, AlertStatus.BELOW_THRESHOLD}:
            return ["no_action"]
        if status == AlertStatus.DEDUPED:
            return ["attach_to_existing_financial_alert", "update_correlation_context"]
        if status == AlertStatus.COOLDOWN:
            return ["monitor_during_cooldown", "update_existing_alert"]

        name = metric.metric_name
        if name in {"cash_balance", "closing_balance", "liquidity_gap", "runway_periods"}:
            actions = ["review_cash_position", "accelerate_collections", "prioritize_payments", "prepare_short_term_funding_options"]
        elif name in {"revenue", "revenue_growth_percent"}:
            actions = ["review_revenue_pipeline", "validate_billing_and_recognition", "inspect_customer_or_channel_variance"]
        elif name in {"gross_margin_percent", "net_margin_percent"}:
            actions = ["review_pricing_and_cost_structure", "analyze_margin_by_product_or_channel", "prepare_margin_recovery_plan"]
        elif name in {"cost", "cost_variance_percent", "budget_variance_percent", "payroll_cost"}:
            actions = ["review_budget_variance", "identify_cost_drivers", "freeze_discretionary_spend_if_needed"]
        elif name in {"debt_to_equity", "current_ratio", "coverage_ratio"}:
            actions = ["review_balance_sheet_risk", "assess_covenants", "prepare_financing_or_deleveraging_options"]
        elif name == "ar_overdue":
            actions = ["prioritize_collection_queue", "contact_overdue_customers", "review_credit_terms"]
        else:
            actions = ["manual_financial_review", "monitor_next_period"]

        if decision == FinancialDecision.INCIDENT:
            return ["open_financial_incident", "notify_executive_owner"] + actions
        if decision == FinancialDecision.ESCALATE:
            return ["create_financial_case", "notify_owner_team"] + actions
        return actions

    def _should_suppress(self, metric: FinancialMetric) -> bool:
        return self.policy.suppress_closed_metrics and metric.status in {"closed", "resolved", "suppressed", "cancelled", "canceled"}

    def _is_duplicate(self, metric: FinancialMetric, dedupe_key: str) -> bool:
        self._evict_dedupe(metric.timestamp)
        return dedupe_key in self.seen_dedupe_keys

    def _is_in_cooldown(self, metric: FinancialMetric, cooldown_key: str) -> bool:
        last = self.last_alert_by_key.get(cooldown_key)
        if not last:
            return False
        return metric.timestamp - last <= timedelta(minutes=self.policy.cooldown_minutes)

    def _update_state(self, metric: FinancialMetric, alert: FinancialAlert) -> None:
        self.seen_dedupe_keys[alert.dedupe_key] = metric.timestamp
        if alert.status == AlertStatus.FIRED.value:
            self.last_alert_by_key[self._cooldown_key(metric)] = metric.timestamp

    def _evict_dedupe(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=self.policy.dedupe_window_minutes)
        for key, timestamp in list(self.seen_dedupe_keys.items()):
            if timestamp < cutoff:
                self.seen_dedupe_keys.pop(key, None)

    def _dedupe_key(self, metric: FinancialMetric) -> str:
        raw = "|".join([metric.entity_id_hash, metric.period or "", metric.domain or "", metric.metric_name, metric.category or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _cooldown_key(self, metric: FinancialMetric) -> str:
        raw = "|".join([metric.entity_id_hash, metric.domain or "", metric.metric_name, metric.category or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _correlation_key(self, metric: FinancialMetric) -> str:
        raw = "|".join([metric.entity_id_hash, metric.domain or "finance", metric.period or "unknown"])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _alert_id(self, metric: FinancialMetric, dedupe_key: str) -> str:
        raw = f"{metric.metric_id}|{metric.timestamp.isoformat()}|{dedupe_key}|{uuid.uuid4()}"
        return "fin_alert_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _payload(self, metric: FinancialMetric, evaluation: AlertEvaluation, status: AlertStatus, decision: FinancialDecision, route: AlertRoute) -> Dict[str, Any]:
        return {
            "engine": APP_NAME,
            "engine_version": ENGINE_VERSION,
            "metric_id": metric.metric_id,
            "entity_id_hash": metric.entity_id_hash,
            "period": metric.period,
            "timestamp": metric.timestamp.isoformat(),
            "metric_name": metric.metric_name,
            "metric_value": decimal_str(metric.metric_value),
            "baseline_value": None if metric.baseline_value is None else decimal_str(metric.baseline_value),
            "target_value": None if metric.target_value is None else decimal_str(metric.target_value),
            "budget_value": None if metric.budget_value is None else decimal_str(metric.budget_value),
            "currency": metric.currency,
            "domain": metric.domain,
            "category": metric.category,
            "status": status.value,
            "decision": decision.value,
            "severity": evaluation.severity.value,
            "route": route.value,
            "score": decimal_str(evaluation.score),
            "metadata": metric.metadata,
        }

    def _summary(self, metrics: Sequence[FinancialMetric], alerts: Sequence[FinancialAlert]) -> FinancialAlertSummary:
        statuses = Counter(item.status for item in alerts)
        severities = Counter(item.severity for item in alerts)
        routes = Counter(item.route for item in alerts)
        decisions = Counter(item.decision for item in alerts)
        domains = Counter((metric.domain or "unknown") for metric in metrics)
        metric_names = Counter(metric.metric_name for metric in metrics)
        return FinancialAlertSummary(
            total_metrics=len(metrics),
            total_alerts=len(alerts),
            fired=statuses.get(AlertStatus.FIRED.value, 0),
            suppressed=statuses.get(AlertStatus.SUPPRESSED.value, 0),
            deduped=statuses.get(AlertStatus.DEDUPED.value, 0),
            cooldown=statuses.get(AlertStatus.COOLDOWN.value, 0),
            below_threshold=statuses.get(AlertStatus.BELOW_THRESHOLD.value, 0),
            critical=severities.get(Severity.CRITICAL.value, 0),
            high=severities.get(Severity.HIGH.value, 0),
            medium=severities.get(Severity.MEDIUM.value, 0),
            low=severities.get(Severity.LOW.value, 0),
            routes=dict(routes),
            decisions=dict(decisions),
            domains=dict(domains),
            top_metrics=[{"metric_name": key, "count": value} for key, value in metric_names.most_common(20)],
        )


class ResultWriter:
    @staticmethod
    def write(summary: FinancialAlertSummary, alerts: Sequence[FinancialAlert], output: Path, output_format: OutputFormat) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output_format == OutputFormat.JSON:
            payload = {
                "engine_version": ENGINE_VERSION,
                "generated_at": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
                "summary": summary.to_dict(),
                "alerts": [item.to_dict() for item in alerts],
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return output
        if output_format == OutputFormat.CSV:
            fieldnames = [
                "alert_id", "metric_id", "entity_id_hash", "period", "timestamp", "status", "decision", "severity",
                "route", "metric_name", "metric_value", "currency", "score", "reasons", "recommended_actions",
                "dedupe_key", "correlation_key",
            ]
            with output.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                for alert in alerts:
                    payload = alert.to_dict()
                    writer.writerow(
                        {
                            "alert_id": payload["alert_id"],
                            "metric_id": payload["metric_id"],
                            "entity_id_hash": payload["entity_id_hash"],
                            "period": payload["period"],
                            "timestamp": payload["timestamp"],
                            "status": payload["status"],
                            "decision": payload["decision"],
                            "severity": payload["severity"],
                            "route": payload["route"],
                            "metric_name": payload["metric_name"],
                            "metric_value": payload["metric_value"],
                            "currency": payload["currency"],
                            "score": payload["score"],
                            "reasons": "|".join(payload["reasons"]),
                            "recommended_actions": "|".join(payload["recommended_actions"]),
                            "dedupe_key": payload["dedupe_key"],
                            "correlation_key": payload["correlation_key"],
                        }
                    )
            return output
        raise FinancialAlertError(f"Formato não suportado: {output_format}")


def required_str(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"campo obrigatório ausente: {key}")
    return str(value).strip()


def optional_str(row: Mapping[str, Any], key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def optional_decimal(row: Mapping[str, Any], key: str) -> Optional[Decimal]:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return None
    return to_decimal(value)


def to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"valor decimal inválido: {value}") from exc


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"datetime inválido: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed.astimezone(DEFAULT_TIMEZONE)


def parse_metadata(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
        return payload if isinstance(payload, dict) else {"value": payload}
    except json.JSONDecodeError:
        return {"raw": value}


def normalize_lower(value: Optional[str]) -> Optional[str]:
    return value.lower() if value else None


def normalize_metric_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def max_decimal(left: Decimal, right: Decimal) -> Decimal:
    return left if left >= right else right


def money_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def unique_ordered(values: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Enterprise financial alert engine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="Avalia métricas financeiras e gera alertas.")
    evaluate.add_argument("--input", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    evaluate.add_argument("--format", default=OutputFormat.JSON.value, choices=[item.value for item in OutputFormat])
    evaluate.add_argument("--currency", default=DEFAULT_CURRENCY)
    evaluate.add_argument("--min-cash-balance", default="10000")
    evaluate.add_argument("--min-current-ratio", default="1.20")
    evaluate.add_argument("--min-coverage-ratio", default="1.10")
    evaluate.add_argument("--min-runway-periods", default="3")
    evaluate.add_argument("--min-gross-margin-percent", default="20")
    evaluate.add_argument("--min-net-margin-percent", default="5")
    evaluate.add_argument("--max-budget-variance-percent", default="10")
    evaluate.add_argument("--max-cost-variance-percent", default="12")
    evaluate.add_argument("--max-debt-to-equity", default="2.50")
    evaluate.add_argument("--max-ar-overdue", default="50000")
    evaluate.add_argument("--cooldown-minutes", default=60, type=int)
    evaluate.add_argument("--dedupe-window-minutes", default=240, type=int)
    evaluate.add_argument("--no-hash-entity-ids", action="store_true")

    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger(APP_NAME)

    try:
        if args.command == "evaluate":
            policy = FinancialAlertPolicy(
                currency=args.currency.upper(),
                min_cash_balance=to_decimal(args.min_cash_balance),
                min_current_ratio=to_decimal(args.min_current_ratio),
                min_coverage_ratio=to_decimal(args.min_coverage_ratio),
                min_runway_periods=to_decimal(args.min_runway_periods),
                min_gross_margin_percent=to_decimal(args.min_gross_margin_percent),
                min_net_margin_percent=to_decimal(args.min_net_margin_percent),
                max_budget_variance_percent=to_decimal(args.max_budget_variance_percent),
                max_cost_variance_percent=to_decimal(args.max_cost_variance_percent),
                max_debt_to_equity=to_decimal(args.max_debt_to_equity),
                max_ar_overdue=to_decimal(args.max_ar_overdue),
                cooldown_minutes=args.cooldown_minutes,
                dedupe_window_minutes=args.dedupe_window_minutes,
                hash_entity_ids=not args.no_hash_entity_ids,
            )
            logger.info("Carregando métricas financeiras de %s", args.input)
            rows = FileLoader.load(args.input)
            metrics = FinancialMetricParser.parse_many(rows, policy)
            logger.info("Avaliando %s métrica(s)", len(metrics))
            engine = FinancialAlertEngine(policy)
            summary, alerts = engine.evaluate(metrics)
            ResultWriter.write(summary, alerts, args.output, OutputFormat(args.format))
            logger.info("Alertas salvos em %s", args.output)
            print(args.output)
            return 0

        raise FinancialAlertError(f"Comando não suportado: {args.command}")

    except FinancialAlertError as exc:
        logger.error("Erro no financial alert: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
