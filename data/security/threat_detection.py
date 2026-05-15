"""
data/security/threat_detection.py

Enterprise-grade threat detection module for Python services, APIs, data
platforms, workers, pipelines and security operations.

Core capabilities:
- Threat event normalization
- Indicator of Compromise (IOC) matching
- Rule-based threat detection
- Behavioral anomaly signals
- Correlation windows
- Risk scoring and severity classification
- MITRE ATT&CK mapping
- Alert generation and deduplication
- Threat intelligence provider abstraction
- In-memory repositories for local development/tests
- Structured audit/metrics hooks
- JSON report export

This module is framework-agnostic and can be connected to API gateways,
application logs, authentication events, authorization decisions, audit events,
IDS alerts, SIEM streams, Kafka consumers or batch log pipelines.
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


# =============================================================================
# Exceptions
# =============================================================================


class ThreatDetectionError(Exception):
    """Base threat detection error."""


class ThreatRuleValidationError(ThreatDetectionError):
    """Raised when a detection rule is invalid."""


class ThreatEventValidationError(ThreatDetectionError):
    """Raised when a threat event is invalid."""


class ThreatIntelError(ThreatDetectionError):
    """Raised when threat intelligence lookup fails."""


class ThreatRepositoryError(ThreatDetectionError):
    """Raised when repository operations fail."""


class ThreatAlertSinkError(ThreatDetectionError):
    """Raised when alert sink emission fails."""


# =============================================================================
# Enums/config
# =============================================================================


class ThreatCategory(str, Enum):
    AUTHENTICATION_ATTACK = "authentication_attack"
    CREDENTIAL_ACCESS = "credential_access"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    MALWARE = "malware"
    COMMAND_AND_CONTROL = "command_and_control"
    RECONNAISSANCE = "reconnaissance"
    LATERAL_MOVEMENT = "lateral_movement"
    POLICY_ABUSE = "policy_abuse"
    INSIDER_THREAT = "insider_threat"
    SUPPLY_CHAIN = "supply_chain"
    VULNERABILITY_EXPLOIT = "vulnerability_exploit"
    CUSTOM = "custom"


class ThreatSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CONFIRMED = "confirmed"


class ThreatEventType(str, Enum):
    LOGIN = "login"
    AUTHORIZATION = "authorization"
    API_REQUEST = "api_request"
    DATA_ACCESS = "data_access"
    PROCESS = "process"
    NETWORK = "network"
    FILE = "file"
    SECRET = "secret"
    KEY = "key"
    AUDIT = "audit"
    IDS_ALERT = "ids_alert"
    CUSTOM = "custom"


class ThreatOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class IndicatorType(str, Enum):
    IP = "ip"
    CIDR = "cidr"
    DOMAIN = "domain"
    URL = "url"
    HASH_SHA256 = "hash_sha256"
    HASH_SHA1 = "hash_sha1"
    HASH_MD5 = "hash_md5"
    EMAIL = "email"
    USER_AGENT = "user_agent"
    PROCESS_NAME = "process_name"
    FILE_PATH = "file_path"
    REGEX = "regex"
    CUSTOM = "custom"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class RuleAction(str, Enum):
    ALERT = "alert"
    SCORE_ONLY = "score_only"
    SUPPRESS = "suppress"


@dataclass(frozen=True)
class ThreatDetectionConfig:
    """Runtime configuration for threat detection."""

    enabled: bool = True
    fail_open: bool = True
    enable_alert_deduplication: bool = True
    alert_dedup_window_seconds: int = 300
    event_retention_seconds: int = 60 * 60 * 24
    max_events_in_memory: int = 250_000
    enable_threat_intel: bool = True
    enable_anomaly_detection: bool = True
    baseline_min_samples: int = 30
    anomaly_zscore_threshold: float = 3.0
    high_risk_threshold: float = 70.0
    critical_risk_threshold: float = 90.0
    redact_sensitive_fields: bool = True
    suspicious_user_agents: Tuple[str, ...] = (
        "sqlmap", "nikto", "nmap", "masscan", "acunetix", "nessus", "wpscan",
        "hydra", "dirbuster", "gobuster", "ffuf", "zgrab", "censysinspect",
    )
    high_risk_countries: Tuple[str, ...] = ()


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class ThreatEvent:
    """Normalized threat detection event."""

    event_id: str
    event_type: ThreatEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    outcome: ThreatOutcome = ThreatOutcome.UNKNOWN
    tenant_id: Optional[str] = None
    principal_id: Optional[str] = None
    username: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    source_ip: Optional[str] = None
    destination_ip: Optional[str] = None
    source_country: Optional[str] = None
    user_agent: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    bytes_in: Optional[int] = None
    bytes_out: Optional[int] = None
    latency_ms: Optional[float] = None
    process_name: Optional[str] = None
    file_path: Optional[str] = None
    file_hash_sha256: Optional[str] = None
    domain: Optional[str] = None
    url: Optional[str] = None
    risk_score: float = 0.0
    tags: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id:
            raise ThreatEventValidationError("event_id is required.")
        if self.source_ip:
            _validate_ip(self.source_ip, "source_ip")
        if self.destination_ip:
            _validate_ip(self.destination_ip, "destination_ip")

    def fingerprint(self) -> str:
        payload = {
            "event_type": self.event_type.value,
            "outcome": self.outcome.value,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "username": self.username,
            "source_ip": self.source_ip,
            "resource": self.resource,
            "action": self.action,
            "path": self.path,
            "process_name": self.process_name,
            "file_hash_sha256": self.file_hash_sha256,
            "domain": self.domain,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = dataclasses.asdict(self)
        data["event_type"] = self.event_type.value
        data["outcome"] = self.outcome.value
        data["timestamp"] = self.timestamp.isoformat()
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class ThreatIndicator:
    """Indicator of compromise or suspicious indicator."""

    indicator_id: str
    indicator_type: IndicatorType
    value: str
    category: ThreatCategory
    severity: ThreatSeverity
    confidence: ThreatConfidence = ThreatConfidence.MEDIUM
    source: str = "internal"
    description: Optional[str] = None
    active: bool = True
    expires_at: Optional[datetime] = None
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    tags: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_active(self) -> bool:
        return self.active and (self.expires_at is None or self.expires_at > datetime.now(timezone.utc))

    def matches(self, event: ThreatEvent) -> bool:
        if not self.is_active():
            return False
        value = self.value
        if self.indicator_type == IndicatorType.IP:
            return value in {event.source_ip, event.destination_ip}
        if self.indicator_type == IndicatorType.CIDR:
            return _ip_in_cidr(event.source_ip, value) or _ip_in_cidr(event.destination_ip, value)
        if self.indicator_type == IndicatorType.DOMAIN:
            return _domain_matches(event.domain, value) or _domain_matches(_extract_domain(event.url), value)
        if self.indicator_type == IndicatorType.URL:
            return bool(event.url and value.lower() in event.url.lower())
        if self.indicator_type == IndicatorType.HASH_SHA256:
            return bool(event.file_hash_sha256 and event.file_hash_sha256.lower() == value.lower())
        if self.indicator_type == IndicatorType.USER_AGENT:
            return bool(event.user_agent and value.lower() in event.user_agent.lower())
        if self.indicator_type == IndicatorType.PROCESS_NAME:
            return bool(event.process_name and value.lower() == event.process_name.lower())
        if self.indicator_type == IndicatorType.FILE_PATH:
            return bool(event.file_path and value.lower() in event.file_path.lower())
        if self.indicator_type == IndicatorType.EMAIL:
            return value.lower() in {str(event.username or "").lower(), str(event.attributes.get("email", "")).lower()}
        if self.indicator_type == IndicatorType.REGEX:
            haystack = json.dumps(event.to_dict(redact=False), default=str)
            return re.search(value, haystack, flags=re.IGNORECASE) is not None
        return False


@dataclass(frozen=True)
class ThreatRule:
    """Rule-based threat detection definition."""

    rule_id: str
    name: str
    description: str
    category: ThreatCategory
    severity: ThreatSeverity
    confidence: ThreatConfidence = ThreatConfidence.MEDIUM
    action: RuleAction = RuleAction.ALERT
    enabled: bool = True
    event_types: Tuple[ThreatEventType, ...] = ()
    outcomes: Tuple[ThreatOutcome, ...] = ()
    tags_any: Tuple[str, ...] = ()
    tags_all: Tuple[str, ...] = ()
    source_ip_cidrs: Tuple[str, ...] = ()
    user_agent_regex: Optional[str] = None
    path_regex: Optional[str] = None
    resource_regex: Optional[str] = None
    action_regex: Optional[str] = None
    attribute_equals: JsonDict = field(default_factory=dict)
    min_event_risk_score: Optional[float] = None
    risk_score_delta: float = 0.0
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    remediation: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise ThreatRuleValidationError("rule_id is required.")
        if not self.name:
            raise ThreatRuleValidationError("name is required.")
        for cidr in self.source_ip_cidrs:
            ipaddress.ip_network(cidr, strict=False)
        for pattern in (self.user_agent_regex, self.path_regex, self.resource_regex, self.action_regex):
            if pattern:
                re.compile(pattern)

    def matches(self, event: ThreatEvent) -> bool:
        if not self.enabled:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.outcomes and event.outcome not in self.outcomes:
            return False
        if self.tags_any and not set(self.tags_any).intersection(event.tags):
            return False
        if self.tags_all and not set(self.tags_all).issubset(event.tags):
            return False
        if self.source_ip_cidrs and not any(_ip_in_cidr(event.source_ip, cidr) for cidr in self.source_ip_cidrs):
            return False
        if self.user_agent_regex and not re.search(self.user_agent_regex, event.user_agent or "", flags=re.IGNORECASE):
            return False
        if self.path_regex and not re.search(self.path_regex, event.path or "", flags=re.IGNORECASE):
            return False
        if self.resource_regex and not re.search(self.resource_regex, event.resource or "", flags=re.IGNORECASE):
            return False
        if self.action_regex and not re.search(self.action_regex, event.action or "", flags=re.IGNORECASE):
            return False
        if self.min_event_risk_score is not None and event.risk_score < self.min_event_risk_score:
            return False
        for key, expected in self.attribute_equals.items():
            if resolve_path(event.attributes, key) != expected:
                return False
        return True


@dataclass(frozen=True)
class CorrelationRule:
    """Sliding-window threat correlation rule."""

    rule_id: str
    name: str
    description: str
    category: ThreatCategory
    severity: ThreatSeverity
    event_type: Optional[ThreatEventType] = None
    outcome: Optional[ThreatOutcome] = None
    group_by: Tuple[str, ...] = ("source_ip",)
    threshold: int = 5
    window_seconds: int = 300
    action: RuleAction = RuleAction.ALERT
    enabled: bool = True
    risk_score_delta: float = 25.0
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    remediation: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise ThreatRuleValidationError("correlation rule_id is required.")
        if self.threshold <= 0:
            raise ThreatRuleValidationError("threshold must be positive.")
        if self.window_seconds <= 0:
            raise ThreatRuleValidationError("window_seconds must be positive.")

    def event_matches(self, event: ThreatEvent) -> bool:
        if not self.enabled:
            return False
        if self.event_type and event.event_type != self.event_type:
            return False
        if self.outcome and event.outcome != self.outcome:
            return False
        return True


@dataclass(frozen=True)
class ThreatFinding:
    """Single detection finding."""

    finding_id: str
    category: ThreatCategory
    severity: ThreatSeverity
    confidence: ThreatConfidence
    risk_score: float
    title: str
    description: str
    source: str
    source_id: str
    event_ids: Tuple[str, ...]
    action: RuleAction = RuleAction.ALERT
    indicators: Tuple[str, ...] = ()
    mitre_tactics: Tuple[str, ...] = ()
    mitre_techniques: Tuple[str, ...] = ()
    remediation: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def fingerprint(self) -> str:
        payload = {
            "category": self.category.value,
            "source": self.source,
            "source_id": self.source_id,
            "event_ids": sorted(self.event_ids),
            "indicators": sorted(self.indicators),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def to_dict(self) -> JsonDict:
        return {
            "finding_id": self.finding_id,
            "fingerprint": self.fingerprint(),
            "category": self.category.value,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "risk_score": self.risk_score,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "source_id": self.source_id,
            "event_ids": list(self.event_ids),
            "action": self.action.value,
            "indicators": list(self.indicators),
            "mitre_tactics": list(self.mitre_tactics),
            "mitre_techniques": list(self.mitre_techniques),
            "remediation": list(self.remediation),
            "metadata": redact_sensitive(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class ThreatAlert:
    """Threat alert generated from findings."""

    alert_id: str
    title: str
    severity: ThreatSeverity
    confidence: ThreatConfidence
    risk_score: float
    status: AlertStatus
    primary_event: ThreatEvent
    findings: Tuple[ThreatFinding, ...]
    dedup_key: str
    tenant_id: Optional[str] = None
    principal_id: Optional[str] = None
    username: Optional[str] = None
    source_ip: Optional[str] = None
    correlation_id: Optional[str] = None
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    recommendations: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "alert_id": self.alert_id,
            "title": self.title,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "risk_score": self.risk_score,
            "status": self.status.value,
            "primary_event": self.primary_event.to_dict(redact=redact),
            "findings": [f.to_dict() for f in self.findings],
            "dedup_key": self.dedup_key,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "username": self.username,
            "source_ip": self.source_ip,
            "correlation_id": self.correlation_id,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "recommendations": list(self.recommendations),
            "metadata": dict(self.metadata),
        }
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class ThreatDetectionResult:
    """Threat detection result for one event."""

    event_id: str
    findings: Tuple[ThreatFinding, ...]
    alerts: Tuple[ThreatAlert, ...]
    risk_score: float
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    diagnostics: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "event_id": self.event_id,
            "risk_score": self.risk_score,
            "findings": [f.to_dict() for f in self.findings],
            "alerts": [a.to_dict(redact=redact) for a in self.alerts],
            "evaluated_at": self.evaluated_at.isoformat(),
            "diagnostics": redact_sensitive(self.diagnostics) if redact else dict(self.diagnostics),
        }


# =============================================================================
# Repositories / providers / sinks
# =============================================================================


class ThreatEventRepository(ABC):
    @abstractmethod
    def append(self, event: ThreatEvent) -> None:
        """Store a threat event."""

    @abstractmethod
    def query_since(self, since: datetime, filters: Optional[Mapping[str, Any]] = None) -> Sequence[ThreatEvent]:
        """Query events since timestamp."""

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """Prune old events."""


class ThreatIndicatorRepository(ABC):
    @abstractmethod
    def list_indicators(self) -> Sequence[ThreatIndicator]:
        """List indicators."""

    @abstractmethod
    def upsert_indicator(self, indicator: ThreatIndicator) -> None:
        """Create or update indicator."""


class ThreatAlertRepository(ABC):
    @abstractmethod
    def upsert(self, alert: ThreatAlert) -> None:
        """Create or update alert."""

    @abstractmethod
    def find_recent_by_dedup_key(self, dedup_key: str, since: datetime) -> Optional[ThreatAlert]:
        """Find recent alert by dedup key."""

    @abstractmethod
    def list_open(self) -> Sequence[ThreatAlert]:
        """List open alerts."""


class ThreatIntelProvider(ABC):
    @abstractmethod
    def lookup(self, event: ThreatEvent) -> Sequence[ThreatIndicator]:
        """Return matching indicators from external/internal intelligence."""


class ThreatAlertSink(ABC):
    @abstractmethod
    def emit(self, alert: ThreatAlert) -> None:
        """Emit a threat alert."""


class InMemoryThreatEventRepository(ThreatEventRepository):
    def __init__(self, max_events: int = 250_000) -> None:
        self._events: Deque[ThreatEvent] = deque(maxlen=max(1, max_events))
        self._lock = threading.RLock()

    def append(self, event: ThreatEvent) -> None:
        with self._lock:
            self._events.append(event)

    def query_since(self, since: datetime, filters: Optional[Mapping[str, Any]] = None) -> Sequence[ThreatEvent]:
        filters = filters or {}
        with self._lock:
            return tuple(event for event in self._events if event.timestamp >= since and _event_matches_filters(event, filters))

    def prune_before(self, before: datetime) -> int:
        with self._lock:
            old_len = len(self._events)
            self._events = deque((e for e in self._events if e.timestamp >= before), maxlen=self._events.maxlen)
            return old_len - len(self._events)


class InMemoryThreatIndicatorRepository(ThreatIndicatorRepository):
    def __init__(self, indicators: Optional[Iterable[ThreatIndicator]] = None) -> None:
        self._indicators: Dict[str, ThreatIndicator] = {}
        self._lock = threading.RLock()
        for indicator in indicators or ():
            self.upsert_indicator(indicator)

    def list_indicators(self) -> Sequence[ThreatIndicator]:
        with self._lock:
            return tuple(self._indicators.values())

    def upsert_indicator(self, indicator: ThreatIndicator) -> None:
        with self._lock:
            self._indicators[indicator.indicator_id] = indicator


class InMemoryThreatAlertRepository(ThreatAlertRepository):
    def __init__(self) -> None:
        self._alerts: Dict[str, ThreatAlert] = {}
        self._lock = threading.RLock()

    def upsert(self, alert: ThreatAlert) -> None:
        with self._lock:
            self._alerts[alert.alert_id] = alert

    def find_recent_by_dedup_key(self, dedup_key: str, since: datetime) -> Optional[ThreatAlert]:
        with self._lock:
            candidates = [a for a in self._alerts.values() if a.dedup_key == dedup_key and a.last_seen >= since and a.status == AlertStatus.OPEN]
            return max(candidates, key=lambda a: a.last_seen) if candidates else None

    def list_open(self) -> Sequence[ThreatAlert]:
        with self._lock:
            return tuple(a for a in self._alerts.values() if a.status == AlertStatus.OPEN)


class RepositoryThreatIntelProvider(ThreatIntelProvider):
    def __init__(self, repository: ThreatIndicatorRepository) -> None:
        self.repository = repository

    def lookup(self, event: ThreatEvent) -> Sequence[ThreatIndicator]:
        return tuple(indicator for indicator in self.repository.list_indicators() if indicator.matches(event))


class LoggingThreatAlertSink(ThreatAlertSink):
    def __init__(self, alert_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.alert_logger = alert_logger or logging.getLogger("security.threat_detection.alerts")
        self.redact = redact

    def emit(self, alert: ThreatAlert) -> None:
        level = logging.CRITICAL if alert.severity == ThreatSeverity.CRITICAL else logging.WARNING
        self.alert_logger.log(level, "threat_alert=%s", json.dumps(alert.to_dict(redact=self.redact), sort_keys=True, default=str))


# =============================================================================
# Baseline/anomaly helpers
# =============================================================================


@dataclass
class NumericBaseline:
    name: str
    values: Deque[float] = field(default_factory=lambda: deque(maxlen=10_000))

    def add(self, value: Optional[Union[int, float]]) -> None:
        if value is None:
            return
        numeric = float(value)
        if math.isfinite(numeric):
            self.values.append(numeric)

    def zscore(self, value: Optional[Union[int, float]]) -> Optional[float]:
        if value is None or len(self.values) < 2:
            return None
        numeric = float(value)
        mean = statistics.mean(self.values)
        stdev = statistics.pstdev(self.values)
        if stdev == 0:
            return 0.0
        return (numeric - mean) / stdev


class BaselineStore:
    def __init__(self) -> None:
        self._baselines: Dict[str, NumericBaseline] = {}
        self._lock = threading.RLock()

    def get(self, name: str) -> NumericBaseline:
        with self._lock:
            if name not in self._baselines:
                self._baselines[name] = NumericBaseline(name)
            return self._baselines[name]

    def add(self, name: str, value: Optional[Union[int, float]]) -> None:
        self.get(name).add(value)

    def zscore(self, name: str, value: Optional[Union[int, float]]) -> Optional[float]:
        return self.get(name).zscore(value)

    def count(self, name: str) -> int:
        return len(self.get(name).values)


# =============================================================================
# Threat detection engine
# =============================================================================


class ThreatDetectionEngine:
    """Enterprise threat detection engine."""

    def __init__(
        self,
        config: Optional[ThreatDetectionConfig] = None,
        event_repository: Optional[ThreatEventRepository] = None,
        indicator_repository: Optional[ThreatIndicatorRepository] = None,
        alert_repository: Optional[ThreatAlertRepository] = None,
        intel_provider: Optional[ThreatIntelProvider] = None,
        alert_sink: Optional[ThreatAlertSink] = None,
        rules: Optional[Iterable[ThreatRule]] = None,
        correlation_rules: Optional[Iterable[CorrelationRule]] = None,
        baseline_store: Optional[BaselineStore] = None,
    ) -> None:
        self.config = config or ThreatDetectionConfig()
        self.event_repository = event_repository or InMemoryThreatEventRepository(self.config.max_events_in_memory)
        self.indicator_repository = indicator_repository or InMemoryThreatIndicatorRepository(default_indicators())
        self.alert_repository = alert_repository or InMemoryThreatAlertRepository()
        self.intel_provider = intel_provider or RepositoryThreatIntelProvider(self.indicator_repository)
        self.alert_sink = alert_sink or LoggingThreatAlertSink(redact=self.config.redact_sensitive_fields)
        self.baseline_store = baseline_store or BaselineStore()
        self._rules: Dict[str, ThreatRule] = {}
        self._correlation_rules: Dict[str, CorrelationRule] = {}
        self._lock = threading.RLock()
        for rule in rules or default_threat_rules():
            self.upsert_rule(rule)
        for rule in correlation_rules or default_correlation_rules():
            self.upsert_correlation_rule(rule)

    def upsert_rule(self, rule: ThreatRule) -> None:
        rule.validate()
        with self._lock:
            self._rules[rule.rule_id] = rule

    def upsert_correlation_rule(self, rule: CorrelationRule) -> None:
        rule.validate()
        with self._lock:
            self._correlation_rules[rule.rule_id] = rule

    def upsert_indicator(self, indicator: ThreatIndicator) -> None:
        self.indicator_repository.upsert_indicator(indicator)

    def process_event(self, event: ThreatEvent) -> ThreatDetectionResult:
        if not self.config.enabled:
            return ThreatDetectionResult(event.event_id, (), (), event.risk_score)
        try:
            event.validate()
            self.event_repository.append(event)
            self._prune_events()
            findings: List[ThreatFinding] = []
            findings.extend(self._evaluate_iocs(event))
            findings.extend(self._evaluate_rules(event))
            findings.extend(self._evaluate_correlation(event))
            findings.extend(self._evaluate_builtin_heuristics(event))
            findings.extend(self._evaluate_anomalies(event))
            risk_score = clamp_score(event.risk_score + sum(f.risk_score for f in findings))
            alerts = tuple(self._create_alerts(event, tuple(findings), risk_score))
            self._update_baselines(event)
            return ThreatDetectionResult(
                event_id=event.event_id,
                findings=tuple(findings),
                alerts=alerts,
                risk_score=risk_score,
                diagnostics={
                    "finding_count": len(findings),
                    "alert_count": len(alerts),
                    "rule_count": len(self._rules),
                    "correlation_rule_count": len(self._correlation_rules),
                },
            )
        except Exception as exc:
            logger.exception("Threat detection failed. event_id=%s", event.event_id)
            if self.config.fail_open:
                return ThreatDetectionResult(event.event_id, (), (), event.risk_score, diagnostics={"error": str(exc), "error_type": type(exc).__name__, "fail_open": True})
            raise

    def process_many(self, events: Iterable[ThreatEvent]) -> Tuple[ThreatDetectionResult, ...]:
        return tuple(self.process_event(event) for event in events)

    def list_open_alerts(self) -> Sequence[ThreatAlert]:
        return self.alert_repository.list_open()

    def export_alerts_json(self, redact: bool = True) -> str:
        return json.dumps([alert.to_dict(redact=redact) for alert in self.list_open_alerts()], indent=2, sort_keys=True, default=str)

    def _evaluate_iocs(self, event: ThreatEvent) -> List[ThreatFinding]:
        if not self.config.enable_threat_intel:
            return []
        findings: List[ThreatFinding] = []
        indicators = self.intel_provider.lookup(event)
        for indicator in indicators:
            findings.append(ThreatFinding(
                finding_id=str(uuid.uuid4()),
                category=indicator.category,
                severity=indicator.severity,
                confidence=indicator.confidence,
                risk_score=score_for_severity(indicator.severity) + confidence_bonus(indicator.confidence),
                title=f"Threat indicator matched: {indicator.indicator_type.value}",
                description=indicator.description or f"Event matched threat indicator from {indicator.source}.",
                source="threat_intel",
                source_id=indicator.indicator_id,
                event_ids=(event.event_id,),
                indicators=(indicator.value,),
                mitre_tactics=indicator.mitre_tactics,
                mitre_techniques=indicator.mitre_techniques,
                remediation=("Investigate the matched indicator and block or contain affected assets if malicious activity is confirmed.",),
                metadata={"indicator_type": indicator.indicator_type.value, "indicator_source": indicator.source, "tags": list(indicator.tags)},
            ))
        return findings

    def _evaluate_rules(self, event: ThreatEvent) -> List[ThreatFinding]:
        findings: List[ThreatFinding] = []
        with self._lock:
            rules = tuple(self._rules.values())
        for rule in rules:
            if not rule.matches(event):
                continue
            findings.append(ThreatFinding(
                finding_id=str(uuid.uuid4()),
                category=rule.category,
                severity=rule.severity,
                confidence=rule.confidence,
                risk_score=score_for_severity(rule.severity) + confidence_bonus(rule.confidence) + rule.risk_score_delta,
                title=rule.name,
                description=rule.description,
                source="rule",
                source_id=rule.rule_id,
                event_ids=(event.event_id,),
                action=rule.action,
                mitre_tactics=rule.mitre_tactics,
                mitre_techniques=rule.mitre_techniques,
                remediation=rule.remediation,
                metadata=dict(rule.metadata),
            ))
        return findings

    def _evaluate_correlation(self, event: ThreatEvent) -> List[ThreatFinding]:
        findings: List[ThreatFinding] = []
        with self._lock:
            rules = tuple(self._correlation_rules.values())
        for rule in rules:
            if not rule.event_matches(event):
                continue
            group_filter = {field_name: getattr(event, field_name, None) for field_name in rule.group_by}
            if any(value in {None, ""} for value in group_filter.values()):
                continue
            filters: Dict[str, Any] = dict(group_filter)
            if rule.event_type:
                filters["event_type"] = rule.event_type
            if rule.outcome:
                filters["outcome"] = rule.outcome
            since = event.timestamp - timedelta(seconds=rule.window_seconds)
            related = tuple(self.event_repository.query_since(since, filters))
            if len(related) < rule.threshold:
                continue
            findings.append(ThreatFinding(
                finding_id=str(uuid.uuid4()),
                category=rule.category,
                severity=rule.severity,
                confidence=ThreatConfidence.MEDIUM,
                risk_score=score_for_severity(rule.severity) + rule.risk_score_delta,
                title=rule.name,
                description=f"{rule.description} Threshold reached: {len(related)} events in {rule.window_seconds}s.",
                source="correlation",
                source_id=rule.rule_id,
                event_ids=tuple(e.event_id for e in related),
                action=rule.action,
                mitre_tactics=rule.mitre_tactics,
                mitre_techniques=rule.mitre_techniques,
                remediation=rule.remediation,
                metadata={"group_by": group_filter, "event_count": len(related)},
            ))
        return findings

    def _evaluate_builtin_heuristics(self, event: ThreatEvent) -> List[ThreatFinding]:
        findings: List[ThreatFinding] = []
        ua = (event.user_agent or "").lower()
        if any(pattern in ua for pattern in self.config.suspicious_user_agents):
            findings.append(self._heuristic(
                event,
                "builtin-suspicious-user-agent",
                "Suspicious user-agent detected",
                ThreatCategory.RECONNAISSANCE,
                ThreatSeverity.MEDIUM,
                "User-Agent matches common scanner/offensive tooling patterns.",
                ("Inspect request paths/payloads and consider blocking or challenging the source IP.",),
                metadata={"user_agent": event.user_agent},
            ))
        if event.event_type == ThreatEventType.AUTHORIZATION and event.outcome == ThreatOutcome.DENIED and event.action in {"grant_role", "disable_mfa", "delete", "admin", "change_policy"}:
            findings.append(self._heuristic(
                event,
                "builtin-privilege-escalation-denied",
                "Possible privilege escalation attempt",
                ThreatCategory.PRIVILEGE_ESCALATION,
                ThreatSeverity.HIGH,
                "Denied sensitive administrative action detected.",
                ("Review account activity, recent permissions and session legitimacy.",),
                mitre_tactics=("Privilege Escalation",),
            ))
        if event.bytes_out and event.bytes_out > 100 * 1024 * 1024:
            findings.append(self._heuristic(
                event,
                "builtin-large-data-egress",
                "Large data egress event",
                ThreatCategory.DATA_EXFILTRATION,
                ThreatSeverity.HIGH,
                "A single event transferred an unusually large amount of outbound data.",
                ("Verify whether the transfer was authorized and expected.",),
                mitre_tactics=("Exfiltration",),
                metadata={"bytes_out": event.bytes_out},
            ))
        if event.source_country and event.source_country in self.config.high_risk_countries:
            findings.append(self._heuristic(
                event,
                "builtin-high-risk-geo",
                "High-risk geolocation observed",
                ThreatCategory.RECONNAISSANCE,
                ThreatSeverity.LOW,
                "Event originated from a configured high-risk country/region.",
                ("Use additional context before taking action; geolocation is not definitive.",),
                metadata={"source_country": event.source_country},
            ))
        return findings

    def _evaluate_anomalies(self, event: ThreatEvent) -> List[ThreatFinding]:
        if not self.config.enable_anomaly_detection:
            return []
        findings: List[ThreatFinding] = []
        for metric_name, value in (("bytes_out", event.bytes_out), ("bytes_in", event.bytes_in), ("latency_ms", event.latency_ms)):
            key = self._baseline_key(event, metric_name)
            if self.baseline_store.count(key) < self.config.baseline_min_samples:
                continue
            z = self.baseline_store.zscore(key, value)
            if z is not None and z >= self.config.anomaly_zscore_threshold:
                severity = ThreatSeverity.HIGH if z >= self.config.anomaly_zscore_threshold * 2 else ThreatSeverity.MEDIUM
                findings.append(ThreatFinding(
                    finding_id=str(uuid.uuid4()),
                    category=ThreatCategory.CUSTOM,
                    severity=severity,
                    confidence=ThreatConfidence.LOW,
                    risk_score=score_for_severity(severity),
                    title=f"Anomalous {metric_name}",
                    description=f"{metric_name} z-score {z:.2f} exceeded threshold {self.config.anomaly_zscore_threshold:.2f}.",
                    source="anomaly",
                    source_id=f"anomaly-{metric_name}",
                    event_ids=(event.event_id,),
                    remediation=("Review whether the anomalous behavior is expected for this user, service or tenant.",),
                    metadata={"metric": metric_name, "value": value, "zscore": z},
                ))
        return findings

    def _create_alerts(self, event: ThreatEvent, findings: Tuple[ThreatFinding, ...], risk_score: float) -> List[ThreatAlert]:
        actionable = tuple(f for f in findings if f.action == RuleAction.ALERT)
        if not actionable:
            return []
        severity = max((f.severity for f in actionable), key=severity_rank)
        confidence = max((f.confidence for f in actionable), key=confidence_rank)
        dedup_key = self._dedup_key(event, actionable)
        now = datetime.now(timezone.utc)
        if self.config.enable_alert_deduplication:
            existing = self.alert_repository.find_recent_by_dedup_key(dedup_key, now - timedelta(seconds=self.config.alert_dedup_window_seconds))
            if existing:
                merged = _merge_findings(existing.findings, actionable)
                updated = dataclasses.replace(
                    existing,
                    findings=merged,
                    risk_score=max(existing.risk_score, risk_score),
                    severity=max((f.severity for f in merged), key=severity_rank),
                    confidence=max((f.confidence for f in merged), key=confidence_rank),
                    last_seen=now,
                    recommendations=self._recommendations(merged),
                )
                self.alert_repository.upsert(updated)
                return []
        alert = ThreatAlert(
            alert_id=str(uuid.uuid4()),
            title=f"[{severity.value.upper()}] {actionable[0].title}",
            severity=severity,
            confidence=confidence,
            risk_score=risk_score,
            status=AlertStatus.OPEN,
            primary_event=event,
            findings=actionable,
            dedup_key=dedup_key,
            tenant_id=event.tenant_id,
            principal_id=event.principal_id,
            username=event.username,
            source_ip=event.source_ip,
            correlation_id=event.correlation_id,
            first_seen=event.timestamp,
            last_seen=event.timestamp,
            recommendations=self._recommendations(actionable),
            metadata={"event_fingerprint": event.fingerprint()},
        )
        self.alert_repository.upsert(alert)
        self._emit_alert(alert)
        return [alert]

    def _heuristic(self, event: ThreatEvent, source_id: str, title: str, category: ThreatCategory, severity: ThreatSeverity, description: str, remediation: Tuple[str, ...], mitre_tactics: Tuple[str, ...] = (), metadata: Optional[Mapping[str, Any]] = None) -> ThreatFinding:
        return ThreatFinding(
            finding_id=str(uuid.uuid4()),
            category=category,
            severity=severity,
            confidence=ThreatConfidence.MEDIUM,
            risk_score=score_for_severity(severity),
            title=title,
            description=description,
            source="builtin",
            source_id=source_id,
            event_ids=(event.event_id,),
            mitre_tactics=mitre_tactics,
            remediation=remediation,
            metadata=dict(metadata or {}),
        )

    def _baseline_key(self, event: ThreatEvent, metric: str) -> str:
        return f"{event.tenant_id or '*'}:{event.event_type.value}:{event.principal_id or event.source_ip or '*'}:{metric}"

    def _update_baselines(self, event: ThreatEvent) -> None:
        for metric, value in (("bytes_out", event.bytes_out), ("bytes_in", event.bytes_in), ("latency_ms", event.latency_ms)):
            self.baseline_store.add(self._baseline_key(event, metric), value)

    def _dedup_key(self, event: ThreatEvent, findings: Tuple[ThreatFinding, ...]) -> str:
        payload = {
            "tenant_id": event.tenant_id,
            "principal_id": event.principal_id,
            "source_ip": event.source_ip,
            "rules": sorted({f.source_id for f in findings}),
            "category": sorted({f.category.value for f in findings}),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _recommendations(self, findings: Sequence[ThreatFinding]) -> Tuple[str, ...]:
        recs: List[str] = []
        for f in findings:
            recs.extend(f.remediation)
        categories = {f.category for f in findings}
        if ThreatCategory.CREDENTIAL_ACCESS in categories or ThreatCategory.AUTHENTICATION_ATTACK in categories:
            recs.extend(("Review authentication history and enforce MFA where appropriate.", "Consider credential reset if compromise is suspected."))
        if ThreatCategory.DATA_EXFILTRATION in categories:
            recs.extend(("Validate business justification for the data transfer.", "Check downstream destinations and revoke access if unauthorized."))
        if ThreatCategory.PRIVILEGE_ESCALATION in categories:
            recs.extend(("Review recent role and policy changes.", "Invalidate suspicious sessions and rotate privileged credentials if needed."))
        if not recs:
            recs.append("Review related events and determine whether this activity is expected.")
        return tuple(dict.fromkeys(recs))

    def _emit_alert(self, alert: ThreatAlert) -> None:
        try:
            self.alert_sink.emit(alert)
        except Exception as exc:
            logger.exception("Threat alert sink failed. alert_id=%s", alert.alert_id)
            if not self.config.fail_open:
                raise ThreatAlertSinkError("Threat alert sink failed.") from exc

    def _prune_events(self) -> None:
        self.event_repository.prune_before(datetime.now(timezone.utc) - timedelta(seconds=self.config.event_retention_seconds))


# =============================================================================
# Defaults
# =============================================================================


def default_threat_rules() -> Tuple[ThreatRule, ...]:
    return (
        ThreatRule(
            rule_id="threat-sqli-path",
            name="Possible SQL injection probe",
            description="Request path or query contains common SQL injection patterns.",
            category=ThreatCategory.VULNERABILITY_EXPLOIT,
            severity=ThreatSeverity.HIGH,
            confidence=ThreatConfidence.MEDIUM,
            event_types=(ThreatEventType.API_REQUEST,),
            path_regex=r"(?i)(union\s+select|or\s+1=1|sleep\(|benchmark\(|information_schema|xp_cmdshell)",
            mitre_tactics=("Initial Access",),
            mitre_techniques=("T1190",),
            remediation=("Inspect payloads, validate WAF/API protections and ensure parameterized queries are used.",),
        ),
        ThreatRule(
            rule_id="threat-path-traversal",
            name="Possible path traversal probe",
            description="Request path contains path traversal patterns.",
            category=ThreatCategory.VULNERABILITY_EXPLOIT,
            severity=ThreatSeverity.HIGH,
            event_types=(ThreatEventType.API_REQUEST,),
            path_regex=r"(\.\./|\.\.\\|%2e%2e%2f|%252e%252e%252f)",
            mitre_tactics=("Initial Access",),
            mitre_techniques=("T1190",),
            remediation=("Normalize paths, enforce allowlists and block traversal payloads.",),
        ),
        ThreatRule(
            rule_id="threat-secret-access-denied",
            name="Denied secret access",
            description="A secret access operation was denied and may indicate credential probing.",
            category=ThreatCategory.CREDENTIAL_ACCESS,
            severity=ThreatSeverity.HIGH,
            event_types=(ThreatEventType.SECRET,),
            outcomes=(ThreatOutcome.DENIED, ThreatOutcome.FAILURE),
            mitre_tactics=("Credential Access",),
            remediation=("Review caller identity, secret ACLs and recent access patterns.",),
        ),
        ThreatRule(
            rule_id="threat-high-risk-event",
            name="High-risk event",
            description="Event already carries a high risk score.",
            category=ThreatCategory.CUSTOM,
            severity=ThreatSeverity.HIGH,
            min_event_risk_score=70,
            remediation=("Review upstream risk signals and correlate with related activity.",),
        ),
    )


def default_correlation_rules() -> Tuple[CorrelationRule, ...]:
    return (
        CorrelationRule(
            rule_id="corr-login-fail-source-ip",
            name="Possible brute-force from source IP",
            description="Multiple login failures from the same source IP.",
            category=ThreatCategory.AUTHENTICATION_ATTACK,
            severity=ThreatSeverity.HIGH,
            event_type=ThreatEventType.LOGIN,
            outcome=ThreatOutcome.FAILURE,
            group_by=("source_ip",),
            threshold=8,
            window_seconds=300,
            mitre_tactics=("Credential Access",),
            mitre_techniques=("T1110",),
            remediation=("Throttle or block source IP and review targeted accounts.",),
        ),
        CorrelationRule(
            rule_id="corr-authorization-denied-principal",
            name="Repeated denied authorization decisions",
            description="Multiple denied sensitive actions by the same principal.",
            category=ThreatCategory.PRIVILEGE_ESCALATION,
            severity=ThreatSeverity.HIGH,
            event_type=ThreatEventType.AUTHORIZATION,
            outcome=ThreatOutcome.DENIED,
            group_by=("principal_id",),
            threshold=5,
            window_seconds=300,
            remediation=("Review principal permissions and session legitimacy.",),
        ),
        CorrelationRule(
            rule_id="corr-large-data-egress-user",
            name="Repeated large data egress by principal",
            description="Multiple data access events by the same principal in a short period.",
            category=ThreatCategory.DATA_EXFILTRATION,
            severity=ThreatSeverity.MEDIUM,
            event_type=ThreatEventType.DATA_ACCESS,
            outcome=ThreatOutcome.SUCCESS,
            group_by=("principal_id",),
            threshold=10,
            window_seconds=600,
            remediation=("Validate data access intent and destination.",),
        ),
    )


def default_indicators() -> Tuple[ThreatIndicator, ...]:
    return (
        ThreatIndicator(
            indicator_id="ioc-user-agent-sqlmap",
            indicator_type=IndicatorType.USER_AGENT,
            value="sqlmap",
            category=ThreatCategory.VULNERABILITY_EXPLOIT,
            severity=ThreatSeverity.HIGH,
            confidence=ThreatConfidence.HIGH,
            source="builtin",
            description="sqlmap user-agent observed.",
            mitre_tactics=("Initial Access",),
            mitre_techniques=("T1190",),
            tags=("scanner", "sql-injection"),
        ),
    )


# =============================================================================
# Utility functions
# =============================================================================


def new_threat_event(event_type: ThreatEventType, outcome: ThreatOutcome = ThreatOutcome.UNKNOWN, **kwargs: Any) -> ThreatEvent:
    return ThreatEvent(event_id=str(kwargs.pop("event_id", uuid.uuid4())), event_type=event_type, outcome=outcome, **kwargs)


def event_from_audit_mapping(payload: Mapping[str, Any]) -> ThreatEvent:
    actor = dict(payload.get("actor") or {})
    resource = dict(payload.get("resource") or {})
    category = str(payload.get("category") or "")
    event_type = ThreatEventType.AUDIT
    if "auth" in category:
        event_type = ThreatEventType.LOGIN
    elif "authorization" in category or "rbac" in category or "abac" in category:
        event_type = ThreatEventType.AUTHORIZATION
    elif "secret" in category:
        event_type = ThreatEventType.SECRET
    elif "key" in category:
        event_type = ThreatEventType.KEY
    return ThreatEvent(
        event_id=str(payload.get("event_id") or uuid.uuid4()),
        event_type=event_type,
        timestamp=parse_datetime(payload.get("timestamp")) if payload.get("timestamp") else datetime.now(timezone.utc),
        outcome=ThreatOutcome(str(payload.get("outcome") or ThreatOutcome.UNKNOWN.value)) if str(payload.get("outcome") or ThreatOutcome.UNKNOWN.value) in ThreatOutcome._value2member_map_ else ThreatOutcome.UNKNOWN,
        tenant_id=payload.get("tenant_id") or actor.get("tenant_id") or resource.get("tenant_id"),
        principal_id=payload.get("principal_id") or actor.get("actor_id"),
        username=payload.get("username") or actor.get("username"),
        session_id=payload.get("session_id"),
        request_id=payload.get("request_id"),
        correlation_id=payload.get("correlation_id"),
        source_ip=payload.get("ip_address") or actor.get("ip_address"),
        user_agent=payload.get("user_agent") or actor.get("user_agent"),
        resource=resource.get("name") or payload.get("resource"),
        action=str(payload.get("action") or ""),
        risk_score=float(payload.get("risk_score") or 0.0),
        tags=tuple(payload.get("tags") or ()),
        attributes=dict(payload.get("metadata") or {}),
    )


def score_for_severity(severity: ThreatSeverity) -> float:
    return {
        ThreatSeverity.INFO: 1.0,
        ThreatSeverity.LOW: 15.0,
        ThreatSeverity.MEDIUM: 40.0,
        ThreatSeverity.HIGH: 70.0,
        ThreatSeverity.CRITICAL: 95.0,
    }[severity]


def severity_rank(severity: ThreatSeverity) -> int:
    return {
        ThreatSeverity.INFO: 0,
        ThreatSeverity.LOW: 1,
        ThreatSeverity.MEDIUM: 2,
        ThreatSeverity.HIGH: 3,
        ThreatSeverity.CRITICAL: 4,
    }[severity]


def confidence_rank(confidence: ThreatConfidence) -> int:
    return {
        ThreatConfidence.LOW: 0,
        ThreatConfidence.MEDIUM: 1,
        ThreatConfidence.HIGH: 2,
        ThreatConfidence.CONFIRMED: 3,
    }[confidence]


def confidence_bonus(confidence: ThreatConfidence) -> float:
    return {
        ThreatConfidence.LOW: 0.0,
        ThreatConfidence.MEDIUM: 5.0,
        ThreatConfidence.HIGH: 10.0,
        ThreatConfidence.CONFIRMED: 20.0,
    }[confidence]


def clamp_score(value: float) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except Exception:
        return 0.0


def resolve_path(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = ("password", "secret", "token", "api_key", "apikey", "authorization", "credential", "private_key", "session", "cookie")

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                if any(term in str(key).lower() for term in sensitive_terms):
                    output[str(key)] = "***REDACTED***"
                else:
                    output[str(key)] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, tuple):
            return tuple(walk(v) for v in value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    return walk(dict(data))


def _validate_ip(value: str, field_name: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ThreatEventValidationError(f"Invalid {field_name}: {value}") from exc


def _ip_in_cidr(ip_value: Optional[str], cidr: str) -> bool:
    if not ip_value:
        return False
    try:
        return ipaddress.ip_address(ip_value) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _domain_matches(domain: Optional[str], indicator: str) -> bool:
    if not domain:
        return False
    d = domain.lower().strip(".")
    i = indicator.lower().strip(".")
    return d == i or d.endswith("." + i)


def _extract_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/:?#]+)", url)
    return match.group(1).lower() if match else None


def _event_matches_filters(event: ThreatEvent, filters: Mapping[str, Any]) -> bool:
    for key, expected in filters.items():
        actual = getattr(event, key, None)
        if isinstance(actual, Enum):
            actual = actual.value
        if isinstance(expected, Enum):
            expected = expected.value
        if actual != expected:
            return False
    return True


def _merge_findings(existing: Sequence[ThreatFinding], new: Sequence[ThreatFinding]) -> Tuple[ThreatFinding, ...]:
    by_fp: Dict[str, ThreatFinding] = {}
    for finding in tuple(existing) + tuple(new):
        by_fp[finding.fingerprint()] = finding
    return tuple(by_fp.values())


def create_default_threat_detection_engine() -> ThreatDetectionEngine:
    return ThreatDetectionEngine()


__all__ = [
    "AlertStatus",
    "BaselineStore",
    "CorrelationRule",
    "IndicatorType",
    "InMemoryThreatAlertRepository",
    "InMemoryThreatEventRepository",
    "InMemoryThreatIndicatorRepository",
    "LoggingThreatAlertSink",
    "NumericBaseline",
    "RepositoryThreatIntelProvider",
    "RuleAction",
    "ThreatAlert",
    "ThreatAlertRepository",
    "ThreatAlertSink",
    "ThreatAlertSinkError",
    "ThreatCategory",
    "ThreatConfidence",
    "ThreatDetectionConfig",
    "ThreatDetectionEngine",
    "ThreatDetectionError",
    "ThreatDetectionResult",
    "ThreatEvent",
    "ThreatEventRepository",
    "ThreatEventType",
    "ThreatEventValidationError",
    "ThreatFinding",
    "ThreatIndicator",
    "ThreatIndicatorRepository",
    "ThreatIntelError",
    "ThreatIntelProvider",
    "ThreatOutcome",
    "ThreatRepositoryError",
    "ThreatRule",
    "ThreatRuleValidationError",
    "ThreatSeverity",
    "clamp_score",
    "confidence_bonus",
    "confidence_rank",
    "create_default_threat_detection_engine",
    "default_correlation_rules",
    "default_indicators",
    "default_threat_rules",
    "event_from_audit_mapping",
    "new_threat_event",
    "parse_datetime",
    "redact_sensitive",
    "resolve_path",
    "score_for_severity",
    "severity_rank",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    engine = create_default_threat_detection_engine()
    event = new_threat_event(
        ThreatEventType.API_REQUEST,
        ThreatOutcome.UNKNOWN,
        tenant_id="default",
        principal_id="user-001",
        source_ip="203.0.113.10",
        user_agent="sqlmap/1.7",
        path="/api/items?id=1 UNION SELECT password FROM users",
        status_code=400,
        request_id="req-demo",
        correlation_id="corr-demo",
    )
    result = engine.process_event(event)
    print(json.dumps(result.to_dict(), indent=2, default=str))
