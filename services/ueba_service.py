"""
kwanza-ai-core/services/ueba_service.py

Enterprise-grade UEBA service layer.

UEBA = User and Entity Behavior Analytics.

Purpose
-------
Detect anomalous and risky behavior across users, accounts, devices, IPs,
applications, merchants, employees and service identities.

Capabilities
------------
- Event ingestion and normalization.
- Entity behavior baselines.
- Velocity, novelty, impossible-travel and out-of-hours signals.
- Optional ML anomaly provider integration.
- Explainable risk scoring and reason codes.
- Multi-tenant isolation by design.
- Async repository abstraction.
- Cache, metrics, audit and safe fallbacks.
- Batch evaluation for streams/workers.

This module is framework-agnostic and can be used from FastAPI, Kafka consumers,
Celery workers, Supabase Edge workflows, Airflow/Prefect pipelines or internal
security automation services.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class UEBAServiceError(RuntimeError):
    """Base exception for UEBA service failures."""


class UEBAValidationError(UEBAServiceError):
    """Raised when UEBA input is invalid."""


class UEBADependencyError(UEBAServiceError):
    """Raised when repository/provider dependencies fail."""


# =============================================================================
# Enums and models
# =============================================================================


class EntityType(str, Enum):
    USER = "user"
    ACCOUNT = "account"
    DEVICE = "device"
    IP = "ip"
    API_KEY = "api_key"
    SERVICE_ACCOUNT = "service_account"
    MERCHANT = "merchant"
    EMPLOYEE = "employee"
    CUSTOMER = "customer"
    TENANT = "tenant"


class EventType(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    TRANSACTION = "transaction"
    PAYMENT = "payment"
    TRANSFER = "transfer"
    PASSWORD_CHANGE = "password_change"
    MFA_CHALLENGE = "mfa_challenge"
    MFA_FAILURE = "mfa_failure"
    PERMISSION_CHANGE = "permission_change"
    DATA_EXPORT = "data_export"
    API_CALL = "api_call"
    ADMIN_ACTION = "admin_action"
    FILE_ACCESS = "file_access"
    CONFIG_CHANGE = "config_change"
    FAILED_AUTH = "failed_auth"
    GENERIC = "generic"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class UEBADecision(str, Enum):
    ALLOW = "allow"
    MONITOR = "monitor"
    CHALLENGE = "challenge"
    REVIEW = "review"
    BLOCK = "block"


class SignalSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class GeoPoint:
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def compact(self) -> str:
        return "/".join(str(x).strip().lower() for x in [self.country, self.region, self.city] if x)


@dataclass(frozen=True)
class UEBAEvent:
    event_id: str
    tenant_id: str
    entity_id: str
    entity_type: EntityType
    event_type: EventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    actor_id: Optional[str] = None
    session_id: Optional[str] = None
    device_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    application: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    geo: Optional[GeoPoint] = None
    success: Optional[bool] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityBaseline:
    tenant_id: str
    entity_id: str
    entity_type: EntityType
    window_days: int
    event_count: int
    event_type_counts: Mapping[str, int]
    common_countries: Sequence[str]
    common_cities: Sequence[str]
    common_devices: Sequence[str]
    common_ips: Sequence[str]
    common_applications: Sequence[str]
    active_hours: Sequence[int]
    avg_events_per_hour: float
    avg_amount: Optional[float]
    std_amount: Optional[float]
    failed_auth_rate: float
    last_seen_at: Optional[datetime]
    fingerprint: str
    updated_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UEBASignal:
    code: str
    message: str
    severity: SignalSeverity
    score_delta: float
    evidence: Mapping[str, Any] = field(default_factory=dict)
    rule_id: Optional[str] = None


@dataclass(frozen=True)
class UEBAEvaluationRequest:
    event: UEBAEvent
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    explain: bool = True
    persist_event: bool = True
    update_baseline: bool = False
    use_cache: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UEBAEvaluationResult:
    request_id: str
    event_id: str
    tenant_id: str
    entity_id: str
    entity_type: EntityType
    risk_score: float
    risk_level: RiskLevel
    decision: UEBADecision
    confidence: float
    signals: Sequence[UEBASignal]
    reason_codes: Sequence[str]
    baseline_fingerprint: Optional[str]
    model_score: Optional[float]
    evaluated_at: datetime
    processing_ms: float
    recommended_actions: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["entity_type"] = self.entity_type.value
        payload["risk_level"] = self.risk_level.value
        payload["decision"] = self.decision.value
        payload["evaluated_at"] = self.evaluated_at.isoformat()
        for signal in payload["signals"]:
            signal["severity"] = signal["severity"].value if hasattr(signal["severity"], "value") else signal["severity"]
        return payload


@dataclass(frozen=True)
class UEBABatchResult:
    request_id: str
    tenant_id: str
    results: Sequence[UEBAEvaluationResult]
    total: int
    succeeded: int
    failed: int
    processing_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UEBAServiceConfig:
    baseline_window_days: int = 30
    recent_window_minutes: int = 60
    min_events_for_baseline: int = 10
    cache_ttl_seconds: int = 180
    cache_max_size: int = 50_000
    model_weight: float = 0.35
    rule_weight: float = 0.65
    monitor_threshold: float = 30.0
    challenge_threshold: float = 55.0
    review_threshold: float = 72.0
    block_threshold: float = 90.0
    max_score: float = 100.0
    impossible_travel_kmh: float = 900.0
    failed_auth_rate_threshold: float = 0.35
    velocity_count_threshold: int = 40
    sensitive_event_score_bonus: float = 12.0
    audit_enabled: bool = True
    fail_open: bool = True
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.baseline_window_days <= 0:
            raise UEBAValidationError("baseline_window_days must be positive.")
        if self.recent_window_minutes <= 0:
            raise UEBAValidationError("recent_window_minutes must be positive.")
        if not math.isclose(self.model_weight + self.rule_weight, 1.0, rel_tol=0.0001):
            raise UEBAValidationError("model_weight and rule_weight must sum to 1.0.")
        thresholds = [self.monitor_threshold, self.challenge_threshold, self.review_threshold, self.block_threshold]
        if thresholds != sorted(thresholds):
            raise UEBAValidationError("Risk thresholds must be ordered increasingly.")


# =============================================================================
# Protocols
# =============================================================================


class UEBARepository(Protocol):
    async def save_event(self, event: UEBAEvent) -> None: ...

    async def get_recent_events(
        self,
        tenant_id: str,
        entity_id: str,
        entity_type: EntityType,
        since: datetime,
        limit: int = 1000,
    ) -> Sequence[UEBAEvent]: ...

    async def get_baseline(self, tenant_id: str, entity_id: str, entity_type: EntityType) -> Optional[EntityBaseline]: ...

    async def save_baseline(self, baseline: EntityBaseline) -> None: ...

    async def save_evaluation(self, result: UEBAEvaluationResult) -> None: ...


class UEBAAnomalyModel(Protocol):
    async def score(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> float:
        """Return anomaly score in [0, 1] or [0, 100]."""


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


class InMemoryUEBARepository:
    def __init__(self) -> None:
        self._events: List[UEBAEvent] = []
        self._baselines: Dict[Tuple[str, str, EntityType], EntityBaseline] = {}
        self._evaluations: List[UEBAEvaluationResult] = []
        self._lock = asyncio.Lock()

    async def save_event(self, event: UEBAEvent) -> None:
        async with self._lock:
            self._events.append(event)

    async def get_recent_events(
        self,
        tenant_id: str,
        entity_id: str,
        entity_type: EntityType,
        since: datetime,
        limit: int = 1000,
    ) -> Sequence[UEBAEvent]:
        async with self._lock:
            rows = [
                e for e in self._events
                if e.tenant_id == tenant_id and e.entity_id == entity_id and e.entity_type == entity_type and e.timestamp >= since
            ]
        return tuple(sorted(rows, key=lambda e: e.timestamp, reverse=True)[:limit])

    async def get_baseline(self, tenant_id: str, entity_id: str, entity_type: EntityType) -> Optional[EntityBaseline]:
        return self._baselines.get((tenant_id, entity_id, entity_type))

    async def save_baseline(self, baseline: EntityBaseline) -> None:
        self._baselines[(baseline.tenant_id, baseline.entity_id, baseline.entity_type)] = baseline

    async def save_evaluation(self, result: UEBAEvaluationResult) -> None:
        self._evaluations.append(result)


class AsyncTTLCache:
    def __init__(self, ttl_seconds: int, max_size: int = 50_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
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
            if len(self._items) >= self.max_size:
                self._items.pop(next(iter(self._items)), None)
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _haversine_km(a: GeoPoint, b: GeoPoint) -> Optional[float]:
    if a.latitude is None or a.longitude is None or b.latitude is None or b.longitude is None:
        return None
    radius = 6371.0
    lat1 = math.radians(a.latitude)
    lon1 = math.radians(a.longitude)
    lat2 = math.radians(b.latitude)
    lon2 = math.radians(b.longitude)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def _is_public_ip(ip: Optional[str]) -> Optional[bool]:
    if not ip:
        return None
    try:
        parsed = ipaddress.ip_address(ip)
        return not (parsed.is_private or parsed.is_loopback or parsed.is_reserved or parsed.is_multicast)
    except ValueError:
        return None


# =============================================================================
# Baseline builder and rule engine
# =============================================================================


class UEBABaselineBuilder:
    def __init__(self, config: UEBAServiceConfig) -> None:
        self.config = config

    def build(self, tenant_id: str, entity_id: str, entity_type: EntityType, events: Sequence[UEBAEvent]) -> EntityBaseline:
        ordered = sorted(events, key=lambda e: e.timestamp)
        event_type_counts = Counter(e.event_type.value for e in ordered)
        countries = Counter(e.geo.country for e in ordered if e.geo and e.geo.country)
        cities = Counter(e.geo.compact() for e in ordered if e.geo and e.geo.compact())
        devices = Counter(e.device_id for e in ordered if e.device_id)
        ips = Counter(e.ip_address for e in ordered if e.ip_address)
        apps = Counter(e.application for e in ordered if e.application)
        hours = Counter(e.timestamp.hour for e in ordered)
        amounts = [e.amount for e in ordered if e.amount is not None]
        failed_auth = [e for e in ordered if e.event_type in {EventType.FAILED_AUTH, EventType.MFA_FAILURE} or e.success is False]
        avg_amount = statistics.mean(amounts) if amounts else None
        std_amount = statistics.pstdev(amounts) if len(amounts) > 1 else None
        avg_events_per_hour = len(ordered) / max(self.config.baseline_window_days * 24, 1)
        payload = {
            "tenant_id": tenant_id,
            "entity_id": entity_id,
            "entity_type": entity_type.value,
            "event_count": len(ordered),
            "event_type_counts": dict(event_type_counts),
            "countries": countries.most_common(20),
            "devices": devices.most_common(20),
            "ips": ips.most_common(20),
            "hours": hours.most_common(24),
        }
        return EntityBaseline(
            tenant_id=tenant_id,
            entity_id=entity_id,
            entity_type=entity_type,
            window_days=self.config.baseline_window_days,
            event_count=len(ordered),
            event_type_counts=dict(event_type_counts),
            common_countries=tuple(k for k, _ in countries.most_common(10)),
            common_cities=tuple(k for k, _ in cities.most_common(10)),
            common_devices=tuple(k for k, _ in devices.most_common(10)),
            common_ips=tuple(k for k, _ in ips.most_common(10)),
            common_applications=tuple(k for k, _ in apps.most_common(10)),
            active_hours=tuple(k for k, _ in hours.most_common(24)),
            avg_events_per_hour=avg_events_per_hour,
            avg_amount=avg_amount,
            std_amount=std_amount,
            failed_auth_rate=len(failed_auth) / len(ordered) if ordered else 0.0,
            last_seen_at=ordered[-1].timestamp if ordered else None,
            fingerprint=_stable_hash(payload),
            updated_at=_utc_now(),
        )


class UEBARuleEngine:
    SENSITIVE_EVENTS = {
        EventType.PERMISSION_CHANGE,
        EventType.DATA_EXPORT,
        EventType.ADMIN_ACTION,
        EventType.CONFIG_CHANGE,
        EventType.PASSWORD_CHANGE,
        EventType.PAYMENT,
        EventType.TRANSFER,
    }

    def __init__(self, config: UEBAServiceConfig) -> None:
        self.config = config

    async def evaluate(
        self,
        event: UEBAEvent,
        baseline: Optional[EntityBaseline],
        recent_events: Sequence[UEBAEvent],
    ) -> Tuple[float, List[UEBASignal]]:
        signals: List[UEBASignal] = []
        checks = [
            self._new_entity,
            self._novel_country,
            self._novel_city,
            self._novel_device,
            self._novel_ip,
            self._novel_application,
            self._out_of_hours,
            self._velocity_spike,
            self._failed_auth_spike,
            self._amount_outlier,
            self._impossible_travel,
            self._sensitive_event,
            self._bad_ip_shape,
        ]
        for check in checks:
            signal = await check(event, baseline, recent_events)
            if signal:
                signals.append(signal)
        return _clamp(sum(s.score_delta for s in signals), 0.0, self.config.max_score), signals

    async def _new_entity(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if baseline is None or baseline.event_count < self.config.min_events_for_baseline:
            return UEBASignal(
                code="INSUFFICIENT_BASELINE",
                message="Entity has insufficient historical behavior baseline.",
                severity=SignalSeverity.MEDIUM,
                score_delta=12.0,
                evidence={"min_events": self.config.min_events_for_baseline, "baseline_events": baseline.event_count if baseline else 0},
                rule_id="baseline.insufficient",
            )
        return None

    async def _novel_country(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        country = event.geo.country if event.geo else None
        if baseline and country and baseline.common_countries and country not in baseline.common_countries:
            return UEBASignal("NOVEL_COUNTRY", "Event originated from an unusual country.", SignalSeverity.HIGH, 18.0, {"country": country}, "geo.country")
        return None

    async def _novel_city(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        city = event.geo.compact() if event.geo else None
        if baseline and city and baseline.common_cities and city not in baseline.common_cities:
            return UEBASignal("NOVEL_CITY", "Event originated from an unusual city/region.", SignalSeverity.MEDIUM, 9.0, {"city": city}, "geo.city")
        return None

    async def _novel_device(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if baseline and event.device_id and baseline.common_devices and event.device_id not in baseline.common_devices:
            return UEBASignal("NOVEL_DEVICE", "Event used a device not commonly associated with entity.", SignalSeverity.HIGH, 16.0, {"device_hash": _stable_hash(event.device_id)[:12]}, "device.novel")
        return None

    async def _novel_ip(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if baseline and event.ip_address and baseline.common_ips and event.ip_address not in baseline.common_ips:
            return UEBASignal("NOVEL_IP", "Event used an IP address not commonly associated with entity.", SignalSeverity.MEDIUM, 8.0, {"ip_public": _is_public_ip(event.ip_address)}, "ip.novel")
        return None

    async def _novel_application(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if baseline and event.application and baseline.common_applications and event.application not in baseline.common_applications:
            return UEBASignal("NOVEL_APPLICATION", "Event accessed an unusual application.", SignalSeverity.MEDIUM, 8.0, {"application": event.application}, "application.novel")
        return None

    async def _out_of_hours(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if baseline and baseline.active_hours and event.timestamp.hour not in baseline.active_hours:
            return UEBASignal("OUT_OF_HOURS", "Event occurred outside the entity's usual active hours.", SignalSeverity.MEDIUM, 10.0, {"hour": event.timestamp.hour, "active_hours": list(baseline.active_hours)}, "time.out_of_hours")
        return None

    async def _velocity_spike(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if len(recent_events) >= self.config.velocity_count_threshold:
            return UEBASignal("VELOCITY_SPIKE", "Recent event volume exceeded configured velocity threshold.", SignalSeverity.HIGH, 20.0, {"recent_count": len(recent_events), "threshold": self.config.velocity_count_threshold}, "velocity.count")
        if baseline and baseline.avg_events_per_hour > 0:
            expected = baseline.avg_events_per_hour * (self.config.recent_window_minutes / 60)
            if expected > 0 and len(recent_events) > max(10, expected * 5):
                return UEBASignal("VELOCITY_BASELINE_SPIKE", "Recent event volume is much higher than baseline.", SignalSeverity.HIGH, 18.0, {"recent_count": len(recent_events), "expected": round(expected, 4)}, "velocity.baseline")
        return None

    async def _failed_auth_spike(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        failures = [e for e in recent_events if e.event_type in {EventType.FAILED_AUTH, EventType.MFA_FAILURE} or e.success is False]
        if recent_events and len(failures) / len(recent_events) >= self.config.failed_auth_rate_threshold:
            return UEBASignal("FAILED_AUTH_SPIKE", "Recent authentication failure rate is elevated.", SignalSeverity.HIGH, 17.0, {"failure_rate": round(len(failures) / len(recent_events), 4)}, "auth.failures")
        if baseline and baseline.failed_auth_rate >= self.config.failed_auth_rate_threshold:
            return UEBASignal("BASELINE_FAILED_AUTH_HIGH", "Entity baseline already has elevated failed-auth behavior.", SignalSeverity.MEDIUM, 7.0, {"baseline_failed_auth_rate": baseline.failed_auth_rate}, "auth.baseline")
        return None

    async def _amount_outlier(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if event.amount is None or not baseline or baseline.avg_amount is None:
            return None
        std = baseline.std_amount or max(abs(baseline.avg_amount) * 0.15, 1.0)
        z = abs(event.amount - baseline.avg_amount) / std
        if z >= 4:
            return UEBASignal("AMOUNT_EXTREME_OUTLIER", "Event amount is an extreme outlier for entity.", SignalSeverity.CRITICAL, 26.0, {"amount": event.amount, "zscore": round(z, 4)}, "amount.outlier")
        if z >= 2.5:
            return UEBASignal("AMOUNT_OUTLIER", "Event amount is unusual for entity.", SignalSeverity.MEDIUM, 12.0, {"amount": event.amount, "zscore": round(z, 4)}, "amount.outlier")
        return None

    async def _impossible_travel(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if not event.geo or event.geo.latitude is None or event.geo.longitude is None:
            return None
        previous = next((e for e in sorted(recent_events, key=lambda x: x.timestamp, reverse=True) if e.geo and e.geo.latitude is not None and e.geo.longitude is not None and e.timestamp < event.timestamp), None)
        if not previous or not previous.geo:
            return None
        distance = _haversine_km(previous.geo, event.geo)
        if distance is None:
            return None
        hours = max((event.timestamp - previous.timestamp).total_seconds() / 3600, 1 / 60)
        speed = distance / hours
        if speed >= self.config.impossible_travel_kmh:
            return UEBASignal("IMPOSSIBLE_TRAVEL", "Geographic movement speed exceeds plausible threshold.", SignalSeverity.CRITICAL, 30.0, {"distance_km": round(distance, 2), "hours": round(hours, 3), "speed_kmh": round(speed, 2)}, "geo.impossible_travel")
        return None

    async def _sensitive_event(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        if event.event_type in self.SENSITIVE_EVENTS:
            return UEBASignal("SENSITIVE_EVENT", "Event type is sensitive and increases risk context.", SignalSeverity.MEDIUM, self.config.sensitive_event_score_bonus, {"event_type": event.event_type.value}, "event.sensitive")
        return None

    async def _bad_ip_shape(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[UEBASignal]:
        public = _is_public_ip(event.ip_address)
        if event.ip_address and public is None:
            return UEBASignal("INVALID_IP", "Event contains malformed IP address.", SignalSeverity.LOW, 4.0, {}, "ip.invalid")
        return None


# =============================================================================
# Optional local model
# =============================================================================


class LocalUEBAAnomalyModel:
    async def score(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> float:
        score = 0.0
        if baseline is None or baseline.event_count < 10:
            score += 0.18
        if event.event_type in {EventType.ADMIN_ACTION, EventType.DATA_EXPORT, EventType.CONFIG_CHANGE}:
            score += 0.20
        if event.success is False:
            score += 0.18
        if event.amount is not None and baseline and baseline.avg_amount is not None:
            std = baseline.std_amount or max(abs(baseline.avg_amount) * 0.15, 1.0)
            z = abs(event.amount - baseline.avg_amount) / std
            score += min(z / 10, 0.25)
        if len(recent_events) > 20:
            score += 0.12
        return _clamp(score * 100, 0.0, 100.0)


# =============================================================================
# Main service
# =============================================================================


class UEBAService:
    def __init__(
        self,
        repository: UEBARepository,
        config: Optional[UEBAServiceConfig] = None,
        model: Optional[UEBAAnomalyModel] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        cache: Optional[AsyncTTLCache] = None,
    ) -> None:
        self.config = config or UEBAServiceConfig()
        self.config.validate()
        self.repository = repository
        self.model = model
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.cache = cache or AsyncTTLCache(self.config.cache_ttl_seconds, self.config.cache_max_size)
        self.baseline_builder = UEBABaselineBuilder(self.config)
        self.rule_engine = UEBARuleEngine(self.config)

    async def evaluate(self, request: UEBAEvaluationRequest) -> UEBAEvaluationResult:
        started = time.perf_counter()
        self._validate_event(request.event)
        tags = {"tenant_id": request.event.tenant_id, "entity_type": request.event.entity_type.value, "event_type": request.event.event_type.value}
        self.metrics.increment("ueba.evaluation.started", tags=tags)

        try:
            result = await self._evaluate_internal(request, started)
            self.metrics.increment("ueba.evaluation.completed", tags={**tags, "decision": result.decision.value, "risk_level": result.risk_level.value})
            self.metrics.gauge("ueba.risk_score", result.risk_score, tags=tags)
            self.metrics.timing("ueba.evaluation.processing_ms", result.processing_ms, tags=tags)
            return result
        except Exception as exc:
            self.metrics.increment("ueba.evaluation.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("UEBA evaluation failed", extra={"event_id": request.event.event_id, "request_id": request.request_id})
            if not self.config.fail_open:
                raise
            result = self._fallback_result(request, started, exc)
            await self._persist_result(request, result)
            return result

    async def evaluate_batch(self, tenant_id: str, events: Sequence[UEBAEvent], request_id: Optional[str] = None) -> UEBABatchResult:
        started = time.perf_counter()
        request_id = request_id or str(uuid.uuid4())
        results: List[UEBAEvaluationResult] = []
        failed = 0
        for event in events:
            try:
                if event.tenant_id != tenant_id:
                    raise UEBAValidationError("Batch event tenant_id mismatch.")
                results.append(await self.evaluate(UEBAEvaluationRequest(event=event, request_id=f"{request_id}:{len(results)}")))
            except Exception:
                failed += 1
                if not self.config.fail_open:
                    raise
        return UEBABatchResult(
            request_id=request_id,
            tenant_id=tenant_id,
            results=tuple(results),
            total=len(events),
            succeeded=len(results),
            failed=failed,
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
        )

    async def rebuild_baseline(self, tenant_id: str, entity_id: str, entity_type: EntityType) -> EntityBaseline:
        since = _utc_now() - timedelta(days=self.config.baseline_window_days)
        events = await self.repository.get_recent_events(tenant_id, entity_id, entity_type, since=since, limit=50_000)
        baseline = self.baseline_builder.build(tenant_id, entity_id, entity_type, events)
        await self.repository.save_baseline(baseline)
        await self.cache.set(self._baseline_cache_key(tenant_id, entity_id, entity_type), baseline)
        await self._audit_generic("ueba.baseline.rebuilt", {"tenant_id": tenant_id, "entity_hash": _hash_value(entity_id, self.config.privacy_hash_salt), "entity_type": entity_type.value, "event_count": baseline.event_count})
        return baseline

    async def _evaluate_internal(self, request: UEBAEvaluationRequest, started: float) -> UEBAEvaluationResult:
        event = request.event
        baseline = await self._get_baseline(event.tenant_id, event.entity_id, event.entity_type)
        since = _utc_now() - timedelta(minutes=self.config.recent_window_minutes)
        recent_events = await self.repository.get_recent_events(event.tenant_id, event.entity_id, event.entity_type, since=since, limit=5000)

        rule_score, signals = await self.rule_engine.evaluate(event, baseline, recent_events)
        model_score = await self._score_model(event, baseline, recent_events)
        if model_score is None:
            risk_score = rule_score
        else:
            risk_score = (rule_score * self.config.rule_weight) + (model_score * self.config.model_weight)
        risk_score = _clamp(risk_score, 0.0, self.config.max_score)

        decision = self._decision(risk_score)
        risk_level = self._risk_level(risk_score)
        confidence = self._confidence(signals, baseline, model_score is not None)
        result = UEBAEvaluationResult(
            request_id=request.request_id,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            entity_id=event.entity_id,
            entity_type=event.entity_type,
            risk_score=round(risk_score, 4),
            risk_level=risk_level,
            decision=decision,
            confidence=confidence,
            signals=tuple(sorted(signals, key=lambda s: s.score_delta, reverse=True)),
            reason_codes=tuple(s.code for s in sorted(signals, key=lambda s: s.score_delta, reverse=True)),
            baseline_fingerprint=baseline.fingerprint if baseline else None,
            model_score=None if model_score is None else round(model_score, 4),
            evaluated_at=_utc_now(),
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            recommended_actions=tuple(self._recommended_actions(decision, signals)),
            metadata={
                "recent_event_count": len(recent_events),
                "baseline_event_count": baseline.event_count if baseline else 0,
                "event_hash": _stable_hash(self._safe_event_payload(event)),
            },
        )
        await self._persist_result(request, result)
        if request.update_baseline:
            await self.rebuild_baseline(event.tenant_id, event.entity_id, event.entity_type)
        return result

    async def _get_baseline(self, tenant_id: str, entity_id: str, entity_type: EntityType) -> Optional[EntityBaseline]:
        key = self._baseline_cache_key(tenant_id, entity_id, entity_type)
        cached = await self.cache.get(key)
        if cached is not None:
            return cached
        baseline = await self.repository.get_baseline(tenant_id, entity_id, entity_type)
        if baseline:
            await self.cache.set(key, baseline)
        return baseline

    async def _score_model(self, event: UEBAEvent, baseline: Optional[EntityBaseline], recent_events: Sequence[UEBAEvent]) -> Optional[float]:
        if not self.model:
            return None
        try:
            raw = await self.model.score(event, baseline, recent_events)
            score = float(raw)
            if 0 <= score <= 1:
                score *= 100
            return _clamp(score, 0.0, self.config.max_score)
        except Exception as exc:
            self.metrics.increment("ueba.model.failed", tags={"error": exc.__class__.__name__})
            logger.exception("UEBA model scoring failed")
            return None

    async def _persist_result(self, request: UEBAEvaluationRequest, result: UEBAEvaluationResult) -> None:
        try:
            if request.persist_event:
                await self.repository.save_event(request.event)
            await self.repository.save_evaluation(result)
        except Exception:
            logger.exception("Failed to persist UEBA event/evaluation", extra={"event_id": request.event.event_id})

        await self._audit_generic(
            "ueba.evaluation.completed",
            {
                "request_id": result.request_id,
                "event_id": result.event_id,
                "tenant_id": result.tenant_id,
                "entity_hash": _hash_value(result.entity_id, self.config.privacy_hash_salt),
                "entity_type": result.entity_type.value,
                "risk_score": result.risk_score,
                "risk_level": result.risk_level.value,
                "decision": result.decision.value,
                "reason_codes": list(result.reason_codes),
                "evaluated_at": result.evaluated_at.isoformat(),
            },
        )

    def _decision(self, score: float) -> UEBADecision:
        if score >= self.config.block_threshold:
            return UEBADecision.BLOCK
        if score >= self.config.review_threshold:
            return UEBADecision.REVIEW
        if score >= self.config.challenge_threshold:
            return UEBADecision.CHALLENGE
        if score >= self.config.monitor_threshold:
            return UEBADecision.MONITOR
        return UEBADecision.ALLOW

    def _risk_level(self, score: float) -> RiskLevel:
        if score >= self.config.block_threshold:
            return RiskLevel.CRITICAL
        if score >= self.config.review_threshold:
            return RiskLevel.HIGH
        if score >= self.config.challenge_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _confidence(self, signals: Sequence[UEBASignal], baseline: Optional[EntityBaseline], model_available: bool) -> float:
        base = 0.55
        if baseline and baseline.event_count >= self.config.min_events_for_baseline:
            base += 0.15
        if model_available:
            base += 0.1
        base += min(len(signals) * 0.025, 0.2)
        return round(min(base, 0.98), 4)

    def _recommended_actions(self, decision: UEBADecision, signals: Sequence[UEBASignal]) -> List[str]:
        actions: List[str] = []
        codes = {s.code for s in signals}
        if decision == UEBADecision.ALLOW:
            actions.append("allow_event")
        elif decision == UEBADecision.MONITOR:
            actions.extend(["increase_monitoring", "attach_risk_context"])
        elif decision == UEBADecision.CHALLENGE:
            actions.extend(["step_up_authentication", "require_mfa"])
        elif decision == UEBADecision.REVIEW:
            actions.extend(["open_security_case", "notify_risk_team", "hold_sensitive_action"])
        elif decision == UEBADecision.BLOCK:
            actions.extend(["block_action", "revoke_session", "escalate_incident"])
        if "IMPOSSIBLE_TRAVEL" in codes:
            actions.append("force_session_reauthentication")
        if "NOVEL_DEVICE" in codes:
            actions.append("verify_device_binding")
        if "FAILED_AUTH_SPIKE" in codes:
            actions.append("temporarily_rate_limit_authentication")
        if "DATA_EXPORT" in codes or "SENSITIVE_EVENT" in codes:
            actions.append("audit_sensitive_resource_access")
        return list(dict.fromkeys(actions))

    def _fallback_result(self, request: UEBAEvaluationRequest, started: float, exc: Exception) -> UEBAEvaluationResult:
        event = request.event
        signal = UEBASignal(
            code="UEBA_SERVICE_FALLBACK",
            message=f"UEBA fallback activated due to {exc.__class__.__name__}.",
            severity=SignalSeverity.MEDIUM,
            score_delta=self.config.monitor_threshold,
            evidence={"error": exc.__class__.__name__},
            rule_id="service.fallback",
        )
        return UEBAEvaluationResult(
            request_id=request.request_id,
            event_id=event.event_id,
            tenant_id=event.tenant_id,
            entity_id=event.entity_id,
            entity_type=event.entity_type,
            risk_score=self.config.monitor_threshold,
            risk_level=RiskLevel.LOW,
            decision=UEBADecision.MONITOR,
            confidence=0.35,
            signals=(signal,),
            reason_codes=(signal.code,),
            baseline_fingerprint=None,
            model_score=None,
            evaluated_at=_utc_now(),
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            recommended_actions=("increase_monitoring",),
            metadata={"fallback": True, "error": exc.__class__.__name__},
        )

    def _baseline_cache_key(self, tenant_id: str, entity_id: str, entity_type: EntityType) -> str:
        return f"ueba:baseline:{tenant_id}:{entity_type.value}:{_stable_hash(entity_id)}"

    def _validate_event(self, event: UEBAEvent) -> None:
        if not event.event_id:
            raise UEBAValidationError("event_id is required.")
        if not event.tenant_id:
            raise UEBAValidationError("tenant_id is required.")
        if not event.entity_id:
            raise UEBAValidationError("entity_id is required.")
        if event.amount is not None and event.amount < 0:
            raise UEBAValidationError("event.amount cannot be negative.")
        _ensure_aware(event.timestamp)

    def _safe_event_payload(self, event: UEBAEvent) -> Mapping[str, Any]:
        payload = asdict(event)
        payload["entity_id"] = _hash_value(event.entity_id, self.config.privacy_hash_salt)
        payload["actor_id"] = _hash_value(event.actor_id, self.config.privacy_hash_salt)
        payload["device_id"] = _hash_value(event.device_id, self.config.privacy_hash_salt)
        payload["ip_address"] = _hash_value(event.ip_address, self.config.privacy_hash_salt)
        payload["timestamp"] = event.timestamp.isoformat()
        payload["entity_type"] = event.entity_type.value
        payload["event_type"] = event.event_type.value
        return payload

    async def _audit_generic(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write UEBA audit event", extra={"event_name": event_name})

    @classmethod
    def event_from_payload(cls, payload: Mapping[str, Any]) -> UEBAEvent:
        geo_payload = payload.get("geo") or {}
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif isinstance(timestamp, datetime):
            ts = timestamp
        else:
            ts = _utc_now()
        return UEBAEvent(
            event_id=str(payload.get("event_id") or uuid.uuid4()),
            tenant_id=str(payload["tenant_id"]),
            entity_id=str(payload["entity_id"]),
            entity_type=EntityType(payload.get("entity_type", EntityType.USER.value)),
            event_type=EventType(payload.get("event_type", EventType.GENERIC.value)),
            timestamp=_ensure_aware(ts),
            actor_id=payload.get("actor_id"),
            session_id=payload.get("session_id"),
            device_id=payload.get("device_id"),
            ip_address=payload.get("ip_address"),
            user_agent=payload.get("user_agent"),
            application=payload.get("application"),
            resource=payload.get("resource"),
            action=payload.get("action"),
            amount=_safe_float(payload.get("amount")),
            currency=payload.get("currency"),
            geo=GeoPoint(
                country=geo_payload.get("country"),
                region=geo_payload.get("region"),
                city=geo_payload.get("city"),
                latitude=_safe_float(geo_payload.get("latitude")),
                longitude=_safe_float(geo_payload.get("longitude")),
            ) if geo_payload else None,
            success=payload.get("success"),
            metadata=payload.get("metadata") or {},
        )


# =============================================================================
# Factory
# =============================================================================


def build_ueba_service(
    repository: Optional[UEBARepository] = None,
    config: Optional[UEBAServiceConfig] = None,
    model: Optional[UEBAAnomalyModel] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> UEBAService:
    return UEBAService(
        repository=repository or InMemoryUEBARepository(),
        config=config,
        model=model,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    repo = InMemoryUEBARepository()
    service = build_ueba_service(
        repository=repo,
        model=LocalUEBAAnomalyModel(),
        config=UEBAServiceConfig(privacy_hash_salt="local-dev-salt"),
    )

    base_time = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    for idx in range(30):
        await repo.save_event(
            UEBAEvent(
                event_id=f"base-{idx}",
                tenant_id="tenant-ao",
                entity_id="user-123",
                entity_type=EntityType.USER,
                event_type=EventType.LOGIN,
                timestamp=base_time + timedelta(hours=idx),
                device_id="device-known",
                ip_address="203.0.113.10",
                application="core-app",
                geo=GeoPoint(country="AO", city="Luanda", latitude=-8.839, longitude=13.289),
                success=True,
            )
        )

    await service.rebuild_baseline("tenant-ao", "user-123", EntityType.USER)

    event = UEBAEvent(
        event_id="evt-risk-001",
        tenant_id="tenant-ao",
        entity_id="user-123",
        entity_type=EntityType.USER,
        event_type=EventType.DATA_EXPORT,
        timestamp=datetime(2026, 5, 14, 3, 30, tzinfo=UTC),
        device_id="device-new",
        ip_address="198.51.100.22",
        application="admin-console",
        geo=GeoPoint(country="BR", city="Porto Alegre", latitude=-30.0346, longitude=-51.2177),
        success=True,
    )
    result = await service.evaluate(UEBAEvaluationRequest(event=event))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
