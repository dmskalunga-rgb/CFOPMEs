"""
data/validation/validation_metrics.py

Enterprise-grade validation metrics module.

Este módulo centraliza métricas de validação para qualidade, schema, integridade,
PII, compliance, consistência, contratos e drift.

Capacidades principais:
- Contadores, gauges, timings/histogramas e métricas derivadas.
- Tags padronizadas por dataset, pipeline, ambiente, tenant, regra e severidade.
- Sinks plugáveis: memória, logging, JSONL e Prometheus text exposition.
- Snapshots agregados por janela de execução.
- Cálculo de SLO/SLA, taxa de falha, taxa de violação, p95/p99 e score médio.
- Sanitização e normalização de tags para observabilidade enterprise.
- Thread-safe, sem dependências obrigatórias externas.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


class MetricType(str, Enum):
    """Tipos de métricas suportadas."""

    COUNTER = "COUNTER"
    GAUGE = "GAUGE"
    TIMING = "TIMING"
    HISTOGRAM = "HISTOGRAM"
    EVENT = "EVENT"


class ValidationMetricName(str, Enum):
    """Nomes padronizados de métricas de validação."""

    VALIDATION_EXECUTED = "validation.executed"
    VALIDATION_SUCCEEDED = "validation.succeeded"
    VALIDATION_FAILED = "validation.failed"
    VALIDATION_DURATION_MS = "validation.duration_ms"
    VALIDATION_SCORE = "validation.score"
    VALIDATION_ROWS = "validation.rows"
    VALIDATION_COLUMNS = "validation.columns"
    VALIDATION_RULES = "validation.rules"
    VALIDATION_ISSUES = "validation.issues"
    VALIDATION_VIOLATIONS = "validation.violations"
    VALIDATION_WARNINGS = "validation.warnings"
    VALIDATION_ERRORS = "validation.errors"
    RULE_EXECUTED = "validation.rule.executed"
    RULE_FAILED = "validation.rule.failed"
    RULE_DURATION_MS = "validation.rule.duration_ms"
    RULE_SCORE = "validation.rule.score"
    RULE_ISSUES = "validation.rule.issues"
    DATASET_FRESHNESS_SECONDS = "validation.dataset.freshness_seconds"
    DATASET_NULL_RATIO = "validation.dataset.null_ratio"
    DATASET_DUPLICATE_RATIO = "validation.dataset.duplicate_ratio"
    DATASET_DRIFT_SCORE = "validation.dataset.drift_score"
    PII_FINDINGS = "validation.pii.findings"
    SCHEMA_VIOLATIONS = "validation.schema.violations"
    QUALITY_SCORE = "validation.quality.score"
    INTEGRITY_VIOLATIONS = "validation.integrity.violations"
    COMPLIANCE_VIOLATIONS = "validation.compliance.violations"


class MetricsError(Exception):
    """Erro base do módulo de métricas."""


class MetricsConfigurationError(MetricsError):
    """Erro de configuração de métricas."""


class MetricsWriteError(MetricsError):
    """Erro ao publicar métrica em sink."""


class MetricsSink(Protocol):
    """Contrato de destino de métricas."""

    def emit(self, metric: Mapping[str, Any]) -> None:
        """Publica uma métrica serializada."""


@dataclass(frozen=True)
class MetricsIdentity:
    """Identidade da origem das métricas."""

    service_name: str = "data-validation"
    service_version: Optional[str] = None
    host: Optional[str] = None
    process_id: Optional[int] = None
    runtime: str = "python"

    def to_dict(self) -> JsonDict:
        return {
            "service_name": self.service_name,
            "service_version": self.service_version,
            "host": self.host or os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME"),
            "process_id": self.process_id or os.getpid(),
            "runtime": self.runtime,
        }


@dataclass(frozen=True)
class MetricsContext:
    """Contexto padronizado para tags de validação."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    data_product: Optional[str] = None
    data_owner: Optional[str] = None
    validation_type: Optional[str] = None
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        tags = {
            "dataset": self.dataset_name,
            "pipeline": self.pipeline_name or "unknown",
            "environment": self.environment,
            "tenant": self.tenant_id or "default",
            "source": self.source_system or "unknown",
            "product": self.data_product or "unknown",
            "owner": self.data_owner or "unknown",
            "validation_type": self.validation_type or "unknown",
            "run_id": self.run_id,
        }
        if self.correlation_id:
            tags["correlation_id"] = self.correlation_id
        if extra:
            tags.update({str(k): str(v) for k, v in extra.items()})
        return normalize_tags(tags)


@dataclass(frozen=True)
class MetricPoint:
    """Representa uma métrica individual."""

    name: str
    metric_type: MetricType
    value: float
    tags: Mapping[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    unit: Optional[str] = None
    description: Optional[str] = None
    identity: MetricsIdentity = field(default_factory=MetricsIdentity)

    @property
    def key(self) -> str:
        return metric_key(self.name, self.tags)

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "metric_type": self.metric_type.value,
            "value": self.value,
            "tags": normalize_tags(self.tags),
            "timestamp": self.timestamp.isoformat(),
            "unit": self.unit,
            "description": self.description,
            "identity": self.identity.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class TimingSummary:
    """Resumo estatístico de timings/histogramas."""

    count: int
    min: float
    max: float
    avg: float
    p50: float
    p90: float
    p95: float
    p99: float

    def to_dict(self) -> JsonDict:
        return {
            "count": self.count,
            "min": self.min,
            "max": self.max,
            "avg": self.avg,
            "p50": self.p50,
            "p90": self.p90,
            "p95": self.p95,
            "p99": self.p99,
        }


@dataclass(frozen=True)
class MetricsSnapshot:
    """Snapshot agregado das métricas em memória."""

    created_at: datetime
    counters: Mapping[str, float]
    gauges: Mapping[str, float]
    timings: Mapping[str, TimingSummary]
    events: Mapping[str, int]
    total_points: int

    def to_dict(self) -> JsonDict:
        return {
            "created_at": self.created_at.isoformat(),
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "timings": {key: value.to_dict() for key, value in self.timings.items()},
            "events": dict(self.events),
            "total_points": self.total_points,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class ValidationSLO:
    """Definição simples de SLO/SLA de validação."""

    name: str
    min_success_rate: float = 0.99
    max_failure_rate: float = 0.01
    max_p95_duration_ms: Optional[float] = None
    min_average_score: Optional[float] = None
    max_violation_rate: Optional[float] = None


@dataclass(frozen=True)
class SLOEvaluationResult:
    """Resultado de avaliação de SLO."""

    slo_name: str
    passed: bool
    observed: Mapping[str, Any]
    violations: Tuple[str, ...]

    def to_dict(self) -> JsonDict:
        return {
            "slo_name": self.slo_name,
            "passed": self.passed,
            "observed": safe_json_value(dict(self.observed)),
            "violations": list(self.violations),
        }


class ValidationMetricsCollector:
    """Coletor central de métricas de validação."""

    def __init__(
        self,
        *,
        sinks: Optional[Sequence[MetricsSink]] = None,
        identity: Optional[MetricsIdentity] = None,
        namespace: str = "data_validation",
        keep_memory: bool = True,
        max_points: int = 100_000,
        fail_on_sink_error: bool = False,
    ) -> None:
        self.sinks = list(sinks or [])
        self.identity = identity or MetricsIdentity()
        self.namespace = sanitize_metric_name(namespace)
        self.keep_memory = keep_memory
        self.max_points = max_points
        self.fail_on_sink_error = fail_on_sink_error
        self._points: List[MetricPoint] = []
        self._counters: MutableMapping[str, float] = defaultdict(float)
        self._gauges: MutableMapping[str, float] = {}
        self._timings: MutableMapping[str, List[float]] = defaultdict(list)
        self._events: MutableMapping[str, int] = defaultdict(int)
        self._lock = threading.RLock()

    @property
    def points(self) -> Tuple[MetricPoint, ...]:
        with self._lock:
            return tuple(self._points)

    def add_sink(self, sink: MetricsSink) -> None:
        with self._lock:
            self.sinks.append(sink)

    def increment(self, name: Union[str, ValidationMetricName], value: int = 1, tags: Optional[MetricTags] = None) -> MetricPoint:
        return self.record(name, MetricType.COUNTER, float(value), tags=tags, unit="count")

    def gauge(self, name: Union[str, ValidationMetricName], value: float, tags: Optional[MetricTags] = None, unit: Optional[str] = None) -> MetricPoint:
        return self.record(name, MetricType.GAUGE, float(value), tags=tags, unit=unit)

    def timing(self, name: Union[str, ValidationMetricName], value_ms: float, tags: Optional[MetricTags] = None) -> MetricPoint:
        return self.record(name, MetricType.TIMING, float(value_ms), tags=tags, unit="ms")

    def histogram(self, name: Union[str, ValidationMetricName], value: float, tags: Optional[MetricTags] = None, unit: Optional[str] = None) -> MetricPoint:
        return self.record(name, MetricType.HISTOGRAM, float(value), tags=tags, unit=unit)

    def event(self, name: Union[str, ValidationMetricName], tags: Optional[MetricTags] = None, value: float = 1.0) -> MetricPoint:
        return self.record(name, MetricType.EVENT, float(value), tags=tags, unit="event")

    def record(
        self,
        name: Union[str, ValidationMetricName],
        metric_type: MetricType,
        value: float,
        *,
        tags: Optional[MetricTags] = None,
        unit: Optional[str] = None,
        description: Optional[str] = None,
    ) -> MetricPoint:
        metric_name = self._metric_name(name)
        point = MetricPoint(
            name=metric_name,
            metric_type=metric_type,
            value=float(value),
            tags=normalize_tags(tags or {}),
            unit=unit,
            description=description,
            identity=self.identity,
        )
        self._store_and_emit(point)
        return point

    def record_validation_result(
        self,
        *,
        context: MetricsContext,
        status: str,
        score: Optional[float] = None,
        duration_ms: Optional[float] = None,
        rows: Optional[int] = None,
        columns: Optional[int] = None,
        rules: Optional[int] = None,
        issues: Optional[int] = None,
        violations: Optional[int] = None,
        warnings: Optional[int] = None,
        errors: Optional[int] = None,
        extra_tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        tags = context.tags({"status": status, **dict(extra_tags or {})})
        self.increment(ValidationMetricName.VALIDATION_EXECUTED, tags=tags)
        if status.upper() in {"PASSED", "SUCCEEDED", "COMPLIANT"}:
            self.increment(ValidationMetricName.VALIDATION_SUCCEEDED, tags=tags)
        elif status.upper() in {"FAILED", "ERROR", "NON_COMPLIANT"}:
            self.increment(ValidationMetricName.VALIDATION_FAILED, tags=tags)
        if score is not None:
            self.gauge(ValidationMetricName.VALIDATION_SCORE, score, tags=tags, unit="ratio")
        if duration_ms is not None:
            self.timing(ValidationMetricName.VALIDATION_DURATION_MS, duration_ms, tags=tags)
        if rows is not None:
            self.gauge(ValidationMetricName.VALIDATION_ROWS, rows, tags=tags, unit="rows")
        if columns is not None:
            self.gauge(ValidationMetricName.VALIDATION_COLUMNS, columns, tags=tags, unit="columns")
        if rules is not None:
            self.gauge(ValidationMetricName.VALIDATION_RULES, rules, tags=tags, unit="rules")
        if issues is not None:
            self.gauge(ValidationMetricName.VALIDATION_ISSUES, issues, tags=tags, unit="issues")
        if violations is not None:
            self.gauge(ValidationMetricName.VALIDATION_VIOLATIONS, violations, tags=tags, unit="violations")
        if warnings is not None:
            self.gauge(ValidationMetricName.VALIDATION_WARNINGS, warnings, tags=tags, unit="warnings")
        if errors is not None:
            self.gauge(ValidationMetricName.VALIDATION_ERRORS, errors, tags=tags, unit="errors")

    def record_rule_result(
        self,
        *,
        context: MetricsContext,
        rule_id: str,
        rule_type: str,
        status: str,
        severity: Optional[str] = None,
        score: Optional[float] = None,
        duration_ms: Optional[float] = None,
        issues: Optional[int] = None,
    ) -> None:
        extra = {"rule_id": rule_id, "rule_type": rule_type, "status": status}
        if severity:
            extra["severity"] = severity
        tags = context.tags(extra)
        self.increment(ValidationMetricName.RULE_EXECUTED, tags=tags)
        if status.upper() in {"FAILED", "ERROR", "NON_COMPLIANT"}:
            self.increment(ValidationMetricName.RULE_FAILED, tags=tags)
        if score is not None:
            self.gauge(ValidationMetricName.RULE_SCORE, score, tags=tags, unit="ratio")
        if duration_ms is not None:
            self.timing(ValidationMetricName.RULE_DURATION_MS, duration_ms, tags=tags)
        if issues is not None:
            self.gauge(ValidationMetricName.RULE_ISSUES, issues, tags=tags, unit="issues")

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            timings = {key: summarize_values(values) for key, values in self._timings.items()}
            return MetricsSnapshot(
                created_at=datetime.now(timezone.utc),
                counters=dict(self._counters),
                gauges=dict(self._gauges),
                timings=timings,
                events=dict(self._events),
                total_points=len(self._points),
            )

    def evaluate_slo(self, slo: ValidationSLO, *, tag_filter: Optional[Mapping[str, str]] = None) -> SLOEvaluationResult:
        points = self.filter_points(tag_filter or {})
        executed = sum(p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_EXECUTED.value) and p.metric_type == MetricType.COUNTER)
        failed = sum(p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_FAILED.value) and p.metric_type == MetricType.COUNTER)
        succeeded = sum(p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_SUCCEEDED.value) and p.metric_type == MetricType.COUNTER)
        durations = [p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_DURATION_MS.value)]
        scores = [p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_SCORE.value)]
        violations_values = [p.value for p in points if p.name.endswith(ValidationMetricName.VALIDATION_VIOLATIONS.value)]

        success_rate = succeeded / executed if executed else 1.0
        failure_rate = failed / executed if executed else 0.0
        p95_duration = percentile(durations, 0.95) if durations else None
        avg_score = statistics.mean(scores) if scores else None
        avg_violations = statistics.mean(violations_values) if violations_values else 0.0
        violation_rate = avg_violations / max(1.0, executed)

        violations: List[str] = []
        if success_rate < slo.min_success_rate:
            violations.append(f"success_rate {success_rate:.6f} < {slo.min_success_rate:.6f}")
        if failure_rate > slo.max_failure_rate:
            violations.append(f"failure_rate {failure_rate:.6f} > {slo.max_failure_rate:.6f}")
        if slo.max_p95_duration_ms is not None and p95_duration is not None and p95_duration > slo.max_p95_duration_ms:
            violations.append(f"p95_duration_ms {p95_duration:.2f} > {slo.max_p95_duration_ms:.2f}")
        if slo.min_average_score is not None and avg_score is not None and avg_score < slo.min_average_score:
            violations.append(f"average_score {avg_score:.6f} < {slo.min_average_score:.6f}")
        if slo.max_violation_rate is not None and violation_rate > slo.max_violation_rate:
            violations.append(f"violation_rate {violation_rate:.6f} > {slo.max_violation_rate:.6f}")

        return SLOEvaluationResult(
            slo_name=slo.name,
            passed=not violations,
            observed={
                "executed": executed,
                "succeeded": succeeded,
                "failed": failed,
                "success_rate": success_rate,
                "failure_rate": failure_rate,
                "p95_duration_ms": p95_duration,
                "average_score": avg_score,
                "violation_rate": violation_rate,
            },
            violations=tuple(violations),
        )

    def filter_points(self, tag_filter: Mapping[str, str]) -> List[MetricPoint]:
        normalized = normalize_tags(tag_filter)
        with self._lock:
            points = list(self._points)
        if not normalized:
            return points
        return [point for point in points if all(point.tags.get(k) == v for k, v in normalized.items())]

    def export_jsonl(self, path: Union[str, Path]) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            for point in self.points:
                file.write(point.to_json() + "\n")
        return output_path

    def export_prometheus_text(self, path: Optional[Union[str, Path]] = None) -> str:
        text = prometheus_text_from_snapshot(self.snapshot())
        if path is not None:
            output_path = Path(path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(text, encoding="utf-8")
        return text

    def clear(self) -> None:
        with self._lock:
            self._points.clear()
            self._counters.clear()
            self._gauges.clear()
            self._timings.clear()
            self._events.clear()

    def _metric_name(self, name: Union[str, ValidationMetricName]) -> str:
        raw = name.value if isinstance(name, ValidationMetricName) else str(name)
        raw = sanitize_metric_name(raw)
        if raw.startswith(self.namespace + "."):
            return raw
        return f"{self.namespace}.{raw}"

    def _store_and_emit(self, point: MetricPoint) -> None:
        payload = point.to_dict()
        errors: List[str] = []
        with self._lock:
            if self.keep_memory:
                self._points.append(point)
                if len(self._points) > self.max_points:
                    self._points = self._points[-self.max_points :]
            if point.metric_type == MetricType.COUNTER:
                self._counters[point.key] += point.value
            elif point.metric_type == MetricType.GAUGE:
                self._gauges[point.key] = point.value
            elif point.metric_type in {MetricType.TIMING, MetricType.HISTOGRAM}:
                self._timings[point.key].append(point.value)
            elif point.metric_type == MetricType.EVENT:
                self._events[point.key] += int(point.value)

            for sink in self.sinks:
                try:
                    sink.emit(payload)
                except Exception as exc:
                    logger.exception("Metrics sink failed")
                    errors.append(str(exc))
        if errors and self.fail_on_sink_error:
            raise MetricsWriteError("One or more metrics sinks failed: " + "; ".join(errors))


class InMemoryMetricsSink:
    """Sink em memória que armazena pontos serializados."""

    def __init__(self, max_points: int = 100_000) -> None:
        self.max_points = max_points
        self.points: List[Mapping[str, Any]] = []
        self._lock = threading.RLock()

    def emit(self, metric: Mapping[str, Any]) -> None:
        with self._lock:
            self.points.append(dict(metric))
            if len(self.points) > self.max_points:
                self.points = self.points[-self.max_points :]

    def query(self, *, name: Optional[str] = None, tags: Optional[Mapping[str, str]] = None) -> List[Mapping[str, Any]]:
        normalized = normalize_tags(tags or {})
        with self._lock:
            result = list(self.points)
        if name is not None:
            result = [p for p in result if str(p.get("name")) == name or str(p.get("name", "")).endswith(name)]
        if normalized:
            result = [p for p in result if all(p.get("tags", {}).get(k) == v for k, v in normalized.items())]
        return result


class JsonLineMetricsSink:
    """Sink que persiste métricas em arquivo JSONL."""

    def __init__(self, path: Union[str, Path], *, flush: bool = True) -> None:
        self.path = Path(path)
        self.flush = flush
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def emit(self, metric: Mapping[str, Any]) -> None:
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(safe_json_value(dict(metric)), ensure_ascii=False, sort_keys=True, default=str) + "\n")
                    if self.flush:
                        file.flush()
        except Exception as exc:
            raise MetricsWriteError(f"Failed to write metric to {self.path}: {exc}") from exc


class LoggingMetricsSink:
    """Sink que encaminha métricas para logging."""

    def __init__(self, logger_name: str = "data.validation.metrics", level: int = logging.INFO) -> None:
        self.logger = logging.getLogger(logger_name)
        self.level = level

    def emit(self, metric: Mapping[str, Any]) -> None:
        self.logger.log(self.level, json.dumps(safe_json_value(dict(metric)), ensure_ascii=False, sort_keys=True, default=str))


class CompositeMetricsSink:
    """Sink composto para fan-out de métricas."""

    def __init__(self, sinks: Sequence[MetricsSink], *, fail_fast: bool = False) -> None:
        self.sinks = list(sinks)
        self.fail_fast = fail_fast

    def emit(self, metric: Mapping[str, Any]) -> None:
        errors: List[str] = []
        for sink in self.sinks:
            try:
                sink.emit(metric)
            except Exception as exc:
                errors.append(str(exc))
                if self.fail_fast:
                    raise
        if errors:
            logger.warning("CompositeMetricsSink completed with sink errors: %s", errors)


class MetricsTimer:
    """Context manager para medir duração e publicar timing automaticamente."""

    def __init__(
        self,
        collector: ValidationMetricsCollector,
        name: Union[str, ValidationMetricName],
        tags: Optional[MetricTags] = None,
    ) -> None:
        self.collector = collector
        self.name = name
        self.tags = tags or {}
        self.started_perf: Optional[float] = None
        self.finished_perf: Optional[float] = None

    def __enter__(self) -> "MetricsTimer":
        self.started_perf = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finished_perf = time.perf_counter()
        self.collector.timing(self.name, self.duration_ms, tags=self.tags)

    @property
    def duration_ms(self) -> float:
        if self.started_perf is None:
            return 0.0
        end = self.finished_perf if self.finished_perf is not None else time.perf_counter()
        return max(0.0, (end - self.started_perf) * 1000.0)


def metric_key(name: str, tags: Mapping[str, str]) -> str:
    normalized = normalize_tags(tags)
    if not normalized:
        return name
    tag_text = ",".join(f"{key}={value}" for key, value in sorted(normalized.items()))
    return f"{name}|{tag_text}"


def normalize_tags(tags: Mapping[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key, value in tags.items():
        clean_key = sanitize_tag_key(str(key))
        clean_value = sanitize_tag_value(str(value))
        if clean_key:
            normalized[clean_key] = clean_value
    return normalized


def sanitize_metric_name(name: str) -> str:
    value = str(name).strip().replace("/", ".").replace("-", "_")
    value = re.sub(r"[^a-zA-Z0-9_.:]", "_", value)
    value = re.sub(r"_+", "_", value)
    value = re.sub(r"\.+", ".", value)
    return value.strip("._") or "metric"


def sanitize_tag_key(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip().lower())
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:128]


def sanitize_tag_value(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\n\r\t]+", " ", value)
    return value[:256] if value else "unknown"


def summarize_values(values: Sequence[float]) -> TimingSummary:
    if not values:
        return TimingSummary(count=0, min=0.0, max=0.0, avg=0.0, p50=0.0, p90=0.0, p95=0.0, p99=0.0)
    ordered = sorted(float(v) for v in values)
    return TimingSummary(
        count=len(ordered),
        min=min(ordered),
        max=max(ordered),
        avg=statistics.mean(ordered),
        p50=percentile(ordered, 0.50) or 0.0,
        p90=percentile(ordered, 0.90) or 0.0,
        p95=percentile(ordered, 0.95) or 0.0,
        p99=percentile(ordered, 0.99) or 0.0,
    )


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def prometheus_text_from_snapshot(snapshot: MetricsSnapshot) -> str:
    """Converte snapshot para formato Prometheus text exposition simplificado."""
    lines: List[str] = []
    for key, value in snapshot.counters.items():
        name, labels = split_metric_key(key)
        prom_name = to_prometheus_name(name) + "_total"
        lines.append(f"# TYPE {prom_name} counter")
        lines.append(f"{prom_name}{labels} {float(value)}")
    for key, value in snapshot.gauges.items():
        name, labels = split_metric_key(key)
        prom_name = to_prometheus_name(name)
        lines.append(f"# TYPE {prom_name} gauge")
        lines.append(f"{prom_name}{labels} {float(value)}")
    for key, summary in snapshot.timings.items():
        name, labels = split_metric_key(key)
        base = to_prometheus_name(name)
        lines.append(f"# TYPE {base} summary")
        label_prefix = labels[:-1] + "," if labels else "{"
        label_suffix = "}" if labels else "}"
        for quantile, value in [("0.5", summary.p50), ("0.9", summary.p90), ("0.95", summary.p95), ("0.99", summary.p99)]:
            lines.append(f'{base}{label_prefix}quantile="{quantile}"{label_suffix} {float(value)}')
        lines.append(f"{base}_count{labels} {summary.count}")
        lines.append(f"{base}_sum{labels} {summary.avg * summary.count}")
    return "\n".join(lines) + "\n"


def split_metric_key(key: str) -> Tuple[str, str]:
    if "|" not in key:
        return key, ""
    name, tag_text = key.split("|", 1)
    labels = []
    for pair in tag_text.split(","):
        if "=" not in pair:
            continue
        label, value = pair.split("=", 1)
        labels.append(f'{sanitize_tag_key(label)}="{escape_prometheus_label(value)}"')
    return name, "{" + ",".join(labels) + "}" if labels else ""


def to_prometheus_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    value = re.sub(r"_+", "_", value).strip("_")
    if value and value[0].isdigit():
        value = "m_" + value
    return value or "metric"


def escape_prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def load_jsonl_metrics(path: Union[str, Path]) -> List[Mapping[str, Any]]:
    input_path = Path(path)
    if not input_path.exists():
        raise MetricsConfigurationError(f"Metrics JSONL file does not exist: {input_path}")
    points: List[Mapping[str, Any]] = []
    with input_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                points.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise MetricsConfigurationError(f"Invalid JSONL metrics file at line {line_number}: {exc}") from exc
    return points


def build_default_metrics_collector(
    *,
    jsonl_path: Optional[Union[str, Path]] = None,
    enable_logging_sink: bool = False,
    namespace: str = "data_validation",
    service_name: str = "data-validation",
    service_version: Optional[str] = None,
    keep_memory: bool = True,
) -> ValidationMetricsCollector:
    sinks: List[MetricsSink] = []
    if jsonl_path is not None:
        sinks.append(JsonLineMetricsSink(jsonl_path))
    if enable_logging_sink:
        sinks.append(LoggingMetricsSink())
    return ValidationMetricsCollector(
        sinks=sinks,
        identity=MetricsIdentity(service_name=service_name, service_version=service_version),
        namespace=namespace,
        keep_memory=keep_memory,
    )


__all__ = [
    "CompositeMetricsSink",
    "InMemoryMetricsSink",
    "JsonLineMetricsSink",
    "LoggingMetricsSink",
    "MetricPoint",
    "MetricTags",
    "MetricType",
    "MetricsConfigurationError",
    "MetricsContext",
    "MetricsError",
    "MetricsIdentity",
    "MetricsSink",
    "MetricsSnapshot",
    "MetricsTimer",
    "MetricsWriteError",
    "SLOEvaluationResult",
    "TimingSummary",
    "ValidationMetricName",
    "ValidationMetricsCollector",
    "ValidationSLO",
    "build_default_metrics_collector",
    "escape_prometheus_label",
    "load_jsonl_metrics",
    "metric_key",
    "normalize_tags",
    "percentile",
    "prometheus_text_from_snapshot",
    "safe_json_value",
    "sanitize_metric_name",
    "sanitize_tag_key",
    "sanitize_tag_value",
    "split_metric_key",
    "summarize_values",
    "to_prometheus_name",
]
