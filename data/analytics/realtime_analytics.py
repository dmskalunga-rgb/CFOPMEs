"""
data/analytics/realtime_analytics.py

Enterprise Realtime Analytics Engine.

Recursos:
- Processamento de eventos em tempo real
- Tumbling windows, sliding windows e session windows
- Agregações incrementais: count, sum, avg, min, max, distinct count
- Watermark e tolerância a eventos atrasados
- Deduplicação por event_id
- Regras de alerta em tempo real
- Estado em memória com interface extensível
- Multi-tenant
- Auditoria e métricas plugáveis
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Protocol, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class RealtimeEventType(str, Enum):
    BUSINESS = "business"
    USER_BEHAVIOR = "user_behavior"
    SYSTEM = "system"
    METRIC = "metric"
    TRANSACTION = "transaction"
    CUSTOM = "custom"


class WindowType(str, Enum):
    TUMBLING = "tumbling"
    SLIDING = "sliding"
    SESSION = "session"


class AggregationType(str, Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT_DISTINCT = "count_distinct"
    LAST = "last"


class AlertOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ProcessingStatus(str, Enum):
    ACCEPTED = "accepted"
    DROPPED_DUPLICATE = "dropped_duplicate"
    DROPPED_LATE = "dropped_late"
    ERROR = "error"


# =============================================================================
# Exceptions
# =============================================================================

class RealtimeAnalyticsError(Exception):
    """Erro base de realtime analytics."""


class RealtimeValidationError(RealtimeAnalyticsError):
    """Erro de validação."""


class StreamDefinitionError(RealtimeAnalyticsError):
    """Erro na definição de stream."""


class AlertRuleError(RealtimeAnalyticsError):
    """Erro na regra de alerta."""


# =============================================================================
# Protocols
# =============================================================================

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


class AlertSink(Protocol):
    def send(self, alert: "RealtimeAlert") -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("realtime_analytics_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


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


class LoggingAlertSink:
    def send(self, alert: "RealtimeAlert") -> None:
        logger.warning("realtime_alert=%s", json.dumps(alert.to_dict(), ensure_ascii=False, default=str))


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class RealtimeContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    source: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass(frozen=True)
class RealtimeEvent:
    event_id: str
    event_type: RealtimeEventType
    timestamp: datetime
    payload: Dict[str, Any]
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    key: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id:
            raise RealtimeValidationError("event_id é obrigatório")

        if not isinstance(self.timestamp, datetime):
            raise RealtimeValidationError("timestamp precisa ser datetime")

        if self.payload is None:
            raise RealtimeValidationError("payload não pode ser None")


@dataclass(frozen=True)
class WindowDefinition:
    window_type: WindowType
    size_seconds: int
    slide_seconds: Optional[int] = None
    session_gap_seconds: Optional[int] = None
    allowed_lateness_seconds: int = 60

    def validate(self) -> None:
        if self.size_seconds <= 0:
            raise RealtimeValidationError("size_seconds precisa ser maior que zero")

        if self.window_type == WindowType.SLIDING:
            if not self.slide_seconds or self.slide_seconds <= 0:
                raise RealtimeValidationError("Sliding window exige slide_seconds > 0")

        if self.window_type == WindowType.SESSION:
            if not self.session_gap_seconds or self.session_gap_seconds <= 0:
                raise RealtimeValidationError("Session window exige session_gap_seconds > 0")


@dataclass(frozen=True)
class AggregationDefinition:
    aggregation_id: str
    name: str
    aggregation_type: AggregationType
    field_name: Optional[str] = None
    distinct_field: Optional[str] = None
    group_by: List[str] = field(default_factory=list)
    filters: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.aggregation_id:
            raise RealtimeValidationError("aggregation_id é obrigatório")

        if self.aggregation_type in {
            AggregationType.SUM,
            AggregationType.AVG,
            AggregationType.MIN,
            AggregationType.MAX,
            AggregationType.LAST,
        } and not self.field_name:
            raise RealtimeValidationError(
                f"{self.aggregation_type.value} exige field_name"
            )

        if self.aggregation_type == AggregationType.COUNT_DISTINCT and not self.distinct_field:
            raise RealtimeValidationError("COUNT_DISTINCT exige distinct_field")


@dataclass(frozen=True)
class StreamDefinition:
    stream_id: str
    name: str
    window: WindowDefinition
    aggregations: List[AggregationDefinition]
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    event_type: Optional[RealtimeEventType] = None
    enabled: bool = True
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.stream_id:
            raise StreamDefinitionError("stream_id é obrigatório")

        if not self.aggregations:
            raise StreamDefinitionError("Stream precisa de pelo menos uma agregação")

        self.window.validate()

        for aggregation in self.aggregations:
            aggregation.validate()


@dataclass
class WindowKey:
    stream_id: str
    window_start: datetime
    window_end: datetime
    group_key: Tuple[Any, ...] = field(default_factory=tuple)

    def as_string(self) -> str:
        raw = json.dumps(
            {
                "stream_id": self.stream_id,
                "window_start": self.window_start.isoformat(),
                "window_end": self.window_end.isoformat(),
                "group_key": self.group_key,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class AggregationState:
    count: int = 0
    sum_value: float = 0.0
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    last_value: Any = None
    distinct_values: Set[Any] = field(default_factory=set)

    def update(self, value: Any, aggregation: AggregationDefinition) -> None:
        self.count += 1

        if aggregation.aggregation_type == AggregationType.COUNT:
            return

        if aggregation.aggregation_type == AggregationType.COUNT_DISTINCT:
            self.distinct_values.add(value)
            return

        if aggregation.aggregation_type == AggregationType.LAST:
            self.last_value = value
            return

        numeric = self._to_float(value)

        if numeric is None:
            return

        self.sum_value += numeric

        if self.min_value is None or numeric < self.min_value:
            self.min_value = numeric

        if self.max_value is None or numeric > self.max_value:
            self.max_value = numeric

    def value(self, aggregation: AggregationDefinition) -> Any:
        if aggregation.aggregation_type == AggregationType.COUNT:
            return self.count

        if aggregation.aggregation_type == AggregationType.SUM:
            return self.sum_value

        if aggregation.aggregation_type == AggregationType.AVG:
            return self.sum_value / self.count if self.count else 0.0

        if aggregation.aggregation_type == AggregationType.MIN:
            return self.min_value

        if aggregation.aggregation_type == AggregationType.MAX:
            return self.max_value

        if aggregation.aggregation_type == AggregationType.COUNT_DISTINCT:
            return len(self.distinct_values)

        if aggregation.aggregation_type == AggregationType.LAST:
            return self.last_value

        return None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        except Exception:
            return None


@dataclass
class RealtimeAggregateResult:
    stream_id: str
    aggregation_id: str
    window_start: datetime
    window_end: datetime
    value: Any
    group_values: Dict[str, Any]
    event_count: int
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["window_start"] = self.window_start.isoformat()
        data["window_end"] = self.window_end.isoformat()
        data["computed_at"] = self.computed_at.isoformat()
        return data


@dataclass(frozen=True)
class AlertRule:
    rule_id: str
    stream_id: str
    aggregation_id: str
    operator: AlertOperator
    threshold: float
    severity: AlertSeverity
    enabled: bool = True
    cooldown_seconds: int = 300
    message_template: str = "Alerta em {aggregation_id}: valor={value}, threshold={threshold}"
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise AlertRuleError("rule_id é obrigatório")

        if not self.stream_id:
            raise AlertRuleError("stream_id é obrigatório")

        if not self.aggregation_id:
            raise AlertRuleError("aggregation_id é obrigatório")


@dataclass
class RealtimeAlert:
    alert_id: str
    rule_id: str
    stream_id: str
    aggregation_id: str
    severity: AlertSeverity
    value: float
    threshold: float
    message: str
    window_start: datetime
    window_end: datetime
    group_values: Dict[str, Any]
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        data["window_start"] = self.window_start.isoformat()
        data["window_end"] = self.window_end.isoformat()
        data["triggered_at"] = self.triggered_at.isoformat()
        return data


@dataclass
class ProcessingResult:
    event_id: str
    status: ProcessingStatus
    processed_at: datetime
    updated_windows: int = 0
    emitted_results: List[RealtimeAggregateResult] = field(default_factory=list)
    alerts: List[RealtimeAlert] = field(default_factory=list)
    error: Optional[str] = None


# =============================================================================
# State Store
# =============================================================================

class InMemoryRealtimeStateStore:
    def __init__(self, max_dedup_events: int = 100_000) -> None:
        self._states: Dict[str, Dict[str, AggregationState]] = defaultdict(dict)
        self._window_meta: Dict[str, WindowKey] = {}
        self._seen_events: Set[str] = set()
        self._seen_order: Deque[str] = deque()
        self._alert_last_triggered: Dict[str, datetime] = {}
        self._max_dedup_events = max_dedup_events
        self._lock = threading.RLock()

    def is_duplicate(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._seen_events

    def mark_seen(self, event_id: str) -> None:
        with self._lock:
            if event_id in self._seen_events:
                return

            self._seen_events.add(event_id)
            self._seen_order.append(event_id)

            while len(self._seen_order) > self._max_dedup_events:
                old = self._seen_order.popleft()
                self._seen_events.discard(old)

    def update_state(
        self,
        window_key: WindowKey,
        aggregation: AggregationDefinition,
        value: Any,
    ) -> AggregationState:
        with self._lock:
            key = window_key.as_string()
            self._window_meta[key] = window_key

            if aggregation.aggregation_id not in self._states[key]:
                self._states[key][aggregation.aggregation_id] = AggregationState()

            state = self._states[key][aggregation.aggregation_id]
            state.update(value, aggregation)
            return state

    def list_results(
        self,
        stream: StreamDefinition,
        only_closed_before: Optional[datetime] = None,
    ) -> List[RealtimeAggregateResult]:
        with self._lock:
            results: List[RealtimeAggregateResult] = []

            for key, aggregation_states in self._states.items():
                window_key = self._window_meta[key]

                if window_key.stream_id != stream.stream_id:
                    continue

                if only_closed_before and window_key.window_end > only_closed_before:
                    continue

                for aggregation in stream.aggregations:
                    state = aggregation_states.get(aggregation.aggregation_id)
                    if not state:
                        continue

                    group_values = {
                        field: window_key.group_key[index]
                        for index, field in enumerate(aggregation.group_by)
                    }

                    results.append(
                        RealtimeAggregateResult(
                            stream_id=stream.stream_id,
                            aggregation_id=aggregation.aggregation_id,
                            window_start=window_key.window_start,
                            window_end=window_key.window_end,
                            value=state.value(aggregation),
                            group_values=group_values,
                            event_count=state.count,
                        )
                    )

            return results

    def get_alert_last_triggered(self, alert_identity: str) -> Optional[datetime]:
        with self._lock:
            return self._alert_last_triggered.get(alert_identity)

    def set_alert_last_triggered(self, alert_identity: str, timestamp: datetime) -> None:
        with self._lock:
            self._alert_last_triggered[alert_identity] = timestamp

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._window_meta.clear()
            self._seen_events.clear()
            self._seen_order.clear()
            self._alert_last_triggered.clear()


# =============================================================================
# Repository
# =============================================================================

class RealtimeStreamRepository:
    def __init__(self, streams: Optional[List[StreamDefinition]] = None) -> None:
        self._streams: Dict[str, StreamDefinition] = {}

        for stream in streams or []:
            self.save(stream)

    def save(self, stream: StreamDefinition) -> None:
        stream.validate()
        self._streams[stream.stream_id] = stream

    def get(self, stream_id: str) -> StreamDefinition:
        if stream_id not in self._streams:
            raise StreamDefinitionError(f"Stream não encontrada: {stream_id}")
        return self._streams[stream_id]

    def list_for_event(self, event: RealtimeEvent) -> List[StreamDefinition]:
        streams = [stream for stream in self._streams.values() if stream.enabled]

        matched: List[StreamDefinition] = []

        for stream in streams:
            if stream.tenant_id and event.tenant_id and stream.tenant_id != event.tenant_id:
                continue

            if stream.domain and event.domain and stream.domain != event.domain:
                continue

            if stream.event_type and stream.event_type != event.event_type:
                continue

            matched.append(stream)

        return matched

    def list_all(self) -> List[StreamDefinition]:
        return list(self._streams.values())


class AlertRuleRepository:
    def __init__(self, rules: Optional[List[AlertRule]] = None) -> None:
        self._rules: Dict[str, AlertRule] = {}

        for rule in rules or []:
            self.save(rule)

    def save(self, rule: AlertRule) -> None:
        rule.validate()
        self._rules[rule.rule_id] = rule

    def list_for_result(self, result: RealtimeAggregateResult) -> List[AlertRule]:
        return [
            rule for rule in self._rules.values()
            if rule.enabled
            and rule.stream_id == result.stream_id
            and rule.aggregation_id == result.aggregation_id
        ]


# =============================================================================
# Windowing
# =============================================================================

class WindowAssigner:
    @staticmethod
    def assign_windows(
        event: RealtimeEvent,
        stream: StreamDefinition,
        aggregation: AggregationDefinition,
    ) -> List[WindowKey]:
        window = stream.window

        if window.window_type == WindowType.TUMBLING:
            return [
                WindowAssigner._tumbling_window(
                    event.timestamp,
                    stream.stream_id,
                    window.size_seconds,
                    aggregation,
                    event,
                )
            ]

        if window.window_type == WindowType.SLIDING:
            return WindowAssigner._sliding_windows(
                event.timestamp,
                stream.stream_id,
                window.size_seconds,
                window.slide_seconds or window.size_seconds,
                aggregation,
                event,
            )

        if window.window_type == WindowType.SESSION:
            return [
                WindowAssigner._session_window(
                    event.timestamp,
                    stream.stream_id,
                    window.size_seconds,
                    aggregation,
                    event,
                )
            ]

        raise RealtimeValidationError(f"Window type não suportado: {window.window_type}")

    @staticmethod
    def _tumbling_window(
        timestamp: datetime,
        stream_id: str,
        size_seconds: int,
        aggregation: AggregationDefinition,
        event: RealtimeEvent,
    ) -> WindowKey:
        epoch = int(timestamp.timestamp())
        start_epoch = epoch - (epoch % size_seconds)

        start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
        end = start + timedelta(seconds=size_seconds)

        return WindowKey(
            stream_id=stream_id,
            window_start=start,
            window_end=end,
            group_key=WindowAssigner._group_key(event, aggregation.group_by),
        )

    @staticmethod
    def _sliding_windows(
        timestamp: datetime,
        stream_id: str,
        size_seconds: int,
        slide_seconds: int,
        aggregation: AggregationDefinition,
        event: RealtimeEvent,
    ) -> List[WindowKey]:
        epoch = int(timestamp.timestamp())
        last_start = epoch - (epoch % slide_seconds)

        windows: List[WindowKey] = []

        for start_epoch in range(
            last_start - size_seconds + slide_seconds,
            last_start + 1,
            slide_seconds,
        ):
            start = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
            end = start + timedelta(seconds=size_seconds)

            if start <= timestamp < end:
                windows.append(
                    WindowKey(
                        stream_id=stream_id,
                        window_start=start,
                        window_end=end,
                        group_key=WindowAssigner._group_key(event, aggregation.group_by),
                    )
                )

        return windows

    @staticmethod
    def _session_window(
        timestamp: datetime,
        stream_id: str,
        size_seconds: int,
        aggregation: AggregationDefinition,
        event: RealtimeEvent,
    ) -> WindowKey:
        # Implementação simplificada: cria bucket por tamanho.
        # Para produção, conectar com state store que expanda sessão por chave.
        return WindowAssigner._tumbling_window(
            timestamp,
            stream_id,
            size_seconds,
            aggregation,
            event,
        )

    @staticmethod
    def _group_key(event: RealtimeEvent, group_by: List[str]) -> Tuple[Any, ...]:
        values: List[Any] = []

        for field_name in group_by:
            values.append(
                event.payload.get(field_name)
                if field_name in event.payload
                else event.attributes.get(field_name)
            )

        return tuple(values)


# =============================================================================
# Alert Evaluation
# =============================================================================

class AlertEvaluator:
    @staticmethod
    def evaluate(rule: AlertRule, result: RealtimeAggregateResult) -> Optional[RealtimeAlert]:
        numeric = AlertEvaluator._to_float(result.value)

        if numeric is None:
            return None

        matched = False

        if rule.operator == AlertOperator.GT:
            matched = numeric > rule.threshold
        elif rule.operator == AlertOperator.GTE:
            matched = numeric >= rule.threshold
        elif rule.operator == AlertOperator.LT:
            matched = numeric < rule.threshold
        elif rule.operator == AlertOperator.LTE:
            matched = numeric <= rule.threshold
        elif rule.operator == AlertOperator.EQ:
            matched = numeric == rule.threshold
        elif rule.operator == AlertOperator.NE:
            matched = numeric != rule.threshold

        if not matched:
            return None

        message = rule.message_template.format(
            aggregation_id=result.aggregation_id,
            stream_id=result.stream_id,
            value=numeric,
            threshold=rule.threshold,
            window_start=result.window_start.isoformat(),
            window_end=result.window_end.isoformat(),
        )

        return RealtimeAlert(
            alert_id=str(uuid.uuid4()),
            rule_id=rule.rule_id,
            stream_id=result.stream_id,
            aggregation_id=result.aggregation_id,
            severity=rule.severity,
            value=numeric,
            threshold=rule.threshold,
            message=message,
            window_start=result.window_start,
            window_end=result.window_end,
            group_values=result.group_values,
            metadata={"tags": rule.tags},
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
            return numeric if math.isfinite(numeric) else None
        except Exception:
            return None


# =============================================================================
# Engine
# =============================================================================

class RealtimeAnalyticsEngine:
    def __init__(
        self,
        stream_repository: RealtimeStreamRepository,
        alert_rule_repository: Optional[AlertRuleRepository] = None,
        state_store: Optional[InMemoryRealtimeStateStore] = None,
        alert_sink: Optional[AlertSink] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.stream_repository = stream_repository
        self.alert_rule_repository = alert_rule_repository or AlertRuleRepository()
        self.state_store = state_store or InMemoryRealtimeStateStore()
        self.alert_sink = alert_sink or LoggingAlertSink()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self._max_event_time: Optional[datetime] = None
        self._lock = threading.RLock()

    def process_event(
        self,
        event: RealtimeEvent,
        context: Optional[RealtimeContext] = None,
    ) -> ProcessingResult:
        context = context or RealtimeContext(
            tenant_id=event.tenant_id,
            domain=event.domain,
        )

        processed_at = datetime.now(timezone.utc)

        try:
            event.validate()

            if self.state_store.is_duplicate(event.event_id):
                self.metrics_backend.increment("realtime.events.duplicate")
                return ProcessingResult(
                    event_id=event.event_id,
                    status=ProcessingStatus.DROPPED_DUPLICATE,
                    processed_at=processed_at,
                )

            with self._lock:
                if self._max_event_time is None or event.timestamp > self._max_event_time:
                    self._max_event_time = event.timestamp

            streams = self.stream_repository.list_for_event(event)

            if self._is_late_for_all_streams(event, streams):
                self.metrics_backend.increment("realtime.events.late")
                return ProcessingResult(
                    event_id=event.event_id,
                    status=ProcessingStatus.DROPPED_LATE,
                    processed_at=processed_at,
                )

            emitted_results: List[RealtimeAggregateResult] = []
            alerts: List[RealtimeAlert] = []
            updated_windows = 0

            for stream in streams:
                for aggregation in stream.aggregations:
                    if not self._event_matches_filters(event, aggregation.filters):
                        continue

                    aggregation_value = self._extract_aggregation_value(event, aggregation)

                    window_keys = WindowAssigner.assign_windows(event, stream, aggregation)

                    for window_key in window_keys:
                        state = self.state_store.update_state(
                            window_key=window_key,
                            aggregation=aggregation,
                            value=aggregation_value,
                        )
                        updated_windows += 1

                        result = RealtimeAggregateResult(
                            stream_id=stream.stream_id,
                            aggregation_id=aggregation.aggregation_id,
                            window_start=window_key.window_start,
                            window_end=window_key.window_end,
                            value=state.value(aggregation),
                            group_values={
                                field: window_key.group_key[index]
                                for index, field in enumerate(aggregation.group_by)
                            },
                            event_count=state.count,
                        )

                        emitted_results.append(result)
                        alerts.extend(self._evaluate_alerts(result))

            self.state_store.mark_seen(event.event_id)

            self._audit(
                "realtime.event.processed",
                context,
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "updated_windows": updated_windows,
                    "emitted_results": len(emitted_results),
                    "alerts": len(alerts),
                },
            )

            self.metrics_backend.increment(
                "realtime.events.processed.total",
                tags={
                    "event_type": event.event_type.value,
                    "tenant_id": event.tenant_id or "-",
                    "domain": event.domain or "-",
                },
            )

            return ProcessingResult(
                event_id=event.event_id,
                status=ProcessingStatus.ACCEPTED,
                processed_at=processed_at,
                updated_windows=updated_windows,
                emitted_results=emitted_results,
                alerts=alerts,
            )

        except Exception as exc:
            logger.exception("Erro ao processar evento realtime")

            self._audit(
                "realtime.event.error",
                context,
                {
                    "event_id": event.event_id,
                    "error": str(exc),
                },
            )

            self.metrics_backend.increment("realtime.events.error.total")

            return ProcessingResult(
                event_id=event.event_id,
                status=ProcessingStatus.ERROR,
                processed_at=processed_at,
                error=str(exc),
            )

    def process_batch(
        self,
        events: Iterable[RealtimeEvent],
        context: Optional[RealtimeContext] = None,
    ) -> List[ProcessingResult]:
        return [
            self.process_event(event, context=context)
            for event in events
        ]

    def current_results(
        self,
        stream_id: str,
        closed_only: bool = False,
    ) -> List[RealtimeAggregateResult]:
        stream = self.stream_repository.get(stream_id)

        watermark = self.current_watermark(stream) if closed_only else None

        return self.state_store.list_results(
            stream=stream,
            only_closed_before=watermark,
        )

    def current_watermark(self, stream: StreamDefinition) -> Optional[datetime]:
        if self._max_event_time is None:
            return None

        return self._max_event_time - timedelta(
            seconds=stream.window.allowed_lateness_seconds
        )

    def export_results_json(
        self,
        stream_id: str,
        closed_only: bool = False,
    ) -> str:
        return json.dumps(
            [result.to_dict() for result in self.current_results(stream_id, closed_only)],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def clear_state(self) -> None:
        self.state_store.clear()

    def _evaluate_alerts(
        self,
        result: RealtimeAggregateResult,
    ) -> List[RealtimeAlert]:
        alerts: List[RealtimeAlert] = []

        rules = self.alert_rule_repository.list_for_result(result)

        for rule in rules:
            alert = AlertEvaluator.evaluate(rule, result)
            if not alert:
                continue

            identity = self._alert_identity(rule, result)
            now = datetime.now(timezone.utc)
            last_triggered = self.state_store.get_alert_last_triggered(identity)

            if (
                last_triggered
                and now - last_triggered < timedelta(seconds=rule.cooldown_seconds)
            ):
                continue

            self.state_store.set_alert_last_triggered(identity, now)
            self.alert_sink.send(alert)
            alerts.append(alert)

            self.metrics_backend.increment(
                "realtime.alerts.triggered.total",
                tags={
                    "rule_id": rule.rule_id,
                    "stream_id": rule.stream_id,
                    "aggregation_id": rule.aggregation_id,
                    "severity": rule.severity.value,
                },
            )

        return alerts

    def _is_late_for_all_streams(
        self,
        event: RealtimeEvent,
        streams: List[StreamDefinition],
    ) -> bool:
        if self._max_event_time is None or not streams:
            return False

        for stream in streams:
            watermark = self.current_watermark(stream)
            if watermark is None:
                return False

            if event.timestamp >= watermark:
                return False

        return True

    @staticmethod
    def _event_matches_filters(
        event: RealtimeEvent,
        filters: Dict[str, Any],
    ) -> bool:
        for key, expected in filters.items():
            actual = event.payload.get(key, event.attributes.get(key))

            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False

        return True

    @staticmethod
    def _extract_aggregation_value(
        event: RealtimeEvent,
        aggregation: AggregationDefinition,
    ) -> Any:
        if aggregation.aggregation_type == AggregationType.COUNT:
            return 1

        if aggregation.aggregation_type == AggregationType.COUNT_DISTINCT:
            return event.payload.get(
                aggregation.distinct_field or "",
                event.attributes.get(aggregation.distinct_field or ""),
            )

        return event.payload.get(
            aggregation.field_name or "",
            event.attributes.get(aggregation.field_name or ""),
        )

    @staticmethod
    def _alert_identity(rule: AlertRule, result: RealtimeAggregateResult) -> str:
        raw = json.dumps(
            {
                "rule_id": rule.rule_id,
                "stream_id": result.stream_id,
                "aggregation_id": result.aggregation_id,
                "group_values": result.group_values,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _audit(
        self,
        event_type: str,
        context: RealtimeContext,
        details: Dict[str, Any],
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "environment": context.environment,
                "source": context.source,
                "correlation_id": context.correlation_id,
                "details": details,
            }
        )


# =============================================================================
# Default Streams and Rules
# =============================================================================

def build_default_realtime_streams() -> List[StreamDefinition]:
    return [
        StreamDefinition(
            stream_id="sales_realtime_1m",
            name="Vendas em Tempo Real - 1 Minuto",
            domain="sales",
            event_type=RealtimeEventType.TRANSACTION,
            window=WindowDefinition(
                window_type=WindowType.TUMBLING,
                size_seconds=60,
                allowed_lateness_seconds=30,
            ),
            aggregations=[
                AggregationDefinition(
                    aggregation_id="sales_count",
                    name="Quantidade de Vendas",
                    aggregation_type=AggregationType.COUNT,
                    group_by=["store_id"],
                ),
                AggregationDefinition(
                    aggregation_id="gross_revenue_sum",
                    name="Receita Bruta",
                    aggregation_type=AggregationType.SUM,
                    field_name="gross_amount",
                    group_by=["store_id"],
                ),
                AggregationDefinition(
                    aggregation_id="avg_ticket",
                    name="Ticket Médio",
                    aggregation_type=AggregationType.AVG,
                    field_name="gross_amount",
                    group_by=["store_id"],
                ),
            ],
            tags={"kpi": "true", "sales": "true"},
        ),
        StreamDefinition(
            stream_id="system_errors_5m",
            name="Erros de Sistema - 5 Minutos",
            domain="platform",
            event_type=RealtimeEventType.SYSTEM,
            window=WindowDefinition(
                window_type=WindowType.SLIDING,
                size_seconds=300,
                slide_seconds=60,
                allowed_lateness_seconds=60,
            ),
            aggregations=[
                AggregationDefinition(
                    aggregation_id="error_count",
                    name="Quantidade de Erros",
                    aggregation_type=AggregationType.COUNT,
                    group_by=["service"],
                    filters={"level": "error"},
                )
            ],
            tags={"sre": "true", "monitoring": "true"},
        ),
    ]


def build_default_alert_rules() -> List[AlertRule]:
    return [
        AlertRule(
            rule_id="high_sales_spike",
            stream_id="sales_realtime_1m",
            aggregation_id="gross_revenue_sum",
            operator=AlertOperator.GTE,
            threshold=10000.0,
            severity=AlertSeverity.INFO,
            cooldown_seconds=300,
            message_template=(
                "Pico de receita em tempo real: {value} "
                "na janela {window_start} - {window_end}"
            ),
            tags={"business": "true"},
        ),
        AlertRule(
            rule_id="system_error_spike",
            stream_id="system_errors_5m",
            aggregation_id="error_count",
            operator=AlertOperator.GTE,
            threshold=10.0,
            severity=AlertSeverity.CRITICAL,
            cooldown_seconds=120,
            message_template=(
                "Alto volume de erros: {value} erros "
                "na janela {window_start} - {window_end}"
            ),
            tags={"sre": "true"},
        ),
    ]


def create_default_realtime_engine() -> RealtimeAnalyticsEngine:
    return RealtimeAnalyticsEngine(
        stream_repository=RealtimeStreamRepository(build_default_realtime_streams()),
        alert_rule_repository=AlertRuleRepository(build_default_alert_rules()),
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    now = datetime.now(timezone.utc)
    engine = create_default_realtime_engine()

    events = [
        RealtimeEvent(
            event_id=str(uuid.uuid4()),
            event_type=RealtimeEventType.TRANSACTION,
            timestamp=now,
            tenant_id="tenant-default",
            domain="sales",
            payload={
                "store_id": "store-a",
                "gross_amount": 120.50,
                "order_id": "o1",
            },
        ),
        RealtimeEvent(
            event_id=str(uuid.uuid4()),
            event_type=RealtimeEventType.TRANSACTION,
            timestamp=now + timedelta(seconds=10),
            tenant_id="tenant-default",
            domain="sales",
            payload={
                "store_id": "store-a",
                "gross_amount": 350.75,
                "order_id": "o2",
            },
        ),
        RealtimeEvent(
            event_id=str(uuid.uuid4()),
            event_type=RealtimeEventType.SYSTEM,
            timestamp=now + timedelta(seconds=20),
            tenant_id="tenant-default",
            domain="platform",
            payload={
                "service": "checkout-api",
                "level": "error",
                "message": "Timeout",
            },
        ),
    ]

    results = engine.process_batch(
        events,
        context=RealtimeContext(
            tenant_id="tenant-default",
            source="example",
            correlation_id="corr-realtime-001",
        ),
    )

    print(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2, default=str))
    print(engine.export_results_json("sales_realtime_1m"))


if __name__ == "__main__":
    example_usage()