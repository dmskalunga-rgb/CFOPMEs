"""
data/quality/quality_metrics.py

Enterprise-grade Quality Metrics module.

This module centralizes metric collection, aggregation, querying, export, and
SLO/SLA evaluation for data quality platforms.

Main capabilities:
- Unified metric model for data quality checks and pipelines
- Counters, gauges, timings, histograms, distributions and rates
- In-memory metric registry with thread-safe writes
- Time-series snapshots and rolling aggregation
- SLO/SLA evaluation helpers
- Prometheus text exposition export
- JSON export for dashboards, audits and APIs
- Metric tags/dimensions for dataset/domain/pipeline/rule/status
- Pluggable sink protocol compatible with other quality modules
- Lightweight dependency footprint

Designed for enterprise data observability, lakehouse quality gates,
orchestration monitoring, governance dashboards, compliance evidence, and
operational SRE-style data quality management.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class QualityMetricsError(Exception):
    """Base exception for quality metrics failures."""


class QualityMetricsConfigurationError(QualityMetricsError):
    """Raised when metric configuration is invalid."""


class QualityMetricsExecutionError(QualityMetricsError):
    """Raised when metric execution/export fails."""


class QualitySLOViolation(QualityMetricsError):
    """Raised when an SLO/SLA assertion fails."""


# =============================================================================
# Enums
# =============================================================================


class MetricType(str, Enum):
    """Supported metric types."""

    COUNTER = "counter"
    GAUGE = "gauge"
    TIMER = "timer"
    HISTOGRAM = "histogram"
    DISTRIBUTION = "distribution"
    RATE = "rate"


class MetricUnit(str, Enum):
    """Common metric units."""

    COUNT = "count"
    RATIO = "ratio"
    PERCENT = "percent"
    MILLISECONDS = "milliseconds"
    SECONDS = "seconds"
    BYTES = "bytes"
    RECORDS = "records"
    ROWS = "rows"
    COLUMNS = "columns"
    SCORE = "score"
    BOOLEAN = "boolean"
    UNKNOWN = "unknown"


class QualityDimension(str, Enum):
    """Standard data quality dimensions."""

    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    FRESHNESS = "freshness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    INTEGRITY = "integrity"
    PROFILING = "profiling"
    NULL_ANALYSIS = "null_analysis"
    AUDIT = "audit"
    DASHBOARD = "dashboard"
    CUSTOM = "custom"


class MetricAggregation(str, Enum):
    """Aggregation modes."""

    SUM = "sum"
    MIN = "min"
    MAX = "max"
    AVG = "avg"
    COUNT = "count"
    LAST = "last"
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"


class SLOStatus(str, Enum):
    """SLO evaluation status."""

    PASSED = "passed"
    WARNING = "warning"
    VIOLATED = "violated"
    UNKNOWN = "unknown"


class ComparisonOperator(str, Enum):
    """Comparison operator for SLO rules."""

    GTE = "gte"
    GT = "gt"
    LTE = "lte"
    LT = "lt"
    EQ = "eq"
    NE = "ne"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Minimal metrics sink protocol used across quality modules."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class MetricIdentity:
    """Stable metric identity based on name and tags."""

    name: str
    tags: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @staticmethod
    def create(name: str, tags: Optional[Mapping[str, Any]] = None) -> "MetricIdentity":
        if not name or not name.strip():
            raise QualityMetricsConfigurationError("Metric name is required.")
        normalized_tags = tuple(sorted((str(k), str(v)) for k, v in (tags or {}).items()))
        return MetricIdentity(name=name.strip(), tags=normalized_tags)

    def tag_dict(self) -> Dict[str, str]:
        return dict(self.tags)

    def key(self) -> str:
        if not self.tags:
            return self.name
        tag_payload = ",".join(f"{k}={v}" for k, v in self.tags)
        return f"{self.name}{{{tag_payload}}}"


@dataclass
class MetricPoint:
    """Single metric observation."""

    metric_id: str
    name: str
    metric_type: MetricType
    value: float
    timestamp: str
    unit: MetricUnit = MetricUnit.UNKNOWN
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["unit"] = self.unit.value
        return _json_safe(data)


@dataclass
class MetricDefinition:
    """Metric metadata definition."""

    name: str
    metric_type: MetricType
    unit: MetricUnit = MetricUnit.UNKNOWN
    description: Optional[str] = None
    dimension: QualityDimension = QualityDimension.CUSTOM
    owner: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise QualityMetricsConfigurationError("MetricDefinition.name is required.")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["unit"] = self.unit.value
        data["dimension"] = self.dimension.value
        return _json_safe(data)


@dataclass
class MetricSeriesSummary:
    """Aggregated metric series summary."""

    name: str
    tags: Dict[str, str]
    metric_type: MetricType
    unit: MetricUnit
    count: int
    sum: float
    min: Optional[float]
    max: Optional[float]
    avg: Optional[float]
    last: Optional[float]
    p50: Optional[float]
    p90: Optional[float]
    p95: Optional[float]
    p99: Optional[float]
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["unit"] = self.unit.value
        return _json_safe(data)


@dataclass(frozen=True)
class MetricQuery:
    """Query filters for metric points."""

    names: Optional[Sequence[str]] = None
    tags: Dict[str, str] = field(default_factory=dict)
    metric_types: Optional[Sequence[MetricType]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    limit: Optional[int] = None


@dataclass(frozen=True)
class SLORule:
    """SLO/SLA rule against an aggregated metric."""

    name: str
    metric_name: str
    aggregation: MetricAggregation
    operator: ComparisonOperator
    threshold: float
    warning_threshold: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)
    window_seconds: Optional[int] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise QualityMetricsConfigurationError("SLORule.name is required.")
        if not self.metric_name.strip():
            raise QualityMetricsConfigurationError("SLORule.metric_name is required.")
        if self.window_seconds is not None and self.window_seconds <= 0:
            raise QualityMetricsConfigurationError("SLORule.window_seconds must be positive.")


@dataclass
class SLOEvaluation:
    """SLO/SLA evaluation result."""

    evaluation_id: str
    rule_name: str
    metric_name: str
    status: SLOStatus
    observed_value: Optional[float]
    threshold: float
    warning_threshold: Optional[float]
    operator: ComparisonOperator
    aggregation: MetricAggregation
    evaluated_at: str
    message: str
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["operator"] = self.operator.value
        data["aggregation"] = self.aggregation.value
        return _json_safe(data)


@dataclass
class QualityMetricsSnapshot:
    """Snapshot of metric summaries and SLO evaluations."""

    snapshot_id: str
    generated_at: str
    metric_count: int
    point_count: int
    summaries: List[MetricSeriesSummary]
    slo_evaluations: List[SLOEvaluation] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "metric_count": self.metric_count,
            "point_count": self.point_count,
            "summaries": [s.to_dict() for s in self.summaries],
            "slo_evaluations": [e.to_dict() for e in self.slo_evaluations],
            "metadata": _json_safe(self.metadata),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save_json(self, path: str | Path, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(indent=indent), encoding="utf-8")
        return output


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, deque)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    clean = sorted(float(v) for v in values if not math.isnan(float(v)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return clean[int(rank)]
    weight = rank - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight


def _compare(observed: float, operator: ComparisonOperator, threshold: float) -> bool:
    if operator == ComparisonOperator.GTE:
        return observed >= threshold
    if operator == ComparisonOperator.GT:
        return observed > threshold
    if operator == ComparisonOperator.LTE:
        return observed <= threshold
    if operator == ComparisonOperator.LT:
        return observed < threshold
    if operator == ComparisonOperator.EQ:
        return observed == threshold
    if operator == ComparisonOperator.NE:
        return observed != threshold
    raise QualityMetricsConfigurationError(f"Unsupported comparison operator: {operator}")


def _prometheus_name(name: str) -> str:
    safe = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            safe.append(ch)
        elif ch in {".", "-", " ", "/"}:
            safe.append("_")
    result = "".join(safe).strip("_")
    if not result:
        result = "metric"
    if result[0].isdigit():
        result = f"m_{result}"
    return result


def _prometheus_labels(tags: Mapping[str, str]) -> str:
    if not tags:
        return ""
    labels = []
    for key, value in sorted(tags.items()):
        safe_key = _prometheus_name(key)
        safe_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
        labels.append(f'{safe_key}="{safe_value}"')
    return "{" + ",".join(labels) + "}"


# =============================================================================
# Registry / Sink
# =============================================================================


class QualityMetricRegistry(MetricsSink):
    """
    Thread-safe in-memory metric registry.

    The registry also implements MetricsSink, so it can be injected directly
    into other quality modules such as checkers, analyzers, dashboards and
    audit services.
    """

    def __init__(
        self,
        *,
        max_points_per_series: int = 10_000,
        default_tags: Optional[Mapping[str, Any]] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        if max_points_per_series <= 0:
            raise QualityMetricsConfigurationError("max_points_per_series must be positive.")
        self.max_points_per_series = max_points_per_series
        self.default_tags = {str(k): str(v) for k, v in (default_tags or {}).items()}
        self.logger = logger_ or logger
        self._definitions: Dict[str, MetricDefinition] = {}
        self._series: Dict[MetricIdentity, Deque[MetricPoint]] = defaultdict(
            lambda: deque(maxlen=self.max_points_per_series)
        )
        self._lock = threading.RLock()

    def register(self, definition: MetricDefinition) -> None:
        """Register or replace metric metadata."""
        definition.validate()
        with self._lock:
            self._definitions[definition.name] = definition

    def record(
        self,
        name: str,
        value: float,
        *,
        metric_type: MetricType = MetricType.GAUGE,
        unit: MetricUnit = MetricUnit.UNKNOWN,
        tags: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> MetricPoint:
        """Record a metric point."""
        all_tags = {**self.default_tags, **{str(k): str(v) for k, v in (tags or {}).items()}}
        identity = MetricIdentity.create(name, all_tags)
        definition = self._definitions.get(name)
        if definition:
            metric_type = definition.metric_type
            if unit == MetricUnit.UNKNOWN:
                unit = definition.unit
            all_tags = {**definition.tags, **all_tags}
            identity = MetricIdentity.create(name, all_tags)

        point = MetricPoint(
            metric_id=identity.key(),
            name=name,
            metric_type=metric_type,
            value=float(value),
            timestamp=timestamp or utc_now_iso(),
            unit=unit,
            tags=all_tags,
            metadata=dict(metadata or {}),
        )

        with self._lock:
            self._series[identity].append(point)
        return point

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        """Increment a counter metric."""
        current = self.latest_value(metric_name, tags=tags) or 0.0
        self.record(
            metric_name,
            current + value,
            metric_type=MetricType.COUNTER,
            unit=MetricUnit.COUNT,
            tags=tags,
        )

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a gauge value."""
        self.record(metric_name, value, metric_type=MetricType.GAUGE, unit=MetricUnit.UNKNOWN, tags=tags)

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a timing value in milliseconds."""
        self.record(
            metric_name,
            value_ms,
            metric_type=MetricType.TIMER,
            unit=MetricUnit.MILLISECONDS,
            tags=tags,
        )

    def histogram(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Record a histogram/distribution observation."""
        self.record(
            metric_name,
            value,
            metric_type=MetricType.HISTOGRAM,
            unit=MetricUnit.UNKNOWN,
            tags=tags,
        )

    def latest_value(self, metric_name: str, *, tags: Optional[Mapping[str, Any]] = None) -> Optional[float]:
        identity = MetricIdentity.create(metric_name, {**self.default_tags, **{str(k): str(v) for k, v in (tags or {}).items()}})
        with self._lock:
            points = self._series.get(identity)
            if points:
                return points[-1].value
        return None

    def points(self, query: Optional[MetricQuery] = None) -> List[MetricPoint]:
        """Return metric points matching a query."""
        query = query or MetricQuery()
        started_at = _parse_iso(query.started_at)
        finished_at = _parse_iso(query.finished_at)
        names = set(query.names or []) if query.names else None
        metric_types = {item.value for item in query.metric_types} if query.metric_types else None

        with self._lock:
            all_points = [point for series in self._series.values() for point in series]

        result: List[MetricPoint] = []
        for point in all_points:
            point_dt = _parse_iso(point.timestamp)
            if names and point.name not in names:
                continue
            if metric_types and point.metric_type.value not in metric_types:
                continue
            if query.tags and any(point.tags.get(k) != v for k, v in query.tags.items()):
                continue
            if started_at and point_dt and point_dt < started_at:
                continue
            if finished_at and point_dt and point_dt > finished_at:
                continue
            result.append(point)

        result.sort(key=lambda p: p.timestamp)
        if query.limit is not None:
            return result[: query.limit]
        return result

    def summaries(self, query: Optional[MetricQuery] = None) -> List[MetricSeriesSummary]:
        """Return aggregated summaries per metric series."""
        points = self.points(query)
        grouped: Dict[str, List[MetricPoint]] = defaultdict(list)
        for point in points:
            grouped[point.metric_id].append(point)

        summaries: List[MetricSeriesSummary] = []
        for series_points in grouped.values():
            series_points.sort(key=lambda p: p.timestamp)
            values = [point.value for point in series_points]
            first = series_points[0]
            last = series_points[-1]
            summaries.append(
                MetricSeriesSummary(
                    name=first.name,
                    tags=first.tags,
                    metric_type=first.metric_type,
                    unit=first.unit,
                    count=len(values),
                    sum=round(sum(values), 8),
                    min=round(min(values), 8) if values else None,
                    max=round(max(values), 8) if values else None,
                    avg=round(sum(values) / len(values), 8) if values else None,
                    last=round(last.value, 8) if values else None,
                    p50=_round_optional(_percentile(values, 0.50)),
                    p90=_round_optional(_percentile(values, 0.90)),
                    p95=_round_optional(_percentile(values, 0.95)),
                    p99=_round_optional(_percentile(values, 0.99)),
                    first_timestamp=first.timestamp,
                    last_timestamp=last.timestamp,
                )
            )
        return sorted(summaries, key=lambda s: (s.name, sorted(s.tags.items())))

    def aggregate(self, metric_name: str, aggregation: MetricAggregation, *, tags: Optional[Mapping[str, str]] = None, window_seconds: Optional[int] = None) -> Optional[float]:
        """Aggregate a metric by name, tags and optional recent time window."""
        started_at = None
        if window_seconds is not None:
            started_at = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
        points = self.points(MetricQuery(names=[metric_name], tags=dict(tags or {}), started_at=started_at))
        values = [p.value for p in points]
        if not values:
            return None
        if aggregation == MetricAggregation.SUM:
            return round(sum(values), 8)
        if aggregation == MetricAggregation.MIN:
            return round(min(values), 8)
        if aggregation == MetricAggregation.MAX:
            return round(max(values), 8)
        if aggregation == MetricAggregation.AVG:
            return round(sum(values) / len(values), 8)
        if aggregation == MetricAggregation.COUNT:
            return float(len(values))
        if aggregation == MetricAggregation.LAST:
            return round(points[-1].value, 8)
        if aggregation == MetricAggregation.P50:
            return _round_optional(_percentile(values, 0.50))
        if aggregation == MetricAggregation.P90:
            return _round_optional(_percentile(values, 0.90))
        if aggregation == MetricAggregation.P95:
            return _round_optional(_percentile(values, 0.95))
        if aggregation == MetricAggregation.P99:
            return _round_optional(_percentile(values, 0.99))
        raise QualityMetricsConfigurationError(f"Unsupported aggregation: {aggregation}")

    def evaluate_slo(self, rule: SLORule) -> SLOEvaluation:
        """Evaluate one SLO rule."""
        rule.validate()
        observed = self.aggregate(
            rule.metric_name,
            rule.aggregation,
            tags=rule.tags,
            window_seconds=rule.window_seconds,
        )
        evaluated_at = utc_now_iso()

        if observed is None:
            return SLOEvaluation(
                evaluation_id=str(uuid.uuid4()),
                rule_name=rule.name,
                metric_name=rule.metric_name,
                status=SLOStatus.UNKNOWN,
                observed_value=None,
                threshold=rule.threshold,
                warning_threshold=rule.warning_threshold,
                operator=rule.operator,
                aggregation=rule.aggregation,
                evaluated_at=evaluated_at,
                message="No metric points available for SLO evaluation.",
                tags=dict(rule.tags),
                metadata=rule.metadata,
            )

        passed = _compare(observed, rule.operator, rule.threshold)
        warning = False
        if not passed and rule.warning_threshold is not None:
            warning = _compare(observed, rule.operator, rule.warning_threshold)

        if passed:
            status = SLOStatus.PASSED
            message = "SLO passed."
        elif warning:
            status = SLOStatus.WARNING
            message = "SLO is within warning range."
        else:
            status = SLOStatus.VIOLATED
            message = "SLO violated."

        return SLOEvaluation(
            evaluation_id=str(uuid.uuid4()),
            rule_name=rule.name,
            metric_name=rule.metric_name,
            status=status,
            observed_value=observed,
            threshold=rule.threshold,
            warning_threshold=rule.warning_threshold,
            operator=rule.operator,
            aggregation=rule.aggregation,
            evaluated_at=evaluated_at,
            message=message,
            tags=dict(rule.tags),
            metadata=rule.metadata,
        )

    def evaluate_slos(self, rules: Sequence[SLORule]) -> List[SLOEvaluation]:
        return [self.evaluate_slo(rule) for rule in rules]

    def assert_slo(self, rule: SLORule) -> SLOEvaluation:
        evaluation = self.evaluate_slo(rule)
        if evaluation.status == SLOStatus.VIOLATED:
            raise QualitySLOViolation(evaluation.message)
        return evaluation

    def snapshot(
        self,
        *,
        query: Optional[MetricQuery] = None,
        slo_rules: Optional[Sequence[SLORule]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QualityMetricsSnapshot:
        points = self.points(query)
        summaries = self.summaries(query)
        evaluations = self.evaluate_slos(slo_rules or [])
        return QualityMetricsSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=utc_now_iso(),
            metric_count=len(summaries),
            point_count=len(points),
            summaries=summaries,
            slo_evaluations=evaluations,
            metadata=metadata or {},
        )

    def export_json(self, *, query: Optional[MetricQuery] = None, indent: int = 2) -> str:
        payload = {
            "generated_at": utc_now_iso(),
            "definitions": [definition.to_dict() for definition in self._definitions.values()],
            "points": [point.to_dict() for point in self.points(query)],
            "summaries": [summary.to_dict() for summary in self.summaries(query)],
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)

    def save_json(self, path: str | Path, *, query: Optional[MetricQuery] = None, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.export_json(query=query, indent=indent), encoding="utf-8")
        return output

    def export_prometheus(self, *, query: Optional[MetricQuery] = None) -> str:
        """Export latest metric values in Prometheus text exposition format."""
        summaries = self.summaries(query)
        lines: List[str] = []
        for summary in summaries:
            prom_name = _prometheus_name(summary.name)
            definition = self._definitions.get(summary.name)
            metric_type = "counter" if summary.metric_type == MetricType.COUNTER else "gauge"
            help_text = definition.description if definition and definition.description else f"Data quality metric {summary.name}"
            lines.append(f"# HELP {prom_name} {help_text}")
            lines.append(f"# TYPE {prom_name} {metric_type}")
            value = summary.last if summary.last is not None else 0.0
            lines.append(f"{prom_name}{_prometheus_labels(summary.tags)} {value}")
        return "\n".join(lines) + ("\n" if lines else "")

    def save_prometheus(self, path: str | Path, *, query: Optional[MetricQuery] = None) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.export_prometheus(query=query), encoding="utf-8")
        return output

    def clear(self) -> None:
        with self._lock:
            self._series.clear()


def _round_optional(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 8)


# =============================================================================
# Standard Quality Metrics
# =============================================================================


class StandardQualityMetrics:
    """Standard metric names used across the quality platform."""

    OVERALL_SCORE = "data_quality.overall_score"
    ACCURACY_SCORE = "data_quality.accuracy.score"
    COMPLETENESS_SCORE = "data_quality.completeness.score"
    CONSISTENCY_SCORE = "data_quality.consistency.score"
    FRESHNESS_SCORE = "data_quality.freshness.score"
    VALIDITY_SCORE = "data_quality.validity.score"
    UNIQUENESS_SCORE = "data_quality.uniqueness.score"
    CHECK_DURATION_MS = "data_quality.check.duration_ms"
    CHECK_FAILURES = "data_quality.check.failures"
    CHECK_ERRORS = "data_quality.check.errors"
    FINDING_COUNT = "data_quality.findings.count"
    CRITICAL_FINDING_COUNT = "data_quality.findings.critical_count"
    ROWS_EVALUATED = "data_quality.rows.evaluated"
    ROWS_FAILED = "data_quality.rows.failed"
    NULL_RATE = "data_quality.null_rate"
    STALE_RATE = "data_quality.stale_rate"
    OUTLIER_RATE = "data_quality.outlier_rate"

    @staticmethod
    def definitions() -> List[MetricDefinition]:
        return [
            MetricDefinition(StandardQualityMetrics.OVERALL_SCORE, MetricType.GAUGE, MetricUnit.SCORE, "Overall data quality score", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.ACCURACY_SCORE, MetricType.GAUGE, MetricUnit.SCORE, "Accuracy score", QualityDimension.ACCURACY),
            MetricDefinition(StandardQualityMetrics.COMPLETENESS_SCORE, MetricType.GAUGE, MetricUnit.SCORE, "Completeness score", QualityDimension.COMPLETENESS),
            MetricDefinition(StandardQualityMetrics.CONSISTENCY_SCORE, MetricType.GAUGE, MetricUnit.SCORE, "Consistency score", QualityDimension.CONSISTENCY),
            MetricDefinition(StandardQualityMetrics.FRESHNESS_SCORE, MetricType.GAUGE, MetricUnit.SCORE, "Freshness score", QualityDimension.FRESHNESS),
            MetricDefinition(StandardQualityMetrics.CHECK_DURATION_MS, MetricType.TIMER, MetricUnit.MILLISECONDS, "Quality check duration", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.CHECK_FAILURES, MetricType.COUNTER, MetricUnit.COUNT, "Quality check failures", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.CHECK_ERRORS, MetricType.COUNTER, MetricUnit.COUNT, "Quality check errors", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.FINDING_COUNT, MetricType.GAUGE, MetricUnit.COUNT, "Quality finding count", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.CRITICAL_FINDING_COUNT, MetricType.GAUGE, MetricUnit.COUNT, "Critical quality finding count", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.ROWS_EVALUATED, MetricType.GAUGE, MetricUnit.ROWS, "Rows evaluated", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.ROWS_FAILED, MetricType.GAUGE, MetricUnit.ROWS, "Rows failed", QualityDimension.CUSTOM),
            MetricDefinition(StandardQualityMetrics.NULL_RATE, MetricType.GAUGE, MetricUnit.RATIO, "Null rate", QualityDimension.NULL_ANALYSIS),
            MetricDefinition(StandardQualityMetrics.STALE_RATE, MetricType.GAUGE, MetricUnit.RATIO, "Stale data rate", QualityDimension.FRESHNESS),
            MetricDefinition(StandardQualityMetrics.OUTLIER_RATE, MetricType.GAUGE, MetricUnit.RATIO, "Outlier rate", QualityDimension.PROFILING),
        ]


def create_standard_registry(
    *,
    default_tags: Optional[Mapping[str, Any]] = None,
    max_points_per_series: int = 10_000,
) -> QualityMetricRegistry:
    """Create a registry preloaded with standard quality metric definitions."""
    registry = QualityMetricRegistry(
        default_tags=default_tags,
        max_points_per_series=max_points_per_series,
    )
    for definition in StandardQualityMetrics.definitions():
        registry.register(definition)
    return registry


# =============================================================================
# Report Ingestion Helpers
# =============================================================================


def record_quality_report_metrics(
    registry: QualityMetricRegistry,
    report: Mapping[str, Any],
    *,
    dataset_name: Optional[str] = None,
    dimension: Optional[QualityDimension] = None,
    extra_tags: Optional[Mapping[str, Any]] = None,
) -> None:
    """Record common metrics from a quality report dictionary."""
    dataset_name = dataset_name or report.get("dataset_name") or "unknown"
    tags = {"dataset": dataset_name, **{str(k): str(v) for k, v in (extra_tags or {}).items()}}
    if dimension:
        tags["dimension"] = dimension.value

    score = _first_float(report, ["weighted_score", "overall_score", "quality_score", "score"])
    if score is not None:
        registry.record(StandardQualityMetrics.OVERALL_SCORE, score, metric_type=MetricType.GAUGE, unit=MetricUnit.SCORE, tags=tags)

    duration = _first_float(report, ["duration_ms"])
    if duration is not None:
        registry.timing(StandardQualityMetrics.CHECK_DURATION_MS, duration, tags=tags)

    total_records = _first_float(report, ["total_records", "evaluated_records"])
    if total_records is not None:
        registry.gauge(StandardQualityMetrics.ROWS_EVALUATED, total_records, tags=tags)

    for result in report.get("rule_results") or []:
        item = dict(result)
        rule_tags = {**tags, "rule": str(item.get("rule_name") or "unknown")}
        rule_score = _first_float(item, ["score", "accuracy_score", "completeness_score", "consistency_score", "freshness_score"])
        if rule_score is not None:
            registry.record(StandardQualityMetrics.OVERALL_SCORE, rule_score, metric_type=MetricType.GAUGE, unit=MetricUnit.SCORE, tags=rule_tags)
        failed = _first_float(item, ["failed_records", "incomplete_records", "inconsistent_records", "stale_records"])
        if failed is not None:
            registry.gauge(StandardQualityMetrics.ROWS_FAILED, failed, tags=rule_tags)
        errors = _first_float(item, ["error_records"])
        if errors:
            registry.increment(StandardQualityMetrics.CHECK_ERRORS, int(errors), tags=rule_tags)

    findings = report.get("findings") or []
    if findings:
        registry.gauge(StandardQualityMetrics.FINDING_COUNT, len(findings), tags=tags)
        critical = sum(1 for f in findings if str(dict(f).get("severity", "")).lower() == "critical")
        registry.gauge(StandardQualityMetrics.CRITICAL_FINDING_COUNT, critical, tags=tags)


def _first_float(payload: Mapping[str, Any], keys: Sequence[str]) -> Optional[float]:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                continue
    return None


# =============================================================================
# Context Managers / Timers
# =============================================================================


class MetricTimer:
    """Context manager for timing operations into a registry."""

    def __init__(
        self,
        registry: MetricsSink,
        metric_name: str,
        *,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        self.registry = registry
        self.metric_name = metric_name
        self.tags = tags
        self.started: Optional[float] = None
        self.duration_ms: Optional[float] = None

    def __enter__(self) -> "MetricTimer":
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if self.started is not None:
            self.duration_ms = (time.perf_counter() - self.started) * 1000
            self.registry.timing(self.metric_name, self.duration_ms, tags=self.tags)
        return False


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    registry = create_standard_registry(default_tags={"env": "dev", "domain": "customers"})

    registry.record(
        StandardQualityMetrics.COMPLETENESS_SCORE,
        0.982,
        metric_type=MetricType.GAUGE,
        unit=MetricUnit.SCORE,
        tags={"dataset": "customers"},
    )
    registry.record(
        StandardQualityMetrics.ACCURACY_SCORE,
        0.971,
        metric_type=MetricType.GAUGE,
        unit=MetricUnit.SCORE,
        tags={"dataset": "customers"},
    )
    registry.gauge(StandardQualityMetrics.FINDING_COUNT, 4, tags={"dataset": "customers"})
    registry.gauge(StandardQualityMetrics.CRITICAL_FINDING_COUNT, 1, tags={"dataset": "customers"})

    with MetricTimer(registry, StandardQualityMetrics.CHECK_DURATION_MS, tags={"dataset": "customers", "check": "completeness"}):
        time.sleep(0.01)

    slo = SLORule(
        name="customer_completeness_score_slo",
        metric_name=StandardQualityMetrics.COMPLETENESS_SCORE,
        aggregation=MetricAggregation.LAST,
        operator=ComparisonOperator.GTE,
        threshold=0.98,
        warning_threshold=0.95,
        tags={"dataset": "customers", "env": "dev", "domain": "customers"},
    )

    snapshot = registry.snapshot(slo_rules=[slo])
    print(snapshot.to_json())
    print(registry.export_prometheus())
