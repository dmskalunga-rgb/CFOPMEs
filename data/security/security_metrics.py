"""
data/security/security_metrics.py

Enterprise-grade security metrics module for Python services, data platforms,
APIs, workers, pipelines and security governance tooling.

Core capabilities:
- Security metric model with counters, gauges, histograms and timers
- In-memory time-series repository for local development/tests
- Pluggable exporters/sinks
- KPI/SLO evaluation and threshold alerts
- Rolling-window aggregations
- Authentication, authorization, audit, IDS, crypto and secret-management metrics
- Risk scoring summaries
- Tenant-aware and service-aware dimensions
- JSON/Prometheus-style export helpers
- Framework-agnostic integration

Production recommendations:
- Export metrics to Prometheus, OpenTelemetry, Datadog, CloudWatch or your SIEM.
- Keep high-cardinality labels under control.
- Avoid labels containing secrets, tokens, emails or full URLs.
- Use security metrics together with structured audit events.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import statistics
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Labels = Dict[str, str]
Numeric = Union[int, float]


# =============================================================================
# Exceptions
# =============================================================================


class SecurityMetricsError(Exception):
    """Base security metrics error."""


class MetricValidationError(SecurityMetricsError):
    """Raised when a metric definition/sample is invalid."""


class MetricRepositoryError(SecurityMetricsError):
    """Raised when metric repository operations fail."""


class MetricExporterError(SecurityMetricsError):
    """Raised when a metric exporter fails."""


class SLOViolationError(SecurityMetricsError):
    """Raised when a configured SLO is violated and fail_closed is enabled."""


# =============================================================================
# Enums/config
# =============================================================================


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class MetricUnit(str, Enum):
    COUNT = "count"
    RATIO = "ratio"
    PERCENT = "percent"
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"
    BYTES = "bytes"
    SCORE = "score"


class SecurityMetricCategory(str, Enum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RBAC = "rbac"
    ABAC = "abac"
    AUDIT = "audit"
    IDS = "intrusion_detection"
    ENCRYPTION = "encryption"
    DECRYPTION = "decryption"
    KEY_MANAGEMENT = "key_management"
    SECRET_MANAGEMENT = "secret_management"
    DATA_ACCESS = "data_access"
    COMPLIANCE = "compliance"
    SYSTEM = "system"
    CUSTOM = "custom"


class Aggregation(str, Enum):
    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"
    RATE_PER_SECOND = "rate_per_second"


class ThresholdOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"


class MetricAlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SecurityMetricsConfig:
    """Runtime configuration for security metrics."""

    enabled: bool = True
    fail_closed: bool = False
    max_samples_in_memory: int = 1_000_000
    default_retention_seconds: int = 60 * 60 * 24
    max_label_count: int = 20
    max_label_value_length: int = 128
    redact_sensitive_labels: bool = True
    enable_threshold_alerts: bool = True
    export_include_timestamps: bool = True
    sensitive_label_terms: Tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "cookie",
        "session",
    )


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class MetricDefinition:
    """Metric metadata definition."""

    name: str
    metric_type: MetricType
    category: SecurityMetricCategory
    unit: MetricUnit = MetricUnit.COUNT
    description: str = ""
    allowed_labels: Tuple[str, ...] = ()
    enabled: bool = True
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name:
            raise MetricValidationError("Metric name is required.")
        if not self.name.replace("_", "").replace(":", "").replace(".", "").isalnum():
            raise MetricValidationError(f"Invalid metric name: {self.name}")


@dataclass(frozen=True)
class MetricSample:
    """Single metric sample."""

    name: str
    value: float
    metric_type: MetricType
    category: SecurityMetricCategory
    unit: MetricUnit = MetricUnit.COUNT
    labels: Labels = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    sample_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: JsonDict = field(default_factory=dict)

    def validate(self, config: Optional[SecurityMetricsConfig] = None) -> None:
        config = config or SecurityMetricsConfig()
        if not self.name:
            raise MetricValidationError("Metric sample name is required.")
        if not math.isfinite(float(self.value)):
            raise MetricValidationError("Metric value must be finite.")
        if len(self.labels) > config.max_label_count:
            raise MetricValidationError("Metric sample has too many labels.")
        for key, value in self.labels.items():
            if not key:
                raise MetricValidationError("Metric label key cannot be empty.")
            if len(str(value)) > config.max_label_value_length:
                raise MetricValidationError(f"Metric label value is too long: {key}")

    def to_dict(self, redact: bool = True, include_timestamp: bool = True) -> JsonDict:
        labels = redact_labels(self.labels) if redact else dict(self.labels)
        data = {
            "sample_id": self.sample_id,
            "name": self.name,
            "value": self.value,
            "metric_type": self.metric_type.value,
            "category": self.category.value,
            "unit": self.unit.value,
            "labels": labels,
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }
        if include_timestamp:
            data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass(frozen=True)
class MetricQuery:
    """Metric query object."""

    names: Tuple[str, ...] = ()
    categories: Tuple[SecurityMetricCategory, ...] = ()
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    labels: Labels = field(default_factory=dict)
    limit: Optional[int] = 10_000


@dataclass(frozen=True)
class AggregatedMetric:
    """Aggregated metric result."""

    name: str
    aggregation: Aggregation
    value: float
    sample_count: int
    category: Optional[SecurityMetricCategory] = None
    unit: Optional[MetricUnit] = None
    labels: Labels = field(default_factory=dict)
    window_seconds: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "aggregation": self.aggregation.value,
            "value": self.value,
            "sample_count": self.sample_count,
            "category": self.category.value if self.category else None,
            "unit": self.unit.value if self.unit else None,
            "labels": dict(self.labels),
            "window_seconds": self.window_seconds,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class ThresholdRule:
    """Threshold rule for metric-based alerts."""

    rule_id: str
    name: str
    metric_name: str
    operator: ThresholdOperator
    threshold: float
    aggregation: Aggregation = Aggregation.SUM
    window_seconds: int = 300
    severity: MetricAlertSeverity = MetricAlertSeverity.WARNING
    labels: Labels = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise MetricValidationError("Threshold rule_id is required.")
        if not self.metric_name:
            raise MetricValidationError("Threshold metric_name is required.")
        if self.window_seconds <= 0:
            raise MetricValidationError("Threshold window_seconds must be positive.")


@dataclass(frozen=True)
class MetricAlert:
    """Alert generated from a metric threshold/SLO violation."""

    alert_id: str
    rule_id: str
    rule_name: str
    metric_name: str
    severity: MetricAlertSeverity
    value: float
    threshold: float
    operator: ThresholdOperator
    message: str
    labels: Labels = field(default_factory=dict)
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "alert_id": self.alert_id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "metric_name": self.metric_name,
            "severity": self.severity.value,
            "value": self.value,
            "threshold": self.threshold,
            "operator": self.operator.value,
            "message": self.message,
            "labels": redact_labels(self.labels) if redact else dict(self.labels),
            "triggered_at": self.triggered_at.isoformat(),
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }


@dataclass(frozen=True)
class SecurityDashboardSnapshot:
    """High-level security metric snapshot."""

    generated_at: datetime
    window_seconds: int
    total_auth_success: float = 0.0
    total_auth_failure: float = 0.0
    authorization_denies: float = 0.0
    intrusion_alerts: float = 0.0
    critical_intrusion_alerts: float = 0.0
    secret_reads: float = 0.0
    key_rotations: float = 0.0
    crypto_failures: float = 0.0
    audit_events: float = 0.0
    average_risk_score: float = 0.0
    p95_latency_ms: float = 0.0
    alerts: Tuple[MetricAlert, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "window_seconds": self.window_seconds,
            "total_auth_success": self.total_auth_success,
            "total_auth_failure": self.total_auth_failure,
            "authorization_denies": self.authorization_denies,
            "intrusion_alerts": self.intrusion_alerts,
            "critical_intrusion_alerts": self.critical_intrusion_alerts,
            "secret_reads": self.secret_reads,
            "key_rotations": self.key_rotations,
            "crypto_failures": self.crypto_failures,
            "audit_events": self.audit_events,
            "average_risk_score": self.average_risk_score,
            "p95_latency_ms": self.p95_latency_ms,
            "alerts": [alert.to_dict() for alert in self.alerts],
        }


# =============================================================================
# Repository/exporter abstractions
# =============================================================================


class MetricRepository(ABC):
    """Metric repository abstraction."""

    @abstractmethod
    def append(self, sample: MetricSample) -> None:
        """Append one metric sample."""

    @abstractmethod
    def append_many(self, samples: Sequence[MetricSample]) -> None:
        """Append many metric samples."""

    @abstractmethod
    def query(self, query: MetricQuery) -> Sequence[MetricSample]:
        """Query metric samples."""

    @abstractmethod
    def prune_before(self, before: datetime) -> int:
        """Prune old samples and return count."""


class InMemoryMetricRepository(MetricRepository):
    """Thread-safe in-memory metric repository."""

    def __init__(self, max_samples: int = 1_000_000) -> None:
        self.max_samples = max(1, max_samples)
        self._samples: Deque[MetricSample] = deque(maxlen=self.max_samples)
        self._lock = threading.RLock()

    def append(self, sample: MetricSample) -> None:
        with self._lock:
            self._samples.append(sample)

    def append_many(self, samples: Sequence[MetricSample]) -> None:
        with self._lock:
            self._samples.extend(samples)

    def query(self, query: MetricQuery) -> Sequence[MetricSample]:
        with self._lock:
            result: List[MetricSample] = []
            for sample in self._samples:
                if _sample_matches_query(sample, query):
                    result.append(sample)
                    if query.limit is not None and len(result) >= query.limit:
                        break
            return tuple(result)

    def prune_before(self, before: datetime) -> int:
        with self._lock:
            kept = deque((sample for sample in self._samples if sample.timestamp >= before), maxlen=self.max_samples)
            removed = len(self._samples) - len(kept)
            self._samples = kept
            return removed


class MetricExporter(ABC):
    """Metric exporter abstraction."""

    @abstractmethod
    def export_samples(self, samples: Sequence[MetricSample]) -> None:
        """Export metric samples."""

    def export_alerts(self, alerts: Sequence[MetricAlert]) -> None:
        """Export metric alerts."""
        return None


class LoggingMetricExporter(MetricExporter):
    """Logging-backed metric exporter."""

    def __init__(self, metrics_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.metrics_logger = metrics_logger or logging.getLogger("security.metrics")
        self.redact = redact

    def export_samples(self, samples: Sequence[MetricSample]) -> None:
        for sample in samples:
            self.metrics_logger.info("security_metric=%s", json.dumps(sample.to_dict(redact=self.redact), sort_keys=True, default=str))

    def export_alerts(self, alerts: Sequence[MetricAlert]) -> None:
        for alert in alerts:
            self.metrics_logger.warning("security_metric_alert=%s", json.dumps(alert.to_dict(redact=self.redact), sort_keys=True, default=str))


class CompositeMetricExporter(MetricExporter):
    """Fan-out metric exporter."""

    def __init__(self, exporters: Sequence[MetricExporter]) -> None:
        self.exporters = tuple(exporters)

    def export_samples(self, samples: Sequence[MetricSample]) -> None:
        errors = []
        for exporter in self.exporters:
            try:
                exporter.export_samples(samples)
            except Exception as exc:
                errors.append(exc)
                logger.exception("Metric exporter failed: %s", type(exporter).__name__)
        if errors:
            raise MetricExporterError(f"{len(errors)} metric exporter(s) failed.")

    def export_alerts(self, alerts: Sequence[MetricAlert]) -> None:
        for exporter in self.exporters:
            exporter.export_alerts(alerts)


# =============================================================================
# Main service
# =============================================================================


class SecurityMetricsService:
    """Enterprise security metrics service."""

    def __init__(
        self,
        repository: Optional[MetricRepository] = None,
        exporter: Optional[MetricExporter] = None,
        config: Optional[SecurityMetricsConfig] = None,
        definitions: Optional[Iterable[MetricDefinition]] = None,
        threshold_rules: Optional[Iterable[ThresholdRule]] = None,
    ) -> None:
        self.config = config or SecurityMetricsConfig()
        self.repository = repository or InMemoryMetricRepository(self.config.max_samples_in_memory)
        self.exporter = exporter or LoggingMetricExporter(redact=self.config.redact_sensitive_labels)
        self._definitions: Dict[str, MetricDefinition] = {}
        self._threshold_rules: Dict[str, ThresholdRule] = {}
        self._lock = threading.RLock()

        for definition in definitions or default_metric_definitions():
            self.register_definition(definition)
        for rule in threshold_rules or default_threshold_rules():
            self.upsert_threshold_rule(rule)

    def register_definition(self, definition: MetricDefinition) -> None:
        definition.validate()
        with self._lock:
            self._definitions[definition.name] = definition

    def upsert_threshold_rule(self, rule: ThresholdRule) -> None:
        rule.validate()
        with self._lock:
            self._threshold_rules[rule.rule_id] = rule

    def record(
        self,
        name: str,
        value: Numeric = 1,
        labels: Optional[Mapping[str, str]] = None,
        category: Optional[SecurityMetricCategory] = None,
        metric_type: Optional[MetricType] = None,
        unit: Optional[MetricUnit] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[datetime] = None,
        export: bool = True,
    ) -> Tuple[MetricSample, Tuple[MetricAlert, ...]]:
        """Record one metric sample and evaluate threshold rules."""
        if not self.config.enabled:
            sample = self._build_sample(name, value, labels, category, metric_type, unit, metadata, timestamp)
            return sample, ()

        try:
            sample = self._build_sample(name, value, labels, category, metric_type, unit, metadata, timestamp)
            sample.validate(self.config)
            self.repository.append(sample)
            self._prune_old_samples()
            alerts = self._evaluate_thresholds(sample)
            if export:
                self.exporter.export_samples((sample,))
                if alerts:
                    self.exporter.export_alerts(alerts)
            return sample, alerts
        except Exception as exc:
            logger.exception("Failed to record security metric. name=%s", name)
            if self.config.fail_closed:
                if isinstance(exc, SecurityMetricsError):
                    raise
                raise SecurityMetricsError("Failed to record security metric.") from exc
            sample = self._build_sample(name, value, labels, category, metric_type, unit, metadata, timestamp)
            return sample, ()

    def increment(self, name: str, amount: Numeric = 1, labels: Optional[Mapping[str, str]] = None, **kwargs: Any) -> Tuple[MetricSample, Tuple[MetricAlert, ...]]:
        return self.record(name, amount, labels=labels, metric_type=MetricType.COUNTER, **kwargs)

    def gauge(self, name: str, value: Numeric, labels: Optional[Mapping[str, str]] = None, **kwargs: Any) -> Tuple[MetricSample, Tuple[MetricAlert, ...]]:
        return self.record(name, value, labels=labels, metric_type=MetricType.GAUGE, **kwargs)

    def observe(self, name: str, value: Numeric, labels: Optional[Mapping[str, str]] = None, **kwargs: Any) -> Tuple[MetricSample, Tuple[MetricAlert, ...]]:
        return self.record(name, value, labels=labels, metric_type=MetricType.HISTOGRAM, **kwargs)

    def timer(self, name: str, duration_ms: Numeric, labels: Optional[Mapping[str, str]] = None, **kwargs: Any) -> Tuple[MetricSample, Tuple[MetricAlert, ...]]:
        return self.record(name, duration_ms, labels=labels, metric_type=MetricType.TIMER, unit=MetricUnit.MILLISECONDS, **kwargs)

    def record_many(self, samples: Sequence[MetricSample], export: bool = True) -> Tuple[MetricSample, ...]:
        if not self.config.enabled:
            return tuple(samples)
        for sample in samples:
            sample.validate(self.config)
        self.repository.append_many(samples)
        self._prune_old_samples()
        if export:
            self.exporter.export_samples(samples)
        return tuple(samples)

    def query(self, query: MetricQuery) -> Sequence[MetricSample]:
        return self.repository.query(query)

    def aggregate(
        self,
        name: str,
        aggregation: Aggregation,
        window_seconds: Optional[int] = None,
        labels: Optional[Mapping[str, str]] = None,
        category: Optional[SecurityMetricCategory] = None,
    ) -> AggregatedMetric:
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(seconds=window_seconds) if window_seconds else None
        samples = self.repository.query(MetricQuery(
            names=(name,),
            categories=(category,) if category else (),
            start_time=start_time,
            end_time=end_time,
            labels=dict(labels or {}),
            limit=None,
        ))
        value = aggregate_values([sample.value for sample in samples], aggregation, window_seconds)
        unit = samples[0].unit if samples else None
        metric_category = samples[0].category if samples else category
        return AggregatedMetric(
            name=name,
            aggregation=aggregation,
            value=value,
            sample_count=len(samples),
            category=metric_category,
            unit=unit,
            labels=dict(labels or {}),
            window_seconds=window_seconds,
            start_time=start_time,
            end_time=end_time,
        )

    def snapshot(self, window_seconds: int = 300) -> SecurityDashboardSnapshot:
        alerts: List[MetricAlert] = []

        def sum_metric(name: str, labels: Optional[Mapping[str, str]] = None) -> float:
            return self.aggregate(name, Aggregation.SUM, window_seconds, labels).value

        def avg_metric(name: str) -> float:
            return self.aggregate(name, Aggregation.AVG, window_seconds).value

        def p95_metric(name: str) -> float:
            return self.aggregate(name, Aggregation.P95, window_seconds).value

        for rule in tuple(self._threshold_rules.values()):
            if rule.enabled:
                alert = self._evaluate_threshold_rule(rule)
                if alert:
                    alerts.append(alert)

        if alerts:
            self.exporter.export_alerts(tuple(alerts))

        return SecurityDashboardSnapshot(
            generated_at=datetime.now(timezone.utc),
            window_seconds=window_seconds,
            total_auth_success=sum_metric("security_authentication_attempts_total", {"outcome": "success"}),
            total_auth_failure=sum_metric("security_authentication_attempts_total", {"outcome": "failure"}),
            authorization_denies=sum_metric("security_authorization_decisions_total", {"decision": "deny"}),
            intrusion_alerts=sum_metric("security_intrusion_alerts_total"),
            critical_intrusion_alerts=sum_metric("security_intrusion_alerts_total", {"severity": "critical"}),
            secret_reads=sum_metric("security_secret_operations_total", {"operation": "read"}),
            key_rotations=sum_metric("security_key_operations_total", {"operation": "rotate"}),
            crypto_failures=sum_metric("security_crypto_operations_total", {"outcome": "failure"}),
            audit_events=sum_metric("security_audit_events_total"),
            average_risk_score=avg_metric("security_risk_score"),
            p95_latency_ms=p95_metric("security_operation_latency_ms"),
            alerts=tuple(alerts),
        )

    def export_json(self, query: Optional[MetricQuery] = None, redact: bool = True, indent: Optional[int] = 2) -> str:
        samples = self.repository.query(query or MetricQuery(limit=None))
        return json.dumps([sample.to_dict(redact=redact, include_timestamp=self.config.export_include_timestamps) for sample in samples], indent=indent, sort_keys=True, default=str)

    def export_prometheus(self, query: Optional[MetricQuery] = None) -> str:
        samples = self.repository.query(query or MetricQuery(limit=None))
        latest: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], MetricSample] = {}
        for sample in samples:
            key = (sample.name, tuple(sorted(sample.labels.items())))
            latest[key] = sample

        lines: List[str] = []
        emitted_help: set[str] = set()
        for (_, _), sample in latest.items():
            definition = self._definitions.get(sample.name)
            if sample.name not in emitted_help and definition:
                lines.append(f"# HELP {sample.name} {definition.description or sample.name}")
                lines.append(f"# TYPE {sample.name} {_prometheus_type(sample.metric_type)}")
                emitted_help.add(sample.name)
            label_text = _format_prometheus_labels(sample.labels)
            timestamp_ms = int(sample.timestamp.timestamp() * 1000)
            if self.config.export_include_timestamps:
                lines.append(f"{sample.name}{label_text} {sample.value} {timestamp_ms}")
            else:
                lines.append(f"{sample.name}{label_text} {sample.value}")
        return "\n".join(lines) + ("\n" if lines else "")

    def from_audit_event(self, payload: Mapping[str, Any]) -> Tuple[MetricSample, ...]:
        """Create metrics from a generic security audit event mapping."""
        category = str(payload.get("category") or "custom")
        outcome = str(payload.get("outcome") or "unknown")
        action = str(payload.get("action") or "custom")
        tenant_id = str(payload.get("tenant_id") or payload.get("actor", {}).get("tenant_id") or "")
        labels = clean_labels({"category": category, "outcome": outcome, "action": action, "tenant_id": tenant_id})
        sample, _ = self.increment("security_audit_events_total", labels=labels, category=SecurityMetricCategory.AUDIT)
        risk = float(payload.get("risk_score") or 0.0)
        risk_sample, _ = self.gauge("security_risk_score", risk, labels=clean_labels({"category": category, "tenant_id": tenant_id}), category=SecurityMetricCategory.AUDIT, unit=MetricUnit.SCORE)
        return (sample, risk_sample)

    def _build_sample(
        self,
        name: str,
        value: Numeric,
        labels: Optional[Mapping[str, str]],
        category: Optional[SecurityMetricCategory],
        metric_type: Optional[MetricType],
        unit: Optional[MetricUnit],
        metadata: Optional[Mapping[str, Any]],
        timestamp: Optional[datetime],
    ) -> MetricSample:
        definition = self._definitions.get(name)
        return MetricSample(
            name=name,
            value=float(value),
            metric_type=metric_type or (definition.metric_type if definition else MetricType.GAUGE),
            category=category or (definition.category if definition else SecurityMetricCategory.CUSTOM),
            unit=unit or (definition.unit if definition else MetricUnit.COUNT),
            labels=clean_labels(dict(labels or {}), self.config),
            timestamp=normalize_datetime(timestamp or datetime.now(timezone.utc)),
            metadata=dict(metadata or {}),
        )

    def _evaluate_thresholds(self, sample: MetricSample) -> Tuple[MetricAlert, ...]:
        if not self.config.enable_threshold_alerts:
            return ()
        alerts = []
        with self._lock:
            rules = tuple(self._threshold_rules.values())
        for rule in rules:
            if not rule.enabled or rule.metric_name != sample.name:
                continue
            alert = self._evaluate_threshold_rule(rule)
            if alert:
                alerts.append(alert)
        return tuple(alerts)

    def _evaluate_threshold_rule(self, rule: ThresholdRule) -> Optional[MetricAlert]:
        aggregated = self.aggregate(rule.metric_name, rule.aggregation, rule.window_seconds, rule.labels)
        if not compare_threshold(aggregated.value, rule.operator, rule.threshold):
            return None
        return MetricAlert(
            alert_id=str(uuid.uuid4()),
            rule_id=rule.rule_id,
            rule_name=rule.name,
            metric_name=rule.metric_name,
            severity=rule.severity,
            value=aggregated.value,
            threshold=rule.threshold,
            operator=rule.operator,
            message=rule.description or f"Metric {rule.metric_name} {rule.operator.value} {rule.threshold}: current value {aggregated.value}",
            labels=dict(rule.labels),
            metadata={"aggregation": rule.aggregation.value, "window_seconds": rule.window_seconds, "sample_count": aggregated.sample_count},
        )

    def _prune_old_samples(self) -> None:
        before = datetime.now(timezone.utc) - timedelta(seconds=self.config.default_retention_seconds)
        self.repository.prune_before(before)


# =============================================================================
# Defaults
# =============================================================================


def default_metric_definitions() -> Tuple[MetricDefinition, ...]:
    return (
        MetricDefinition("security_authentication_attempts_total", MetricType.COUNTER, SecurityMetricCategory.AUTHENTICATION, MetricUnit.COUNT, "Authentication attempts by outcome.", ("outcome", "tenant_id", "method")),
        MetricDefinition("security_authorization_decisions_total", MetricType.COUNTER, SecurityMetricCategory.AUTHORIZATION, MetricUnit.COUNT, "Authorization decisions by decision.", ("decision", "tenant_id", "resource", "action")),
        MetricDefinition("security_intrusion_alerts_total", MetricType.COUNTER, SecurityMetricCategory.IDS, MetricUnit.COUNT, "Intrusion alerts by severity.", ("severity", "tenant_id", "rule_id")),
        MetricDefinition("security_secret_operations_total", MetricType.COUNTER, SecurityMetricCategory.SECRET_MANAGEMENT, MetricUnit.COUNT, "Secret manager operations.", ("operation", "outcome", "tenant_id")),
        MetricDefinition("security_key_operations_total", MetricType.COUNTER, SecurityMetricCategory.KEY_MANAGEMENT, MetricUnit.COUNT, "Key management operations.", ("operation", "outcome", "tenant_id")),
        MetricDefinition("security_crypto_operations_total", MetricType.COUNTER, SecurityMetricCategory.ENCRYPTION, MetricUnit.COUNT, "Crypto operations by outcome.", ("operation", "outcome", "algorithm", "tenant_id")),
        MetricDefinition("security_audit_events_total", MetricType.COUNTER, SecurityMetricCategory.AUDIT, MetricUnit.COUNT, "Security audit events.", ("category", "outcome", "action", "tenant_id")),
        MetricDefinition("security_risk_score", MetricType.GAUGE, SecurityMetricCategory.SYSTEM, MetricUnit.SCORE, "Security risk score samples.", ("category", "tenant_id")),
        MetricDefinition("security_operation_latency_ms", MetricType.TIMER, SecurityMetricCategory.SYSTEM, MetricUnit.MILLISECONDS, "Security operation latency in milliseconds.", ("operation", "tenant_id")),
        MetricDefinition("security_policy_evaluation_errors_total", MetricType.COUNTER, SecurityMetricCategory.COMPLIANCE, MetricUnit.COUNT, "Policy evaluation errors.", ("engine", "tenant_id")),
    )


def default_threshold_rules() -> Tuple[ThresholdRule, ...]:
    return (
        ThresholdRule(
            rule_id="auth-failures-high",
            name="High authentication failure volume",
            metric_name="security_authentication_attempts_total",
            operator=ThresholdOperator.GTE,
            threshold=25,
            aggregation=Aggregation.SUM,
            window_seconds=300,
            severity=MetricAlertSeverity.HIGH,
            labels={"outcome": "failure"},
            description="Authentication failures exceeded threshold in the rolling window.",
        ),
        ThresholdRule(
            rule_id="critical-ids-alerts",
            name="Critical IDS alerts detected",
            metric_name="security_intrusion_alerts_total",
            operator=ThresholdOperator.GTE,
            threshold=1,
            aggregation=Aggregation.SUM,
            window_seconds=300,
            severity=MetricAlertSeverity.CRITICAL,
            labels={"severity": "critical"},
            description="At least one critical intrusion alert was observed.",
        ),
        ThresholdRule(
            rule_id="crypto-failure-spike",
            name="Crypto failure spike",
            metric_name="security_crypto_operations_total",
            operator=ThresholdOperator.GTE,
            threshold=5,
            aggregation=Aggregation.SUM,
            window_seconds=300,
            severity=MetricAlertSeverity.HIGH,
            labels={"outcome": "failure"},
            description="Cryptographic operation failures exceeded threshold.",
        ),
    )


# =============================================================================
# Utility functions
# =============================================================================


def aggregate_values(values: Sequence[float], aggregation: Aggregation, window_seconds: Optional[int] = None) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(float(value) for value in values)
    if aggregation == Aggregation.SUM:
        return float(sum(sorted_values))
    if aggregation == Aggregation.COUNT:
        return float(len(sorted_values))
    if aggregation == Aggregation.AVG:
        return float(statistics.mean(sorted_values))
    if aggregation == Aggregation.MIN:
        return float(min(sorted_values))
    if aggregation == Aggregation.MAX:
        return float(max(sorted_values))
    if aggregation == Aggregation.P50:
        return percentile(sorted_values, 50)
    if aggregation == Aggregation.P90:
        return percentile(sorted_values, 90)
    if aggregation == Aggregation.P95:
        return percentile(sorted_values, 95)
    if aggregation == Aggregation.P99:
        return percentile(sorted_values, 99)
    if aggregation == Aggregation.RATE_PER_SECOND:
        seconds = max(1, int(window_seconds or 1))
        return float(sum(sorted_values)) / seconds
    raise MetricValidationError(f"Unsupported aggregation: {aggregation.value}")


def percentile(sorted_values: Sequence[float], percent: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (len(sorted_values) - 1) * (percent / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[int(rank)])
    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    return float(lower_value + (upper_value - lower_value) * (rank - lower))


def compare_threshold(value: float, operator: ThresholdOperator, threshold: float) -> bool:
    if operator == ThresholdOperator.GT:
        return value > threshold
    if operator == ThresholdOperator.GTE:
        return value >= threshold
    if operator == ThresholdOperator.LT:
        return value < threshold
    if operator == ThresholdOperator.LTE:
        return value <= threshold
    if operator == ThresholdOperator.EQ:
        return value == threshold
    if operator == ThresholdOperator.NE:
        return value != threshold
    raise MetricValidationError(f"Unsupported threshold operator: {operator.value}")


def normalize_datetime(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def clean_labels(labels: Mapping[str, str], config: Optional[SecurityMetricsConfig] = None) -> Labels:
    config = config or SecurityMetricsConfig()
    cleaned: Labels = {}
    for key, value in labels.items():
        if value is None or value == "":
            continue
        safe_key = str(key).strip().replace("-", "_").replace(".", "_")
        safe_value = str(value).strip()
        if config.redact_sensitive_labels and _is_sensitive_key(safe_key, config.sensitive_label_terms):
            cleaned[safe_key] = "redacted"
        else:
            cleaned[safe_key] = safe_value[: config.max_label_value_length]
    return cleaned


def redact_labels(labels: Mapping[str, str], sensitive_terms: Optional[Sequence[str]] = None) -> Labels:
    terms = tuple(term.lower() for term in (sensitive_terms or SecurityMetricsConfig().sensitive_label_terms))
    return {str(key): ("redacted" if _is_sensitive_key(str(key), terms) else str(value)) for key, value in labels.items()}


def redact_sensitive(data: Mapping[str, Any], sensitive_terms: Optional[Sequence[str]] = None) -> JsonDict:
    terms = tuple(term.lower() for term in (sensitive_terms or SecurityMetricsConfig().sensitive_label_terms))

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key)
                if _is_sensitive_key(key_text, terms):
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


def _is_sensitive_key(key: str, sensitive_terms: Sequence[str]) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in sensitive_terms)


def _sample_matches_query(sample: MetricSample, query: MetricQuery) -> bool:
    if query.names and sample.name not in query.names:
        return False
    if query.categories and sample.category not in query.categories:
        return False
    if query.start_time and sample.timestamp < query.start_time:
        return False
    if query.end_time and sample.timestamp > query.end_time:
        return False
    for key, value in query.labels.items():
        if sample.labels.get(key) != value:
            return False
    return True


def _prometheus_type(metric_type: MetricType) -> str:
    if metric_type == MetricType.COUNTER:
        return "counter"
    if metric_type == MetricType.GAUGE:
        return "gauge"
    if metric_type in {MetricType.HISTOGRAM, MetricType.TIMER}:
        return "gauge"
    return "gauge"


def _format_prometheus_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    parts = []
    for key, value in sorted(labels.items()):
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        parts.append(f'{key}="{escaped}"')
    return "{" + ",".join(parts) + "}"


def create_default_security_metrics_service() -> SecurityMetricsService:
    return SecurityMetricsService()


__all__ = [
    "AggregatedMetric",
    "Aggregation",
    "CompositeMetricExporter",
    "InMemoryMetricRepository",
    "Labels",
    "LoggingMetricExporter",
    "MetricAlert",
    "MetricAlertSeverity",
    "MetricDefinition",
    "MetricExporter",
    "MetricExporterError",
    "MetricQuery",
    "MetricRepository",
    "MetricRepositoryError",
    "MetricSample",
    "MetricType",
    "MetricUnit",
    "MetricValidationError",
    "SLOViolationError",
    "SecurityDashboardSnapshot",
    "SecurityMetricCategory",
    "SecurityMetricsConfig",
    "SecurityMetricsError",
    "SecurityMetricsService",
    "ThresholdOperator",
    "ThresholdRule",
    "aggregate_values",
    "clean_labels",
    "compare_threshold",
    "create_default_security_metrics_service",
    "default_metric_definitions",
    "default_threshold_rules",
    "normalize_datetime",
    "percentile",
    "redact_labels",
    "redact_sensitive",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    metrics = create_default_security_metrics_service()
    for _ in range(3):
        metrics.increment(
            "security_authentication_attempts_total",
            labels={"outcome": "success", "tenant_id": "default", "method": "password"},
            category=SecurityMetricCategory.AUTHENTICATION,
        )
    for _ in range(5):
        metrics.increment(
            "security_authentication_attempts_total",
            labels={"outcome": "failure", "tenant_id": "default", "method": "password"},
            category=SecurityMetricCategory.AUTHENTICATION,
        )
    metrics.timer("security_operation_latency_ms", 123.4, labels={"operation": "authorize", "tenant_id": "default"})

    snapshot = metrics.snapshot(window_seconds=300)
    print(json.dumps(snapshot.to_dict(), indent=2, default=str))
    print(metrics.export_prometheus())
