"""
data/security/intrusion_detection.py

Enterprise-grade Intrusion Detection System (IDS) module for Python services,
data platforms, APIs, workers, pipelines and security monitoring layers.

Core capabilities:
- Rule-based intrusion detection
- Behavioral/anomaly scoring
- Sliding-window event correlation
- Brute-force, credential-stuffing and privilege escalation detection
- Suspicious IP/user-agent/activity fingerprinting
- Severity classification and risk scoring
- Alert generation and deduplication
- Pluggable event repository and alert sinks
- In-memory implementations for local development/tests
- Audit-friendly structured outputs
- Framework-agnostic integration

This module is intentionally dependency-light and does not require a SIEM,
database, Kafka, cloud provider or web framework. In production, replace the
in-memory stores with durable repositories and connect alert sinks to your SIEM,
SOC tooling, Slack, PagerDuty, email, Kafka or cloud-native monitoring platform.
"""

from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import json
import logging
import math
import re
import statistics
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Predicate = Callable[["SecurityEvent"], bool]


# =============================================================================
# Exceptions
# =============================================================================


class IntrusionDetectionError(Exception):
    """Base IDS exception."""


class RuleValidationError(IntrusionDetectionError):
    """Raised when a detection rule is invalid."""


class EventValidationError(IntrusionDetectionError):
    """Raised when a security event is invalid."""


class RepositoryError(IntrusionDetectionError):
    """Raised when an event/alert repository fails."""


class AlertSinkError(IntrusionDetectionError):
    """Raised when an alert sink fails."""


# =============================================================================
# Enums/config
# =============================================================================


class EventCategory(str, Enum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    NETWORK = "network"
    API = "api"
    DATA_ACCESS = "data_access"
    ADMIN_ACTIVITY = "admin_activity"
    SYSTEM = "system"
    MALWARE = "malware"
    CLOUD = "cloud"
    CUSTOM = "custom"


class EventOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    ERROR = "error"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class DetectionType(str, Enum):
    RULE = "rule"
    CORRELATION = "correlation"
    ANOMALY = "anomaly"
    BASELINE = "baseline"
    THREAT_INTEL = "threat_intel"


class RuleAction(str, Enum):
    ALERT = "alert"
    SUPPRESS = "suppress"
    SCORE_ONLY = "score_only"


@dataclass(frozen=True)
class IDSConfig:
    """Runtime configuration for intrusion detection."""

    enabled: bool = True
    fail_open: bool = True
    enable_alert_deduplication: bool = True
    alert_dedup_window_seconds: int = 300
    event_retention_seconds: int = 60 * 60 * 24
    max_events_in_memory: int = 250_000
    default_correlation_window_seconds: int = 300
    baseline_min_samples: int = 20
    anomaly_zscore_threshold: float = 3.0
    high_risk_score_threshold: float = 70.0
    critical_risk_score_threshold: float = 90.0
    redact_sensitive_fields: bool = True
    trusted_networks: Tuple[str, ...] = ()
    blocked_networks: Tuple[str, ...] = ()
    suspicious_user_agents: Tuple[str, ...] = (
        "sqlmap",
        "nikto",
        "nmap",
        "masscan",
        "acunetix",
        "nessus",
        "wpscan",
        "hydra",
        "dirbuster",
    )


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class SecurityEvent:
    """Normalized security event consumed by the IDS engine."""

    event_id: str
    category: EventCategory
    event_type: str
    outcome: EventOutcome = EventOutcome.UNKNOWN
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_ip: Optional[str] = None
    destination_ip: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    user_agent: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    status_code: Optional[int] = None
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None
    latency_ms: Optional[float] = None
    risk_score: float = 0.0
    tags: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id:
            raise EventValidationError("event_id is required.")
        if not self.event_type:
            raise EventValidationError("event_type is required.")
        if self.source_ip:
            try:
                ipaddress.ip_address(self.source_ip)
            except ValueError as exc:
                raise EventValidationError(f"Invalid source_ip: {self.source_ip}") from exc
        if self.destination_ip:
            try:
                ipaddress.ip_address(self.destination_ip)
            except ValueError as exc:
                raise EventValidationError(f"Invalid destination_ip: {self.destination_ip}") from exc

    def fingerprint(self) -> str:
        payload = {
            "category": self.category.value,
            "event_type": self.event_type,
            "source_ip": self.source_ip,
            "user_id": self.user_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "resource": self.resource,
            "action": self.action,
            "outcome": self.outcome.value,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "event_id": self.event_id,
            "category": self.category.value,
            "event_type": self.event_type,
            "outcome": self.outcome.value,
            "timestamp": self.timestamp.isoformat(),
            "source_ip": self.source_ip,
            "destination_ip": self.destination_ip,
            "user_id": self.user_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "user_agent": self.user_agent,
            "resource": self.resource,
            "action": self.action,
            "status_code": self.status_code,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "latency_ms": self.latency_ms,
            "risk_score": self.risk_score,
            "tags": list(self.tags),
            "attributes": dict(self.attributes),
        }
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class DetectionRule:
    """Rule-based detection definition."""

    rule_id: str
    name: str
    description: str
    severity: Severity
    detection_type: DetectionType = DetectionType.RULE
    action: RuleAction = RuleAction.ALERT
    enabled: bool = True
    priority: int = 100
    categories: Tuple[EventCategory, ...] = ()
    event_types: Tuple[str, ...] = ()
    outcomes: Tuple[EventOutcome, ...] = ()
    required_tags: Tuple[str, ...] = ()
    source_ip_cidrs: Tuple[str, ...] = ()
    user_regex: Optional[str] = None
    resource_regex: Optional[str] = None
    user_agent_regex: Optional[str] = None
    attribute_equals: JsonDict = field(default_factory=dict)
    min_event_risk_score: Optional[float] = None
    risk_score_delta: float = 0.0
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise RuleValidationError("rule_id is required.")
        if not self.name:
            raise RuleValidationError("name is required.")
        if self.user_regex:
            re.compile(self.user_regex)
        if self.resource_regex:
            re.compile(self.resource_regex)
        if self.user_agent_regex:
            re.compile(self.user_agent_regex)
        for cidr in self.source_ip_cidrs:
            ipaddress.ip_network(cidr, strict=False)

    def matches(self, event: SecurityEvent) -> bool:
        if not self.enabled:
            return False
        if self.categories and event.category not in self.categories:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.outcomes and event.outcome not in self.outcomes:
            return False
        if self.required_tags and not set(self.required_tags).issubset(set(event.tags)):
            return False
        if self.source_ip_cidrs and not _ip_in_any_network(event.source_ip, self.source_ip_cidrs):
            return False
        if self.user_regex and not re.search(self.user_regex, event.username or event.user_id or ""):
            return False
        if self.resource_regex and not re.search(self.resource_regex, event.resource or ""):
            return False
        if self.user_agent_regex and not re.search(self.user_agent_regex, event.user_agent or "", flags=re.IGNORECASE):
            return False
        if self.min_event_risk_score is not None and event.risk_score < self.min_event_risk_score:
            return False
        for key, expected in self.attribute_equals.items():
            if resolve_path(event.attributes, key) != expected:
                return False
        return True


@dataclass(frozen=True)
class CorrelationRule:
    """Sliding-window correlation rule."""

    rule_id: str
    name: str
    description: str
    severity: Severity
    event_type: Optional[str] = None
    category: Optional[EventCategory] = None
    outcome: Optional[EventOutcome] = None
    group_by: Tuple[str, ...] = ("source_ip",)
    threshold: int = 5
    window_seconds: int = 300
    action: RuleAction = RuleAction.ALERT
    enabled: bool = True
    risk_score_delta: float = 25.0
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise RuleValidationError("correlation rule_id is required.")
        if self.threshold <= 0:
            raise RuleValidationError("correlation threshold must be positive.")
        if self.window_seconds <= 0:
            raise RuleValidationError("correlation window_seconds must be positive.")

    def event_matches(self, event: SecurityEvent) -> bool:
        if not self.enabled:
            return False
        if self.event_type and event.event_type != self.event_type:
            return False
        if self.category and event.category != self.category:
            return False
        if self.outcome and event.outcome != self.outcome:
            return False
        return True


@dataclass(frozen=True)
class DetectionFinding:
    """Single detection finding before final alert generation."""

    finding_id: str
    detection_type: DetectionType
    rule_id: str
    rule_name: str
    severity: Severity
    risk_score: float
    reason: str
    event_ids: Tuple[str, ...]
    action: RuleAction = RuleAction.ALERT
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "finding_id": self.finding_id,
            "detection_type": self.detection_type.value,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "risk_score": self.risk_score,
            "reason": self.reason,
            "event_ids": list(self.event_ids),
            "action": self.action.value,
            "mitre_tactics": list(self.mitre_tactics),
            "mitre_techniques": list(self.mitre_techniques),
            "metadata": redact_sensitive(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class IntrusionAlert:
    """Alert generated by the IDS engine."""

    alert_id: str
    title: str
    severity: Severity
    risk_score: float
    status: AlertStatus
    findings: Tuple[DetectionFinding, ...]
    primary_event: SecurityEvent
    first_seen: datetime
    last_seen: datetime
    dedup_key: str
    tenant_id: Optional[str] = None
    source_ip: Optional[str] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    correlation_id: Optional[str] = None
    recommendations: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "alert_id": self.alert_id,
            "title": self.title,
            "severity": self.severity.value,
            "risk_score": self.risk_score,
            "status": self.status.value,
            "findings": [finding.to_dict() for finding in self.findings],
            "primary_event": self.primary_event.to_dict(redact=redact),
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "dedup_key": self.dedup_key,
            "tenant_id": self.tenant_id,
            "source_ip": self.source_ip,
            "user_id": self.user_id,
            "username": self.username,
            "correlation_id": self.correlation_id,
            "recommendations": list(self.recommendations),
            "metadata": dict(self.metadata),
        }
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class DetectionResult:
    """IDS evaluation result for one event."""

    event_id: str
    findings: Tuple[DetectionFinding, ...]
    alerts: Tuple[IntrusionAlert, ...]
    risk_score: float
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    diagnostics: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "event_id": self.event_id,
            "findings": [finding.to_dict() for finding in self.findings],
            "alerts": [alert.to_dict(redact=redact) for alert in self.alerts],
            "risk_score": self.risk_score,
            "evaluated_at": self.evaluated_at.isoformat(),
            "diagnostics": redact_sensitive(self.diagnostics) if redact else dict(self.diagnostics),
        }


# =============================================================================
# Repositories and sinks
# =============================================================================


class SecurityEventRepository(ABC):
    """Security event repository abstraction."""

    @abstractmethod
    def append(self, event: SecurityEvent) -> None:
        """Store a security event."""

    @abstractmethod
    def query_since(self, since: datetime, filters: Optional[Mapping[str, Any]] = None) -> Sequence[SecurityEvent]:
        """Return events since a timestamp matching optional filters."""

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """Delete/prune events before timestamp and return count."""


class AlertRepository(ABC):
    """Intrusion alert repository abstraction."""

    @abstractmethod
    def upsert(self, alert: IntrusionAlert) -> None:
        """Store or update an alert."""

    @abstractmethod
    def find_recent_by_dedup_key(self, dedup_key: str, since: datetime) -> Optional[IntrusionAlert]:
        """Find recent alert by deduplication key."""

    @abstractmethod
    def list_open(self) -> Sequence[IntrusionAlert]:
        """Return open alerts."""


class AlertSink(ABC):
    """Alert sink abstraction."""

    @abstractmethod
    def emit(self, alert: IntrusionAlert) -> None:
        """Emit an alert to an external system."""


class InMemorySecurityEventRepository(SecurityEventRepository):
    """Thread-safe in-memory event repository."""

    def __init__(self, max_events: int = 250_000) -> None:
        self.max_events = max(1, max_events)
        self._events: Deque[SecurityEvent] = deque(maxlen=self.max_events)
        self._lock = threading.RLock()

    def append(self, event: SecurityEvent) -> None:
        with self._lock:
            self._events.append(event)

    def query_since(self, since: datetime, filters: Optional[Mapping[str, Any]] = None) -> Sequence[SecurityEvent]:
        filters = filters or {}
        with self._lock:
            result = []
            for event in self._events:
                if event.timestamp < since:
                    continue
                if _event_matches_filters(event, filters):
                    result.append(event)
            return tuple(result)

    def prune_before(self, before: datetime) -> int:
        with self._lock:
            kept = deque((event for event in self._events if event.timestamp >= before), maxlen=self.max_events)
            removed = len(self._events) - len(kept)
            self._events = kept
            return removed


class InMemoryAlertRepository(AlertRepository):
    """Thread-safe in-memory alert repository."""

    def __init__(self) -> None:
        self._alerts: Dict[str, IntrusionAlert] = {}
        self._lock = threading.RLock()

    def upsert(self, alert: IntrusionAlert) -> None:
        with self._lock:
            self._alerts[alert.alert_id] = alert

    def find_recent_by_dedup_key(self, dedup_key: str, since: datetime) -> Optional[IntrusionAlert]:
        with self._lock:
            candidates = [
                alert
                for alert in self._alerts.values()
                if alert.dedup_key == dedup_key and alert.last_seen >= since and alert.status == AlertStatus.OPEN
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda alert: alert.last_seen)

    def list_open(self) -> Sequence[IntrusionAlert]:
        with self._lock:
            return tuple(alert for alert in self._alerts.values() if alert.status == AlertStatus.OPEN)


class LoggingAlertSink(AlertSink):
    """Alert sink backed by Python logging."""

    def __init__(self, alert_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.alert_logger = alert_logger or logging.getLogger("security.ids.alerts")
        self.redact = redact

    def emit(self, alert: IntrusionAlert) -> None:
        self.alert_logger.warning("intrusion_alert=%s", json.dumps(alert.to_dict(redact=self.redact), sort_keys=True, default=str))


class CompositeAlertSink(AlertSink):
    """Fan-out alert sink."""

    def __init__(self, sinks: Sequence[AlertSink]) -> None:
        self.sinks = tuple(sinks)

    def emit(self, alert: IntrusionAlert) -> None:
        errors = []
        for sink in self.sinks:
            try:
                sink.emit(alert)
            except Exception as exc:
                errors.append(exc)
                logger.exception("Alert sink failed: %s", type(sink).__name__)
        if errors:
            raise AlertSinkError(f"{len(errors)} alert sink(s) failed.")


# =============================================================================
# Anomaly detection
# =============================================================================


@dataclass
class NumericBaseline:
    """Simple numeric baseline for anomaly detection."""

    name: str
    values: Deque[float] = field(default_factory=lambda: deque(maxlen=10_000))

    def add(self, value: Optional[Union[int, float]]) -> None:
        if value is None:
            return
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if math.isfinite(numeric):
            self.values.append(numeric)

    def zscore(self, value: Optional[Union[int, float]]) -> Optional[float]:
        if value is None or len(self.values) < 2:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        mean = statistics.mean(self.values)
        stdev = statistics.pstdev(self.values)
        if stdev == 0:
            return 0.0
        return (numeric - mean) / stdev

    def sample_count(self) -> int:
        return len(self.values)


class BaselineStore:
    """Thread-safe baseline store."""

    def __init__(self) -> None:
        self._baselines: Dict[str, NumericBaseline] = {}
        self._lock = threading.RLock()

    def get(self, name: str) -> NumericBaseline:
        with self._lock:
            if name not in self._baselines:
                self._baselines[name] = NumericBaseline(name=name)
            return self._baselines[name]

    def add(self, name: str, value: Optional[Union[int, float]]) -> None:
        with self._lock:
            self.get(name).add(value)

    def zscore(self, name: str, value: Optional[Union[int, float]]) -> Optional[float]:
        with self._lock:
            return self.get(name).zscore(value)

    def sample_count(self, name: str) -> int:
        with self._lock:
            return self.get(name).sample_count()


# =============================================================================
# IDS engine
# =============================================================================


class IntrusionDetectionEngine:
    """Enterprise IDS evaluation engine."""

    def __init__(
        self,
        config: Optional[IDSConfig] = None,
        event_repository: Optional[SecurityEventRepository] = None,
        alert_repository: Optional[AlertRepository] = None,
        alert_sink: Optional[AlertSink] = None,
        detection_rules: Optional[Iterable[DetectionRule]] = None,
        correlation_rules: Optional[Iterable[CorrelationRule]] = None,
        baseline_store: Optional[BaselineStore] = None,
    ) -> None:
        self.config = config or IDSConfig()
        self.event_repository = event_repository or InMemorySecurityEventRepository(self.config.max_events_in_memory)
        self.alert_repository = alert_repository or InMemoryAlertRepository()
        self.alert_sink = alert_sink or LoggingAlertSink(redact=self.config.redact_sensitive_fields)
        self.baseline_store = baseline_store or BaselineStore()
        self._detection_rules: Dict[str, DetectionRule] = {}
        self._correlation_rules: Dict[str, CorrelationRule] = {}
        self._lock = threading.RLock()

        for rule in detection_rules or []:
            self.upsert_detection_rule(rule)
        for rule in correlation_rules or []:
            self.upsert_correlation_rule(rule)

    def upsert_detection_rule(self, rule: DetectionRule) -> None:
        rule.validate()
        with self._lock:
            self._detection_rules[rule.rule_id] = rule

    def upsert_correlation_rule(self, rule: CorrelationRule) -> None:
        rule.validate()
        with self._lock:
            self._correlation_rules[rule.rule_id] = rule

    def process_event(self, event: SecurityEvent) -> DetectionResult:
        """Store and evaluate a single security event."""
        if not self.config.enabled:
            return DetectionResult(event_id=event.event_id, findings=(), alerts=(), risk_score=event.risk_score)

        try:
            event.validate()
            self.event_repository.append(event)
            self._prune_old_events()

            findings: List[DetectionFinding] = []
            findings.extend(self._evaluate_detection_rules(event))
            findings.extend(self._evaluate_correlation_rules(event))
            findings.extend(self._evaluate_builtin_heuristics(event))
            findings.extend(self._evaluate_anomalies(event))

            effective_risk_score = clamp_score(event.risk_score + sum(f.risk_score for f in findings))
            alerts = tuple(self._create_alerts(event, tuple(findings), effective_risk_score))
            self._update_baselines(event)

            return DetectionResult(
                event_id=event.event_id,
                findings=tuple(findings),
                alerts=alerts,
                risk_score=effective_risk_score,
                diagnostics={
                    "finding_count": len(findings),
                    "alert_count": len(alerts),
                    "rule_count": len(self._detection_rules),
                    "correlation_rule_count": len(self._correlation_rules),
                },
            )
        except Exception as exc:
            logger.exception("IDS processing failed. event_id=%s", event.event_id)
            if self.config.fail_open:
                return DetectionResult(
                    event_id=event.event_id,
                    findings=(),
                    alerts=(),
                    risk_score=event.risk_score,
                    diagnostics={"error": str(exc), "error_type": type(exc).__name__, "fail_open": True},
                )
            raise

    def process_many(self, events: Iterable[SecurityEvent]) -> Tuple[DetectionResult, ...]:
        return tuple(self.process_event(event) for event in events)

    def list_open_alerts(self) -> Sequence[IntrusionAlert]:
        return self.alert_repository.list_open()

    def _evaluate_detection_rules(self, event: SecurityEvent) -> List[DetectionFinding]:
        findings: List[DetectionFinding] = []
        with self._lock:
            rules = sorted(self._detection_rules.values(), key=lambda rule: (rule.priority, rule.rule_id))

        for rule in rules:
            if not rule.matches(event):
                continue
            findings.append(DetectionFinding(
                finding_id=str(uuid.uuid4()),
                detection_type=rule.detection_type,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity,
                risk_score=score_for_severity(rule.severity) + rule.risk_score_delta,
                reason=rule.description,
                event_ids=(event.event_id,),
                action=rule.action,
                mitre_tactics=rule.mitre_tactics,
                mitre_techniques=rule.mitre_techniques,
                metadata=dict(rule.metadata),
            ))
        return findings

    def _evaluate_correlation_rules(self, event: SecurityEvent) -> List[DetectionFinding]:
        findings: List[DetectionFinding] = []
        with self._lock:
            rules = tuple(self._correlation_rules.values())

        for rule in rules:
            if not rule.event_matches(event):
                continue

            group_filter = {field_name: getattr(event, field_name, None) for field_name in rule.group_by}
            if any(value in {None, ""} for value in group_filter.values()):
                continue

            since = event.timestamp - timedelta(seconds=rule.window_seconds)
            filters = dict(group_filter)
            if rule.event_type:
                filters["event_type"] = rule.event_type
            if rule.category:
                filters["category"] = rule.category
            if rule.outcome:
                filters["outcome"] = rule.outcome

            related = tuple(self.event_repository.query_since(since, filters))
            if len(related) < rule.threshold:
                continue

            findings.append(DetectionFinding(
                finding_id=str(uuid.uuid4()),
                detection_type=DetectionType.CORRELATION,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity,
                risk_score=score_for_severity(rule.severity) + rule.risk_score_delta,
                reason=f"Correlation threshold reached: {len(related)} events in {rule.window_seconds}s.",
                event_ids=tuple(item.event_id for item in related),
                action=rule.action,
                metadata={**dict(rule.metadata), "group_by": group_filter, "event_count": len(related)},
            ))
        return findings

    def _evaluate_builtin_heuristics(self, event: SecurityEvent) -> List[DetectionFinding]:
        findings: List[DetectionFinding] = []

        if self._is_blocked_network(event.source_ip):
            findings.append(self._heuristic_finding(
                event,
                rule_id="builtin-blocked-network",
                name="Source IP belongs to blocked network",
                severity=Severity.HIGH,
                reason="Event originated from a configured blocked network.",
                risk=70,
            ))

        if self._has_suspicious_user_agent(event.user_agent):
            findings.append(self._heuristic_finding(
                event,
                rule_id="builtin-suspicious-user-agent",
                name="Suspicious scanner user-agent",
                severity=Severity.MEDIUM,
                reason="User-Agent matches known offensive security scanner patterns.",
                risk=45,
                metadata={"user_agent": event.user_agent},
            ))

        if event.category == EventCategory.AUTHORIZATION and event.outcome == EventOutcome.DENIED:
            if event.action in {"admin", "delete", "grant_role", "change_policy", "disable_mfa"}:
                findings.append(self._heuristic_finding(
                    event,
                    rule_id="builtin-privilege-escalation-attempt",
                    name="Possible privilege escalation attempt",
                    severity=Severity.HIGH,
                    reason="Denied sensitive administrative action detected.",
                    risk=75,
                    metadata={"action": event.action},
                ))

        if event.status_code in {401, 403} and event.category in {EventCategory.API, EventCategory.AUTHENTICATION, EventCategory.AUTHORIZATION}:
            findings.append(self._heuristic_finding(
                event,
                rule_id="builtin-auth-error",
                name="Authentication/authorization error",
                severity=Severity.LOW,
                reason="Request returned an authentication or authorization error.",
                risk=15,
            ))

        return findings

    def _evaluate_anomalies(self, event: SecurityEvent) -> List[DetectionFinding]:
        findings: List[DetectionFinding] = []

        baseline_keys = [
            ("latency_ms", event.latency_ms),
            ("bytes_out", event.bytes_out),
            ("bytes_in", event.bytes_in),
        ]
        for metric, value in baseline_keys:
            name = self._baseline_name(event, metric)
            sample_count = self.baseline_store.sample_count(name)
            if sample_count < self.config.baseline_min_samples:
                continue
            zscore = self.baseline_store.zscore(name, value)
            if zscore is not None and zscore >= self.config.anomaly_zscore_threshold:
                severity = Severity.HIGH if zscore >= self.config.anomaly_zscore_threshold * 2 else Severity.MEDIUM
                findings.append(DetectionFinding(
                    finding_id=str(uuid.uuid4()),
                    detection_type=DetectionType.ANOMALY,
                    rule_id=f"anomaly-{metric}",
                    rule_name=f"Anomalous {metric}",
                    severity=severity,
                    risk_score=score_for_severity(severity),
                    reason=f"{metric} z-score {zscore:.2f} exceeded threshold {self.config.anomaly_zscore_threshold:.2f}.",
                    event_ids=(event.event_id,),
                    metadata={"metric": metric, "value": value, "zscore": zscore, "sample_count": sample_count},
                ))
        return findings

    def _create_alerts(self, event: SecurityEvent, findings: Tuple[DetectionFinding, ...], risk_score: float) -> List[IntrusionAlert]:
        actionable = tuple(finding for finding in findings if finding.action == RuleAction.ALERT)
        if not actionable:
            return []

        severity = max((finding.severity for finding in actionable), key=severity_rank, default=risk_to_severity(risk_score))
        title = self._alert_title(event, actionable, severity)
        dedup_key = self._dedup_key(event, actionable)
        now = datetime.now(timezone.utc)

        if self.config.enable_alert_deduplication:
            existing = self.alert_repository.find_recent_by_dedup_key(
                dedup_key,
                since=now - timedelta(seconds=self.config.alert_dedup_window_seconds),
            )
            if existing:
                merged_findings = _merge_findings(existing.findings, actionable)
                alert = dataclasses.replace(
                    existing,
                    findings=merged_findings,
                    risk_score=max(existing.risk_score, risk_score),
                    severity=max((item.severity for item in merged_findings), key=severity_rank),
                    last_seen=now,
                    recommendations=self._recommendations(merged_findings),
                )
                self.alert_repository.upsert(alert)
                return []

        alert = IntrusionAlert(
            alert_id=str(uuid.uuid4()),
            title=title,
            severity=severity,
            risk_score=risk_score,
            status=AlertStatus.OPEN,
            findings=actionable,
            primary_event=event,
            first_seen=event.timestamp,
            last_seen=event.timestamp,
            dedup_key=dedup_key,
            tenant_id=event.tenant_id,
            source_ip=event.source_ip,
            user_id=event.user_id,
            username=event.username,
            correlation_id=event.correlation_id,
            recommendations=self._recommendations(actionable),
            metadata={"event_fingerprint": event.fingerprint()},
        )
        self.alert_repository.upsert(alert)
        self._emit_alert(alert)
        return [alert]

    def _emit_alert(self, alert: IntrusionAlert) -> None:
        try:
            self.alert_sink.emit(alert)
        except Exception:
            logger.exception("Failed to emit IDS alert. alert_id=%s", alert.alert_id)
            if not self.config.fail_open:
                raise

    def _update_baselines(self, event: SecurityEvent) -> None:
        for metric, value in (
            ("latency_ms", event.latency_ms),
            ("bytes_out", event.bytes_out),
            ("bytes_in", event.bytes_in),
        ):
            self.baseline_store.add(self._baseline_name(event, metric), value)

    def _prune_old_events(self) -> None:
        before = datetime.now(timezone.utc) - timedelta(seconds=self.config.event_retention_seconds)
        self.event_repository.prune_before(before)

    def _baseline_name(self, event: SecurityEvent, metric: str) -> str:
        return f"{event.tenant_id or '*'}:{event.category.value}:{event.event_type}:{metric}"

    def _is_blocked_network(self, source_ip: Optional[str]) -> bool:
        if not source_ip or not self.config.blocked_networks:
            return False
        return _ip_in_any_network(source_ip, self.config.blocked_networks)

    def _has_suspicious_user_agent(self, user_agent: Optional[str]) -> bool:
        if not user_agent:
            return False
        lowered = user_agent.lower()
        return any(pattern.lower() in lowered for pattern in self.config.suspicious_user_agents)

    def _heuristic_finding(
        self,
        event: SecurityEvent,
        rule_id: str,
        name: str,
        severity: Severity,
        reason: str,
        risk: float,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> DetectionFinding:
        return DetectionFinding(
            finding_id=str(uuid.uuid4()),
            detection_type=DetectionType.RULE,
            rule_id=rule_id,
            rule_name=name,
            severity=severity,
            risk_score=risk,
            reason=reason,
            event_ids=(event.event_id,),
            metadata=dict(metadata or {}),
        )

    def _alert_title(self, event: SecurityEvent, findings: Tuple[DetectionFinding, ...], severity: Severity) -> str:
        primary = findings[0]
        actor = event.username or event.user_id or event.source_ip or "unknown actor"
        return f"[{severity.value.upper()}] {primary.rule_name} involving {actor}"

    def _dedup_key(self, event: SecurityEvent, findings: Tuple[DetectionFinding, ...]) -> str:
        rule_ids = sorted({finding.rule_id for finding in findings})
        payload = {
            "rules": rule_ids,
            "tenant_id": event.tenant_id,
            "source_ip": event.source_ip,
            "user_id": event.user_id,
            "username": event.username,
            "resource": event.resource,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _recommendations(self, findings: Sequence[DetectionFinding]) -> Tuple[str, ...]:
        recommendations: List[str] = []
        rule_ids = {finding.rule_id for finding in findings}

        if any("brute" in rid or "auth" in rid for rid in rule_ids):
            recommendations.extend([
                "Review authentication logs for the impacted account and source IP.",
                "Consider temporary IP throttling or account lockout if failures continue.",
                "Require MFA verification for the impacted user if not already enabled.",
            ])
        if any("privilege" in rid for rid in rule_ids):
            recommendations.extend([
                "Review recent role, permission and policy changes.",
                "Validate whether the attempted administrative action was expected.",
                "Rotate credentials if account compromise is suspected.",
            ])
        if any("suspicious-user-agent" in rid for rid in rule_ids):
            recommendations.extend([
                "Inspect request paths and payloads for scanning behavior.",
                "Block or challenge the source IP if activity is unauthorized.",
            ])
        if not recommendations:
            recommendations.append("Review the correlated events and validate whether the behavior is expected.")

        return tuple(dict.fromkeys(recommendations))


# =============================================================================
# Default rules
# =============================================================================


def default_detection_rules() -> Tuple[DetectionRule, ...]:
    return (
        DetectionRule(
            rule_id="rule-admin-action-denied",
            name="Denied administrative action",
            description="A sensitive administrative action was denied and may indicate privilege probing.",
            severity=Severity.HIGH,
            categories=(EventCategory.AUTHORIZATION,),
            outcomes=(EventOutcome.DENIED,),
            event_types=("authorization_check", "policy_decision"),
            attribute_equals={"sensitive_action": True},
            risk_score_delta=20,
            mitre_tactics=("Privilege Escalation",),
            mitre_techniques=("T1068",),
        ),
        DetectionRule(
            rule_id="rule-malware-tag",
            name="Malware indicator event",
            description="Event contains a malware-related tag.",
            severity=Severity.CRITICAL,
            required_tags=("malware",),
            risk_score_delta=40,
            mitre_tactics=("Execution", "Defense Evasion"),
        ),
        DetectionRule(
            rule_id="rule-high-risk-event",
            name="High-risk security event",
            description="Event risk score exceeds high-risk threshold.",
            severity=Severity.HIGH,
            min_event_risk_score=70,
            risk_score_delta=10,
        ),
        DetectionRule(
            rule_id="rule-secret-access-failure",
            name="Failed secret access",
            description="Failed access attempt against a secret or restricted credential resource.",
            severity=Severity.HIGH,
            categories=(EventCategory.DATA_ACCESS, EventCategory.API),
            outcomes=(EventOutcome.FAILURE, EventOutcome.DENIED),
            resource_regex=r"(?i)(secret|credential|token|private[_-]?key)",
            risk_score_delta=30,
            mitre_tactics=("Credential Access",),
        ),
    )


def default_correlation_rules() -> Tuple[CorrelationRule, ...]:
    return (
        CorrelationRule(
            rule_id="corr-bruteforce-source-ip",
            name="Possible brute-force from source IP",
            description="Multiple authentication failures from the same source IP in a short period.",
            severity=Severity.HIGH,
            category=EventCategory.AUTHENTICATION,
            outcome=EventOutcome.FAILURE,
            group_by=("source_ip",),
            threshold=8,
            window_seconds=300,
            risk_score_delta=35,
            metadata={"attack_type": "brute_force"},
        ),
        CorrelationRule(
            rule_id="corr-credential-stuffing-username",
            name="Possible credential stuffing against account",
            description="Multiple authentication failures against the same username.",
            severity=Severity.MEDIUM,
            category=EventCategory.AUTHENTICATION,
            outcome=EventOutcome.FAILURE,
            group_by=("username",),
            threshold=6,
            window_seconds=300,
            risk_score_delta=25,
            metadata={"attack_type": "credential_stuffing"},
        ),
        CorrelationRule(
            rule_id="corr-api-403-source-ip",
            name="Repeated forbidden API responses from source IP",
            description="Multiple authorization denied API responses from the same source IP.",
            severity=Severity.MEDIUM,
            category=EventCategory.API,
            outcome=EventOutcome.DENIED,
            group_by=("source_ip",),
            threshold=10,
            window_seconds=600,
            risk_score_delta=20,
        ),
        CorrelationRule(
            rule_id="corr-admin-denied-user",
            name="Repeated denied admin actions by user",
            description="Repeated denied administrative actions may indicate privilege escalation attempts.",
            severity=Severity.HIGH,
            category=EventCategory.AUTHORIZATION,
            outcome=EventOutcome.DENIED,
            group_by=("user_id",),
            threshold=4,
            window_seconds=300,
            risk_score_delta=40,
            metadata={"attack_type": "privilege_escalation"},
        ),
    )


def create_default_ids_engine() -> IntrusionDetectionEngine:
    return IntrusionDetectionEngine(
        detection_rules=default_detection_rules(),
        correlation_rules=default_correlation_rules(),
    )


# =============================================================================
# Utility functions
# =============================================================================


def new_event(
    category: EventCategory,
    event_type: str,
    outcome: EventOutcome = EventOutcome.UNKNOWN,
    **kwargs: Any,
) -> SecurityEvent:
    """Convenience factory for normalized security events."""
    return SecurityEvent(
        event_id=str(kwargs.pop("event_id", uuid.uuid4())),
        category=category,
        event_type=event_type,
        outcome=outcome,
        **kwargs,
    )


def score_for_severity(severity: Severity) -> float:
    return {
        Severity.INFO: 1.0,
        Severity.LOW: 10.0,
        Severity.MEDIUM: 35.0,
        Severity.HIGH: 65.0,
        Severity.CRITICAL: 90.0,
    }[severity]


def severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }[severity]


def risk_to_severity(score: float) -> Severity:
    score = clamp_score(score)
    if score >= 90:
        return Severity.CRITICAL
    if score >= 70:
        return Severity.HIGH
    if score >= 40:
        return Severity.MEDIUM
    if score >= 10:
        return Severity.LOW
    return Severity.INFO


def clamp_score(value: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(100.0, numeric))


def resolve_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def _ip_in_any_network(ip_value: Optional[str], networks: Sequence[str]) -> bool:
    if not ip_value:
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_value)
        return any(ip_obj in ipaddress.ip_network(network, strict=False) for network in networks)
    except ValueError:
        return False


def _event_matches_filters(event: SecurityEvent, filters: Mapping[str, Any]) -> bool:
    for key, expected in filters.items():
        actual = getattr(event, key, None)
        if isinstance(actual, Enum):
            actual_value = actual.value
        else:
            actual_value = actual
        if isinstance(expected, Enum):
            expected_value = expected.value
        else:
            expected_value = expected
        if actual_value != expected_value:
            return False
    return True


def _merge_findings(existing: Sequence[DetectionFinding], new: Sequence[DetectionFinding]) -> Tuple[DetectionFinding, ...]:
    by_key: Dict[Tuple[str, Tuple[str, ...]], DetectionFinding] = {}
    for finding in tuple(existing) + tuple(new):
        key = (finding.rule_id, tuple(sorted(finding.event_ids)))
        by_key[key] = finding
    return tuple(by_key.values())


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "session",
        "cookie",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key)
                if any(term in key_text.lower() for term in sensitive_terms):
                    output[key_text] = "***REDACTED***"
                else:
                    output[key_text] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


__all__ = [
    "AlertRepository",
    "AlertSink",
    "AlertSinkError",
    "AlertStatus",
    "BaselineStore",
    "CompositeAlertSink",
    "CorrelationRule",
    "DetectionFinding",
    "DetectionResult",
    "DetectionRule",
    "DetectionType",
    "EventCategory",
    "EventOutcome",
    "EventValidationError",
    "IDSConfig",
    "InMemoryAlertRepository",
    "InMemorySecurityEventRepository",
    "IntrusionAlert",
    "IntrusionDetectionEngine",
    "IntrusionDetectionError",
    "LoggingAlertSink",
    "NumericBaseline",
    "RepositoryError",
    "RuleAction",
    "RuleValidationError",
    "SecurityEvent",
    "SecurityEventRepository",
    "Severity",
    "clamp_score",
    "create_default_ids_engine",
    "default_correlation_rules",
    "default_detection_rules",
    "new_event",
    "redact_sensitive",
    "resolve_path",
    "risk_to_severity",
    "score_for_severity",
    "severity_rank",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = create_default_ids_engine()

    for index in range(9):
        result = engine.process_event(
            new_event(
                category=EventCategory.AUTHENTICATION,
                event_type="login",
                outcome=EventOutcome.FAILURE,
                source_ip="203.0.113.10",
                username="admin@example.com",
                user_agent="Mozilla/5.0",
                risk_score=5,
            )
        )
        if result.alerts:
            print(json.dumps(result.to_dict(), indent=2, default=str))
