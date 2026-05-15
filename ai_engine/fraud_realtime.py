#!/usr/bin/env python3
"""
ai_engine/fraud_realtime.py

Enterprise-grade Real-time Fraud Decision Engine.

Objetivo:
- Avaliar eventos/transações em tempo quase real com baixa latência.
- Manter estado em memória com TTL para velocity, deduplicação, cooldown e perfis recentes.
- Gerar score 0-100, decisão síncrona e explicabilidade.
- Suportar modo batch para simulação/replay e integração com filas/API.
- Ser usado por pagamentos, antifraude, UEBA, risco, segurança, checkout, billing e transações digitais.

Exemplos:
    python ai_engine/fraud_realtime.py replay \
        --input data/realtime_fraud_events.csv \
        --output reports/fraud/realtime_decisions.json \
        --format json

    python ai_engine/fraud_realtime.py replay \
        --input data/realtime_fraud_events.csv \
        --output reports/fraud/realtime_decisions.csv \
        --format csv \
        --high-threshold 65 \
        --critical-threshold 85 \
        --velocity-window-seconds 600

Formato esperado CSV/JSON:
    event_id,entity_id,timestamp,event_type,amount,currency,direction,channel,ip_address,device_id,country,city,counterparty,merchant_id,reference_id,status,success

Campos mínimos:
    event_id: string
    entity_id: string
    timestamp: ISO datetime
    event_type: string

Campos opcionais:
    amount: número
    currency: BRL|USD|...
    direction: credit|debit|inflow|outflow
    channel: web|mobile|api|pos|atm|card|bank_transfer
    ip_address: string
    device_id: string
    country: string
    city: string
    counterparty: string
    merchant_id: string
    reference_id: string
    status: approved|declined|failed|pending|cancelled|reversed|chargeback|refund
    success: true|false

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
import ipaddress
import json
import logging
import math
import sys
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from pathlib import Path
from typing import Any, Deque, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


APP_NAME = "fraud_realtime"
ENGINE_VERSION = "1.0.0"
DEFAULT_CURRENCY = "BRL"
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION


class OutputFormat(str, Enum):
    JSON = "json"
    CSV = "csv"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FraudDecision(str, Enum):
    APPROVE = "approve"
    MONITOR = "monitor"
    CHALLENGE = "challenge"
    REVIEW = "review"
    DECLINE_CANDIDATE = "decline_candidate"


class Direction(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"
    UNKNOWN = "unknown"


class EventStatus(str, Enum):
    APPROVED = "approved"
    DECLINED = "declined"
    FAILED = "failed"
    PENDING = "pending"
    CANCELLED = "cancelled"
    REVERSED = "reversed"
    CHARGEBACK = "chargeback"
    REFUND = "refund"
    UNKNOWN = "unknown"


class FraudSignal(str, Enum):
    HIGH_AMOUNT = "high_amount"
    HIGH_VELOCITY = "high_velocity"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    NEW_DEVICE = "new_device"
    NEW_IP = "new_ip"
    NEW_COUNTRY = "new_country"
    NEW_CHANNEL = "new_channel"
    PRIVATE_OR_INVALID_IP = "private_or_invalid_ip"
    FAILED_OR_DECLINED = "failed_or_declined"
    CHARGEBACK_OR_REFUND = "chargeback_or_refund"
    OFF_HOURS = "off_hours"
    ROUND_AMOUNT = "round_amount"
    COUNTERPARTY_BURST = "counterparty_burst"
    MERCHANT_BURST = "merchant_burst"
    BASELINE_NORMAL = "baseline_normal"


@dataclass(frozen=True)
class RealtimeFraudPolicy:
    currency: str = DEFAULT_CURRENCY
    medium_threshold: Decimal = Decimal("35")
    high_threshold: Decimal = Decimal("65")
    critical_threshold: Decimal = Decimal("85")
    velocity_window_seconds: int = 600
    velocity_count_threshold: int = 8
    amount_velocity_threshold: Decimal = Decimal("5000")
    duplicate_window_seconds: int = 600
    duplicate_amount_tolerance: Decimal = Decimal("0.01")
    profile_ttl_seconds: int = 86400
    cooldown_seconds: int = 900
    off_hours_start: int = 22
    off_hours_end: int = 6
    high_amount_multiplier: Decimal = Decimal("3")
    min_profile_events_for_baseline: int = 5
    hash_entity_ids: bool = True
    score_high_amount: Decimal = Decimal("24")
    score_high_velocity: Decimal = Decimal("22")
    score_duplicate: Decimal = Decimal("35")
    score_new_device: Decimal = Decimal("16")
    score_new_ip: Decimal = Decimal("10")
    score_new_country: Decimal = Decimal("22")
    score_new_channel: Decimal = Decimal("8")
    score_private_or_invalid_ip: Decimal = Decimal("6")
    score_failed: Decimal = Decimal("12")
    score_chargeback_refund: Decimal = Decimal("30")
    score_off_hours: Decimal = Decimal("8")
    score_round_amount: Decimal = Decimal("5")
    score_counterparty_burst: Decimal = Decimal("15")
    score_merchant_burst: Decimal = Decimal("12")


@dataclass(frozen=True)
class FraudRealtimeEvent:
    event_id: str
    entity_id: str
    entity_id_hash: str
    timestamp: datetime
    event_type: str
    amount: Decimal
    currency: str
    direction: Direction
    channel: Optional[str]
    ip_address: Optional[str]
    device_id_hash: Optional[str]
    country: Optional[str]
    city: Optional[str]
    counterparty_hash: Optional[str]
    merchant_id_hash: Optional[str]
    reference_id_hash: Optional[str]
    status: EventStatus
    success: bool
    raw: Dict[str, Any]


@dataclass
class EntityRealtimeProfile:
    entity_id_hash: str
    first_seen_at: str
    last_seen_at: str
    event_count: int = 0
    amount_sum: Decimal = Decimal("0")
    amount_values: Deque[Decimal] = field(default_factory=lambda: deque(maxlen=500))
    known_devices: set[str] = field(default_factory=set)
    known_ips: set[str] = field(default_factory=set)
    known_countries: set[str] = field(default_factory=set)
    known_channels: set[str] = field(default_factory=set)
    known_counterparties: set[str] = field(default_factory=set)
    known_merchants: set[str] = field(default_factory=set)
    active_hours: Counter = field(default_factory=Counter)
    status_counts: Counter = field(default_factory=Counter)

    @property
    def median_amount(self) -> Decimal:
        return median_decimal(list(self.amount_values))

    @property
    def avg_amount(self) -> Decimal:
        if not self.amount_values:
            return Decimal("0")
        return sum_decimal(self.amount_values) / Decimal(len(self.amount_values))

    @property
    def p95_amount(self) -> Decimal:
        return percentile_decimal(list(self.amount_values), 95)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id_hash": self.entity_id_hash,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "event_count": self.event_count,
            "avg_amount": money_str(self.avg_amount),
            "median_amount": money_str(self.median_amount),
            "p95_amount": money_str(self.p95_amount),
            "known_devices": sorted(self.known_devices),
            "known_ips": sorted(self.known_ips),
            "known_countries": sorted(self.known_countries),
            "known_channels": sorted(self.known_channels),
            "known_counterparties": sorted(self.known_counterparties),
            "known_merchants": sorted(self.known_merchants),
            "active_hours": dict(self.active_hours),
            "status_counts": dict(self.status_counts),
        }


@dataclass(frozen=True)
class RealtimeFeatures:
    velocity_count: int
    velocity_amount: Decimal
    duplicate_candidate: bool
    amount_ratio_to_median: Decimal
    is_new_device: bool
    is_new_ip: bool
    is_new_country: bool
    is_new_channel: bool
    private_or_invalid_ip: bool
    failed_or_declined: bool
    chargeback_or_refund: bool
    off_hours: bool
    round_amount: bool
    counterparty_burst_count: int
    merchant_burst_count: int
    profile_event_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "velocity_count": self.velocity_count,
            "velocity_amount": money_str(self.velocity_amount),
            "duplicate_candidate": self.duplicate_candidate,
            "amount_ratio_to_median": decimal_str(self.amount_ratio_to_median),
            "is_new_device": self.is_new_device,
            "is_new_ip": self.is_new_ip,
            "is_new_country": self.is_new_country,
            "is_new_channel": self.is_new_channel,
            "private_or_invalid_ip": self.private_or_invalid_ip,
            "failed_or_declined": self.failed_or_declined,
            "chargeback_or_refund": self.chargeback_or_refund,
            "off_hours": self.off_hours,
            "round_amount": self.round_amount,
            "counterparty_burst_count": self.counterparty_burst_count,
            "merchant_burst_count": self.merchant_burst_count,
            "profile_event_count": self.profile_event_count,
        }


@dataclass(frozen=True)
class FraudDecisionResult:
    decision_id: str
    event_id: str
    entity_id_hash: str
    timestamp: str
    decision: str
    risk_level: str
    risk_score: Decimal
    latency_ms: Decimal
    signals: List[str]
    reasons: List[str]
    recommended_actions: List[str]
    features: RealtimeFeatures
    cooldown_active: bool
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "event_id": self.event_id,
            "entity_id_hash": self.entity_id_hash,
            "timestamp": self.timestamp,
            "decision": self.decision,
            "risk_level": self.risk_level,
            "risk_score": decimal_str(self.risk_score),
            "latency_ms": decimal_str(self.latency_ms),
            "signals": self.signals,
            "reasons": self.reasons,
            "recommended_actions": self.recommended_actions,
            "features": self.features.to_dict(),
            "cooldown_active": self.cooldown_active,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ReplaySummary:
    total_events: int
    approve: int
    monitor: int
    challenge: int
    review: int
    decline_candidate: int
    low: int
    medium: int
    high: int
    critical: int
    avg_risk_score: Decimal
    avg_latency_ms: Decimal
    top_signals: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_events": self.total_events,
            "approve": self.approve,
            "monitor": self.monitor,
            "challenge": self.challenge,
            "review": self.review,
            "decline_candidate": self.decline_candidate,
            "low": self.low,
            "medium": self.medium,
            "high": self.high,
            "critical": self.critical,
            "avg_risk_score": decimal_str(self.avg_risk_score),
            "avg_latency_ms": decimal_str(self.avg_latency_ms),
            "top_signals": self.top_signals,
        }


class FraudRealtimeError(Exception):
    """Base exception for realtime fraud engine."""


class InputValidationError(FraudRealtimeError):
    """Raised when input data is invalid."""


class TTLStateStore:
    """In-memory TTL state for realtime decisions."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.profiles: Dict[str, EntityRealtimeProfile] = {}
        self.events_by_entity: DefaultDict[str, Deque[FraudRealtimeEvent]] = defaultdict(deque)
        self.last_decision_by_key: Dict[str, datetime] = {}
        self.dedupe_seen: Dict[str, datetime] = {}

    def evict(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.ttl_seconds)
        for entity, events in list(self.events_by_entity.items()):
            while events and events[0].timestamp < cutoff:
                events.popleft()
            if not events and entity in self.profiles:
                self.profiles.pop(entity, None)
        for key, seen_at in list(self.dedupe_seen.items()):
            if seen_at < cutoff:
                self.dedupe_seen.pop(key, None)
        for key, seen_at in list(self.last_decision_by_key.items()):
            if seen_at < cutoff:
                self.last_decision_by_key.pop(key, None)


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


class FraudRealtimeEventParser:
    @staticmethod
    def parse_many(rows: Iterable[Dict[str, Any]], policy: RealtimeFraudPolicy) -> List[FraudRealtimeEvent]:
        events: List[FraudRealtimeEvent] = []
        errors: List[str] = []
        for index, row in enumerate(rows, start=1):
            try:
                events.append(FraudRealtimeEventParser.parse(row, policy))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"linha={index}: {exc}")
        if errors:
            preview = "\n".join(errors[:30])
            extra = "" if len(errors) <= 30 else f"\n... e mais {len(errors) - 30} erro(s)."
            raise InputValidationError(f"Falha ao validar eventos realtime antifraude:\n{preview}{extra}")
        return sorted(events, key=lambda item: item.timestamp)

    @staticmethod
    def parse(row: Dict[str, Any], policy: RealtimeFraudPolicy) -> FraudRealtimeEvent:
        event_id = required_str(row, "event_id")
        entity_id = required_str(row, "entity_id")
        timestamp = parse_datetime(required_str(row, "timestamp"))
        raw_amount = optional_decimal(row, "amount", Decimal("0"))
        direction = parse_direction(optional_str(row, "direction"), raw_amount)
        status = parse_status(optional_str(row, "status"))
        success = optional_bool(row.get("success"), status not in {EventStatus.DECLINED, EventStatus.FAILED, EventStatus.CANCELLED})
        device_id = optional_str(row, "device_id")
        counterparty = optional_str(row, "counterparty")
        merchant_id = optional_str(row, "merchant_id")
        reference_id = optional_str(row, "reference_id")
        entity_hash = hash_identifier(entity_id) if policy.hash_entity_ids else entity_id
        return FraudRealtimeEvent(
            event_id=event_id,
            entity_id=entity_id,
            entity_id_hash=entity_hash,
            timestamp=timestamp,
            event_type=required_str(row, "event_type").lower(),
            amount=abs(raw_amount),
            currency=(optional_str(row, "currency") or policy.currency).upper(),
            direction=direction,
            channel=normalize_lower(optional_str(row, "channel")),
            ip_address=optional_str(row, "ip_address"),
            device_id_hash=hash_identifier(device_id) if device_id else None,
            country=normalize_upper(optional_str(row, "country")),
            city=normalize_title(optional_str(row, "city")),
            counterparty_hash=hash_identifier(counterparty) if counterparty else None,
            merchant_id_hash=hash_identifier(merchant_id) if merchant_id else None,
            reference_id_hash=hash_identifier(reference_id) if reference_id else None,
            status=status,
            success=success,
            raw=row,
        )


class FraudRealtimeEngine:
    def __init__(self, policy: RealtimeFraudPolicy) -> None:
        self.policy = policy
        self.state = TTLStateStore(ttl_seconds=policy.profile_ttl_seconds)
        self.logger = logging.getLogger(f"{APP_NAME}.{self.__class__.__name__}")

    def decide(self, event: FraudRealtimeEvent) -> FraudDecisionResult:
        started = time.perf_counter()
        self.state.evict(event.timestamp)
        profile = self._get_or_create_profile(event)
        features = self._features(event, profile)
        score, signals, reasons = self._score(event, features)
        risk_level = self._risk_level(score)
        cooldown_active = self._cooldown_active(event, risk_level)
        decision = self._decision(risk_level, cooldown_active)
        latency_ms = Decimal(str((time.perf_counter() - started) * 1000))
        result = FraudDecisionResult(
            decision_id=self._decision_id(event),
            event_id=event.event_id,
            entity_id_hash=event.entity_id_hash,
            timestamp=event.timestamp.isoformat(),
            decision=decision.value,
            risk_level=risk_level.value,
            risk_score=score,
            latency_ms=latency_ms,
            signals=[signal.value for signal in unique_signals(signals)] or [FraudSignal.BASELINE_NORMAL.value],
            reasons=unique_ordered(reasons) or ["baseline_normal"],
            recommended_actions=self._actions(decision, risk_level, signals),
            features=features,
            cooldown_active=cooldown_active,
            payload=self._payload(event, score, risk_level, decision, signals, features),
        )
        self._update_state(event, result)
        return result

    def replay(self, events: Sequence[FraudRealtimeEvent]) -> Tuple[ReplaySummary, List[FraudDecisionResult]]:
        if not events:
            raise InputValidationError("Nenhum evento válido para replay")
        results = [self.decide(event) for event in sorted(events, key=lambda item: item.timestamp)]
        return self._summary(results), results

    def _get_or_create_profile(self, event: FraudRealtimeEvent) -> EntityRealtimeProfile:
        profile = self.state.profiles.get(event.entity_id_hash)
        if profile is None:
            profile = EntityRealtimeProfile(
                entity_id_hash=event.entity_id_hash,
                first_seen_at=event.timestamp.isoformat(),
                last_seen_at=event.timestamp.isoformat(),
            )
            self.state.profiles[event.entity_id_hash] = profile
        return profile

    def _features(self, event: FraudRealtimeEvent, profile: EntityRealtimeProfile) -> RealtimeFeatures:
        recent = self._recent_events(event.entity_id_hash, event.timestamp, self.policy.velocity_window_seconds)
        velocity_count = len(recent)
        velocity_amount = sum_decimal(item.amount for item in recent) + event.amount
        duplicate = self._duplicate_candidate(event, recent)
        median_amount = profile.median_amount
        amount_ratio = event.amount / max_decimal(median_amount, Decimal("1"))
        counterparty_burst = sum(1 for item in recent if event.counterparty_hash and item.counterparty_hash == event.counterparty_hash)
        merchant_burst = sum(1 for item in recent if event.merchant_id_hash and item.merchant_id_hash == event.merchant_id_hash)
        return RealtimeFeatures(
            velocity_count=velocity_count + 1,
            velocity_amount=velocity_amount,
            duplicate_candidate=duplicate,
            amount_ratio_to_median=amount_ratio,
            is_new_device=bool(event.device_id_hash and event.device_id_hash not in profile.known_devices and profile.event_count > 0),
            is_new_ip=bool(event.ip_address and event.ip_address not in profile.known_ips and profile.event_count > 0),
            is_new_country=bool(event.country and event.country not in profile.known_countries and profile.event_count > 0),
            is_new_channel=bool(event.channel and event.channel not in profile.known_channels and profile.event_count > 0),
            private_or_invalid_ip=bool(event.ip_address and (is_private_ip(event.ip_address) or not is_valid_ip(event.ip_address))),
            failed_or_declined=(not event.success) or event.status in {EventStatus.DECLINED, EventStatus.FAILED},
            chargeback_or_refund=event.status in {EventStatus.CHARGEBACK, EventStatus.REFUND},
            off_hours=self._off_hours(event.timestamp.hour),
            round_amount=self._round_amount(event.amount),
            counterparty_burst_count=counterparty_burst,
            merchant_burst_count=merchant_burst,
            profile_event_count=profile.event_count,
        )

    def _score(self, event: FraudRealtimeEvent, features: RealtimeFeatures) -> Tuple[Decimal, List[FraudSignal], List[str]]:
        score = Decimal("0")
        signals: List[FraudSignal] = []
        reasons: List[str] = []

        def add(condition: bool, points: Decimal, signal: FraudSignal, reason: str) -> None:
            nonlocal score
            if condition:
                score += points
                signals.append(signal)
                reasons.append(reason)

        baseline_ready = features.profile_event_count >= self.policy.min_profile_events_for_baseline
        add(baseline_ready and features.amount_ratio_to_median >= self.policy.high_amount_multiplier and event.amount > 0, self.policy.score_high_amount, FraudSignal.HIGH_AMOUNT, f"amount_ratio_to_median={decimal_str(features.amount_ratio_to_median)}")
        add(features.velocity_count >= self.policy.velocity_count_threshold, self.policy.score_high_velocity, FraudSignal.HIGH_VELOCITY, f"velocity_count={features.velocity_count}")
        add(features.velocity_amount >= self.policy.amount_velocity_threshold and features.velocity_count >= 3, self.policy.score_high_velocity, FraudSignal.HIGH_VELOCITY, f"velocity_amount={money_str(features.velocity_amount)}")
        add(features.duplicate_candidate, self.policy.score_duplicate, FraudSignal.DUPLICATE_CANDIDATE, "duplicate_candidate_same_amount_reference_counterparty_or_merchant")
        add(features.is_new_device, self.policy.score_new_device, FraudSignal.NEW_DEVICE, "new_device_for_entity")
        add(features.is_new_ip, self.policy.score_new_ip, FraudSignal.NEW_IP, "new_ip_for_entity")
        add(features.is_new_country, self.policy.score_new_country, FraudSignal.NEW_COUNTRY, "new_country_for_entity")
        add(features.is_new_channel, self.policy.score_new_channel, FraudSignal.NEW_CHANNEL, "new_channel_for_entity")
        add(features.private_or_invalid_ip, self.policy.score_private_or_invalid_ip, FraudSignal.PRIVATE_OR_INVALID_IP, "private_or_invalid_ip")
        add(features.failed_or_declined, self.policy.score_failed, FraudSignal.FAILED_OR_DECLINED, "failed_or_declined_event")
        add(features.chargeback_or_refund, self.policy.score_chargeback_refund, FraudSignal.CHARGEBACK_OR_REFUND, "chargeback_or_refund_event")
        add(features.off_hours, self.policy.score_off_hours, FraudSignal.OFF_HOURS, "off_hours_event")
        add(features.round_amount and event.amount > Decimal("0") and (not baseline_ready or event.amount >= Decimal("1000")), self.policy.score_round_amount, FraudSignal.ROUND_AMOUNT, "round_amount_pattern")
        add(features.counterparty_burst_count >= 3, self.policy.score_counterparty_burst, FraudSignal.COUNTERPARTY_BURST, f"counterparty_burst_count={features.counterparty_burst_count}")
        add(features.merchant_burst_count >= 3, self.policy.score_merchant_burst, FraudSignal.MERCHANT_BURST, f"merchant_burst_count={features.merchant_burst_count}")
        return clamp_decimal(score, Decimal("0"), Decimal("100")), signals, reasons

    def _risk_level(self, score: Decimal) -> RiskLevel:
        if score >= self.policy.critical_threshold:
            return RiskLevel.CRITICAL
        if score >= self.policy.high_threshold:
            return RiskLevel.HIGH
        if score >= self.policy.medium_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _decision(level: RiskLevel, cooldown_active: bool) -> FraudDecision:
        if cooldown_active and level in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
            return FraudDecision.MONITOR
        if level == RiskLevel.CRITICAL:
            return FraudDecision.DECLINE_CANDIDATE
        if level == RiskLevel.HIGH:
            return FraudDecision.REVIEW
        if level == RiskLevel.MEDIUM:
            return FraudDecision.CHALLENGE
        if cooldown_active:
            return FraudDecision.MONITOR
        return FraudDecision.APPROVE

    @staticmethod
    def _actions(decision: FraudDecision, level: RiskLevel, signals: Sequence[FraudSignal]) -> List[str]:
        signal_set = set(signals)
        if decision == FraudDecision.DECLINE_CANDIDATE:
            return ["hold_or_decline_candidate", "manual_fraud_review", "step_up_authentication", "create_case"]
        if FraudSignal.DUPLICATE_CANDIDATE in signal_set:
            return ["review_duplicate", "reconcile_reference", "manual_review"]
        if decision == FraudDecision.REVIEW:
            return ["manual_review", "step_up_authentication", "enhanced_monitoring"]
        if decision == FraudDecision.CHALLENGE:
            return ["challenge_mfa", "monitor_next_events"]
        if decision == FraudDecision.MONITOR:
            return ["record_signal", "monitor_velocity"]
        return ["approve"]

    def _cooldown_active(self, event: FraudRealtimeEvent, level: RiskLevel) -> bool:
        key = self._cooldown_key(event, level)
        last = self.state.last_decision_by_key.get(key)
        if not last:
            return False
        return event.timestamp - last <= timedelta(seconds=self.policy.cooldown_seconds)

    def _update_state(self, event: FraudRealtimeEvent, result: FraudDecisionResult) -> None:
        profile = self._get_or_create_profile(event)
        profile.last_seen_at = event.timestamp.isoformat()
        profile.event_count += 1
        profile.amount_sum += event.amount
        if event.amount > 0:
            profile.amount_values.append(event.amount)
        if event.device_id_hash:
            profile.known_devices.add(event.device_id_hash)
        if event.ip_address:
            profile.known_ips.add(event.ip_address)
        if event.country:
            profile.known_countries.add(event.country)
        if event.channel:
            profile.known_channels.add(event.channel)
        if event.counterparty_hash:
            profile.known_counterparties.add(event.counterparty_hash)
        if event.merchant_id_hash:
            profile.known_merchants.add(event.merchant_id_hash)
        profile.active_hours[event.timestamp.hour] += 1
        profile.status_counts[event.status.value] += 1
        self.state.events_by_entity[event.entity_id_hash].append(event)
        self.state.dedupe_seen[self._dedupe_key(event)] = event.timestamp
        if result.risk_level in {RiskLevel.MEDIUM.value, RiskLevel.HIGH.value, RiskLevel.CRITICAL.value}:
            self.state.last_decision_by_key[self._cooldown_key(event, RiskLevel(result.risk_level))] = event.timestamp

    def _recent_events(self, entity_hash: str, now: datetime, window_seconds: int) -> List[FraudRealtimeEvent]:
        events = self.state.events_by_entity[entity_hash]
        cutoff = now - timedelta(seconds=window_seconds)
        while events and events[0].timestamp < cutoff:
            events.popleft()
        return list(events)

    def _duplicate_candidate(self, event: FraudRealtimeEvent, recent: Sequence[FraudRealtimeEvent]) -> bool:
        cutoff = event.timestamp - timedelta(seconds=self.policy.duplicate_window_seconds)
        for other in reversed(recent):
            if other.timestamp < cutoff:
                break
            same_amount = abs(other.amount - event.amount) <= self.policy.duplicate_amount_tolerance
            same_reference = event.reference_id_hash and event.reference_id_hash == other.reference_id_hash
            same_counterparty = event.counterparty_hash and event.counterparty_hash == other.counterparty_hash
            same_merchant = event.merchant_id_hash and event.merchant_id_hash == other.merchant_id_hash
            if same_amount and (same_reference or same_counterparty or same_merchant):
                return True
        return False

    def _off_hours(self, hour: int) -> bool:
        if self.policy.off_hours_start > self.policy.off_hours_end:
            return hour >= self.policy.off_hours_start or hour < self.policy.off_hours_end
        return self.policy.off_hours_start <= hour < self.policy.off_hours_end

    @staticmethod
    def _round_amount(amount: Decimal) -> bool:
        if amount <= 0:
            return False
        return amount % Decimal("100") == 0 or amount % Decimal("1000") == 0

    def _dedupe_key(self, event: FraudRealtimeEvent) -> str:
        raw = "|".join([
            event.entity_id_hash,
            event.event_type,
            money_str(event.amount),
            event.reference_id_hash or "",
            event.counterparty_hash or "",
            event.merchant_id_hash or "",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _cooldown_key(event: FraudRealtimeEvent, level: RiskLevel) -> str:
        raw = "|".join([event.entity_id_hash, event.event_type, level.value, event.channel or ""])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _decision_id(self, event: FraudRealtimeEvent) -> str:
        raw = f"{event.event_id}|{event.timestamp.isoformat()}|{uuid.uuid4()}"
        return "frd_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def _payload(self, event: FraudRealtimeEvent, score: Decimal, level: RiskLevel, decision: FraudDecision, signals: Sequence[FraudSignal], features: RealtimeFeatures) -> Dict[str, Any]:
        return {
            "engine": APP_NAME,
            "engine_version": ENGINE_VERSION,
            "event_id": event.event_id,
            "entity_id_hash": event.entity_id_hash,
            "timestamp": event.timestamp.isoformat(),
            "event_type": event.event_type,
            "amount": money_str(event.amount),
            "currency": event.currency,
            "channel": event.channel,
            "country": event.country,
            "risk_score": decimal_str(score),
            "risk_level": level.value,
            "decision": decision.value,
            "signals": [signal.value for signal in unique_signals(signals)],
            "features": features.to_dict(),
        }

    @staticmethod
    def _summary(results: Sequence[FraudDecisionResult]) -> ReplaySummary:
        decisions = Counter(item.decision for item in results)
        levels = Counter(item.risk_level for item in results)
        signal_counter: Counter[str] = Counter()
        for item in results:
            signal_counter.update(item.signals)
        return ReplaySummary(
            total_events=len(results),
            approve=decisions.get(FraudDecision.APPROVE.value, 0),
            monitor=decisions.get(FraudDecision.MONITOR.value, 0),
            challenge=decisions.get(FraudDecision.CHALLENGE.value, 0),
            review=decisions.get(FraudDecision.REVIEW.value, 0),
            decline_candidate=decisions.get(FraudDecision.DECLINE_CANDIDATE.value, 0),
            low=levels.get(RiskLevel.LOW.value, 0),
            medium=levels.get(RiskLevel.MEDIUM.value, 0),
            high=levels.get(RiskLevel.HIGH.value, 0),
            critical=levels.get(RiskLevel.CRITICAL.value, 0),
            avg_risk_score=mean_decimal([item.risk_score for item in results]),
            avg_latency_ms=mean_decimal([item.latency_ms for item in results]),
            top_signals=[{"signal": key, "count": value} for key, value in signal_counter.most_common(20)],
        )


class ResultWriter:
    @staticmethod
    def write(summary: ReplaySummary, results: Sequence[FraudDecisionResult], output: Path, output_format: OutputFormat) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output_format == OutputFormat.JSON:
            payload = {
                "engine_version": ENGINE_VERSION,
                "generated_at": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
                "summary": summary.to_dict(),
                "decisions": [item.to_dict() for item in results],
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return output
        if output_format == OutputFormat.CSV:
            fieldnames = [
                "decision_id", "event_id", "entity_id_hash", "timestamp", "decision", "risk_level", "risk_score",
                "latency_ms", "signals", "reasons", "recommended_actions", "cooldown_active",
            ]
            with output.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                for item in results:
                    payload = item.to_dict()
                    writer.writerow(
                        {
                            "decision_id": payload["decision_id"],
                            "event_id": payload["event_id"],
                            "entity_id_hash": payload["entity_id_hash"],
                            "timestamp": payload["timestamp"],
                            "decision": payload["decision"],
                            "risk_level": payload["risk_level"],
                            "risk_score": payload["risk_score"],
                            "latency_ms": payload["latency_ms"],
                            "signals": "|".join(payload["signals"]),
                            "reasons": "|".join(payload["reasons"]),
                            "recommended_actions": "|".join(payload["recommended_actions"]),
                            "cooldown_active": payload["cooldown_active"],
                        }
                    )
            return output
        raise FraudRealtimeError(f"Formato não suportado: {output_format}")


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


def optional_decimal(row: Mapping[str, Any], key: str, default: Decimal) -> Decimal:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        return default
    return to_decimal(value)


def optional_bool(value: Any, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "sim", "s", "ok", "success", "sucesso"}:
        return True
    if text in {"false", "0", "no", "n", "nao", "não", "fail", "failed", "erro", "error"}:
        return False
    raise ValueError("campo booleano inválido")


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


def parse_direction(value: Optional[str], amount: Decimal) -> Direction:
    if value:
        text = value.strip().lower()
        if text in {"credit", "credito", "crédito", "inflow", "entrada", "receita"}:
            return Direction.CREDIT
        if text in {"debit", "debito", "débito", "outflow", "saida", "saída", "despesa"}:
            return Direction.DEBIT
    return Direction.CREDIT if amount >= 0 else Direction.DEBIT


def parse_status(value: Optional[str]) -> EventStatus:
    if not value:
        return EventStatus.UNKNOWN
    text = value.strip().lower()
    aliases = {
        "approved": EventStatus.APPROVED,
        "aprovado": EventStatus.APPROVED,
        "declined": EventStatus.DECLINED,
        "recusado": EventStatus.DECLINED,
        "failed": EventStatus.FAILED,
        "fail": EventStatus.FAILED,
        "erro": EventStatus.FAILED,
        "pending": EventStatus.PENDING,
        "pendente": EventStatus.PENDING,
        "cancelled": EventStatus.CANCELLED,
        "canceled": EventStatus.CANCELLED,
        "cancelado": EventStatus.CANCELLED,
        "reversed": EventStatus.REVERSED,
        "estornado": EventStatus.REVERSED,
        "chargeback": EventStatus.CHARGEBACK,
        "refund": EventStatus.REFUND,
        "refunded": EventStatus.REFUND,
    }
    return aliases.get(text, EventStatus.UNKNOWN)


def normalize_lower(value: Optional[str]) -> Optional[str]:
    return value.lower() if value else None


def normalize_upper(value: Optional[str]) -> Optional[str]:
    return value.upper() if value else None


def normalize_title(value: Optional[str]) -> Optional[str]:
    return value.title() if value else None


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_private_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return False


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def mean_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum_decimal(values) / Decimal(len(values))


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def percentile_decimal(values: Sequence[Decimal], percent_value: int) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = Decimal(len(ordered) - 1) * Decimal(percent_value) / Decimal("100")
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - Decimal(lower)
    return ordered[lower] * (Decimal("1") - weight) + ordered[upper] * weight


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


def unique_signals(values: Sequence[FraudSignal]) -> List[FraudSignal]:
    seen = set()
    result: List[FraudSignal] = []
    for value in values:
        if value.value not in seen:
            seen.add(value.value)
            result.append(value)
    return result


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Enterprise realtime fraud decision engine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay = subparsers.add_parser("replay", help="Executa replay batch de eventos realtime antifraude.")
    replay.add_argument("--input", required=True, type=Path)
    replay.add_argument("--output", required=True, type=Path)
    replay.add_argument("--format", default=OutputFormat.JSON.value, choices=[item.value for item in OutputFormat])
    replay.add_argument("--currency", default=DEFAULT_CURRENCY)
    replay.add_argument("--medium-threshold", default="35")
    replay.add_argument("--high-threshold", default="65")
    replay.add_argument("--critical-threshold", default="85")
    replay.add_argument("--velocity-window-seconds", default=600, type=int)
    replay.add_argument("--velocity-count-threshold", default=8, type=int)
    replay.add_argument("--amount-velocity-threshold", default="5000")
    replay.add_argument("--duplicate-window-seconds", default=600, type=int)
    replay.add_argument("--profile-ttl-seconds", default=86400, type=int)
    replay.add_argument("--cooldown-seconds", default=900, type=int)
    replay.add_argument("--high-amount-multiplier", default="3")
    replay.add_argument("--no-hash-entity-ids", action="store_true")

    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    logger = logging.getLogger(APP_NAME)

    try:
        if args.command == "replay":
            policy = RealtimeFraudPolicy(
                currency=args.currency.upper(),
                medium_threshold=to_decimal(args.medium_threshold),
                high_threshold=to_decimal(args.high_threshold),
                critical_threshold=to_decimal(args.critical_threshold),
                velocity_window_seconds=args.velocity_window_seconds,
                velocity_count_threshold=args.velocity_count_threshold,
                amount_velocity_threshold=to_decimal(args.amount_velocity_threshold),
                duplicate_window_seconds=args.duplicate_window_seconds,
                profile_ttl_seconds=args.profile_ttl_seconds,
                cooldown_seconds=args.cooldown_seconds,
                high_amount_multiplier=to_decimal(args.high_amount_multiplier),
                hash_entity_ids=not args.no_hash_entity_ids,
            )
            logger.info("Carregando eventos realtime de %s", args.input)
            rows = FileLoader.load(args.input)
            events = FraudRealtimeEventParser.parse_many(rows, policy)
            logger.info("Executando replay de %s evento(s)", len(events))
            engine = FraudRealtimeEngine(policy)
            summary, results = engine.replay(events)
            ResultWriter.write(summary, results, args.output, OutputFormat(args.format))
            logger.info("Decisões salvas em %s", args.output)
            print(args.output)
            return 0

        raise FraudRealtimeError(f"Comando não suportado: {args.command}")

    except FraudRealtimeError as exc:
        logger.error("Erro no fraud realtime: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro inesperado: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(run())
