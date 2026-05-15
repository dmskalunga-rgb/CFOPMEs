"""
data/analytics/behavioral_analytics.py

Enterprise Behavioral Analytics Engine.

Recursos:
- Coleta e análise de eventos comportamentais
- Sessões de usuário
- Funis de conversão
- Jornadas
- Cohort analysis
- Segmentação comportamental
- Scoring de engajamento
- Detecção de abandono
- Multi-tenant
- Auditoria e métricas plugáveis
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class BehavioralEventType(str, Enum):
    PAGE_VIEW = "page_view"
    SCREEN_VIEW = "screen_view"
    CLICK = "click"
    SEARCH = "search"
    ADD_TO_CART = "add_to_cart"
    REMOVE_FROM_CART = "remove_from_cart"
    CHECKOUT_STARTED = "checkout_started"
    PURCHASE = "purchase"
    SIGNUP = "signup"
    LOGIN = "login"
    LOGOUT = "logout"
    FEATURE_USED = "feature_used"
    ERROR_OCCURRED = "error_occurred"
    CUSTOM = "custom"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    EXPIRED = "expired"


class FunnelStatus(str, Enum):
    COMPLETED = "completed"
    DROPPED = "dropped"
    IN_PROGRESS = "in_progress"


class SegmentOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"


class EngagementLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    POWER_USER = "power_user"


# =============================================================================
# Exceptions
# =============================================================================

class BehavioralAnalyticsError(Exception):
    """Erro base de behavioral analytics."""


class BehavioralEventValidationError(BehavioralAnalyticsError):
    """Evento comportamental inválido."""


class FunnelDefinitionError(BehavioralAnalyticsError):
    """Definição de funil inválida."""


class SegmentDefinitionError(BehavioralAnalyticsError):
    """Segmento inválido."""


# =============================================================================
# Protocols
# =============================================================================

class BehavioralEventStore(Protocol):
    def append(self, event: "BehavioralEvent") -> None:
        ...

    def list_events(
        self,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> List["BehavioralEvent"]:
        ...


class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class InMemoryBehavioralEventStore:
    def __init__(self) -> None:
        self._events: List[BehavioralEvent] = []

    def append(self, event: "BehavioralEvent") -> None:
        self._events.append(event)

    def list_events(
        self,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> List["BehavioralEvent"]:
        events = list(self._events)

        if tenant_id is not None:
            events = [event for event in events if event.tenant_id == tenant_id]

        if user_id is not None:
            events = [event for event in events if event.user_id == user_id]

        if from_time is not None:
            events = [event for event in events if event.timestamp >= from_time]

        if to_time is not None:
            events = [event for event in events if event.timestamp <= to_time]

        return sorted(events, key=lambda event: event.timestamp)


class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("behavioral_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("gauge=%s value=%s tags=%s", metric_name, value, tags or {})


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class BehavioralContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    source: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class BehavioralEvent:
    event_id: str
    user_id: str
    event_type: BehavioralEventType
    timestamp: datetime
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    anonymous_id: Optional[str] = None
    domain: Optional[str] = None
    name: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)
    device: Dict[str, Any] = field(default_factory=dict)
    location: Dict[str, Any] = field(default_factory=dict)
    campaign: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id:
            raise BehavioralEventValidationError("event_id é obrigatório")

        if not self.user_id and not self.anonymous_id:
            raise BehavioralEventValidationError("user_id ou anonymous_id é obrigatório")

        if not isinstance(self.timestamp, datetime):
            raise BehavioralEventValidationError("timestamp precisa ser datetime")


@dataclass
class UserSession:
    session_id: str
    user_id: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    tenant_id: Optional[str] = None
    status: SessionStatus = SessionStatus.ACTIVE
    events: List[BehavioralEvent] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or datetime.now(timezone.utc)
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def event_count(self) -> int:
        return len(self.events)


@dataclass(frozen=True)
class FunnelStep:
    step_id: str
    name: str
    event_type: BehavioralEventType
    required_properties: Dict[str, Any] = field(default_factory=dict)
    order: int = 0


@dataclass(frozen=True)
class FunnelDefinition:
    funnel_id: str
    name: str
    steps: List[FunnelStep]
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    conversion_window_hours: int = 24
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.funnel_id:
            raise FunnelDefinitionError("funnel_id é obrigatório")

        if len(self.steps) < 2:
            raise FunnelDefinitionError("Funil precisa de pelo menos 2 etapas")

        orders = [step.order for step in self.steps]
        if len(orders) != len(set(orders)):
            raise FunnelDefinitionError("Ordem duplicada nas etapas do funil")


@dataclass
class FunnelUserResult:
    user_id: str
    funnel_id: str
    status: FunnelStatus
    completed_steps: List[str]
    dropped_at_step: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]


@dataclass
class FunnelAnalysisResult:
    funnel_id: str
    name: str
    total_users: int
    completed_users: int
    conversion_rate: float
    step_counts: Dict[str, int]
    step_conversion_rates: Dict[str, float]
    drop_off_rates: Dict[str, float]
    user_results: List[FunnelUserResult]
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SegmentRule:
    field_path: str
    operator: SegmentOperator
    value: Any

    def matches(self, profile: Dict[str, Any]) -> bool:
        candidate = self._get_path(profile, self.field_path)

        if self.operator == SegmentOperator.EQ:
            return candidate == self.value
        if self.operator == SegmentOperator.NE:
            return candidate != self.value
        if self.operator == SegmentOperator.GT:
            return candidate > self.value
        if self.operator == SegmentOperator.GTE:
            return candidate >= self.value
        if self.operator == SegmentOperator.LT:
            return candidate < self.value
        if self.operator == SegmentOperator.LTE:
            return candidate <= self.value
        if self.operator == SegmentOperator.IN:
            return candidate in self.value
        if self.operator == SegmentOperator.NOT_IN:
            return candidate not in self.value
        if self.operator == SegmentOperator.CONTAINS:
            return str(self.value) in str(candidate)

        return False

    @staticmethod
    def _get_path(data: Dict[str, Any], path: str) -> Any:
        current: Any = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current


@dataclass(frozen=True)
class SegmentDefinition:
    segment_id: str
    name: str
    rules: List[SegmentRule]
    match_all: bool = True
    tenant_id: Optional[str] = None
    description: str = ""

    def validate(self) -> None:
        if not self.segment_id:
            raise SegmentDefinitionError("segment_id é obrigatório")

        if not self.rules:
            raise SegmentDefinitionError("Segmento precisa de pelo menos uma regra")


@dataclass
class BehavioralProfile:
    user_id: str
    tenant_id: Optional[str]
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    total_events: int
    sessions_count: int
    events_by_type: Dict[str, int]
    engagement_score: float
    engagement_level: EngagementLevel
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CohortResult:
    cohort_key: str
    users: List[str]
    size: int
    retention_by_period: Dict[str, float]
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Engine
# =============================================================================

class BehavioralAnalyticsEngine:
    def __init__(
        self,
        event_store: Optional[BehavioralEventStore] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        session_timeout_minutes: int = 30,
    ) -> None:
        self.event_store = event_store or InMemoryBehavioralEventStore()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.session_timeout_minutes = session_timeout_minutes
        self._funnels: Dict[str, FunnelDefinition] = {}
        self._segments: Dict[str, SegmentDefinition] = {}

    def track(self, event: BehavioralEvent) -> None:
        event.validate()
        self.event_store.append(event)

        self.metrics_backend.increment(
            "behavioral.events.total",
            tags={
                "event_type": event.event_type.value,
                "tenant_id": event.tenant_id or "-",
                "domain": event.domain or "-",
            },
        )

        self._audit(
            "behavioral.event.tracked",
            {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "user_id_hash": self._hash(event.user_id),
                "tenant_id": event.tenant_id,
                "session_id": event.session_id,
            },
        )

    def track_many(self, events: Iterable[BehavioralEvent]) -> None:
        for event in events:
            self.track(event)

    def register_funnel(self, funnel: FunnelDefinition) -> None:
        funnel.validate()
        self._funnels[funnel.funnel_id] = funnel
        self._audit("behavioral.funnel.registered", asdict(funnel))

    def register_segment(self, segment: SegmentDefinition) -> None:
        segment.validate()
        self._segments[segment.segment_id] = segment
        self._audit("behavioral.segment.registered", asdict(segment))

    def build_sessions(
        self,
        tenant_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> List[UserSession]:
        events = self.event_store.list_events(
            tenant_id=tenant_id,
            from_time=from_time,
            to_time=to_time,
        )

        events_by_user: Dict[str, List[BehavioralEvent]] = defaultdict(list)
        for event in events:
            events_by_user[event.user_id].append(event)

        sessions: List[UserSession] = []

        for user_id, user_events in events_by_user.items():
            user_events = sorted(user_events, key=lambda event: event.timestamp)

            current_session: Optional[UserSession] = None

            for event in user_events:
                if (
                    current_session is None
                    or self._is_session_expired(current_session, event)
                ):
                    if current_session:
                        current_session.status = SessionStatus.CLOSED
                        current_session.ended_at = current_session.events[-1].timestamp
                        sessions.append(current_session)

                    current_session = UserSession(
                        session_id=event.session_id or str(uuid.uuid4()),
                        user_id=user_id,
                        tenant_id=event.tenant_id,
                        started_at=event.timestamp,
                        events=[],
                    )

                current_session.events.append(event)

            if current_session:
                current_session.status = SessionStatus.CLOSED
                current_session.ended_at = current_session.events[-1].timestamp
                sessions.append(current_session)

        self.metrics_backend.gauge(
            "behavioral.sessions.total",
            float(len(sessions)),
            tags={"tenant_id": tenant_id or "-"},
        )

        return sessions

    def analyze_funnel(
        self,
        funnel_id: str,
        tenant_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> FunnelAnalysisResult:
        funnel = self._funnels[funnel_id]
        events = self.event_store.list_events(
            tenant_id=tenant_id or funnel.tenant_id,
            from_time=from_time,
            to_time=to_time,
        )

        events_by_user: Dict[str, List[BehavioralEvent]] = defaultdict(list)
        for event in events:
            events_by_user[event.user_id].append(event)

        user_results: List[FunnelUserResult] = []

        for user_id, user_events in events_by_user.items():
            user_results.append(
                self._evaluate_user_funnel(
                    user_id=user_id,
                    events=sorted(user_events, key=lambda event: event.timestamp),
                    funnel=funnel,
                )
            )

        step_counts: Dict[str, int] = {step.step_id: 0 for step in funnel.steps}

        for result in user_results:
            for step_id in result.completed_steps:
                step_counts[step_id] += 1

        total_users = len(user_results)
        completed_users = sum(
            1 for result in user_results if result.status == FunnelStatus.COMPLETED
        )

        conversion_rate = completed_users / total_users if total_users else 0.0

        step_conversion_rates: Dict[str, float] = {}
        drop_off_rates: Dict[str, float] = {}

        previous_count = total_users

        for step in sorted(funnel.steps, key=lambda s: s.order):
            count = step_counts[step.step_id]
            step_conversion_rates[step.step_id] = count / total_users if total_users else 0.0
            drop_off_rates[step.step_id] = (
                1 - (count / previous_count)
                if previous_count
                else 0.0
            )
            previous_count = count

        result = FunnelAnalysisResult(
            funnel_id=funnel.funnel_id,
            name=funnel.name,
            total_users=total_users,
            completed_users=completed_users,
            conversion_rate=conversion_rate,
            step_counts=step_counts,
            step_conversion_rates=step_conversion_rates,
            drop_off_rates=drop_off_rates,
            user_results=user_results,
        )

        self._audit(
            "behavioral.funnel.analyzed",
            {
                "funnel_id": funnel_id,
                "total_users": total_users,
                "completed_users": completed_users,
                "conversion_rate": conversion_rate,
            },
        )

        return result

    def build_profile(
        self,
        user_id: str,
        tenant_id: Optional[str] = None,
    ) -> BehavioralProfile:
        events = self.event_store.list_events(
            tenant_id=tenant_id,
            user_id=user_id,
        )

        if not events:
            return BehavioralProfile(
                user_id=user_id,
                tenant_id=tenant_id,
                first_seen_at=None,
                last_seen_at=None,
                total_events=0,
                sessions_count=0,
                events_by_type={},
                engagement_score=0.0,
                engagement_level=EngagementLevel.LOW,
            )

        sessions = self.build_sessions(tenant_id=tenant_id)
        user_sessions = [session for session in sessions if session.user_id == user_id]

        events_by_type = Counter(event.event_type.value for event in events)
        score = self._calculate_engagement_score(events, user_sessions)

        return BehavioralProfile(
            user_id=user_id,
            tenant_id=tenant_id,
            first_seen_at=min(event.timestamp for event in events),
            last_seen_at=max(event.timestamp for event in events),
            total_events=len(events),
            sessions_count=len(user_sessions),
            events_by_type=dict(events_by_type),
            engagement_score=score,
            engagement_level=self._engagement_level(score),
            attributes={
                "avg_session_duration_seconds": self._avg_session_duration(user_sessions),
                "purchase_count": events_by_type.get(BehavioralEventType.PURCHASE.value, 0),
                "search_count": events_by_type.get(BehavioralEventType.SEARCH.value, 0),
            },
        )

    def segment_users(
        self,
        segment_id: str,
        tenant_id: Optional[str] = None,
    ) -> List[BehavioralProfile]:
        segment = self._segments[segment_id]
        events = self.event_store.list_events(tenant_id=tenant_id or segment.tenant_id)

        user_ids = sorted({event.user_id for event in events})
        matched: List[BehavioralProfile] = []

        for user_id in user_ids:
            profile = self.build_profile(user_id, tenant_id=tenant_id or segment.tenant_id)
            profile_dict = asdict(profile)

            rule_results = [rule.matches(profile_dict) for rule in segment.rules]

            if all(rule_results) if segment.match_all else any(rule_results):
                matched.append(profile)

        self._audit(
            "behavioral.segment.evaluated",
            {
                "segment_id": segment_id,
                "matched_users": len(matched),
            },
        )

        return matched

    def cohort_retention(
        self,
        tenant_id: Optional[str] = None,
        cohort_event: BehavioralEventType = BehavioralEventType.SIGNUP,
        return_event: BehavioralEventType = BehavioralEventType.LOGIN,
        period_days: int = 7,
        periods: int = 4,
    ) -> List[CohortResult]:
        events = self.event_store.list_events(tenant_id=tenant_id)

        signup_events = [
            event for event in events
            if event.event_type == cohort_event
        ]

        cohorts: Dict[str, List[str]] = defaultdict(list)

        for event in signup_events:
            key = event.timestamp.strftime("%Y-%m-%d")
            cohorts[key].append(event.user_id)

        results: List[CohortResult] = []

        for cohort_key, users in cohorts.items():
            cohort_start = datetime.fromisoformat(cohort_key).replace(tzinfo=timezone.utc)
            retention: Dict[str, float] = {}

            for period in range(1, periods + 1):
                start = cohort_start + timedelta(days=period_days * period)
                end = start + timedelta(days=period_days)

                active_users = {
                    event.user_id
                    for event in events
                    if event.user_id in users
                    and event.event_type == return_event
                    and start <= event.timestamp <= end
                }

                retention[f"period_{period}"] = len(active_users) / len(users) if users else 0.0

            results.append(
                CohortResult(
                    cohort_key=cohort_key,
                    users=users,
                    size=len(users),
                    retention_by_period=retention,
                )
            )

        return results

    def top_events(
        self,
        tenant_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Tuple[str, int]]:
        events = self.event_store.list_events(tenant_id=tenant_id)
        counter = Counter(event.event_type.value for event in events)
        return counter.most_common(limit)

    def export_events_json(
        self,
        tenant_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> str:
        events = self.event_store.list_events(
            tenant_id=tenant_id,
            from_time=from_time,
            to_time=to_time,
        )

        return json.dumps(
            [self._event_to_dict(event) for event in events],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _evaluate_user_funnel(
        self,
        user_id: str,
        events: List[BehavioralEvent],
        funnel: FunnelDefinition,
    ) -> FunnelUserResult:
        completed_steps: List[str] = []
        started_at: Optional[datetime] = None
        last_step_time: Optional[datetime] = None

        ordered_steps = sorted(funnel.steps, key=lambda step: step.order)
        step_index = 0

        for event in events:
            if step_index >= len(ordered_steps):
                break

            step = ordered_steps[step_index]

            if self._event_matches_step(event, step):
                if started_at is None:
                    started_at = event.timestamp

                if started_at and event.timestamp > started_at + timedelta(
                    hours=funnel.conversion_window_hours
                ):
                    break

                completed_steps.append(step.step_id)
                last_step_time = event.timestamp
                step_index += 1

        if len(completed_steps) == len(ordered_steps):
            return FunnelUserResult(
                user_id=user_id,
                funnel_id=funnel.funnel_id,
                status=FunnelStatus.COMPLETED,
                completed_steps=completed_steps,
                dropped_at_step=None,
                started_at=started_at,
                completed_at=last_step_time,
                duration_seconds=(
                    (last_step_time - started_at).total_seconds()
                    if started_at and last_step_time
                    else None
                ),
            )

        dropped_step = ordered_steps[len(completed_steps)].step_id if completed_steps else ordered_steps[0].step_id

        return FunnelUserResult(
            user_id=user_id,
            funnel_id=funnel.funnel_id,
            status=FunnelStatus.DROPPED if completed_steps else FunnelStatus.IN_PROGRESS,
            completed_steps=completed_steps,
            dropped_at_step=dropped_step,
            started_at=started_at,
            completed_at=None,
            duration_seconds=None,
        )

    @staticmethod
    def _event_matches_step(event: BehavioralEvent, step: FunnelStep) -> bool:
        if event.event_type != step.event_type:
            return False

        for key, expected_value in step.required_properties.items():
            if event.properties.get(key) != expected_value:
                return False

        return True

    def _is_session_expired(
        self,
        session: UserSession,
        event: BehavioralEvent,
    ) -> bool:
        if not session.events:
            return False

        last_event_time = session.events[-1].timestamp
        return event.timestamp - last_event_time > timedelta(
            minutes=self.session_timeout_minutes
        )

    @staticmethod
    def _calculate_engagement_score(
        events: List[BehavioralEvent],
        sessions: List[UserSession],
    ) -> float:
        event_weight = len(events) * 1.0
        session_weight = len(sessions) * 5.0
        purchase_weight = sum(
            20.0 for event in events if event.event_type == BehavioralEventType.PURCHASE
        )
        feature_weight = sum(
            3.0 for event in events if event.event_type == BehavioralEventType.FEATURE_USED
        )

        recency_weight = 0.0
        if events:
            last_seen = max(event.timestamp for event in events)
            days_since = max(0, (datetime.now(timezone.utc) - last_seen).days)
            recency_weight = max(0.0, 30.0 - days_since)

        return event_weight + session_weight + purchase_weight + feature_weight + recency_weight

    @staticmethod
    def _engagement_level(score: float) -> EngagementLevel:
        if score >= 150:
            return EngagementLevel.POWER_USER
        if score >= 75:
            return EngagementLevel.HIGH
        if score >= 25:
            return EngagementLevel.MEDIUM
        return EngagementLevel.LOW

    @staticmethod
    def _avg_session_duration(sessions: List[UserSession]) -> float:
        if not sessions:
            return 0.0
        return statistics.mean(session.duration_seconds for session in sessions)

    def _audit(self, event_type: str, details: Dict[str, Any]) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "details": details,
            }
        )

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_to_dict(event: BehavioralEvent) -> Dict[str, Any]:
        data = asdict(event)
        data["event_type"] = event.event_type.value
        data["timestamp"] = event.timestamp.isoformat()
        return data


# =============================================================================
# Default Definitions
# =============================================================================

def build_default_checkout_funnel() -> FunnelDefinition:
    return FunnelDefinition(
        funnel_id="checkout_funnel",
        name="Funil de Checkout",
        domain="commerce",
        conversion_window_hours=24,
        steps=[
            FunnelStep(
                step_id="view_product",
                name="Visualizou Produto",
                event_type=BehavioralEventType.PAGE_VIEW,
                required_properties={"page_type": "product"},
                order=1,
            ),
            FunnelStep(
                step_id="add_to_cart",
                name="Adicionou ao Carrinho",
                event_type=BehavioralEventType.ADD_TO_CART,
                order=2,
            ),
            FunnelStep(
                step_id="checkout_started",
                name="Iniciou Checkout",
                event_type=BehavioralEventType.CHECKOUT_STARTED,
                order=3,
            ),
            FunnelStep(
                step_id="purchase",
                name="Compra Finalizada",
                event_type=BehavioralEventType.PURCHASE,
                order=4,
            ),
        ],
        tags={"commerce": "true", "conversion": "true"},
    )


def build_default_segments() -> List[SegmentDefinition]:
    return [
        SegmentDefinition(
            segment_id="high_engagement_users",
            name="Usuários com Alto Engajamento",
            rules=[
                SegmentRule(
                    field_path="engagement_score",
                    operator=SegmentOperator.GTE,
                    value=75,
                )
            ],
        ),
        SegmentDefinition(
            segment_id="buyers",
            name="Compradores",
            rules=[
                SegmentRule(
                    field_path="attributes.purchase_count",
                    operator=SegmentOperator.GT,
                    value=0,
                )
            ],
        ),
        SegmentDefinition(
            segment_id="search_heavy_users",
            name="Usuários que Buscam Muito",
            rules=[
                SegmentRule(
                    field_path="attributes.search_count",
                    operator=SegmentOperator.GTE,
                    value=5,
                )
            ],
        ),
    ]


def create_default_behavioral_engine() -> BehavioralAnalyticsEngine:
    engine = BehavioralAnalyticsEngine()
    engine.register_funnel(build_default_checkout_funnel())

    for segment in build_default_segments():
        engine.register_segment(segment)

    return engine


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    now = datetime.now(timezone.utc)
    engine = create_default_behavioral_engine()

    events = [
        BehavioralEvent(
            event_id=str(uuid.uuid4()),
            user_id="user-001",
            tenant_id="tenant-default",
            domain="commerce",
            session_id="sess-001",
            event_type=BehavioralEventType.PAGE_VIEW,
            timestamp=now,
            properties={"page_type": "product", "product_id": "sku-001"},
        ),
        BehavioralEvent(
            event_id=str(uuid.uuid4()),
            user_id="user-001",
            tenant_id="tenant-default",
            domain="commerce",
            session_id="sess-001",
            event_type=BehavioralEventType.ADD_TO_CART,
            timestamp=now + timedelta(minutes=2),
            properties={"product_id": "sku-001"},
        ),
        BehavioralEvent(
            event_id=str(uuid.uuid4()),
            user_id="user-001",
            tenant_id="tenant-default",
            domain="commerce",
            session_id="sess-001",
            event_type=BehavioralEventType.CHECKOUT_STARTED,
            timestamp=now + timedelta(minutes=5),
        ),
        BehavioralEvent(
            event_id=str(uuid.uuid4()),
            user_id="user-001",
            tenant_id="tenant-default",
            domain="commerce",
            session_id="sess-001",
            event_type=BehavioralEventType.PURCHASE,
            timestamp=now + timedelta(minutes=8),
            properties={"amount": 120.50},
        ),
    ]

    engine.track_many(events)

    funnel = engine.analyze_funnel(
        funnel_id="checkout_funnel",
        tenant_id="tenant-default",
    )

    profile = engine.build_profile(
        user_id="user-001",
        tenant_id="tenant-default",
    )

    print(json.dumps(asdict(funnel), ensure_ascii=False, indent=2, default=str))
    print(json.dumps(asdict(profile), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    example_usage()