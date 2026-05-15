#!/usr/bin/env python3
"""
ai_engine/anomaly_trigger.py

Enterprise-grade Anomaly Trigger Engine.

Objetivo:
- Transformar scores/anomalias em gatilhos operacionais acionáveis.
- Aplicar thresholds estáticos e dinâmicos, cooldown, deduplicação, supressão e agregação.
- Classificar severidade, decisão recomendada e canal de roteamento.
- Gerar eventos padronizados para alertas, filas, SIEM, antifraude, risco, observabilidade e auditoria.
- Rodar em batch via CSV/JSON e exportar JSON/CSV.

Exemplos:
    python ai_engine/anomaly_trigger.py evaluate \
        --input data/anomaly_events.csv \
        --output reports/ai/anomaly_triggers.json \
        --format json

    python ai_engine/anomaly_trigger.py evaluate \
        --input data/anomaly_events.csv \
        --output reports/ai/anomaly_triggers.csv \
        --format csv \
        --medium-threshold 35 \
        --high-threshold 65 \
        --critical-threshold 85 \
        --cooldown-minutes 30

Formato esperado CSV/JSON:
    event_id,entity_id,timestamp,score,metric_name,metric_value,baseline_value,zscore,source,domain,category,signal,severity_hint,status,metadata

Campos mínimos:
    event_id: string
    entity_id: string
    timestamp: ISO datetime
    score: número 0-100

Campos opcionais:
    metric_name: string
    metric_value: número
    baseline_value: número
    zscore: número
    source: string
    domain: fraud|ueba|risk|finance|ops|security|observability|...
    category: string
    signal: string
    severity_hint: low|medium|high|critical
    status: open|closed|suppressed|resolved
    metadata: JSON string opcional

JSON aceito:
    [ { ... }, { ... } ]
    ou
    { "events": [ { ... }, { ... } ] }
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import math
import statistics
import sys
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, Deque, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


APP_NAME = "anomaly_trigger"
ENGINE_VERSION = "1.0.0"
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


class TriggerDecision(str, Enum):
    IGNORE = "ignore"
    MONITOR = "monitor"
    ALERT = "alert"
    ESCALATE = "escalate"
    INCIDENT = "incident"


class TriggerStatus(str, Enum):
    FIRED = "fired"
    SUPPRESSED = "suppressed"
    DEDUPED = "deduped"
    COOLDOWN = "cooldown"
    BELOW_THRESHOLD = "below_threshold"


class Route(str, Enum):
    NONE = "none"
    OPS = "ops"
    RISK = "risk"
    FRAUD = "fraud"
    SECURITY = "security"
    FINANCE = "finance"
    DATA = "data"
    EXECUTIVE = "executive"


@dataclass(frozen=True)
class TriggerPolicy:
    medium_threshold: Decimal = Decimal("35")
    high_threshold: Decimal = Decimal("65")
    critical_threshold: Decimal = Decimal("85")
    zscore_medium: Decimal = Decimal("2.0")
    zscore_high: Decimal = Decimal("3.0")
    zscore_critical: Decimal = Decimal("4.5")
    cooldown_minutes: int = 30
    dedupe_window_minutes: int = 60
    aggregation_window_minutes: int = 15
    burst_count_threshold: int = 5
    dynamic_threshold_enabled: bool = True
    dynamic_threshold_min_events: int = 20
    dynamic_threshold_std_multiplier: Decimal = Decimal("2.5")
    suppress_closed_events: bool = True
    suppress_low_severity: bool = False
    hash_entity_ids: bool = True


@dataclass(frozen=True)
class AnomalyEvent:
    event_id: str
    entity_id: str
    entity_id_hash: str
    timestamp: datetime
    score: Decimal
    metric_name: Optional[str]
    metric_value: Optional[Decimal]
    baseline_value: Optional[Decimal]
    zscore: Optional[Decimal]
    source: Optional[str]
    domain: Optional[str]
    category: Optional[str]
    signal: Optional[str]
    severity_hint: Optional[Severity]
    status: Optional[str]
    metadata: Dict[str, Any]
    raw: Dict[str, Any]


@dataclass(frozen=True)
class TriggerContext:
    dynamic_threshold: Decimal
    entity_recent_count: int
    signal_recent_count: int
    domain_recent_count: int
    duplicate_key: str
    cooldown_key: str
    last_trigger_at: Optional[str]
    historical_mean_score: Decimal
    historical_std_score: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dynamic_threshold": decimal_str(self.dynamic_threshold),
            "entity_recent_count": self.entity_recent_count,
            "signal_recent_count": self.signal_recent_count,
            "domain_recent_count": self.domain_recent_count,
            "duplicate_key": self.duplicate_key,
            "cooldown_key": self.cooldown_key,
            "last_trigger_at": self.last_trigger_at,
            "historical_mean_score": decimal_str(self.historical_mean_score),
            "historical_std_score": decimal_str(self.historical_std_score),
        }


@dataclass(frozen=True)
class TriggerResult:
    trigger_id: str
    event_id: str
    entity_id_hash: str
    timestamp: str
    status: str
    decision: str
    severity: str
    route: str
    score: Decimal
    threshold_used: Decimal
    reasons: List[str]
    recommended_actions: List[str]
    dedupe_key: str
    correlation_key: str
    context: TriggerContext
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "event_id": self.event_id,
            "entity_id_hash": self.entity_id_hash,
            "timestamp": self.timestamp,
            "status": self.status,
            "decision": self.decision,
            "severity": self.severity,
            "route": self.route,
            "score": decimal_str(self.score),
            "threshold_used": decimal_str(self.threshold_used),
            "reasons": self.reasons,
            "recommended_actions": self.recommended_actions,
            "dedupe_key": self.dedupe_key,
            "correlation_key": self.correlation_key,
            "context": self.context.to_dict(),
            "payload": self.payload,
        }


@dataclass(frozen=True)
class TriggerSummary:
    total_events: int
    total_triggers: int
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
    top_signals: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class AnomalyTriggerError(Exception):
    """Base exception for anomaly trigger engine."""


class InputValidationError(AnomalyTriggerError):
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
        if isinstance(payload, dict) and isinstance(payload.get("events"), list):
            return payload["events"]
        raise InputValidationError("JSON inválido. Esperado lista ou objeto com chave 'events'.")


class AnomalyEventParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]], policy: TriggerPolicy) -> List[AnomalyEvent]:
        events: List[AnomalyEvent] = []
        errors: List[str] = []
        for index, row in enumerate(rows, start=1):
            try:
                events.append(AnomalyEventParser.parse(row, policy))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"linha={index}: {exc}")
        if errors:
            preview = "\n".join(errors[:30])
            extra = "" if len(errors) <= 30 else f"\n... e mais {len(errors) - 30} erro(s)."
            raise InputValidationError(f"Falha ao validar eventos de anomalia:\n{preview}{extra}")
        return sorted(events, key=lambda item: item.timestamp)

    @staticmethod
    def parse(row: Dict[str, Any], policy: TriggerPolicy) -> AnomalyEvent:
        event_id = required_str(row, "event_id")
        entity_id = required_str(row, "entity_id")
        timestamp = parse_datetime(required_str(row, "timestamp"))
        score = clamp_decimal(to_decimal(row.get("score")), Decimal("0"), Decimal("100"))
        metadata = parse_metadata(optional_str(row, "metadata"))
        entity_hash = hash_identifier(entity_id) if policy.hash_entity_ids else entity_id
        return AnomalyEvent(
            event_id=event_id,
            entity_id=entity_id,
            entity_id_hash=entity_hash,
            timestamp=timestamp,
            score=score,
            metric_name=normalize_lower(optional_str(row, "metric_name")),
            metric_value=optional_decimal(row, "metric_value"),
            baseline_value=optional_decimal(row, "baseline_value"),
            zscore=optional_decimal(row, "zscore"),
            source=normalize_lower(optional_str(row, "source")),
            domain=normalize_lower(optional_str(row, "domain")),
            category=normalize_lower(optional_str(row, "category")),
            signal=normalize_lower(optional_str(row, "signal")),
            severity_hint=parse_severity_hint(optional_str(row, "severity_hint")),
            status=normalize_lower(optional_str(row, "status")),
            metadata=metadata,
            raw=row,
        )


class AnomalyTriggerEngine:
    def __init__(self, policy: TriggerPolicy) -> None:
        self.policy = policy
        self.recent_by_entity: DefaultDict[str, Deque[AnomalyEvent]] = defaultdict(deque)
        self.recent_by_signal: DefaultDict[str, Deque[AnomalyEvent]] = defaultdict(deque)
        self.recent_by_domain: DefaultDict[str, Deque[AnomalyEvent]] = defaultdict(deque)
        self.score_history_by_signal: DefaultDict[str, List[Decimal]] = defaultdict(list)
        self.last_trigger_by_cooldown_key: Dict[str, datetime] = {}
        self.seen_dedupe_keys: Dict[str, datetime] = {}
        self.logger = logging.getLogger(f"{APP_NAME}.{self.__class__.__name__}")

    def evaluate(self, events: Sequence[AnomalyEvent]) -> Tuple[TriggerSummary, List[TriggerResult]]:
        if not events:
            raise InputValidationError("Nenhum evento válido para avaliação")
        results: List[TriggerResult] = []
        for event in sorted(events, key=lambda item: item.timestamp):
            result = self.evaluate_one(event)
            results.append(result)
            self._update_state(event, result)
        return self._summary(events, results), results

    def evaluate_one(self, event: AnomalyEvent) -> TriggerResult:
        context = self._build_context(event)
        severity, severity_reasons = self._severity(event, context.dynamic_threshold)
        decision = self._decision(severity)
        route = self._route(event, severity)
        threshold_used = context.dynamic_threshold
        reasons = list(severity_reasons)
        status = TriggerStatus.FIRED

        if self._should_suppress(event):
            status = TriggerStatus.SUPPRESSED
            decision = TriggerDecision.IGNORE
            route = Route.NONE
            reasons.append("suppressed_by_policy")
        elif event.score < threshold_used and not self._zscore_forces_trigger(event):
            status = TriggerStatus.BELOW_THRESHOLD
            decision = TriggerDecision.IGNORE
            route = Route.NONE
            reasons.append("score_below_threshold")
        elif self._is_duplicate(event, context.duplicate_key):
            status = TriggerStatus.DEDUPED
            decision = TriggerDecision.MONITOR
            reasons.append("deduplicated_within_window")
        elif self._is_in_cooldown(event, context.cooldown_key):
            status = TriggerStatus.COOLDOWN
            decision = TriggerDecision.MONITOR
            reasons.append("cooldown_active")

        if context.entity_recent_count >= self.policy.burst_count_threshold:
            reasons.append("entity_burst_detected")
            if status == TriggerStatus.FIRED and severity in {Severity.MEDIUM, Severity.HIGH}:
                severity = Severity.HIGH if severity == Severity.MEDIUM else Severity.CRITICAL
                decision = self._decision(severity)

        actions = self._actions(status, decision, severity, event)
        dedupe_key = context.duplicate_key
        correlation_key = self._correlation_key(event)
        payload = self._payload(event, severity, decision, route, status)

        return TriggerResult(
            trigger_id=self._trigger_id(event, dedupe_key),
            event_id=event.event_id,
            entity_id_hash=event.entity_id_hash,
            timestamp=event.timestamp.isoformat(),
            status=status.value,
            decision=decision.value,
            severity=severity.value,
            route=route.value,
            score=event.score,
            threshold_used=threshold_used,
            reasons=unique_ordered(reasons),
            recommended_actions=actions,
            dedupe_key=dedupe_key,
            correlation_key=correlation_key,
            context=context,
            payload=payload,
        )

    def _build_context(self, event: AnomalyEvent) -> TriggerContext:
        signal_key = self._signal_key(event)
        domain_key = event.domain or "unknown"
        self._evict_old(self.recent_by_entity[event.entity_id_hash], event.timestamp, self.policy.aggregation_window_minutes)
        self._evict_old(self.recent_by_signal[signal_key], event.timestamp, self.policy.aggregation_window_minutes)
        self._evict_old(self.recent_by_domain[domain_key], event.timestamp, self.policy.aggregation_window_minutes)

        scores = self.score_history_by_signal.get(signal_key, [])
        mean_score = mean_decimal(scores)
        std_score = std_decimal(scores)
        dynamic_threshold = self._dynamic_threshold(scores)
        duplicate_key = self._dedupe_key(event)
        cooldown_key = self._cooldown_key(event)
        last_trigger = self.last_trigger_by_cooldown_key.get(cooldown_key)

        return TriggerContext(
            dynamic_threshold=dynamic_threshold,
            entity_recent_count=len(self.recent_by_entity[event.entity_id_hash]),
            signal_recent_count=len(self.recent_by_signal[signal_key]),
            domain_recent_count=len(self.recent_by_domain[domain_key]),
            duplicate_key=duplicate_key,
            cooldown_key=cooldown_key,
            last_trigger_at=last_trigger.isoformat() if last_trigger else None,
            historical_mean_score=mean_score,
            historical_std_score=std_score,
        )

    def _dynamic_threshold(self, scores: Sequence[Decimal]) -> Decimal:
        if not self.policy.dynamic_threshold_enabled or len(scores) < self.policy.dynamic_threshold_min_events:
            return self.policy.medium_threshold
        mean_score = mean_decimal(scores)
        std_score = std_decimal(scores)
        dynamic = mean_score + (std_score * self.policy.dynamic_threshold_std_multiplier)
        return clamp_decimal(max_decimal(dynamic, self.policy.medium_threshold), Decimal("0"), self.policy.critical_threshold)

    def _severity(self, event: AnomalyEvent, threshold: Decimal) -> Tuple[Severity, List[str]]:
        reasons: List[str] = []
        if event.severity_hint:
            reasons.append(f"severity_hint={event.severity_hint.value}")

        zscore = event.zscore
        score = event.score
        if event.severity_hint == Severity.CRITICAL or score >= self.policy.critical_threshold or (zscore is not None and zscore >= self.policy.zscore_critical):
            reasons.append("critical_threshold_met")
            return Severity.CRITICAL, reasons
        if event.severity_hint == Severity.HIGH or score >= self.policy.high_threshold or (zscore is not None and zscore >= self.policy.zscore_high):
            reasons.append("high_threshold_met")
            return Severity.HIGH, reasons
        if event.severity_hint == Severity.MEDIUM or score >= threshold or (zscore is not None and zscore >= self.policy.zscore_medium):
            reasons.append("medium_threshold_met")
            return Severity.MEDIUM, reasons
        if event.severity_hint == Severity.LOW or score > 0:
            reasons.append("low_signal")
            return Severity.LOW, reasons
        return Severity.INFO, ["informational"]

    def _decision(self, severity: Severity) -> TriggerDecision:
        if severity == Severity.CRITICAL:
            return TriggerDecision.INCIDENT
        if severity == Severity.HIGH:
            return TriggerDecision.ESCALATE
        if severity == Severity.MEDIUM:
            return TriggerDecision.ALERT
        if severity == Severity.LOW:
            return TriggerDecision.MONITOR
        return TriggerDecision.IGNORE

    @staticmethod
    def _route(event: AnomalyEvent, severity: Severity) -> Route:
        domain = (event.domain or "").lower()
        if severity == Severity.CRITICAL:
            if domain in {"security", "ueba", "fraud"}:
                return Route.SECURITY if domain in {"security", "ueba"} else Route.FRAUD
            if domain in {"finance", "liquidity", "cashflow", "revenue"}:
                return Route.FINANCE
            return Route.EXECUTIVE
        if domain in {"fraud", "payments", "transaction"}:
            return Route.FRAUD
        if domain in {"security", "ueba", "iam", "soc"}:
            return Route.SECURITY
        if domain in {"risk", "credit", "compliance"}:
            return Route.RISK
        if domain in {"finance", "liquidity", "cashflow", "revenue", "payroll"}:
            return Route.FINANCE
        if domain in {"data", "ml", "ai", "quality"}:
            return Route.DATA
        if domain in {"ops", "observability", "infra"}:
            return Route.OPS
        return Route.OPS

    def _actions(self, status: TriggerStatus, decision: TriggerDecision, severity: Severity, event: AnomalyEvent) -> List[str]:
        if status in {TriggerStatus.SUPPRESSED, TriggerStatus.BELOW_THRESHOLD}:
            return ["no_action"]
        if status == TriggerStatus.DEDUPED:
            return ["attach_to_existing_case", "update_correlation_context"]
        if status == TriggerStatus.COOLDOWN:
            return ["monitor_during_cooldown", "update_existing_alert"]
        if decision == TriggerDecision.INCIDENT:
            return ["open_incident", "page_on_call", "freeze_or_hold_if_applicable", "executive_visibility"]
        if decision == TriggerDecision.ESCALATE:
            return ["create_case", "notify_owner_team", "manual_review", "increase_monitoring"]
        if decision == TriggerDecision.ALERT:
            return ["send_alert", "add_to_review_queue", "monitor_next_events"]
        if decision == TriggerDecision.MONITOR:
            return ["record_signal", "monitor_trend"]
        return ["no_action"]

    def _should_suppress(self, event: AnomalyEvent) -> bool:
        if self.policy.suppress_closed_events and event.status in {"closed", "resolved", "suppressed", "cancelled", "canceled"}:
            return True
        if self.policy.suppress_low_severity and event.severity_hint in {Severity.INFO, Severity.LOW}:
            return True
        return False

    def _zscore_forces_trigger(self, event: AnomalyEvent) -> bool:
        return event.zscore is not None and event.zscore >= self.policy.zscore_medium

    def _is_duplicate(self, event: AnomalyEvent, dedupe_key: str) -> bool:
        self._evict_dedupe(event.timestamp)
        return dedupe_key in self.seen_dedupe_keys

    def _is_in_cooldown(self, event: AnomalyEvent, cooldown_key: str) -> bool:
        last = self.last_trigger_by_cooldown_key.get(cooldown_key)
        if not last:
            return False
        return event.timestamp - last <= timedelta(minutes=self.policy.cooldown_minutes)

    def _update_state(self, event: AnomalyEvent, result: TriggerResult) -> None:
        signal_key = self._signal_key(event)
        domain_key = event.domain or "unknown"
        self.recent_by_entity[event.entity_id_hash].append(event)
        self.recent_by_signal[signal_key].append(event)
        self.recent_by_domain[domain_key].append(event)
        self.score_history_by_signal[signal_key].append(event.score)
        self.seen_dedupe_keys[result.dedupe_key] = event.timestamp
        if result.status == TriggerStatus.FIRED.value:
            self.last_trigger_by_cooldown_key[result.context.cooldown_key] = event.timestamp

    def _evict_old(self, events: Deque[AnomalyEvent], now: datetime, window_minutes: int) -> None:
        cutoff = now - timedelta(minutes=window_minutes)
        while events and events[0].timestamp < cutoff:
            events.popleft()

    def _evict_dedupe(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=self.policy.dedupe_window_minutes)
        old_keys = [key for key, seen_at in self.seen_dedupe_keys.items() if seen_at < cutoff]
        for key in old_keys:
            self.seen_dedupe_keys.pop(key, None)

    def _signal_key(self, event: AnomalyEvent) -> str:
        return "|".join([event.domain or "unknown", event.category or "unknown", event.signal or event.metric_name or "unknown"])

    def _dedupe_key(self, event: AnomalyEvent) -> str:
        raw = "|".join([event.entity_id_hash, event.domain or "", event.category or "", event.signal or "", event.metric_name or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _cooldown_key(self, event: AnomalyEvent) -> str:
        raw = "|".join([event.entity_id_hash, event.domain or "", event.signal or event.metric_name or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _correlation_key(self, event: AnomalyEvent) -> str:
        raw = "|".join([event.entity_id_hash, event.domain or "unknown", event.source or "unknown"])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _trigger_id(self, event: AnomalyEvent, dedupe_key: str) -> str:
        raw = f"{event.event_id}|{event.timestamp.isoformat()}|{dedupe_key}|{uuid.uuid4()}"
        return "trg_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _payload(self, event: AnomalyEvent, severity: Severity, decision: TriggerDecision, route: Route, status: TriggerStatus) -> Dict[str, Any]:
        return {
            "engine": APP_NAME,
            "engine_version": ENGINE_VERSION,
            "event_id": event.event_id,
            "entity_id_hash": event.entity_id_hash,
            "timestamp": event.timestamp.isoformat(),
            "domain": event.domain,
            "source": event.source,
            "category": event.category,
            "signal": event.signal,
            "metric_name": event.metric_name,
            "metric_value": None if event.metric_value is None else decimal_str(event.metric_value),
            "baseline_value": None if event.baseline_value is None else decimal_str(event.baseline_value),
            "zscore": None if event.zscore is None else decimal_str(event.zscore),
            "score": decimal_str(event.score),
            "severity": severity.value,
            "decision": decision.value,
            "route": route.value,
            "status": status.value,
            "metadata": event.metadata,
        }

    def _summary(self, events: Sequence[AnomalyEvent], results: Sequence[TriggerResult]) -> TriggerSummary:
        statuses = Counter(item.status for item in results)
        severities = Counter(item.severity for item in results)
        routes = Counter(item.route for item in results)
        decisions = Counter(item.decision for item in results)
        domains = Counter((event.domain or "unknown") for event in events)
        signals = Counter((event.signal or event.metric_name or "unknown") for event in events)
        fired_statuses = {TriggerStatus.FIRED.value}
        return TriggerSummary(
            total_events=len(events),
            total_triggers=len(results),
            fired=sum(1 for item in results if item.status in fired_statuses),
            suppressed=statuses.get(TriggerStatus.SUPPRESSED.value, 0),
            deduped=statuses.get(TriggerStatus.DEDUPED.value, 0),
            cooldown=statuses.get(TriggerStatus.COOLDOWN.value, 0),
            below_threshold=statuses.get(TriggerStatus.BELOW_THRESHOLD.value, 0),
            critical=severities.get(Severity.CRITICAL.value, 0),
            high=severities.get(Severity.HIGH.value, 0),
            medium=severities.get(Severity.MEDIUM.value, 0),
            low=severities.get(Severity.LOW.value, 0),
            routes=dict(routes),
            decisions=dict(decisions),
            domains=dict(domains),
            top_signals=[{"signal": key, "count": value} for key, value in signals.most_common(20)],
        )


class ResultWriter:
    @staticmethod
    def write(summary: TriggerSummary, results: Sequence[TriggerResult], output: Path, output_format: OutputFormat) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output_format == OutputFormat.JSON:
            payload = {
                "engine_version": ENGINE_VERSION,
                "generated_at": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
                "summary": summary.to_dict(),
                "triggers": [item.to_dict() for item in results],
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return output
        if output_format == OutputFormat.CSV:
            fieldnames = [
                "trigger_id", "event_id", "entity_id_hash", "timestamp", "status", "decision", "severity", "route",
                "score", "threshold_used", "reasons", "recommended_actions", "dedupe_key", "correlation_key",
            ]
            with output.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                for result in results:
                    payload = result.to_dict()
                    writer.writerow(
                        {
                            "trigger_id": payload["trigger_id"],
                            "event_id": payload["event_id"],
                            "entity_id_hash": payload["entity_id_hash"],
                            "timestamp": payload["timestamp"],
                            "status": payload["status"],
                            "decision": payload["decision"],
                            "severity": payload["severity"],
                            "route": payload["route"],
                            "score": payload["score"],
                            "threshold_used": payload["threshold_used"],
                            "reasons": "|".join(payload["reasons"]),
                            "recommended_actions": "|".join(payload["recommended_actions"]),
                            "dedupe_key": payload["dedupe_key"],
                            "correlation_key": payload["correlation_key"],
                        }
                    )
            return output
        raise AnomalyTriggerError(f"Formato não suportado: {output_format}")


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


def parse_severity_hint(value: Optional[str]) -> Optional[Severity]:
    if not value:
        return None
    text = value.strip().lower()
    aliases = {
        "info": Severity.INFO,
        "informational": Severity.INFO,
        "low": Severity.LOW,
        "baixo": Severity.LOW,
        "baixa": Severity.LOW,
        "medium": Severity.MEDIUM,
        "medio": Severity.MEDIUM,
        "médio": Severity.MEDIUM,
        "media": Severity.MEDIUM,
        "média": Severity.MEDIUM,
        "high": Severity.HIGH,
        "alto": Severity.HIGH,
        "alta": Severity.HIGH,
        "critical": Severity.CRITICAL,
        "critico": Severity.CRITICAL,
        "crítico": Severity.CRITICAL,
        "critica": Severity.CRITICAL,
        "crítica": Severity.CRITICAL,
    }
    if text not in aliases:
        raise ValueError(f"severity_hint inválido: {value}")
    return aliases[text]


def normalize_lower(value: Optional[str]) -> Optional[str]:
    return value.lower() if value else None


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def max_decimal(left: Decimal, right: Decimal) -> Decimal:
    return left if left >= right else right


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def std_decimal(values: Sequence[Decimal]) -> Decimal:
    if len(values) < 2:
        return Decimal("0")
    avg = mean_decimal(values)
    variance = sum((item - avg) ** 2 for item in values) / Decimal(len(values) - 1)
    return Decimal(str(math.sqrt(float(variance))))


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
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Enterprise anomaly trigger engine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="Avalia eventos de anomalia e gera triggers.")
    evaluate.add_argument("--input", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    evaluate.add_argument("--format", default=OutputFormat.JSON.value, choices=[item.value for item in OutputFormat])
    evaluate.add_argument("--medium-threshold", default="35")
    evaluate.add_argument("--high-threshold", default="65")
    evaluate.add_argument("--critical-threshold", default="85")
    evaluate.add_argument("--cooldown-minutes", default=30, type=int)
    evaluate.add_argument("--dedupe-window-minutes", default=60, type=int)
    evaluate.add_argument("--aggregation-window-minutes", default=15, type=int)
    evaluate.add_argument("--burst-count-threshold", default=5, type=int)
    evaluate.add_argument("--disable-dynamic-threshold", action="store_true")
    evaluate.add_argument("--suppress-low-severity", action="store_true")
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
            policy = TriggerPolicy(
                medium_threshold=to_decimal(args.medium_threshold),
                high_threshold=to_decimal(args.high_threshold),
                critical_threshold=to_decimal(args.critical_threshold),
                cooldown_minutes=args.cooldown_minutes,
                dedupe_window_minutes=args.dedupe_window_minutes,
                aggregation_window_minutes=args.aggregation_window_minutes,
                burst_count_threshold=args.burst_count_threshold,
                dynamic_threshold_enabled=not args.disable_dynamic_threshold,
                suppress_low_severity=args.suppress_low_severity,
                hash_entity_ids=not args.no_hash_entity_ids,
            )
            logger.info("Carregando eventos de anomalia de %s", args.input)
            rows = FileLoader.load(args.input)
            events = AnomalyEventParser.parse_many(rows, policy)
            logger.info("Avaliando %s evento(s)", len(events))
            engine = AnomalyTriggerEngine(policy)
            summary, results = engine.evaluate(events)
            ResultWriter.write(summary, results, args.output, OutputFormat(args.format))
            logger.info("Triggers salvos em %s", args.output)
            print(args.output)
            return 0

        raise AnomalyTriggerError(f"Comando não suportado: {args.command}")

    except AnomalyTriggerError as exc:
        logger.error("Erro no anomaly trigger: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
