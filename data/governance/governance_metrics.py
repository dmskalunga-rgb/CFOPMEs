"""
governance_metrics.py
=====================

Enterprise-grade governance metrics module for data governance platforms.

Core capabilities
-----------------
- KPI/KRI registry for governance, privacy, compliance, access and quality domains.
- Time-series metric points with labels, dimensions, tenant and asset scope.
- Scorecards by domain, owner, asset, tenant, policy, control and framework.
- SLA/SLO evaluation with thresholds, breaches and severity.
- Aggregations: count, sum, average, min, max, percentile, latest and rate.
- Trend analysis and simple anomaly hints.
- Alert generation for threshold breaches and stale metrics.
- Export helpers for JSON, JSONL and Prometheus-like text format.
- Pluggable metric stores and audit sinks.

This module is vendor-neutral and dependency-light. It can feed dashboards,
compliance reports, executive scorecards, observability systems and governance
workflow automation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
LabelSet = Dict[str, str]


class GovernanceMetricsError(Exception):
    """Base exception for governance metrics failures."""


class MetricDefinitionError(GovernanceMetricsError):
    """Raised when a metric definition is invalid."""


class MetricEvaluationError(GovernanceMetricsError):
    """Raised when metric evaluation fails."""


class GovernanceMetricDomain(str, enum.Enum):
    ACCESS = "access"
    CLASSIFICATION = "classification"
    MASKING = "masking"
    ENCRYPTION = "encryption"
    PRIVACY = "privacy"
    RETENTION = "retention"
    COMPLIANCE = "compliance"
    POLICY = "policy"
    CATALOG = "catalog"
    LINEAGE = "lineage"
    QUALITY = "quality"
    STEWARDSHIP = "stewardship"
    SECURITY = "security"
    PLATFORM = "platform"


class MetricType(str, enum.Enum):
    KPI = "kpi"
    KRI = "kri"
    SLA = "sla"
    SLO = "slo"
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


class MetricUnit(str, enum.Enum):
    COUNT = "count"
    PERCENT = "percent"
    RATIO = "ratio"
    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    SCORE = "score"
    BYTES = "bytes"
    CURRENCY = "currency"


class Aggregation(str, enum.Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    LATEST = "latest"
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"
    RATE = "rate"


class ThresholdOperator(str, enum.Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"


class MetricSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class MetricStatus(str, enum.Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    BREACHED = "breached"
    NO_DATA = "no_data"
    UNKNOWN = "unknown"


class TrendDirection(str, enum.Enum):
    IMPROVING = "improving"
    WORSENING = "worsening"
    STABLE = "stable"
    INSUFFICIENT_DATA = "insufficient_data"


class ExportFormat(str, enum.Enum):
    JSON = "json"
    JSONL = "jsonl"
    PROMETHEUS = "prometheus"


@dataclass(frozen=True)
class MetricThreshold:
    operator: ThresholdOperator
    value: float
    severity: MetricSeverity = MetricSeverity.WARNING
    message: str = ""

    def breached(self, actual: float) -> bool:
        if self.operator == ThresholdOperator.GT:
            return actual > self.value
        if self.operator == ThresholdOperator.GTE:
            return actual >= self.value
        if self.operator == ThresholdOperator.LT:
            return actual < self.value
        if self.operator == ThresholdOperator.LTE:
            return actual <= self.value
        if self.operator == ThresholdOperator.EQ:
            return actual == self.value
        if self.operator == ThresholdOperator.NE:
            return actual != self.value
        return False

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    description: str
    domain: GovernanceMetricDomain
    metric_type: MetricType
    unit: MetricUnit = MetricUnit.COUNT
    aggregation: Aggregation = Aggregation.LATEST
    thresholds: Tuple[MetricThreshold, ...] = field(default_factory=tuple)
    owner_id: Optional[str] = None
    enabled: bool = True
    expected_frequency_minutes: Optional[int] = None
    higher_is_better: bool = True
    tags: Tuple[str, ...] = field(default_factory=tuple)
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metric_id:
            raise MetricDefinitionError("metric_id is required")
        if not self.name:
            raise MetricDefinitionError("name is required")
        if self.expected_frequency_minutes is not None and self.expected_frequency_minutes <= 0:
            raise MetricDefinitionError("expected_frequency_minutes must be > 0")

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class MetricPoint:
    metric_id: str
    value: float
    timestamp: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    labels: LabelSet = field(default_factory=dict)
    tenant_id: Optional[str] = None
    asset_id: Optional[str] = None
    owner_id: Optional[str] = None
    source: str = "governance_metrics"
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    point_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def label_key(self) -> Tuple[Tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in self.labels.items()))

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class MetricEvaluation:
    metric_id: str
    value: Optional[float]
    status: MetricStatus
    severity: Optional[MetricSeverity] = None
    breached_thresholds: List[MetricThreshold] = field(default_factory=list)
    message: str = ""
    points_evaluated: int = 0
    evaluated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    labels: LabelSet = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "metric_id": self.metric_id,
            "value": self.value,
            "status": self.status.value,
            "severity": self.severity.value if self.severity else None,
            "breached_thresholds": [threshold.to_dict() for threshold in self.breached_thresholds],
            "message": self.message,
            "points_evaluated": self.points_evaluated,
            "evaluated_at": self.evaluated_at.isoformat(),
            "labels": dict(self.labels),
            "metadata": dict(self.metadata),
        }


@dataclass
class MetricAlert:
    alert_id: str
    metric_id: str
    severity: MetricSeverity
    status: MetricStatus
    message: str
    value: Optional[float]
    labels: LabelSet = field(default_factory=dict)
    tenant_id: Optional[str] = None
    asset_id: Optional[str] = None
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    resolved_at: Optional[dt.datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def resolve(self) -> None:
        self.resolved_at = dt.datetime.now(dt.timezone.utc)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class MetricTrend:
    metric_id: str
    direction: TrendDirection
    slope: float
    first_value: Optional[float]
    last_value: Optional[float]
    delta: Optional[float]
    delta_percent: Optional[float]
    points_analyzed: int
    message: str = ""

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class GovernanceScorecard:
    scorecard_id: str
    title: str
    domain: Optional[GovernanceMetricDomain]
    overall_score: float
    status: MetricStatus
    evaluations: List[MetricEvaluation]
    trends: List[MetricTrend] = field(default_factory=list)
    alerts: List[MetricAlert] = field(default_factory=list)
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    metadata: JsonDict = field(default_factory=dict)
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "scorecard_id": self.scorecard_id,
            "title": self.title,
            "domain": self.domain.value if self.domain else None,
            "overall_score": self.overall_score,
            "status": self.status.value,
            "evaluations": [evaluation.to_dict() for evaluation in self.evaluations],
            "trends": [trend.to_dict() for trend in self.trends],
            "alerts": [alert.to_dict() for alert in self.alerts],
            "generated_at": self.generated_at.isoformat(),
            "metadata": dict(self.metadata),
            "audit_hash": self.audit_hash,
        }


@dataclass(frozen=True)
class MetricQuery:
    metric_ids: Tuple[str, ...] = field(default_factory=tuple)
    domains: Tuple[GovernanceMetricDomain, ...] = field(default_factory=tuple)
    start_time: Optional[dt.datetime] = None
    end_time: Optional[dt.datetime] = None
    labels: LabelSet = field(default_factory=dict)
    tenant_id: Optional[str] = None
    asset_id: Optional[str] = None
    owner_id: Optional[str] = None
    source: Optional[str] = None
    limit: int = 10000
    offset: int = 0


@dataclass(frozen=True)
class GovernanceMetricsConfig:
    stale_multiplier: float = 3.0
    min_points_for_trend: int = 3
    default_score_no_data: float = 0.0
    enable_audit: bool = True
    metadata: JsonDict = field(default_factory=dict)


@runtime_checkable
class MetricsStore(Protocol):
    def upsert_definition(self, definition: MetricDefinition) -> None:
        ...

    def get_definition(self, metric_id: str) -> Optional[MetricDefinition]:
        ...

    def list_definitions(self, domain: Optional[GovernanceMetricDomain] = None, enabled_only: bool = True) -> List[MetricDefinition]:
        ...

    def write_point(self, point: MetricPoint) -> None:
        ...

    def write_points(self, points: Sequence[MetricPoint]) -> None:
        ...

    def query_points(self, query: MetricQuery) -> List[MetricPoint]:
        ...


class InMemoryMetricsStore(MetricsStore):
    """In-memory metrics store for tests, local runs and fallback mode."""

    def __init__(self) -> None:
        self.definitions: Dict[str, MetricDefinition] = {}
        self.points: List[MetricPoint] = []

    def upsert_definition(self, definition: MetricDefinition) -> None:
        self.definitions[definition.metric_id] = definition

    def get_definition(self, metric_id: str) -> Optional[MetricDefinition]:
        return self.definitions.get(metric_id)

    def list_definitions(self, domain: Optional[GovernanceMetricDomain] = None, enabled_only: bool = True) -> List[MetricDefinition]:
        definitions = list(self.definitions.values())
        if domain:
            definitions = [definition for definition in definitions if definition.domain == domain]
        if enabled_only:
            definitions = [definition for definition in definitions if definition.enabled]
        return sorted(definitions, key=lambda definition: definition.metric_id)

    def write_point(self, point: MetricPoint) -> None:
        self.points.append(point)

    def write_points(self, points: Sequence[MetricPoint]) -> None:
        self.points.extend(points)

    def query_points(self, query: MetricQuery) -> List[MetricPoint]:
        matched = [point for point in self.points if matches_query(point, query, self.definitions)]
        matched.sort(key=lambda point: point.timestamp)
        return matched[query.offset : query.offset + query.limit]


@runtime_checkable
class MetricsAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingMetricsAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("governance_metrics_audit", extra={"event_type": event_type, "payload": dict(payload)})


class GovernanceMetricsEngine:
    """Main enterprise governance metrics engine."""

    def __init__(
        self,
        store: Optional[MetricsStore] = None,
        *,
        config: Optional[GovernanceMetricsConfig] = None,
        audit_sink: Optional[MetricsAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.store = store or InMemoryMetricsStore()
        self.config = config or GovernanceMetricsConfig()
        self.audit = audit_sink or LoggingMetricsAuditSink()
        self.log = log or logger

    def register_metric(self, definition: MetricDefinition) -> None:
        self.store.upsert_definition(definition)
        self._audit("metric_registered", definition.to_dict())

    def register_defaults(self) -> None:
        for definition in default_metric_definitions():
            self.register_metric(definition)

    def record_point(self, point: MetricPoint) -> None:
        definition = self.store.get_definition(point.metric_id)
        if not definition:
            raise MetricDefinitionError(f"Unknown metric_id: {point.metric_id}")
        self.store.write_point(point)
        self._audit("metric_point_recorded", point.to_dict())

    def record_points(self, points: Sequence[MetricPoint]) -> None:
        for point in points:
            if not self.store.get_definition(point.metric_id):
                raise MetricDefinitionError(f"Unknown metric_id: {point.metric_id}")
        self.store.write_points(points)
        self._audit("metric_points_recorded", {"count": len(points), "metric_ids": sorted(set(p.metric_id for p in points))})

    def increment(self, metric_id: str, amount: float = 1.0, **kwargs: Any) -> MetricPoint:
        point = MetricPoint(metric_id=metric_id, value=amount, **kwargs)
        self.record_point(point)
        return point

    def gauge(self, metric_id: str, value: float, **kwargs: Any) -> MetricPoint:
        point = MetricPoint(metric_id=metric_id, value=float(value), **kwargs)
        self.record_point(point)
        return point

    def evaluate_metric(self, metric_id: str, query: Optional[MetricQuery] = None, *, labels: Optional[LabelSet] = None) -> MetricEvaluation:
        definition = self.store.get_definition(metric_id)
        if not definition:
            raise MetricDefinitionError(f"Unknown metric_id: {metric_id}")
        query = query or MetricQuery(metric_ids=(metric_id,), labels=labels or {})
        points = self.store.query_points(query)
        value = aggregate_points(points, definition.aggregation)
        if value is None:
            evaluation = MetricEvaluation(
                metric_id=metric_id,
                value=None,
                status=MetricStatus.NO_DATA,
                message="No metric data available for evaluation window.",
                points_evaluated=0,
                labels=labels or {},
            )
            self._audit("metric_evaluated", evaluation.to_dict())
            return evaluation

        breached = [threshold for threshold in definition.thresholds if threshold.breached(value)]
        if breached:
            highest = max(breached, key=lambda threshold: severity_rank(threshold.severity))
            status = MetricStatus.BREACHED if highest.severity in {MetricSeverity.ERROR, MetricSeverity.CRITICAL} else MetricStatus.WARNING
            message = highest.message or f"Metric {metric_id} breached threshold {highest.operator.value} {highest.value}"
            severity = highest.severity
        else:
            status = MetricStatus.HEALTHY
            message = "Metric is within expected thresholds."
            severity = None

        stale_message = self._staleness_message(definition, points)
        if stale_message and status == MetricStatus.HEALTHY:
            status = MetricStatus.WARNING
            severity = MetricSeverity.WARNING
            message = stale_message

        evaluation = MetricEvaluation(
            metric_id=metric_id,
            value=round(value, 6),
            status=status,
            severity=severity,
            breached_thresholds=breached,
            message=message,
            points_evaluated=len(points),
            labels=labels or {},
            metadata={"aggregation": definition.aggregation.value, "unit": definition.unit.value},
        )
        self._audit("metric_evaluated", evaluation.to_dict())
        return evaluation

    def generate_alerts(self, evaluations: Sequence[MetricEvaluation], *, tenant_id: Optional[str] = None, asset_id: Optional[str] = None) -> List[MetricAlert]:
        alerts: List[MetricAlert] = []
        for evaluation in evaluations:
            if evaluation.status in {MetricStatus.BREACHED, MetricStatus.WARNING, MetricStatus.NO_DATA}:
                severity = evaluation.severity or (MetricSeverity.WARNING if evaluation.status == MetricStatus.NO_DATA else MetricSeverity.INFO)
                alerts.append(
                    MetricAlert(
                        alert_id=str(uuid.uuid4()),
                        metric_id=evaluation.metric_id,
                        severity=severity,
                        status=evaluation.status,
                        message=evaluation.message,
                        value=evaluation.value,
                        labels=evaluation.labels,
                        tenant_id=tenant_id,
                        asset_id=asset_id,
                        metadata=evaluation.metadata,
                    )
                )
        if alerts:
            self._audit("metric_alerts_generated", {"count": len(alerts), "alerts": [alert.to_dict() for alert in alerts]})
        return alerts

    def analyze_trend(self, metric_id: str, query: Optional[MetricQuery] = None) -> MetricTrend:
        definition = self.store.get_definition(metric_id)
        if not definition:
            raise MetricDefinitionError(f"Unknown metric_id: {metric_id}")
        query = query or MetricQuery(metric_ids=(metric_id,))
        points = self.store.query_points(query)
        values = [point.value for point in points]
        if len(values) < self.config.min_points_for_trend:
            return MetricTrend(
                metric_id=metric_id,
                direction=TrendDirection.INSUFFICIENT_DATA,
                slope=0.0,
                first_value=values[0] if values else None,
                last_value=values[-1] if values else None,
                delta=None,
                delta_percent=None,
                points_analyzed=len(values),
                message="Insufficient data for trend analysis.",
            )
        slope = linear_slope(values)
        delta = values[-1] - values[0]
        delta_percent = None if values[0] == 0 else (delta / abs(values[0])) * 100
        direction = trend_direction(slope, definition.higher_is_better)
        return MetricTrend(
            metric_id=metric_id,
            direction=direction,
            slope=round(slope, 6),
            first_value=values[0],
            last_value=values[-1],
            delta=round(delta, 6),
            delta_percent=round(delta_percent, 6) if delta_percent is not None else None,
            points_analyzed=len(values),
            message=f"Trend is {direction.value}.",
        )

    def build_scorecard(
        self,
        *,
        title: str,
        domain: Optional[GovernanceMetricDomain] = None,
        query: Optional[MetricQuery] = None,
        include_trends: bool = True,
        include_alerts: bool = True,
    ) -> GovernanceScorecard:
        definitions = self.store.list_definitions(domain=domain, enabled_only=True)
        evaluations: List[MetricEvaluation] = []
        trends: List[MetricTrend] = []
        query = query or MetricQuery(domains=(domain,) if domain else ())

        for definition in definitions:
            metric_query = dataclasses.replace(query, metric_ids=(definition.metric_id,))
            evaluations.append(self.evaluate_metric(definition.metric_id, metric_query))
            if include_trends:
                trends.append(self.analyze_trend(definition.metric_id, metric_query))

        alerts = self.generate_alerts(evaluations) if include_alerts else []
        overall_score = score_evaluations(evaluations, self.config.default_score_no_data)
        status = status_from_score_and_alerts(overall_score, alerts)
        scorecard = GovernanceScorecard(
            scorecard_id=str(uuid.uuid4()),
            title=title,
            domain=domain,
            overall_score=overall_score,
            status=status,
            evaluations=evaluations,
            trends=trends,
            alerts=alerts,
            metadata={"metric_count": len(definitions)},
        )
        scorecard.audit_hash = stable_hash(scorecard.to_dict())
        self._audit("scorecard_generated", scorecard.to_dict())
        return scorecard

    def query_points(self, query: MetricQuery) -> List[MetricPoint]:
        return self.store.query_points(query)

    def summarize(self, query: Optional[MetricQuery] = None) -> JsonDict:
        query = query or MetricQuery(limit=100000)
        points = self.store.query_points(query)
        definitions = {definition.metric_id: definition for definition in self.store.list_definitions(enabled_only=False)}
        return {
            "total_points": len(points),
            "metric_count": len(set(point.metric_id for point in points)),
            "by_metric": dict(Counter(point.metric_id for point in points)),
            "by_domain": dict(Counter(definitions[point.metric_id].domain.value for point in points if point.metric_id in definitions)),
            "by_tenant": dict(Counter(point.tenant_id or "global" for point in points)),
            "by_source": dict(Counter(point.source for point in points)),
        }

    def export(self, query: Optional[MetricQuery] = None, *, fmt: ExportFormat = ExportFormat.JSON) -> str:
        query = query or MetricQuery(limit=100000)
        points = self.store.query_points(query)
        if fmt == ExportFormat.JSON:
            return json.dumps([point.to_dict() for point in points], ensure_ascii=False, indent=2, default=str)
        if fmt == ExportFormat.JSONL:
            return "\n".join(json.dumps(point.to_dict(), ensure_ascii=False, default=str) for point in points)
        if fmt == ExportFormat.PROMETHEUS:
            return points_to_prometheus(points, self.store)
        raise GovernanceMetricsError(f"Unsupported export format: {fmt}")

    def _staleness_message(self, definition: MetricDefinition, points: Sequence[MetricPoint]) -> Optional[str]:
        if not definition.expected_frequency_minutes or not points:
            return None
        latest = max(point.timestamp for point in points)
        age_minutes = (dt.datetime.now(dt.timezone.utc) - latest).total_seconds() / 60
        allowed = definition.expected_frequency_minutes * self.config.stale_multiplier
        if age_minutes > allowed:
            return f"Metric data is stale: latest point is {age_minutes:.1f} minutes old, allowed {allowed:.1f}."
        return None

    def _audit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.config.enable_audit:
            self.audit.emit(event_type, to_json_safe(payload))


# -----------------------------------------------------------------------------
# Default metric definitions
# -----------------------------------------------------------------------------


def default_metric_definitions() -> List[MetricDefinition]:
    return [
        MetricDefinition(
            metric_id="access_review_completion_rate",
            name="Access Review Completion Rate",
            description="Percentage of access review items completed within campaign window.",
            domain=GovernanceMetricDomain.ACCESS,
            metric_type=MetricType.KPI,
            unit=MetricUnit.PERCENT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.LT, 95, MetricSeverity.ERROR, "Access review completion is below 95%."),),
            owner_id="access-governance-owner",
            expected_frequency_minutes=1440,
            higher_is_better=True,
            tags=("access_review", "recertification"),
        ),
        MetricDefinition(
            metric_id="privileged_access_count",
            name="Privileged Access Count",
            description="Number of active privileged entitlements.",
            domain=GovernanceMetricDomain.ACCESS,
            metric_type=MetricType.KRI,
            unit=MetricUnit.COUNT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.GT, 100, MetricSeverity.WARNING, "Privileged access count is elevated."),),
            owner_id="access-governance-owner",
            expected_frequency_minutes=1440,
            higher_is_better=False,
            tags=("privileged_access",),
        ),
        MetricDefinition(
            metric_id="classified_dataset_rate",
            name="Classified Dataset Rate",
            description="Percentage of cataloged datasets with classification metadata.",
            domain=GovernanceMetricDomain.CLASSIFICATION,
            metric_type=MetricType.KPI,
            unit=MetricUnit.PERCENT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.LT, 98, MetricSeverity.ERROR, "Dataset classification coverage is below target."),),
            owner_id="data-governance-owner",
            expected_frequency_minutes=1440,
            higher_is_better=True,
            tags=("classification", "catalog"),
        ),
        MetricDefinition(
            metric_id="restricted_data_without_encryption",
            name="Restricted Data Without Encryption",
            description="Count of restricted assets missing encryption evidence.",
            domain=GovernanceMetricDomain.ENCRYPTION,
            metric_type=MetricType.KRI,
            unit=MetricUnit.COUNT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.GT, 0, MetricSeverity.CRITICAL, "Restricted data exists without encryption."),),
            owner_id="security-owner",
            expected_frequency_minutes=1440,
            higher_is_better=False,
            tags=("encryption", "restricted_data"),
        ),
        MetricDefinition(
            metric_id="compliance_control_pass_rate",
            name="Compliance Control Pass Rate",
            description="Percentage of assessed controls that are compliant.",
            domain=GovernanceMetricDomain.COMPLIANCE,
            metric_type=MetricType.KPI,
            unit=MetricUnit.PERCENT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.LT, 90, MetricSeverity.ERROR, "Compliance control pass rate is below 90%."),),
            owner_id="compliance-owner",
            expected_frequency_minutes=1440,
            higher_is_better=True,
            tags=("compliance", "controls"),
        ),
        MetricDefinition(
            metric_id="open_critical_findings",
            name="Open Critical Findings",
            description="Number of open critical governance/compliance findings.",
            domain=GovernanceMetricDomain.COMPLIANCE,
            metric_type=MetricType.KRI,
            unit=MetricUnit.COUNT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.GT, 0, MetricSeverity.CRITICAL, "There are open critical findings."),),
            owner_id="compliance-owner",
            expected_frequency_minutes=1440,
            higher_is_better=False,
            tags=("findings", "critical"),
        ),
        MetricDefinition(
            metric_id="data_quality_sla_breach_count",
            name="Data Quality SLA Breach Count",
            description="Number of data quality SLA breaches in the evaluation period.",
            domain=GovernanceMetricDomain.QUALITY,
            metric_type=MetricType.SLA,
            unit=MetricUnit.COUNT,
            aggregation=Aggregation.SUM,
            thresholds=(MetricThreshold(ThresholdOperator.GT, 0, MetricSeverity.WARNING, "Data quality SLA breaches detected."),),
            owner_id="data-quality-owner",
            expected_frequency_minutes=60,
            higher_is_better=False,
            tags=("quality", "sla"),
        ),
        MetricDefinition(
            metric_id="lineage_coverage_rate",
            name="Lineage Coverage Rate",
            description="Percentage of critical assets with complete upstream/downstream lineage.",
            domain=GovernanceMetricDomain.LINEAGE,
            metric_type=MetricType.KPI,
            unit=MetricUnit.PERCENT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.LT, 90, MetricSeverity.WARNING, "Lineage coverage is below target."),),
            owner_id="metadata-owner",
            expected_frequency_minutes=1440,
            higher_is_better=True,
            tags=("lineage", "catalog"),
        ),
        MetricDefinition(
            metric_id="privacy_request_sla_rate",
            name="Privacy Request SLA Rate",
            description="Percentage of privacy/data subject requests completed within SLA.",
            domain=GovernanceMetricDomain.PRIVACY,
            metric_type=MetricType.SLA,
            unit=MetricUnit.PERCENT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.LT, 95, MetricSeverity.ERROR, "Privacy request SLA rate is below 95%."),),
            owner_id="privacy-owner",
            expected_frequency_minutes=1440,
            higher_is_better=True,
            tags=("privacy", "sla"),
        ),
        MetricDefinition(
            metric_id="stewardship_task_overdue_count",
            name="Overdue Stewardship Tasks",
            description="Number of open stewardship tasks past due date.",
            domain=GovernanceMetricDomain.STEWARDSHIP,
            metric_type=MetricType.KRI,
            unit=MetricUnit.COUNT,
            aggregation=Aggregation.LATEST,
            thresholds=(MetricThreshold(ThresholdOperator.GT, 20, MetricSeverity.WARNING, "Stewardship overdue backlog is high."),),
            owner_id="stewardship-owner",
            expected_frequency_minutes=1440,
            higher_is_better=False,
            tags=("stewardship", "workflow"),
        ),
    ]


# -----------------------------------------------------------------------------
# Aggregation and scoring helpers
# -----------------------------------------------------------------------------


def aggregate_points(points: Sequence[MetricPoint], aggregation: Aggregation) -> Optional[float]:
    if not points:
        return None
    values = [float(point.value) for point in points]
    if aggregation == Aggregation.COUNT:
        return float(len(values))
    if aggregation == Aggregation.SUM:
        return float(sum(values))
    if aggregation == Aggregation.AVG:
        return float(sum(values) / len(values))
    if aggregation == Aggregation.MIN:
        return float(min(values))
    if aggregation == Aggregation.MAX:
        return float(max(values))
    if aggregation == Aggregation.LATEST:
        return float(sorted(points, key=lambda point: point.timestamp)[-1].value)
    if aggregation == Aggregation.P50:
        return percentile(values, 50)
    if aggregation == Aggregation.P90:
        return percentile(values, 90)
    if aggregation == Aggregation.P95:
        return percentile(values, 95)
    if aggregation == Aggregation.P99:
        return percentile(values, 99)
    if aggregation == Aggregation.RATE:
        ordered = sorted(points, key=lambda point: point.timestamp)
        elapsed = (ordered[-1].timestamp - ordered[0].timestamp).total_seconds()
        return 0.0 if elapsed <= 0 else float(sum(values) / elapsed)
    return None


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_values[int(k)])
    return float(sorted_values[f] * (c - k) + sorted_values[c] * (k - f))


def linear_slope(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator


def trend_direction(slope: float, higher_is_better: bool, epsilon: float = 1e-9) -> TrendDirection:
    if abs(slope) <= epsilon:
        return TrendDirection.STABLE
    improving = slope > 0 if higher_is_better else slope < 0
    return TrendDirection.IMPROVING if improving else TrendDirection.WORSENING


def severity_rank(severity: MetricSeverity) -> int:
    return {
        MetricSeverity.INFO: 10,
        MetricSeverity.WARNING: 20,
        MetricSeverity.ERROR: 30,
        MetricSeverity.CRITICAL: 40,
    }[severity]


def score_evaluations(evaluations: Sequence[MetricEvaluation], no_data_score: float = 0.0) -> float:
    if not evaluations:
        return 0.0
    scores = []
    for evaluation in evaluations:
        if evaluation.status == MetricStatus.HEALTHY:
            scores.append(100.0)
        elif evaluation.status == MetricStatus.WARNING:
            scores.append(75.0)
        elif evaluation.status == MetricStatus.BREACHED:
            scores.append(35.0 if evaluation.severity != MetricSeverity.CRITICAL else 0.0)
        elif evaluation.status == MetricStatus.NO_DATA:
            scores.append(no_data_score)
        else:
            scores.append(50.0)
    return round(sum(scores) / len(scores), 6)


def status_from_score_and_alerts(score: float, alerts: Sequence[MetricAlert]) -> MetricStatus:
    if any(alert.severity == MetricSeverity.CRITICAL for alert in alerts):
        return MetricStatus.BREACHED
    if any(alert.severity == MetricSeverity.ERROR for alert in alerts):
        return MetricStatus.BREACHED
    if any(alert.severity == MetricSeverity.WARNING for alert in alerts):
        return MetricStatus.WARNING
    if score >= 90:
        return MetricStatus.HEALTHY
    if score >= 70:
        return MetricStatus.WARNING
    return MetricStatus.BREACHED


# -----------------------------------------------------------------------------
# Query/export helpers
# -----------------------------------------------------------------------------


def matches_query(point: MetricPoint, query: MetricQuery, definitions: Mapping[str, MetricDefinition]) -> bool:
    if query.metric_ids and point.metric_id not in query.metric_ids:
        return False
    definition = definitions.get(point.metric_id)
    if query.domains and (definition is None or definition.domain not in query.domains):
        return False
    if query.start_time and point.timestamp < query.start_time:
        return False
    if query.end_time and point.timestamp > query.end_time:
        return False
    if query.tenant_id and point.tenant_id != query.tenant_id:
        return False
    if query.asset_id and point.asset_id != query.asset_id:
        return False
    if query.owner_id and point.owner_id != query.owner_id:
        return False
    if query.source and point.source != query.source:
        return False
    for key, value in query.labels.items():
        if point.labels.get(key) != value:
            return False
    return True


def points_to_prometheus(points: Sequence[MetricPoint], store: MetricsStore) -> str:
    lines: List[str] = []
    emitted_help: Set[str] = set()
    for point in points:
        definition = store.get_definition(point.metric_id)
        metric_name = prometheus_name(point.metric_id)
        if definition and metric_name not in emitted_help:
            lines.append(f"# HELP {metric_name} {definition.description}")
            metric_type = "gauge" if definition.metric_type in {MetricType.KPI, MetricType.KRI, MetricType.SLA, MetricType.SLO, MetricType.GAUGE} else "counter"
            lines.append(f"# TYPE {metric_name} {metric_type}")
            emitted_help.add(metric_name)
        labels = dict(point.labels)
        if point.tenant_id:
            labels["tenant_id"] = point.tenant_id
        if point.asset_id:
            labels["asset_id"] = point.asset_id
        label_text = ""
        if labels:
            label_text = "{" + ",".join(f'{k}="{escape_label(v)}"' for k, v in sorted(labels.items())) + "}"
        timestamp_ms = int(point.timestamp.timestamp() * 1000)
        lines.append(f"{metric_name}{label_text} {point.value} {timestamp_ms}")
    return "\n".join(lines)


def prometheus_name(metric_id: str) -> str:
    return "governance_" + "".join(ch if ch.isalnum() else "_" for ch in metric_id.lower()).strip("_")


def escape_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


# -----------------------------------------------------------------------------
# JSON helpers
# -----------------------------------------------------------------------------


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


# -----------------------------------------------------------------------------
# Convenience factory
# -----------------------------------------------------------------------------


def build_default_governance_metrics_engine() -> GovernanceMetricsEngine:
    engine = GovernanceMetricsEngine()
    engine.register_defaults()
    return engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_governance_metrics_engine()
    now = dt.datetime.now(dt.timezone.utc)
    engine.gauge("access_review_completion_rate", 97.5, timestamp=now, tenant_id="tenant-a", labels={"campaign": "q1"})
    engine.gauge("privileged_access_count", 42, timestamp=now, tenant_id="tenant-a")
    engine.gauge("classified_dataset_rate", 93.0, timestamp=now, tenant_id="tenant-a")
    engine.gauge("restricted_data_without_encryption", 0, timestamp=now, tenant_id="tenant-a")
    engine.gauge("compliance_control_pass_rate", 88.0, timestamp=now, tenant_id="tenant-a")
    engine.gauge("open_critical_findings", 1, timestamp=now, tenant_id="tenant-a")

    scorecard = engine.build_scorecard(title="Enterprise Governance Scorecard")
    print(json.dumps(scorecard.to_dict(), indent=2, ensure_ascii=False, default=str))
    print(engine.export(MetricQuery(tenant_id="tenant-a"), fmt=ExportFormat.PROMETHEUS))
