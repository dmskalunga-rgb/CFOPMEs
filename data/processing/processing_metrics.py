"""
data/processing/processing_metrics.py

Enterprise-grade processing metrics engine for data platforms.

Purpose
-------
Provides a robust, dependency-light metrics layer for ETL/ELT, batch,
streaming, data cleaning, validation, enrichment, feature engineering,
deduplication, anomaly/outlier detection and distributed processing workloads.

Core capabilities
-----------------
- Counters, gauges, histograms and timers.
- High-level processing metrics: input/output/error/skipped records, duration,
  throughput, latency, retries, dead letters and SLA status.
- Context managers and decorators for timing operations.
- In-memory registry with thread-safe updates.
- Cardinality guard for labels/tags.
- Rolling rates and snapshots.
- JSON and Prometheus text exposition exporters.
- Optional JSONL append sink.
- Optional audit integration.
- Optional telemetry bridge.
- Standard library only.

Example
-------
metrics = ProcessingMetrics()

with metrics.timer("pipeline.stage.duration_ms", labels={"stage": "clean"}):
    run_cleaning()

metrics.increment("records.input", 1000, labels={"pipeline": "sales"})
metrics.gauge("records.output", 990)
print(metrics.snapshot().to_json())
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import logging
import math
import os
import re
import statistics
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
METRIC_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_:]")
MAX_TEXT_LENGTH = 16_384
DEFAULT_MAX_SERIES = 100_000
DEFAULT_RATE_WINDOW = 300
DEFAULT_HISTOGRAM_BUCKETS = (
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1_000.0,
    2_500.0,
    5_000.0,
    10_000.0,
)


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class MetricUnit(str, Enum):
    COUNT = "count"
    RECORDS = "records"
    BYTES = "bytes"
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"
    PERCENT = "percent"
    RATIO = "ratio"
    RATE = "rate"
    UNKNOWN = "unknown"


class SlaStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    BREACHED = "breached"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MetricLabels:
    values: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, labels: Optional[Mapping[str, Any]] = None) -> "MetricLabels":
        cleaned: List[Tuple[str, str]] = []
        for key, value in (labels or {}).items():
            key_str = sanitize_label_key(key)
            if SENSITIVE_KEY_PATTERN.search(key_str):
                cleaned.append((key_str, "[REDACTED]"))
            else:
                cleaned.append((key_str, sanitize_label_value(value)))
        return cls(tuple(sorted(cleaned)))

    def to_dict(self) -> Dict[str, str]:
        return dict(self.values)

    def key(self) -> str:
        return json.dumps(self.values, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    metric_type: MetricType
    unit: MetricUnit = MetricUnit.UNKNOWN
    description: str = ""
    buckets: Tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS
    tags: Tuple[str, ...] = field(default_factory=tuple)

    def normalized_name(self) -> str:
        return normalize_metric_name(self.name)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["unit"] = self.unit.value
        return sanitize_mapping(data)


@dataclass
class MetricSeries:
    definition: MetricDefinition
    labels: MetricLabels
    value: float = 0.0
    count: int = 0
    sum: float = 0.0
    min: Optional[float] = None
    max: Optional[float] = None
    buckets: Dict[float, int] = field(default_factory=dict)
    recent_events: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=10_000))
    last_updated: str = field(default_factory=lambda: utc_now_iso())

    def update_counter(self, amount: float) -> None:
        self.value += amount
        self.count += 1
        self.sum += amount
        self._track_recent(amount)

    def update_gauge(self, value: float) -> None:
        self.value = value
        self.count += 1
        self.sum += value
        self._update_min_max(value)
        self._track_recent(value)

    def observe(self, value: float) -> None:
        self.value = value
        self.count += 1
        self.sum += value
        self._update_min_max(value)
        for bucket in self.definition.buckets:
            if value <= bucket:
                self.buckets[bucket] = self.buckets.get(bucket, 0) + 1
        self.buckets[math.inf] = self.buckets.get(math.inf, 0) + 1
        self._track_recent(value)

    def rate(self, window_seconds: int = DEFAULT_RATE_WINDOW) -> float:
        cutoff = time.time() - window_seconds
        values = [amount for ts, amount in self.recent_events if ts >= cutoff]
        return sum(values) / window_seconds if window_seconds > 0 else 0.0

    def average(self) -> Optional[float]:
        return self.sum / self.count if self.count else None

    def percentile(self, percentile_value: float) -> Optional[float]:
        values = sorted(amount for _ts, amount in self.recent_events)
        if not values:
            return None
        return percentile(values, percentile_value)

    def to_dict(self, *, rate_window_seconds: int = DEFAULT_RATE_WINDOW) -> Dict[str, Any]:
        return sanitize_mapping(
            {
                "name": self.definition.normalized_name(),
                "type": self.definition.metric_type.value,
                "unit": self.definition.unit.value,
                "description": self.definition.description,
                "labels": self.labels.to_dict(),
                "value": round_float(self.value),
                "count": self.count,
                "sum": round_float(self.sum),
                "min": round_float(self.min),
                "max": round_float(self.max),
                "avg": round_float(self.average()),
                "rate_per_second": round_float(self.rate(rate_window_seconds)),
                "p50": round_float(self.percentile(50)),
                "p95": round_float(self.percentile(95)),
                "p99": round_float(self.percentile(99)),
                "buckets": {bucket_label(k): v for k, v in sorted(self.buckets.items(), key=lambda item: item[0])},
                "last_updated": self.last_updated,
            }
        )

    def _update_min_max(self, value: float) -> None:
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)

    def _track_recent(self, amount: float) -> None:
        self.recent_events.append((time.time(), amount))
        self.last_updated = utc_now_iso()


@dataclass(frozen=True)
class ProcessingMetricEvent:
    id: str
    timestamp: str
    metric: str
    metric_type: MetricType
    value: float
    labels: Dict[str, str]
    unit: MetricUnit = MetricUnit.UNKNOWN
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["unit"] = self.unit.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class ProcessingSla:
    name: str
    max_duration_ms: Optional[float] = None
    min_throughput_per_second: Optional[float] = None
    max_error_rate: Optional[float] = None
    max_dead_letters: Optional[int] = None
    warning_ratio: float = 0.8

    def evaluate(self, summary: "ProcessingSummary") -> Tuple[SlaStatus, List[str]]:
        issues: List[str] = []
        warnings: List[str] = []
        if self.max_duration_ms is not None:
            if summary.duration_ms > self.max_duration_ms:
                issues.append(f"duration_ms {summary.duration_ms:.3f} > {self.max_duration_ms:.3f}")
            elif summary.duration_ms > self.max_duration_ms * self.warning_ratio:
                warnings.append(f"duration_ms near SLA: {summary.duration_ms:.3f}/{self.max_duration_ms:.3f}")
        if self.min_throughput_per_second is not None:
            if summary.throughput_per_second < self.min_throughput_per_second:
                issues.append(f"throughput {summary.throughput_per_second:.3f} < {self.min_throughput_per_second:.3f}")
        if self.max_error_rate is not None:
            if summary.error_rate > self.max_error_rate:
                issues.append(f"error_rate {summary.error_rate:.6f} > {self.max_error_rate:.6f}")
            elif summary.error_rate > self.max_error_rate * self.warning_ratio:
                warnings.append(f"error_rate near SLA: {summary.error_rate:.6f}/{self.max_error_rate:.6f}")
        if self.max_dead_letters is not None:
            if summary.dead_letter_count > self.max_dead_letters:
                issues.append(f"dead_letter_count {summary.dead_letter_count} > {self.max_dead_letters}")
        if issues:
            return SlaStatus.BREACHED, issues
        if warnings:
            return SlaStatus.WARNING, warnings
        return SlaStatus.OK, []


@dataclass(frozen=True)
class ProcessingSummary:
    operation: str
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    throughput_per_second: float = 0.0
    error_rate: float = 0.0
    success_rate: float = 1.0
    sla_status: SlaStatus = SlaStatus.UNKNOWN
    sla_issues: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["sla_status"] = self.sla_status.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class MetricsSnapshot:
    id: str
    created_at: str
    registry_name: str
    series_count: int
    rate_window_seconds: int
    metrics: List[Dict[str, Any]]
    summaries: List[ProcessingSummary] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(
            {
                "id": self.id,
                "created_at": self.created_at,
                "registry_name": self.registry_name,
                "series_count": self.series_count,
                "rate_window_seconds": self.rate_window_seconds,
                "metrics": self.metrics,
                "summaries": [summary.to_dict() for summary in self.summaries],
                "metadata": self.metadata,
            }
        )

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)

    def to_prometheus(self) -> str:
        lines: List[str] = []
        emitted_help: set[str] = set()
        for metric in self.metrics:
            name = normalize_metric_name(metric["name"])
            metric_type = metric.get("type", "gauge")
            description = str(metric.get("description") or name)
            labels = metric.get("labels") or {}
            label_text = prometheus_labels(labels)
            if name not in emitted_help:
                lines.append(f"# HELP {name} {escape_prometheus_help(description)}")
                lines.append(f"# TYPE {name} {prometheus_type(metric_type)}")
                emitted_help.add(name)
            if metric_type in {MetricType.HISTOGRAM.value, MetricType.TIMER.value} and metric.get("buckets"):
                for bucket, value in metric["buckets"].items():
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = bucket
                    lines.append(f"{name}_bucket{prometheus_labels(bucket_labels)} {value}")
                lines.append(f"{name}_count{label_text} {metric.get('count', 0)}")
                lines.append(f"{name}_sum{label_text} {metric.get('sum', 0)}")
            else:
                lines.append(f"{name}{label_text} {metric.get('value', 0)}")
        return "\n".join(lines) + "\n"


class MetricsSink(Protocol):
    def write_event(self, event: ProcessingMetricEvent) -> None:
        ...

    def write_snapshot(self, snapshot: MetricsSnapshot) -> None:
        ...


class JsonlMetricsSink:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def write_event(self, event: ProcessingMetricEvent) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "event", **event.to_dict()}, ensure_ascii=False, sort_keys=True, default=safe_json_default) + "\n")

    def write_snapshot(self, snapshot: MetricsSnapshot) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"kind": "snapshot", **snapshot.to_dict()}, ensure_ascii=False, sort_keys=True, default=safe_json_default) + "\n")


class InMemoryMetricsSink:
    def __init__(self) -> None:
        self.events: List[ProcessingMetricEvent] = []
        self.snapshots: List[MetricsSnapshot] = []
        self._lock = threading.RLock()

    def write_event(self, event: ProcessingMetricEvent) -> None:
        with self._lock:
            self.events.append(event)

    def write_snapshot(self, snapshot: MetricsSnapshot) -> None:
        with self._lock:
            self.snapshots.append(snapshot)


@dataclass(frozen=True)
class ProcessingMetricsConfig:
    registry_name: str = "processing"
    max_series: int = DEFAULT_MAX_SERIES
    max_label_value_length: int = 256
    rate_window_seconds: int = DEFAULT_RATE_WINDOW
    default_buckets: Tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS
    telemetry_enabled: bool = True
    audit_enabled: bool = False
    fail_open: bool = True
    sink_path: Optional[str] = None
    snapshot_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "ProcessingMetricsConfig":
        return cls(
            registry_name=os.getenv("PROCESSING_METRICS_REGISTRY_NAME", "processing"),
            max_series=int_env("PROCESSING_METRICS_MAX_SERIES", DEFAULT_MAX_SERIES),
            max_label_value_length=int_env("PROCESSING_METRICS_MAX_LABEL_VALUE_LENGTH", 256),
            rate_window_seconds=int_env("PROCESSING_METRICS_RATE_WINDOW_SECONDS", DEFAULT_RATE_WINDOW),
            telemetry_enabled=bool_env("PROCESSING_METRICS_TELEMETRY_ENABLED", True),
            audit_enabled=bool_env("PROCESSING_METRICS_AUDIT_ENABLED", False),
            fail_open=bool_env("PROCESSING_METRICS_FAIL_OPEN", True),
            sink_path=os.getenv("PROCESSING_METRICS_SINK_PATH"),
            snapshot_path=os.getenv("PROCESSING_METRICS_SNAPSHOT_PATH"),
        )


class ProcessingMetricsError(Exception):
    """Base processing metrics error."""


class MetricsCardinalityError(ProcessingMetricsError):
    """Metric series cardinality limit exceeded."""


class ProcessingMetrics:
    """Thread-safe processing metrics registry."""

    def __init__(self, config: Optional[ProcessingMetricsConfig] = None, sink: Optional[MetricsSink] = None) -> None:
        self.config = config or ProcessingMetricsConfig.from_env()
        self.sink = sink or (JsonlMetricsSink(self.config.sink_path) if self.config.sink_path else InMemoryMetricsSink())
        self._series: Dict[str, MetricSeries] = {}
        self._definitions: Dict[str, MetricDefinition] = {}
        self._summaries: List[ProcessingSummary] = []
        self._lock = threading.RLock()

    def define(self, name: str, metric_type: MetricType, *, unit: MetricUnit = MetricUnit.UNKNOWN, description: str = "", buckets: Optional[Sequence[float]] = None, tags: Optional[Sequence[str]] = None) -> MetricDefinition:
        definition = MetricDefinition(
            name=normalize_metric_name(name),
            metric_type=metric_type,
            unit=unit,
            description=description,
            buckets=tuple(float(x) for x in (buckets or self.config.default_buckets)),
            tags=tuple(tags or ()),
        )
        with self._lock:
            self._definitions[definition.normalized_name()] = definition
        return definition

    def increment(self, name: str, amount: float = 1.0, *, labels: Optional[Mapping[str, Any]] = None, unit: MetricUnit = MetricUnit.COUNT, attributes: Optional[Mapping[str, Any]] = None) -> None:
        self._update(name, MetricType.COUNTER, amount, labels=labels, unit=unit, attributes=attributes, op="counter")

    def gauge(self, name: str, value: float, *, labels: Optional[Mapping[str, Any]] = None, unit: MetricUnit = MetricUnit.UNKNOWN, attributes: Optional[Mapping[str, Any]] = None) -> None:
        self._update(name, MetricType.GAUGE, value, labels=labels, unit=unit, attributes=attributes, op="gauge")

    def observe(self, name: str, value: float, *, labels: Optional[Mapping[str, Any]] = None, unit: MetricUnit = MetricUnit.UNKNOWN, buckets: Optional[Sequence[float]] = None, attributes: Optional[Mapping[str, Any]] = None) -> None:
        if buckets:
            self.define(name, MetricType.HISTOGRAM, unit=unit, buckets=buckets)
        self._update(name, MetricType.HISTOGRAM, value, labels=labels, unit=unit, attributes=attributes, op="observe")

    @contextlib.contextmanager
    def timer(self, name: str, *, labels: Optional[Mapping[str, Any]] = None, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            self._update(name, MetricType.TIMER, duration_ms, labels=labels, unit=MetricUnit.MILLISECONDS, attributes=attributes, op="observe")

    def timed(self, name: Optional[str] = None, *, labels: Optional[Mapping[str, Any]] = None) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            metric_name = name or f"{func.__module__}.{func.__qualname__}.duration_ms"

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.timer(metric_name, labels=labels, attributes={"function": func.__qualname__}):
                    return func(*args, **kwargs)

            return cast(F, wrapper)

        return decorator

    @contextlib.contextmanager
    def processing_run(
        self,
        operation: str,
        *,
        labels: Optional[Mapping[str, Any]] = None,
        sla: Optional[ProcessingSla] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Iterator["ProcessingRunTracker"]:
        tracker = ProcessingRunTracker(operation=operation, metrics=self, labels=labels or {}, metadata=metadata or {})
        started = time.perf_counter()
        try:
            yield tracker
        except Exception:
            tracker.error_count += 1
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            summary = tracker.finish(duration_ms=duration_ms, sla=sla)
            self.record_summary(summary)

    def record_processing_counts(
        self,
        *,
        operation: str,
        input_count: int = 0,
        output_count: int = 0,
        error_count: int = 0,
        skipped_count: int = 0,
        retry_count: int = 0,
        dead_letter_count: int = 0,
        duration_ms: Optional[float] = None,
        labels: Optional[Mapping[str, Any]] = None,
    ) -> ProcessingSummary:
        labels = {"operation": operation, **dict(labels or {})}
        self.increment("processing_records_input_total", input_count, labels=labels, unit=MetricUnit.RECORDS)
        self.increment("processing_records_output_total", output_count, labels=labels, unit=MetricUnit.RECORDS)
        self.increment("processing_errors_total", error_count, labels=labels)
        self.increment("processing_skipped_total", skipped_count, labels=labels)
        self.increment("processing_retries_total", retry_count, labels=labels)
        self.increment("processing_dead_letters_total", dead_letter_count, labels=labels)
        if duration_ms is not None:
            self.observe("processing_duration_ms", duration_ms, labels=labels, unit=MetricUnit.MILLISECONDS)
        summary = build_summary(
            operation=operation,
            started_at=utc_now_iso(),
            duration_ms=duration_ms or 0.0,
            input_count=input_count,
            output_count=output_count,
            error_count=error_count,
            skipped_count=skipped_count,
            retry_count=retry_count,
            dead_letter_count=dead_letter_count,
        )
        self.record_summary(summary)
        return summary

    def record_summary(self, summary: ProcessingSummary) -> None:
        with self._lock:
            self._summaries.append(summary)
            if len(self._summaries) > 10_000:
                self._summaries = self._summaries[-10_000:]
        self.gauge("processing_throughput_per_second", summary.throughput_per_second, labels={"operation": summary.operation}, unit=MetricUnit.RATE)
        self.gauge("processing_error_rate", summary.error_rate, labels={"operation": summary.operation}, unit=MetricUnit.RATIO)
        self._audit_summary(summary)

    def snapshot(self, *, metadata: Optional[Mapping[str, Any]] = None) -> MetricsSnapshot:
        with self._lock:
            series = list(self._series.values())
            summaries = list(self._summaries)
        snapshot = MetricsSnapshot(
            id=str(uuid.uuid4()),
            created_at=utc_now_iso(),
            registry_name=self.config.registry_name,
            series_count=len(series),
            rate_window_seconds=self.config.rate_window_seconds,
            metrics=[item.to_dict(rate_window_seconds=self.config.rate_window_seconds) for item in sorted(series, key=lambda s: (s.definition.name, s.labels.key()))],
            summaries=summaries[-100:],
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        with contextlib.suppress(Exception):
            self.sink.write_snapshot(snapshot)
        if self.config.snapshot_path:
            self.save_snapshot(snapshot, self.config.snapshot_path)
        return snapshot

    def save_snapshot(self, snapshot: MetricsSnapshot, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(snapshot.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)
        return target

    def export_prometheus(self) -> str:
        return self.snapshot().to_prometheus()

    def reset(self) -> None:
        with self._lock:
            self._series.clear()
            self._summaries.clear()

    def _update(
        self,
        name: str,
        metric_type: MetricType,
        value: float,
        *,
        labels: Optional[Mapping[str, Any]],
        unit: MetricUnit,
        attributes: Optional[Mapping[str, Any]],
        op: str,
    ) -> None:
        if value is None or not is_finite_number(value):
            return
        definition = self._definitions.get(normalize_metric_name(name)) or self.define(name, metric_type, unit=unit)
        if definition.metric_type != metric_type and definition.metric_type not in {MetricType.HISTOGRAM, MetricType.TIMER}:
            definition = dataclasses.replace(definition, metric_type=metric_type, unit=unit)
        label_obj = MetricLabels.from_mapping(labels)
        key = series_key(definition.normalized_name(), label_obj)
        with self._lock:
            if key not in self._series:
                if len(self._series) >= self.config.max_series:
                    if not self.config.fail_open:
                        raise MetricsCardinalityError(f"max_series exceeded: {self.config.max_series}")
                    key = series_key(definition.normalized_name(), MetricLabels.from_mapping({"overflow": "true"}))
                    label_obj = MetricLabels.from_mapping({"overflow": "true"})
                self._series[key] = MetricSeries(definition=definition, labels=label_obj)
            series = self._series[key]
            if op == "counter":
                series.update_counter(float(value))
            elif op == "gauge":
                series.update_gauge(float(value))
            else:
                series.observe(float(value))
        event = ProcessingMetricEvent(
            id=str(uuid.uuid4()),
            timestamp=utc_now_iso(),
            metric=definition.normalized_name(),
            metric_type=metric_type,
            value=float(value),
            labels=label_obj.to_dict(),
            unit=unit,
            attributes=sanitize_mapping(dict(attributes or {})),
        )
        self._write_event(event)
        self._bridge_telemetry(event)

    def _write_event(self, event: ProcessingMetricEvent) -> None:
        try:
            self.sink.write_event(event)
        except Exception:
            logger.debug("Processing metric sink write failed", exc_info=True)
            if not self.config.fail_open:
                raise

    def _bridge_telemetry(self, event: ProcessingMetricEvent) -> None:
        if not self.config.telemetry_enabled:
            return
        try:
            from data.observability.telemetry import get_telemetry
            telemetry = get_telemetry()
            if event.metric_type == MetricType.COUNTER:
                telemetry.counter(event.metric, event.value, attributes=event.labels)
            else:
                telemetry.gauge(event.metric, event.value, attributes=event.labels)
        except Exception:
            logger.debug("Processing metrics telemetry bridge failed", exc_info=True)

    def _audit_summary(self, summary: ProcessingSummary) -> None:
        if not self.config.audit_enabled:
            return
        try:
            from data.processing.processing_audit import AuditCategory, AuditEventType, AuditSeverity, AuditStatus, get_default_auditor
            severity = AuditSeverity.ERROR if summary.sla_status == SlaStatus.BREACHED else AuditSeverity.WARNING if summary.sla_status == SlaStatus.WARNING else AuditSeverity.INFO
            status = AuditStatus.PARTIAL if summary.error_count else AuditStatus.SUCCEEDED
            get_default_auditor().record(
                event_type=AuditEventType.CUSTOM,
                message=f"Processing metrics summary: {summary.operation}",
                severity=severity,
                status=status,
                category=AuditCategory.PERFORMANCE,
                metrics=summary.to_dict(),
            )
        except Exception:
            logger.debug("Processing metrics audit integration failed", exc_info=True)


@dataclass
class ProcessingRunTracker:
    operation: str
    metrics: ProcessingMetrics
    labels: Mapping[str, Any]
    metadata: Mapping[str, Any]
    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    retry_count: int = 0
    dead_letter_count: int = 0
    started_at: str = field(default_factory=lambda: utc_now_iso())

    def input(self, count: int = 1) -> None:
        self.input_count += count
        self.metrics.increment("processing_records_input_total", count, labels={"operation": self.operation, **dict(self.labels)}, unit=MetricUnit.RECORDS)

    def output(self, count: int = 1) -> None:
        self.output_count += count
        self.metrics.increment("processing_records_output_total", count, labels={"operation": self.operation, **dict(self.labels)}, unit=MetricUnit.RECORDS)

    def error(self, count: int = 1) -> None:
        self.error_count += count
        self.metrics.increment("processing_errors_total", count, labels={"operation": self.operation, **dict(self.labels)})

    def skipped(self, count: int = 1) -> None:
        self.skipped_count += count
        self.metrics.increment("processing_skipped_total", count, labels={"operation": self.operation, **dict(self.labels)})

    def retry(self, count: int = 1) -> None:
        self.retry_count += count
        self.metrics.increment("processing_retries_total", count, labels={"operation": self.operation, **dict(self.labels)})

    def dead_letter(self, count: int = 1) -> None:
        self.dead_letter_count += count
        self.metrics.increment("processing_dead_letters_total", count, labels={"operation": self.operation, **dict(self.labels)})

    def finish(self, *, duration_ms: float, sla: Optional[ProcessingSla] = None) -> ProcessingSummary:
        return build_summary(
            operation=self.operation,
            started_at=self.started_at,
            duration_ms=duration_ms,
            input_count=self.input_count,
            output_count=self.output_count,
            error_count=self.error_count,
            skipped_count=self.skipped_count,
            retry_count=self.retry_count,
            dead_letter_count=self.dead_letter_count,
            sla=sla,
            metadata=dict(self.metadata),
        )


def build_summary(
    *,
    operation: str,
    started_at: str,
    duration_ms: float,
    input_count: int,
    output_count: int,
    error_count: int,
    skipped_count: int,
    retry_count: int,
    dead_letter_count: int,
    sla: Optional[ProcessingSla] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> ProcessingSummary:
    seconds = max(duration_ms / 1000.0, 1e-9)
    denominator = max(input_count, 1)
    throughput = output_count / seconds
    error_rate = error_count / denominator
    success_rate = max(0.0, (input_count - error_count - skipped_count) / denominator)
    base = ProcessingSummary(
        operation=operation,
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_ms=round(duration_ms, 3),
        input_count=input_count,
        output_count=output_count,
        error_count=error_count,
        skipped_count=skipped_count,
        retry_count=retry_count,
        dead_letter_count=dead_letter_count,
        throughput_per_second=round(throughput, 6),
        error_rate=round(error_rate, 8),
        success_rate=round(success_rate, 8),
        metadata=sanitize_mapping(dict(metadata or {})),
    )
    if sla:
        status, issues = sla.evaluate(base)
        return dataclasses.replace(base, sla_status=status, sla_issues=issues)
    return dataclasses.replace(base, sla_status=SlaStatus.UNKNOWN)


def series_key(name: str, labels: MetricLabels) -> str:
    return f"{normalize_metric_name(name)}|{labels.key()}"


def normalize_metric_name(name: str) -> str:
    cleaned = METRIC_NAME_PATTERN.sub("_", str(name).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "metric"
    if cleaned[0].isdigit():
        cleaned = "m_" + cleaned
    return cleaned


def sanitize_label_key(key: Any) -> str:
    return normalize_metric_name(str(key))[:128]


def sanitize_label_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    return text[:256]


def is_finite_number(value: Any) -> bool:
    try:
        number = float(value)
        return not math.isnan(number) and not math.isinf(number)
    except Exception:
        return False


def percentile(sorted_values: Sequence[float], percentile_value: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    k = (len(sorted_values) - 1) * percentile_value / 100.0
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return float(sorted_values[int(k)])
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (k - lower))


def round_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if not is_finite_number(value):
        return None
    return round(float(value), 6)


def bucket_label(value: float) -> str:
    return "+Inf" if value == math.inf else str(float(value)).rstrip("0").rstrip(".")


def prometheus_type(metric_type: str) -> str:
    if metric_type == MetricType.COUNTER.value:
        return "counter"
    if metric_type in {MetricType.HISTOGRAM.value, MetricType.TIMER.value}:
        return "histogram"
    return "gauge"


def prometheus_labels(labels: Mapping[str, Any]) -> str:
    if not labels:
        return ""
    parts = [f'{sanitize_label_key(k)}="{escape_prometheus_label(v)}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def escape_prometheus_label(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def escape_prometheus_help(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n")


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    result: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            result[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set, deque)):
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not is_finite_number(value):
            return None
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set, deque)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple, deque)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_default_metrics: Optional[ProcessingMetrics] = None
_default_lock = threading.RLock()


def get_default_metrics() -> ProcessingMetrics:
    global _default_metrics
    with _default_lock:
        if _default_metrics is None:
            _default_metrics = ProcessingMetrics()
        return _default_metrics


def configure_default_metrics(config: Optional[ProcessingMetricsConfig] = None, sink: Optional[MetricsSink] = None) -> ProcessingMetrics:
    global _default_metrics
    with _default_lock:
        _default_metrics = ProcessingMetrics(config=config, sink=sink)
        return _default_metrics


__all__ = [
    "DEFAULT_HISTOGRAM_BUCKETS",
    "InMemoryMetricsSink",
    "JsonlMetricsSink",
    "MetricDefinition",
    "MetricLabels",
    "MetricSeries",
    "MetricType",
    "MetricUnit",
    "MetricsCardinalityError",
    "MetricsSink",
    "MetricsSnapshot",
    "ProcessingMetricEvent",
    "ProcessingMetrics",
    "ProcessingMetricsConfig",
    "ProcessingMetricsError",
    "ProcessingRunTracker",
    "ProcessingSla",
    "ProcessingSummary",
    "SlaStatus",
    "build_summary",
    "configure_default_metrics",
    "get_default_metrics",
    "normalize_metric_name",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    metrics = ProcessingMetrics(ProcessingMetricsConfig(telemetry_enabled=False, audit_enabled=False))
    with metrics.processing_run("example", sla=ProcessingSla(name="example", max_error_rate=0.01)) as run:
        run.input(100)
        run.output(98)
        run.error(1)
        run.skipped(1)
        with metrics.timer("example_stage_duration_ms", labels={"stage": "clean"}):
            time.sleep(0.01)
    print(metrics.snapshot().to_json())
    print(metrics.export_prometheus())
