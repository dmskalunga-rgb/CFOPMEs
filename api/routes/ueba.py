#!/usr/bin/env python3
"""
api/routes/ueba.py

Enterprise-grade UEBA API Router.

Objetivo:
- Expor endpoints HTTP para User and Entity Behavior Analytics (UEBA).
- Calcular baseline comportamental, risco por evento, anomalias, velocity, novo dispositivo/IP/país,
  horário incomum, ação sensível, falhas de autenticação e desvios de volume.
- Suportar scoring realtime e batch, auditoria em memória, estatísticas e perfil por entidade.
- Aplicar validação Pydantic, autenticação por scopes, request-id e respostas padronizadas.

Endpoints:
    GET  /ueba/health
    POST /ueba/score
    POST /ueba/batch-score
    GET  /ueba/profile/{entity_hash}
    GET  /ueba/audit/{entity_hash}
    GET  /ueba/stats/summary
    POST /ueba/reset/{entity_hash}

Integração:
    from fastapi import FastAPI
    from api.routes.ueba import router as ueba_router

    app.include_router(ueba_router, prefix="/v1")

Notas:
- Este router mantém estado em memória para ser plug-and-play.
- Em produção, substitua UebaStateStore por Redis/Feature Store/Data Lake/Kafka.
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
from typing import Any, Deque, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

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
DEFAULT_TIMEZONE = timezone.utc
DEFAULT_PRECISION = 38

getcontext().prec = DEFAULT_PRECISION

router = APIRouter(prefix="/ueba", tags=["ueba"])


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UebaDecision(str, Enum):
    ALLOW = "allow"
    MONITOR = "monitor"
    CHALLENGE = "challenge"
    REVIEW = "review"
    ESCALATE = "escalate"


class EntityType(str, Enum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    DEVICE = "device"
    API_CLIENT = "api_client"
    UNKNOWN = "unknown"


class EventOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    ERROR = "error"
    UNKNOWN = "unknown"


class UebaEventRequest(BaseModel):
    event_id: str = Field(default_factory=lambda: f"ueba_evt_{uuid.uuid4().hex[:16]}")
    entity_id: str
    entity_type: EntityType = EntityType.USER
    timestamp: str = Field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())
    event_type: str = "activity"
    action: str = "unknown"
    resource: Optional[str] = None
    outcome: EventOutcome = EventOutcome.SUCCESS
    ip_address: Optional[str] = None
    device_id: Optional[str] = None
    user_agent: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    session_id: Optional[str] = None
    amount: Optional[float] = None
    sensitivity: str = "normal"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UebaPolicyRequest(BaseModel):
    medium_threshold: float = 35.0
    high_threshold: float = 65.0
    critical_threshold: float = 85.0
    velocity_window_seconds: int = 600
    velocity_count_threshold: int = 25
    failure_count_threshold: int = 5
    profile_ttl_seconds: int = 604800
    min_profile_events_for_baseline: int = 5
    hash_entity_ids: bool = True
    sensitive_actions: List[str] = Field(default_factory=lambda: [
        "delete", "export", "download_bulk", "privilege_change", "password_reset", "mfa_disable",
        "payment_approve", "wire_transfer", "admin_login", "token_create", "secret_read",
    ])


class UebaBatchRequest(BaseModel):
    events: List[UebaEventRequest] = Field(default_factory=list)
    policy: UebaPolicyRequest = Field(default_factory=UebaPolicyRequest)
    update_state: bool = True

    @validator("events")
    def validate_batch_size(cls, value: List[UebaEventRequest]) -> List[UebaEventRequest]:
        if len(value) > 50_000:
            raise ValueError("batch UEBA excede limite de 50.000 eventos")
        return value


class UebaScoreRequest(BaseModel):
    event: UebaEventRequest
    policy: UebaPolicyRequest = Field(default_factory=UebaPolicyRequest)
    update_state: bool = True


class UebaFeatures(BaseModel):
    profile_event_count: int
    velocity_count: int
    failure_count: int
    new_device: bool
    new_ip: bool
    new_country: bool
    new_city: bool
    new_user_agent: bool
    new_action: bool
    new_resource: bool
    sensitive_action: bool
    off_hours: bool
    private_or_invalid_ip: bool
    impossible_travel_candidate: bool
    amount_ratio_to_median: Optional[float]
    session_burst_count: int
    resource_burst_count: int


class UebaScoreResponse(BaseModel):
    request_id: str
    decision_id: str
    event_id: str
    entity_hash: str
    status: str
    decision: UebaDecision
    risk_level: RiskLevel
    risk_score: float
    signals: List[str]
    reasons: List[str]
    recommended_actions: List[str]
    features: UebaFeatures
    latency_ms: float
    payload: Dict[str, Any]


class UebaBatchResponse(BaseModel):
    request_id: str
    status: str
    total_events: int
    latency_ms: float
    summary: Dict[str, Any]
    results: List[UebaScoreResponse]


class UebaProfileResponse(BaseModel):
    request_id: str
    entity_hash: str
    profile: Dict[str, Any]


class UebaAuditResponse(BaseModel):
    request_id: str
    entity_hash: str
    events: List[Dict[str, Any]]


@dataclass(frozen=True)
class UebaEvent:
    event_id: str
    entity_id: str
    entity_hash: str
    entity_type: EntityType
    timestamp: datetime
    event_type: str
    action: str
    resource_hash: Optional[str]
    resource_label: Optional[str]
    outcome: EventOutcome
    ip_address: Optional[str]
    device_hash: Optional[str]
    user_agent_hash: Optional[str]
    country: Optional[str]
    city: Optional[str]
    session_hash: Optional[str]
    amount: Optional[Decimal]
    sensitivity: str
    metadata: Dict[str, Any]


@dataclass
class UebaProfile:
    entity_hash: str
    entity_type: str
    first_seen_at: str
    last_seen_at: str
    event_count: int = 0
    known_devices: Set[str] = field(default_factory=set)
    known_ips: Set[str] = field(default_factory=set)
    known_countries: Set[str] = field(default_factory=set)
    known_cities: Set[str] = field(default_factory=set)
    known_user_agents: Set[str] = field(default_factory=set)
    known_actions: Set[str] = field(default_factory=set)
    known_resources: Set[str] = field(default_factory=set)
    active_hours: Counter = field(default_factory=Counter)
    outcome_counts: Counter = field(default_factory=Counter)
    event_type_counts: Counter = field(default_factory=Counter)
    amount_values: Deque[Decimal] = field(default_factory=lambda: deque(maxlen=500))
    last_country: Optional[str] = None
    last_city: Optional[str] = None
    last_seen_ts: Optional[datetime] = None

    @property
    def median_amount(self) -> Optional[Decimal]:
        if not self.amount_values:
            return None
        ordered = sorted(self.amount_values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / Decimal("2")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_hash": self.entity_hash,
            "entity_type": self.entity_type,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "event_count": self.event_count,
            "known_devices_count": len(self.known_devices),
            "known_ips_count": len(self.known_ips),
            "known_countries": sorted(self.known_countries),
            "known_cities": sorted(self.known_cities),
            "known_actions": sorted(self.known_actions),
            "known_resources_count": len(self.known_resources),
            "active_hours": dict(self.active_hours),
            "outcome_counts": dict(self.outcome_counts),
            "event_type_counts": dict(self.event_type_counts),
            "median_amount": None if self.median_amount is None else decimal_str(self.median_amount),
            "last_country": self.last_country,
            "last_city": self.last_city,
            "last_seen_ts": None if self.last_seen_ts is None else self.last_seen_ts.isoformat(),
        }


@dataclass(frozen=True)
class ExecutionContext:
    request_id: str
    user_subject: str
    started_at: float


class UebaStateStore:
    def __init__(self) -> None:
        self.profiles: Dict[str, UebaProfile] = {}
        self.events_by_entity: DefaultDict[str, Deque[UebaEvent]] = defaultdict(deque)
        self.audit_by_entity: DefaultDict[str, Deque[Dict[str, Any]]] = defaultdict(lambda: deque(maxlen=1000))

    def evict(self, now: datetime, ttl_seconds: int) -> None:
        cutoff = now - timedelta(seconds=ttl_seconds)
        for entity_hash, events in list(self.events_by_entity.items()):
            while events and events[0].timestamp < cutoff:
                events.popleft()
            if not events and entity_hash in self.profiles:
                self.profiles.pop(entity_hash, None)

    def get_or_create_profile(self, event: UebaEvent) -> UebaProfile:
        profile = self.profiles.get(event.entity_hash)
        if profile is None:
            profile = UebaProfile(
                entity_hash=event.entity_hash,
                entity_type=event.entity_type.value,
                first_seen_at=event.timestamp.isoformat(),
                last_seen_at=event.timestamp.isoformat(),
            )
            self.profiles[event.entity_hash] = profile
        return profile

    def update(self, event: UebaEvent, result: Mapping[str, Any]) -> None:
        profile = self.get_or_create_profile(event)
        profile.last_seen_at = event.timestamp.isoformat()
        profile.event_count += 1
        if event.device_hash:
            profile.known_devices.add(event.device_hash)
        if event.ip_address:
            profile.known_ips.add(event.ip_address)
        if event.country:
            profile.known_countries.add(event.country)
            profile.last_country = event.country
        if event.city:
            profile.known_cities.add(event.city)
            profile.last_city = event.city
        if event.user_agent_hash:
            profile.known_user_agents.add(event.user_agent_hash)
        profile.known_actions.add(event.action)
        if event.resource_hash:
            profile.known_resources.add(event.resource_hash)
        profile.active_hours[event.timestamp.hour] += 1
        profile.outcome_counts[event.outcome.value] += 1
        profile.event_type_counts[event.event_type] += 1
        if event.amount is not None:
            profile.amount_values.append(event.amount)
        profile.last_seen_ts = event.timestamp
        self.events_by_entity[event.entity_hash].append(event)
        self.audit_by_entity[event.entity_hash].append(dict(result))

    def reset(self, entity_hash: str) -> bool:
        existed = entity_hash in self.profiles or entity_hash in self.events_by_entity or entity_hash in self.audit_by_entity
        self.profiles.pop(entity_hash, None)
        self.events_by_entity.pop(entity_hash, None)
        self.audit_by_entity.pop(entity_hash, None)
        return existed


state_store = UebaStateStore()


@router.get("/health")
async def ueba_health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "router": "ueba",
        "version": ROUTER_VERSION,
        "timestamp": utc_now_iso(),
        "state": {
            "profile_count": len(state_store.profiles),
            "tracked_entities": len(state_store.events_by_entity),
        },
    }


@router.post("/score", response_model=UebaScoreResponse, dependencies=[Depends(require_scopes("ueba:score"))])
async def score_ueba(payload: UebaScoreRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> UebaScoreResponse:
    ctx = build_context(request, user)
    event = parse_event(payload.event, payload.policy)
    state_store.evict(event.timestamp, payload.policy.profile_ttl_seconds)
    return score_event(event, payload.policy, payload.update_state, ctx)


@router.post("/batch-score", response_model=UebaBatchResponse, dependencies=[Depends(require_scopes("ueba:score"))])
async def batch_score_ueba(payload: UebaBatchRequest, request: Request, user: CurrentUser = Depends(get_current_user)) -> UebaBatchResponse:
    ctx = build_context(request, user)
    started = time.perf_counter()
    results: List[UebaScoreResponse] = []
    for raw in sorted(payload.events, key=lambda item: item.timestamp):
        event = parse_event(raw, payload.policy)
        state_store.evict(event.timestamp, payload.policy.profile_ttl_seconds)
        results.append(score_event(event, payload.policy, payload.update_state, ctx))
    return UebaBatchResponse(
        request_id=ctx.request_id,
        status="success",
        total_events=len(results),
        latency_ms=elapsed_ms(started),
        summary=batch_summary(results),
        results=results,
    )


@router.get("/profile/{entity_hash}", response_model=UebaProfileResponse, dependencies=[Depends(require_scopes("ueba:read"))])
async def get_ueba_profile(entity_hash: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> UebaProfileResponse:
    ctx = build_context(request, user)
    profile = state_store.profiles.get(entity_hash)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil UEBA não encontrado")
    return UebaProfileResponse(request_id=ctx.request_id, entity_hash=entity_hash, profile=profile.to_dict())


@router.get("/audit/{entity_hash}", response_model=UebaAuditResponse, dependencies=[Depends(require_scopes("ueba:read"))])
async def get_ueba_audit(entity_hash: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> UebaAuditResponse:
    ctx = build_context(request, user)
    return UebaAuditResponse(request_id=ctx.request_id, entity_hash=entity_hash, events=list(state_store.audit_by_entity.get(entity_hash, [])))


@router.get("/stats/summary", dependencies=[Depends(require_scopes("ueba:read"))])
async def ueba_stats() -> Dict[str, Any]:
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


@router.post("/reset/{entity_hash}", dependencies=[Depends(require_scopes("ueba:write"))])
async def reset_ueba_profile(entity_hash: str, request: Request, user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    ctx = build_context(request, user)
    existed = state_store.reset(entity_hash)
    return {"request_id": ctx.request_id, "status": "reset" if existed else "not_found", "entity_hash": entity_hash}


def score_event(event: UebaEvent, policy: UebaPolicyRequest, update_state: bool, ctx: ExecutionContext) -> UebaScoreResponse:
    started = time.perf_counter()
    profile = state_store.get_or_create_profile(event)
    recent = recent_events(event.entity_hash, event.timestamp, policy.velocity_window_seconds)
    features = build_features(event, profile, recent, policy)
    score, signals, reasons = calculate_score(event, features, policy)
    level = risk_level(score, policy)
    decision = decision_for_level(level)
    result = UebaScoreResponse(
        request_id=ctx.request_id,
        decision_id=f"ueba_{uuid.uuid4().hex[:20]}",
        event_id=event.event_id,
        entity_hash=event.entity_hash,
        status="success",
        decision=decision,
        risk_level=level,
        risk_score=to_float(score),
        signals=signals or ["baseline_normal"],
        reasons=reasons or ["no_material_behavioral_deviation_detected"],
        recommended_actions=actions_for_decision(decision, level, signals),
        features=features,
        latency_ms=elapsed_ms(started),
        payload={
            "event_id": event.event_id,
            "entity_hash": event.entity_hash,
            "entity_type": event.entity_type.value,
            "event_type": event.event_type,
            "action": event.action,
            "outcome": event.outcome.value,
            "timestamp": event.timestamp.isoformat(),
        },
    )
    if update_state:
        state_store.update(event, result.dict())
    return result


def build_features(event: UebaEvent, profile: UebaProfile, recent: Sequence[UebaEvent], policy: UebaPolicyRequest) -> UebaFeatures:
    failures = [item for item in recent if item.outcome in {EventOutcome.FAILURE, EventOutcome.DENIED, EventOutcome.ERROR}]
    resource_burst = sum(1 for item in recent if event.resource_hash and item.resource_hash == event.resource_hash)
    session_burst = sum(1 for item in recent if event.session_hash and item.session_hash == event.session_hash)
    amount_ratio: Optional[Decimal] = None
    if event.amount is not None and profile.median_amount is not None:
        amount_ratio = event.amount / max_decimal(profile.median_amount, Decimal("1"))
    return UebaFeatures(
        profile_event_count=profile.event_count,
        velocity_count=len(recent) + 1,
        failure_count=len(failures) + (1 if event.outcome in {EventOutcome.FAILURE, EventOutcome.DENIED, EventOutcome.ERROR} else 0),
        new_device=bool(event.device_hash and event.device_hash not in profile.known_devices and profile.event_count > 0),
        new_ip=bool(event.ip_address and event.ip_address not in profile.known_ips and profile.event_count > 0),
        new_country=bool(event.country and event.country not in profile.known_countries and profile.event_count > 0),
        new_city=bool(event.city and event.city not in profile.known_cities and profile.event_count > 0),
        new_user_agent=bool(event.user_agent_hash and event.user_agent_hash not in profile.known_user_agents and profile.event_count > 0),
        new_action=bool(event.action and event.action not in profile.known_actions and profile.event_count > 0),
        new_resource=bool(event.resource_hash and event.resource_hash not in profile.known_resources and profile.event_count > 0),
        sensitive_action=event.action in {normalize_text(item) for item in policy.sensitive_actions} or normalize_text(event.sensitivity) in {"high", "critical", "restricted"},
        off_hours=is_off_hours(event.timestamp.hour, profile),
        private_or_invalid_ip=bool(event.ip_address and is_private_or_invalid_ip(event.ip_address)),
        impossible_travel_candidate=impossible_travel_candidate(event, profile),
        amount_ratio_to_median=None if amount_ratio is None else to_float(amount_ratio),
        session_burst_count=session_burst,
        resource_burst_count=resource_burst,
    )


def calculate_score(event: UebaEvent, features: UebaFeatures, policy: UebaPolicyRequest) -> Tuple[Decimal, List[str], List[str]]:
    score = Decimal("0")
    signals: List[str] = []
    reasons: List[str] = []

    def add(condition: bool, points: Decimal, signal: str, reason: str) -> None:
        nonlocal score
        if condition:
            score += points
            signals.append(signal)
            reasons.append(reason)

    baseline_ready = features.profile_event_count >= policy.min_profile_events_for_baseline
    add(features.velocity_count >= policy.velocity_count_threshold, Decimal("18"), "high_velocity", f"velocity_count={features.velocity_count}")
    add(features.failure_count >= policy.failure_count_threshold, Decimal("22"), "failure_burst", f"failure_count={features.failure_count}")
    add(baseline_ready and features.new_device, Decimal("14"), "new_device", "new_device_for_entity")
    add(baseline_ready and features.new_ip, Decimal("10"), "new_ip", "new_ip_for_entity")
    add(baseline_ready and features.new_country, Decimal("20"), "new_country", "new_country_for_entity")
    add(baseline_ready and features.new_city, Decimal("8"), "new_city", "new_city_for_entity")
    add(baseline_ready and features.new_user_agent, Decimal("8"), "new_user_agent", "new_user_agent_for_entity")
    add(baseline_ready and features.new_action, Decimal("16"), "new_action", "new_action_for_entity")
    add(baseline_ready and features.new_resource, Decimal("8"), "new_resource", "new_resource_for_entity")
    add(features.sensitive_action, Decimal("18"), "sensitive_action", "sensitive_action_or_high_sensitivity_event")
    add(features.off_hours, Decimal("8"), "off_hours", "event_outside_common_hours")
    add(features.private_or_invalid_ip, Decimal("5"), "private_or_invalid_ip", "private_or_invalid_ip")
    add(features.impossible_travel_candidate, Decimal("25"), "impossible_travel_candidate", "country_changed_in_short_time_window")
    add(features.amount_ratio_to_median is not None and Decimal(str(features.amount_ratio_to_median)) >= Decimal("3"), Decimal("16"), "amount_deviation", "amount_ratio_to_median_above_3x")
    add(features.session_burst_count >= 20, Decimal("10"), "session_burst", f"session_burst_count={features.session_burst_count}")
    add(features.resource_burst_count >= 15, Decimal("10"), "resource_burst", f"resource_burst_count={features.resource_burst_count}")
    add(event.outcome in {EventOutcome.DENIED, EventOutcome.ERROR}, Decimal("8"), "denied_or_error", f"outcome={event.outcome.value}")

    if event.entity_type == EntityType.SERVICE_ACCOUNT and features.new_country:
        score += Decimal("10")
        signals.append("service_account_geo_change")
        reasons.append("service_account_with_new_country")

    return clamp_decimal(score, Decimal("0"), Decimal("100")), unique_ordered(signals), unique_ordered(reasons)


def parse_event(payload: UebaEventRequest, policy: UebaPolicyRequest) -> UebaEvent:
    entity_hash = hash_identifier(payload.entity_id) if policy.hash_entity_ids else payload.entity_id
    amount = None if payload.amount is None else money(payload.amount).copy_abs()
    resource_label = normalize_text(payload.resource) if payload.resource else None
    return UebaEvent(
        event_id=payload.event_id,
        entity_id=payload.entity_id,
        entity_hash=entity_hash,
        entity_type=payload.entity_type,
        timestamp=parse_datetime(payload.timestamp),
        event_type=normalize_text(payload.event_type),
        action=normalize_text(payload.action),
        resource_hash=hash_identifier(payload.resource) if payload.resource else None,
        resource_label=resource_label,
        outcome=payload.outcome,
        ip_address=payload.ip_address,
        device_hash=hash_identifier(payload.device_id) if payload.device_id else None,
        user_agent_hash=hash_identifier(payload.user_agent) if payload.user_agent else None,
        country=payload.country.upper() if payload.country else None,
        city=payload.city.title() if payload.city else None,
        session_hash=hash_identifier(payload.session_id) if payload.session_id else None,
        amount=amount,
        sensitivity=normalize_text(payload.sensitivity),
        metadata=payload.metadata,
    )


def recent_events(entity_hash: str, now: datetime, window_seconds: int) -> List[UebaEvent]:
    events = state_store.events_by_entity[entity_hash]
    cutoff = now - timedelta(seconds=window_seconds)
    while events and events[0].timestamp < cutoff:
        events.popleft()
    return list(events)


def impossible_travel_candidate(event: UebaEvent, profile: UebaProfile) -> bool:
    if not event.country or not profile.last_country or not profile.last_seen_ts:
        return False
    if event.country == profile.last_country:
        return False
    elapsed = abs((event.timestamp - profile.last_seen_ts).total_seconds())
    return elapsed <= 3600


def is_off_hours(hour: int, profile: UebaProfile) -> bool:
    if profile.event_count >= 20 and profile.active_hours:
        common_hours = {hour_value for hour_value, count in profile.active_hours.items() if count >= 2}
        if common_hours and hour not in common_hours:
            return True
    return hour >= 22 or hour < 6


def risk_level(score: Decimal, policy: UebaPolicyRequest) -> RiskLevel:
    if score >= Decimal(str(policy.critical_threshold)):
        return RiskLevel.CRITICAL
    if score >= Decimal(str(policy.high_threshold)):
        return RiskLevel.HIGH
    if score >= Decimal(str(policy.medium_threshold)):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def decision_for_level(level: RiskLevel) -> UebaDecision:
    if level == RiskLevel.CRITICAL:
        return UebaDecision.ESCALATE
    if level == RiskLevel.HIGH:
        return UebaDecision.REVIEW
    if level == RiskLevel.MEDIUM:
        return UebaDecision.CHALLENGE
    return UebaDecision.ALLOW


def actions_for_decision(decision: UebaDecision, level: RiskLevel, signals: Sequence[str]) -> List[str]:
    signal_set = set(signals)
    if decision == UebaDecision.ESCALATE:
        return ["open_security_incident", "notify_soc", "force_session_review", "step_up_authentication"]
    if "failure_burst" in signal_set:
        return ["rate_limit_entity", "review_auth_failures", "require_mfa"]
    if "impossible_travel_candidate" in signal_set:
        return ["verify_geo_anomaly", "challenge_mfa", "review_recent_sessions"]
    if decision == UebaDecision.REVIEW:
        return ["create_security_case", "review_user_activity", "increase_monitoring"]
    if decision == UebaDecision.CHALLENGE:
        return ["challenge_mfa", "monitor_next_events"]
    if decision == UebaDecision.MONITOR:
        return ["record_signal", "monitor_behavior"]
    return ["allow"]


def batch_summary(results: Sequence[UebaScoreResponse]) -> Dict[str, Any]:
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


def parse_datetime(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"timestamp inválido: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed.astimezone(DEFAULT_TIMEZONE)


def is_private_or_invalid_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_private
    except ValueError:
        return True


def normalize_text(value: Optional[str]) -> str:
    if value is None:
        return "unknown"
    return str(value).strip().lower().replace(" ", "_") or "unknown"


def hash_identifier(value: str, length: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def money(value: Any) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"decimal inválido: {value}") from exc


def max_decimal(left: Decimal, right: Decimal) -> Decimal:
    return left if left >= right else right


def clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


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
