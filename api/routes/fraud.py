#!/usr/bin/env python3
"""
api/routes/fraud.py

Enterprise-grade Fraud API Router.

Objetivo:
- Expor endpoints HTTP para análise antifraude em tempo real e batch.
- Calcular score 0-100, nível de risco, decisão recomendada e explicabilidade.
- Detectar sinais comuns: valor alto, velocity, duplicidade, novo dispositivo/IP/país/canal,
  IP privado/inválido, status arriscado, chargeback/refund, horário incomum e bursts.
- Aplicar validação Pydantic, autenticação por scopes, request-id, auditoria leve e respostas padronizadas.

Endpoints:
    GET  /fraud/health
    POST /fraud/score
    POST /fraud/batch-score
    POST /fraud/rules/evaluate
    GET  /fraud/audit/{entity_hash}
    GET  /fraud/stats/summary

Integração:
    from fastapi import FastAPI
    from api.routes.fraud import router as fraud_router

    app.include_router(fraud_router, prefix="/v1")

Notas:
- Este router mantém estado em memória para velocity e perfis recentes.
- Em produção, substitua FraudStateStore por Redis/Kafka/Feature Store.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, getcontext
from enum import Enum
from typing import Any, Deque, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

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

router = APIRouter(prefix="/fraud", tags=["fraud"])


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


class RuleOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    CONTAINS = "contains"


class FraudEventRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"frd_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    event_type: str = "transaction"
    amount: float = 0.0
    currency: str = DEFAULT_CURRENCY
    direction: str = "debit"
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
    metadata: Dict[str, Any] = Field(default_factory=dict)


class FraudBatchRequest(BaseModel):
    events: List[FraudEventRequest] = Field(default_factory=list)
    update_state: bool = True


class FraudRule(BaseModel):
    rule_id: str = Field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:12]}")
    field: str
    operator: RuleOperator
    value: Any
    points: float = Field(default=10.0, ge=0, le=100)
    signal: str = "custom_rule"
    reason: Optional[str] = None
    enabled: bool = True


class RuleEvaluateRequest(BaseModel):
    event: FraudEventRequest
    rules: List[FraudRule] = Field(default_factory=list)


class FraudScoreResponse(BaseModel):
    request_id: str
    decision_id: str
    event_id: str
    entity_hash: str
    status: str
    decision: FraudDecision
    risk_level: RiskLevel
    risk_score: float
    signals: List[str]
    reasons: List[str]
    recommended_actions: List[str]
    features: Dict[str, Any]
    latency_ms: float
    payload: Dict[str, Any]


class FraudBatchResponse(BaseModel):
    request_id: str
    status: str
    total_events: int
    latency_ms: float
    summary: Dict[str, Any]
    results: List[FraudScoreResponse]


class FraudAuditResponse(BaseModel):
    request_id: str
    entity_hash: str
    events: List[Dict[str, Any]]


@dataclass(frozen=True)
class FraudEvent:
    event_id: str
    entity_id: str
    entity_hash: str
    timestamp: datetime
    event_type: str
    amount: Decimal
    currency: str
    direction: Direction
    channel: Optional[str]
    ip_address: Optional[str]
    device_hash: Optional[str]
    country: Optional[str]
    city: Optional[str]
    counterparty_hash: Optional[str]
    merchant_hash: Optional[str]
    reference_hash: Optional[str]
    status: EventStatus
    success: bool
    metadata: Dict[str, Any]


@dataclass
class EntityProfile:
    entity_hash: str
    first_seen_at: str
    last_seen_at: str
    event_count: int = 0
    amounts: Deque[Decimal] = field(default_factory=lambda: deque(maxlen=500))
    known_devices: set[str] = field(default_factory=set)
    known_ips: set[str] = field(default_factory=set)
    known_countries: set[str] = field(default_factory=set)
    known_channels: set[str] = field(default_factory=set)
    known_counterparties: set[str] = field(default_factory=set)
    known_merchants: set[str] = field(default_factory=set)

    @property
    def median_amount(self) -> Decimal:
        return median_decimal(list(self.amounts))


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


class FraudStateStore:
    def __init__(self, ttl_seconds: int = 86_400) -> None:
        self.ttl_seconds = ttl_seconds
        self.profiles: Dict[str, EntityProfile] = {}
        self.events_by_entity: DefaultDict[str, Deque[FraudEvent]] = defaultdict(deque)
        self.audit_by_entity: DefaultDict[str, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=500))

    def evict(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.ttl_seconds)
        for entity_hash, events in list(self.events_by_entity.items()):
            while events and events[0].timestamp < cutoff:
                events.popleft()
            if not events and entity_hash in self.profiles:
                self.profiles.pop(entity_hash, None)

    def profile(self, event: FraudEvent) -> EntityProfile:
        profile = self.profiles.get(event.entity_hash)
        if profile is None:
            profile = EntityProfile(event.entity_hash, event.timestamp.isoformat(), event.timestamp.isoformat())
            self.profiles[event.entity_hash] = profile
        return profile

    def update(self, event: FraudEvent, result: Mapping[str, Any]) -> None:
        profile = self.profile(event)
        profile.last_seen_at = event.timestamp.isoformat()
        profile.event_count += 1
        if event.amount > 0:
            profile.amounts.append(event.amount)
        if event.device_hash:
            profile.known_devices.add(event.device_hash)
        if event.ip_address:
            profile.known_ips.add(event.ip_address)
        if event.country:
            profile.known_countries.add(event.country)
        if event.channel:
            profile.known_channels.add(event.channel)
        if event.counterparty_hash:
            profile.known_counterparties.add(event.counterparty_hash)
        if event.merchant_hash:
            profile.known_merchants.add(event.merchant_hash)
        self.events_by_entity[event.entity_hash].append(event)
        self.audit_by_entity[event.entity_hash].append(dict(result))


state_store = FraudStateStore()


@router.get("/health")
async def fraud_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "router": "fraud",
        "version": ROUTER_VERSION,
        "timestamp": utc_now_iso(),
        "state": {
            "profile_count": len(state_store.profiles),
            "tracked_entities": len(state_store.events_by_entity),
        },
    }


@router.post("/score", response_model=FraudScoreResponse, dependencies=[Depends(require_scopes("fraud:score"))])
async def score_fraud(payload: FraudEventRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> FraudScoreResponse:
    ctx = build_context(request, user)
    event = parse_event(payload)
    state_store.evict(event.timestamp)
    result = score_event(event, update_state=True, ctx=ctx)
    return result


@router.post("/batch-score", response_model=FraudBatchResponse, dependencies=[Depends(require_scopes("fraud:score"))])
async def batch_score_fraud(payload: FraudBatchRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> FraudBatchResponse:
    ctx = build_context(request, user)
    started = time.perf_counter()
    results: List[FraudScoreResponse] = []
    for raw in sorted(payload.events, key=lambda item: item.timestamp):
        event = parse_event(raw)
        state_store.evict(event.timestamp)
        results.append(score_event(event, update_state=payload.update_state, ctx=ctx))
    return FraudBatchResponse(
        request_id=ctx.request_id,
        status="success",
        total_events=len(results),
        latency_ms=elapsed_ms(started),
        summary=batch_summary(results),
        results=results,
    )


@router.post("/rules/evaluate", response_model=FraudScoreResponse, dependencies=[Depends(require_scopes("fraud:score"))])
async def evaluate_fraud_rules(payload: RuleEvaluateRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> FraudScoreResponse:
    ctx = build_context(request, user)
    event = parse_event(payload.event)
    base = score_event(event, update_state=False, ctx=ctx)
    custom_score, custom_signals, custom_reasons = evaluate_rules(event, payload.rules)
    total_score = clamp_decimal(Decimal(str(base.risk_score)) + custom_score, Decimal("0"), Decimal("100"))
    level = risk_level(total_score)
    decision = decision_for_level(level)
    base.risk_score = to_float(total_score)
    base.risk_level = level
    base.decision = decision
    base.signals = unique_ordered(base.signals + custom_signals)
    base.reasons = unique_ordered(base.reasons + custom_reasons)
    base.recommended_actions = actions_for_decision(decision, level, base.signals)
    base.payload["custom_rule_score"] = decimal_str(custom_score)
    return base


@router.get("/audit/{entity_hash}", response_model=FraudAuditResponse, dependencies=[Depends(require_scopes("fraud:read"))])
async def fraud_audit(entity_hash: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> FraudAuditResponse:
    ctx = build_context(request, user)
    return FraudAuditResponse(request_id=ctx.request_id, entity_hash=entity_hash, events=list(state_store.audit_by_entity.get(entity_hash, [])))


@router.get("/stats/summary", dependencies=[Depends(require_scopes("fraud:read"))])
async def fraud_stats() -> Dict[str, Any]:
    audit_events = [item for events in state_store.audit_by_entity.values() for item in events]
    decisions = Counter(item.get("decision") for item in audit_events)
    levels = Counter(item.get("risk_level") for item in audit_events)
    signals: Counter[str] = Counter()
    for item in audit_events:
        signals.update(item.get("signals", []))
    return {
        "status": "success",
        "version": ROUTER_VERSION,
        "profile_count": len(state_store.profiles),
        "tracked_entities": len(state_store.events_by_entity),
        "audit_event_count": len(audit_events),
        "decisions": dict(decisions),
        "risk_levels": dict(levels),
        "top_signals": [{"signal": key, "count": value} for key, value in signals.most_common(20)],
    }


def score_event(event: FraudEvent, update_state: bool, ctx: ExecutionContext) -> FraudScoreResponse:
    started = time.perf_counter()
    profile = state_store.profile(event)
    recent = recent_events(event.entity_hash, event.timestamp, window_seconds=600)
    features = build_features(event, profile, recent)
    score, signals, reasons = calculate_score(event, features)
    level = risk_level(score)
    decision = decision_for_level(level)
    result = FraudScoreResponse(
        request_id=ctx.request_id,
        decision_id=f"frd_{uuid.uuid4().hex[:20]}",
        event_id=event.event_id,
        entity_hash=event.entity_hash,
        status="success",
        decision=decision,
        risk_level=level,
        risk_score=to_float(score),
        signals=signals or ["baseline_normal"],
        reasons=reasons or ["no_material_fraud_signal_detected"],
        recommended_actions=actions_for_decision(decision, level, signals),
        features=features,
        latency_ms=elapsed_ms(started),
        payload={
            "event_id": event.event_id,
            "entity_hash": event.entity_hash,
            "event_type": event.event_type,
            "amount": money_str(event.amount),
            "currency": event.currency,
            "timestamp": event.timestamp.isoformat(),
        },
    )
    if update_state:
        state_store.update(event, result.dict())
    return result


def build_features(event: FraudEvent, profile: EntityProfile, recent: Sequence[FraudEvent]) -> Dict[str, Any]:
    velocity_count = len(recent) + 1
    velocity_amount = sum_decimal(item.amount for item in recent) + event.amount
    median_amount = profile.median_amount
    amount_ratio = event.amount / max_decimal(median_amount, Decimal("1"))
    duplicate = duplicate_candidate(event, recent)
    counterparty_burst = sum(1 for item in recent if event.counterparty_hash and item.counterparty_hash == event.counterparty_hash)
    merchant_burst = sum(1 for item in recent if event.merchant_hash and item.merchant_hash == event.merchant_hash)
    return {
        "profile_event_count": profile.event_count,
        "velocity_count_10m": velocity_count,
        "velocity_amount_10m": money_str(velocity_amount),
        "amount_ratio_to_median": decimal_str(amount_ratio),
        "duplicate_candidate": duplicate,
        "new_device": bool(event.device_hash and event.device_hash not in profile.known_devices and profile.event_count > 0),
        "new_ip": bool(event.ip_address and event.ip_address not in profile.known_ips and profile.event_count > 0),
        "new_country": bool(event.country and event.country not in profile.known_countries and profile.event_count > 0),
        "new_channel": bool(event.channel and event.channel not in profile.known_channels and profile.event_count > 0),
        "private_or_invalid_ip": bool(event.ip_address and is_private_or_invalid_ip(event.ip_address)),
        "failed_or_declined": (not event.success) or event.status in {EventStatus.DECLINED, EventStatus.FAILED},
        "chargeback_or_refund": event.status in {EventStatus.CHARGEBACK, EventStatus.REFUND},
        "off_hours": is_off_hours(event.timestamp.hour),
        "round_amount": is_round_amount(event.amount),
        "counterparty_burst_count": counterparty_burst,
        "merchant_burst_count": merchant_burst,
    }


def calculate_score(event: FraudEvent, features: Mapping[str, Any]) -> Tuple[Decimal, List[str], List[str]]:
    score = Decimal("0")
    signals: List[str] = []
    reasons: List[str] = []

    def add(condition: bool, points: Decimal, signal: str, reason: str) -> None:
        nonlocal score
        if condition:
            score += points
            signals.append(signal)
            reasons.append(reason)

    amount_ratio = Decimal(str(features.get("amount_ratio_to_median", "0")))
    profile_count = int(features.get("profile_event_count", 0))
    velocity_count = int(features.get("velocity_count_10m", 0))
    velocity_amount = Decimal(str(features.get("velocity_amount_10m", "0")).replace(",", "."))

    add(profile_count >= 5 and amount_ratio >= Decimal("3") and event.amount > 0, Decimal("24"), "high_amount", f"amount_ratio_to_median={decimal_str(amount_ratio)}")
    add(event.amount >= Decimal("5000"), Decimal("18"), "high_amount_absolute", "amount_above_absolute_threshold")
    add(velocity_count >= 8, Decimal("22"), "high_velocity", f"velocity_count_10m={velocity_count}")
    add(velocity_amount >= Decimal("5000") and velocity_count >= 3, Decimal("18"), "amount_velocity", f"velocity_amount_10m={money_str(velocity_amount)}")
    add(bool(features.get("duplicate_candidate")), Decimal("35"), "duplicate_candidate", "duplicate_same_amount_reference_counterparty_or_merchant")
    add(bool(features.get("new_device")), Decimal("16"), "new_device", "new_device_for_entity")
    add(bool(features.get("new_ip")), Decimal("10"), "new_ip", "new_ip_for_entity")
    add(bool(features.get("new_country")), Decimal("22"), "new_country", "new_country_for_entity")
    add(bool(features.get("new_channel")), Decimal("8"), "new_channel", "new_channel_for_entity")
    add(bool(features.get("private_or_invalid_ip")), Decimal("6"), "private_or_invalid_ip", "private_or_invalid_ip")
    add(bool(features.get("failed_or_declined")), Decimal("12"), "failed_or_declined", "failed_or_declined_event")
    add(bool(features.get("chargeback_or_refund")), Decimal("30"), "chargeback_or_refund", "chargeback_or_refund_event")
    add(bool(features.get("off_hours")), Decimal("8"), "off_hours", "event_outside_business_hours")
    add(bool(features.get("round_amount")) and event.amount >= Decimal("1000"), Decimal("5"), "round_amount", "large_round_amount")
    add(int(features.get("counterparty_burst_count", 0)) >= 3, Decimal("15"), "counterparty_burst", "counterparty_burst_detected")
    add(int(features.get("merchant_burst_count", 0)) >= 3, Decimal("12"), "merchant_burst", "merchant_burst_detected")

    return clamp_decimal(score, Decimal("0"), Decimal("100")), unique_ordered(signals), unique_ordered(reasons)


def evaluate_rules(event: FraudEvent, rules: Sequence[FraudRule]) -> Tuple[Decimal, List[str], List[str]]:
    score = Decimal("0")
    signals: List[str] = []
    reasons: List[str] = []
    event_map = event_to_map(event)
    for rule in rules:
        if not rule.enabled:
            continue
        actual = event_map.get(rule.field)
        if match_rule(actual, rule.operator, rule.value):
            score += Decimal(str(rule.points))
            signals.append(rule.signal)
            reasons.append(rule.reason or f"rule_matched:{rule.rule_id}")
    return clamp_decimal(score, Decimal("0"), Decimal("100")), unique_ordered(signals), unique_ordered(reasons)


def match_rule(actual: Any, operator: RuleOperator, expected: Any) -> bool:
    if operator == RuleOperator.EQ:
        return actual == expected
    if operator == RuleOperator.NE:
        return actual != expected
    if operator in {RuleOperator.GT, RuleOperator.GTE, RuleOperator.LT, RuleOperator.LTE}:
        left = to_decimal(actual)
        right = to_decimal(expected)
        if operator == RuleOperator.GT:
            return left > right
        if operator == RuleOperator.GTE:
            return left >= right
        if operator == RuleOperator.LT:
            return left < right
        return left <= right
    if operator == RuleOperator.IN:
        return actual in expected if isinstance(expected, list) else actual == expected
    if operator == RuleOperator.CONTAINS:
        return str(expected).lower() in str(actual).lower()
    return False


def parse_event(payload: FraudEventRequest) -> FraudEvent:
    entity_hash = hash_identifier(payload.entity_id)
    raw_amount = Decimal(str(payload.amount))
    status_value = parse_status(payload.status)
    success = payload.success if payload.success is not None else status_value not in {EventStatus.DECLINED, EventStatus.FAILED, EventStatus.CANCELLED}
    return FraudEvent(
        event_id=payload.event_id,
        entity_id=payload.entity_id,
        entity_hash=entity_hash,
        timestamp=parse_datetime(payload.timestamp),
        event_type=payload.event_type.lower(),
        amount=abs(raw_amount),
        currency=payload.currency.upper(),
        direction=parse_direction(payload.direction, raw_amount),
        channel=normalize(payload.channel),
        ip_address=payload.ip_address,
        device_hash=hash_identifier(payload.device_id) if payload.device_id else None,
        country=payload.country.upper() if payload.country else None,
        city=payload.city.title() if payload.city else None,
        counterparty_hash=hash_identifier(payload.counterparty) if payload.counterparty else None,
        merchant_hash=hash_identifier(payload.merchant_id) if payload.merchant_id else None,
        reference_hash=hash_identifier(payload.reference_id) if payload.reference_id else None,
        status=status_value,
        success=bool(success),
        metadata=payload.metadata,
    )


def recent_events(entity_hash: str, now: datetime, window_seconds: int) -> List[FraudEvent]:
    events = state_store.events_by_entity[entity_hash]
    cutoff = now - timedelta(seconds=window_seconds)
    while events and events[0].timestamp < cutoff:
        events.popleft()
    return list(events)


def duplicate_candidate(event: FraudEvent, recent: Sequence[FraudEvent]) -> bool:
    cutoff = event.timestamp - timedelta(seconds=600)
    for other in reversed(recent):
        if other.timestamp < cutoff:
            break
        same_amount = abs(other.amount - event.amount) <= Decimal("0.01")
        same_ref = event.reference_hash and event.reference_hash == other.reference_hash
        same_counterparty = event.counterparty_hash and event.counterparty_hash == other.counterparty_hash
        same_merchant = event.merchant_hash and event.merchant_hash == other.merchant_hash
        if same_amount and (same_ref or same_counterparty or same_merchant):
            return True
    return False


def batch_summary(results: Sequence[FraudScoreResponse]) -> Dict[str, Any]:
    decisions = Counter(item.decision.value for item in results)
    levels = Counter(item.risk_level.value for item in results)
    signals: Counter[str] = Counter()
    for item in results:
        signals.update(item.signals)
    avg_score = sum(Decimal(str(item.risk_score)) for item in results) / Decimal(len(results)) if results else Decimal("0")
    return {
        "decisions": dict(decisions),
        "risk_levels": dict(levels),
        "avg_risk_score": decimal_str(avg_score),
        "top_signals": [{"signal": key, "count": value} for key, value in signals.most_common(20)],
    }


def decision_for_level(level: RiskLevel) -> FraudDecision:
    if level == RiskLevel.CRITICAL:
        return FraudDecision.DECLINE_CANDIDATE
    if level == RiskLevel.HIGH:
        return FraudDecision.REVIEW
    if level == RiskLevel.MEDIUM:
        return FraudDecision.CHALLENGE
    return FraudDecision.APPROVE


def actions_for_decision(decision: FraudDecision, level: RiskLevel, signals: Sequence[str]) -> List[str]:
    signal_set = set(signals)
    if decision == FraudDecision.DECLINE_CANDIDATE:
        return ["hold_or_decline_candidate", "manual_fraud_review", "step_up_authentication", "create_case"]
    if "duplicate_candidate" in signal_set:
        return ["review_duplicate", "reconcile_reference", "manual_review"]
    if decision == FraudDecision.REVIEW:
        return ["manual_review", "step_up_authentication", "enhanced_monitoring"]
    if decision == FraudDecision.CHALLENGE:
        return ["challenge_mfa", "monitor_next_events"]
    if decision == FraudDecision.MONITOR:
        return ["record_signal", "monitor_velocity"]
    return ["approve"]


def risk_level(score: Decimal) -> RiskLevel:
    if score >= Decimal("85"):
        return RiskLevel.CRITICAL
    if score >= Decimal("65"):
        return RiskLevel.HIGH
    if score >= Decimal("35"):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def event_to_map(event: FraudEvent) -> Dict[str, Any]:
    return {
        "event_id": event.event_id,
        "entity_hash": event.entity_hash,
        "event_type": event.event_type,
        "amount": event.amount,
        "currency": event.currency,
        "direction": event.direction.value,
        "channel": event.channel,
        "ip_address": event.ip_address,
        "country": event.country,
        "city": event.city,
        "status": event.status.value,
        "success": event.success,
        **{f"metadata.{key}": value for key, value in event.metadata.items()},
    }


def parse_datetime(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"timestamp inválido: {value}") from exc
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


def is_private_or_invalid_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return True


def is_off_hours(hour: int) -> bool:
    return hour >= 22 or hour < 6


def is_round_amount(amount: Decimal) -> bool:
    if amount <= 0:
        return False
    return amount % Decimal("100") == 0 or amount % Decimal("1000") == 0


def normalize(value: Optional[str]) -> Optional[str]:
    return value.strip().lower() if value else None


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"decimal inválido: {value}") from exc


def sum_decimal(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += value
    return total


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal("2")


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def max_decimal(left: Decimal, right: Decimal) -> Decimal:
    return left if left >= right else right


def money_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def decimal_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def to_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def unique_ordered(values: Sequence[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def build_context(request: Request, user: Any) -> ExecutionContext:
    request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    return ExecutionContext(request_id=request_id, user_subject=str(subject), started_at=time.perf_counter())


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 4)


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
