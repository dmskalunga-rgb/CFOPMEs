"""
data/orchestration/orchestration_metrics.py

Enterprise Orchestration Metrics Engine.

Recursos:
- Métricas de workflows, DAGs, tasks, filas, workers e schedulers
- Counters, gauges, timings e histogram-like summaries
- SLO/SLA tracking
- Health score operacional
- Snapshots agregados
- Multi-tenant
- Backends plugáveis
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    TIMING = "timing"
    HISTOGRAM = "histogram"


class MetricUnit(str, Enum):
    COUNT = "count"
    MILLISECONDS = "milliseconds"
    SECONDS = "seconds"
    PERCENT = "percent"
    RATIO = "ratio"
    BYTES = "bytes"
    TASKS = "tasks"
    RUNS = "runs"
    WORKERS = "workers"


class OrchestrationComponent(str, Enum):
    WORKFLOW = "workflow"
    DAG = "dag"
    TASK = "task"
    QUEUE = "queue"
    WORKER = "worker"
    SCHEDULER = "scheduler"
    PIPELINE = "pipeline"
    EVENT_BUS = "event_bus"
    RUNTIME = "runtime"
    SYSTEM = "system"


class SLOStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    BREACHED = "breached"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


# =============================================================================
# Exceptions
# =============================================================================

class OrchestrationMetricsError(Exception):
    """Erro base do módulo de métricas de orquestração."""


class MetricValidationError(OrchestrationMetricsError):
    """Erro de validação de métrica."""


class MetricNotFoundError(OrchestrationMetricsError):
    """Métrica não encontrada."""


# =============================================================================
# Protocols
# =============================================================================

class MetricsSink(Protocol):
    def emit(self, metric: "OrchestrationMetric") -> None:
        ...


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class MetricsContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    dag_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    scheduler_id: Optional[str] = None
    queue_id: Optional[str] = None


@dataclass(frozen=True)
class OrchestrationMetric:
    metric_id: str
    name: str
    metric_type: MetricType
    component: OrchestrationComponent
    value: float
    unit: MetricUnit
    timestamp: datetime
    tags: Dict[str, str] = field(default_factory=dict)
    context: MetricsContext = field(default_factory=MetricsContext)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.metric_id:
            raise MetricValidationError("metric_id é obrigatório")

        if not self.name:
            raise MetricValidationError("name é obrigatório")

        if not math.isfinite(float(self.value)):
            raise MetricValidationError("value precisa ser numérico finito")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["metric_type"] = self.metric_type.value
        data["component"] = self.component.value
        data["unit"] = self.unit.value
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class MetricSeriesSummary:
    name: str
    count: int
    min_value: Optional[float]
    max_value: Optional[float]
    avg_value: Optional[float]
    median_value: Optional[float]
    p95_value: Optional[float]
    p99_value: Optional[float]
    sum_value: float
    unit: Optional[MetricUnit]
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["unit"] = self.unit.value if self.unit else None
        data["first_seen_at"] = self.first_seen_at.isoformat() if self.first_seen_at else None
        data["last_seen_at"] = self.last_seen_at.isoformat() if self.last_seen_at else None
        return data


@dataclass(frozen=True)
class SLODefinition:
    slo_id: str
    name: str
    metric_name: str
    component: OrchestrationComponent
    threshold: float
    comparison: str
    unit: MetricUnit
    evaluation_window_minutes: int = 60
    warning_ratio: float = 0.8
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    enabled: bool = True
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.slo_id:
            raise MetricValidationError("slo_id é obrigatório")

        if self.comparison not in {">", ">=", "<", "<=", "==", "!="}:
            raise MetricValidationError("comparison inválido")

        if self.evaluation_window_minutes <= 0:
            raise MetricValidationError("evaluation_window_minutes precisa ser > 0")


@dataclass
class SLOEvaluation:
    slo_id: str
    name: str
    status: SLOStatus
    metric_name: str
    current_value: Optional[float]
    threshold: float
    comparison: str
    evaluated_at: datetime
    window_start: datetime
    window_end: datetime
    breach: bool
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComponentHealth:
    component: OrchestrationComponent
    status: HealthStatus
    score: float
    evaluated_at: datetime
    metrics: Dict[str, Any] = field(default_factory=dict)
    slo_evaluations: List[SLOEvaluation] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)


@dataclass
class OrchestrationMetricsSnapshot:
    snapshot_id: str
    generated_at: datetime
    tenant_id: Optional[str]
    domain: Optional[str]
    total_metrics: int
    summaries: Dict[str, MetricSeriesSummary]
    slo_evaluations: List[SLOEvaluation]
    component_health: List[ComponentHealth]
    global_health_status: HealthStatus
    global_health_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at.isoformat(),
            "tenant_id": self.tenant_id,
            "domain": self.domain,
            "total_metrics": self.total_metrics,
            "summaries": {
                key: value.to_dict()
                for key, value in self.summaries.items()
            },
            "slo_evaluations": [
                OrchestrationMetricsEngine.slo_to_dict(item)
                for item in self.slo_evaluations
            ],
            "component_health": [
                OrchestrationMetricsEngine.health_to_dict(item)
                for item in self.component_health
            ],
            "global_health_status": self.global_health_status.value,
            "global_health_score": self.global_health_score,
        }


# =============================================================================
# Sinks / Store
# =============================================================================

class LoggingMetricsSink:
    def emit(self, metric: OrchestrationMetric) -> None:
        logger.info(
            "orchestration_metric=%s",
            json.dumps(metric.to_dict(), ensure_ascii=False, default=str),
        )


class InMemoryMetricsStore:
    def __init__(self, max_metrics: int = 250_000) -> None:
        self._metrics: List[OrchestrationMetric] = []
        self._max_metrics = max_metrics
        self._lock = threading.RLock()

    def append(self, metric: OrchestrationMetric) -> None:
        metric.validate()

        with self._lock:
            self._metrics.append(metric)

            if len(self._metrics) > self._max_metrics:
                overflow = len(self._metrics) - self._max_metrics
                self._metrics = self._metrics[overflow:]

    def list_metrics(
        self,
        name: Optional[str] = None,
        component: Optional[OrchestrationComponent] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> List[OrchestrationMetric]:
        with self._lock:
            metrics = list(self._metrics)

        if name is not None:
            metrics = [metric for metric in metrics if metric.name == name]

        if component is not None:
            metrics = [metric for metric in metrics if metric.component == component]

        if tenant_id is not None:
            metrics = [metric for metric in metrics if metric.context.tenant_id == tenant_id]

        if domain is not None:
            metrics = [metric for metric in metrics if metric.context.domain == domain]

        if from_time is not None:
            metrics = [metric for metric in metrics if metric.timestamp >= from_time]

        if to_time is not None:
            metrics = [metric for metric in metrics if metric.timestamp <= to_time]

        if tags:
            metrics = [
                metric for metric in metrics
                if all(metric.tags.get(key) == value for key, value in tags.items())
            ]

        return metrics

    def count(self) -> int:
        with self._lock:
            return len(self._metrics)

    def clear(self) -> None:
        with self._lock:
            self._metrics.clear()


# =============================================================================
# Engine
# =============================================================================

class OrchestrationMetricsEngine:
    def __init__(
        self,
        store: Optional[InMemoryMetricsStore] = None,
        sinks: Optional[List[MetricsSink]] = None,
        slo_definitions: Optional[List[SLODefinition]] = None,
    ) -> None:
        self.store = store or InMemoryMetricsStore()
        self.sinks = sinks or [LoggingMetricsSink()]
        self._slos: Dict[str, SLODefinition] = {}

        for slo in slo_definitions or []:
            self.register_slo(slo)

    def emit(
        self,
        name: str,
        value: float,
        metric_type: MetricType,
        component: OrchestrationComponent,
        unit: MetricUnit = MetricUnit.COUNT,
        context: Optional[MetricsContext] = None,
        tags: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> OrchestrationMetric:
        metric = OrchestrationMetric(
            metric_id=str(uuid.uuid4()),
            name=name,
            metric_type=metric_type,
            component=component,
            value=float(value),
            unit=unit,
            timestamp=datetime.now(timezone.utc),
            tags=tags or {},
            context=context or MetricsContext(),
            metadata=metadata or {},
        )

        self.store.append(metric)

        for sink in self.sinks:
            sink.emit(metric)

        return metric

    def increment(
        self,
        name: str,
        value: int = 1,
        component: OrchestrationComponent = OrchestrationComponent.SYSTEM,
        context: Optional[MetricsContext] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> OrchestrationMetric:
        return self.emit(
            name=name,
            value=float(value),
            metric_type=MetricType.COUNTER,
            component=component,
            unit=MetricUnit.COUNT,
            context=context,
            tags=tags,
        )

    def gauge(
        self,
        name: str,
        value: float,
        component: OrchestrationComponent = OrchestrationComponent.SYSTEM,
        unit: MetricUnit = MetricUnit.COUNT,
        context: Optional[MetricsContext] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> OrchestrationMetric:
        return self.emit(
            name=name,
            value=value,
            metric_type=MetricType.GAUGE,
            component=component,
            unit=unit,
            context=context,
            tags=tags,
        )

    def timing(
        self,
        name: str,
        value_ms: float,
        component: OrchestrationComponent = OrchestrationComponent.SYSTEM,
        context: Optional[MetricsContext] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> OrchestrationMetric:
        return self.emit(
            name=name,
            value=value_ms,
            metric_type=MetricType.TIMING,
            component=component,
            unit=MetricUnit.MILLISECONDS,
            context=context,
            tags=tags,
        )

    def register_slo(self, slo: SLODefinition) -> None:
        slo.validate()
        self._slos[slo.slo_id] = slo

    def list_slos(self, enabled_only: bool = True) -> List[SLODefinition]:
        items = list(self._slos.values())

        if enabled_only:
            items = [item for item in items if item.enabled]

        return items

    def query(
        self,
        name: Optional[str] = None,
        component: Optional[OrchestrationComponent] = None,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> List[OrchestrationMetric]:
        return self.store.list_metrics(
            name=name,
            component=component,
            tenant_id=tenant_id,
            domain=domain,
            from_time=from_time,
            to_time=to_time,
            tags=tags,
        )

    def summarize(
        self,
        metrics: Iterable[OrchestrationMetric],
    ) -> Dict[str, MetricSeriesSummary]:
        grouped: Dict[str, List[OrchestrationMetric]] = defaultdict(list)

        for metric in metrics:
            grouped[metric.name].append(metric)

        summaries: Dict[str, MetricSeriesSummary] = {}

        for name, items in grouped.items():
            values = [metric.value for metric in items if math.isfinite(metric.value)]
            timestamps = [metric.timestamp for metric in items]

            if values:
                sorted_values = sorted(values)
                summaries[name] = MetricSeriesSummary(
                    name=name,
                    count=len(values),
                    min_value=min(values),
                    max_value=max(values),
                    avg_value=sum(values) / len(values),
                    median_value=statistics.median(values),
                    p95_value=self._percentile(sorted_values, 95),
                    p99_value=self._percentile(sorted_values, 99),
                    sum_value=sum(values),
                    unit=items[-1].unit,
                    first_seen_at=min(timestamps),
                    last_seen_at=max(timestamps),
                )
            else:
                summaries[name] = MetricSeriesSummary(
                    name=name,
                    count=0,
                    min_value=None,
                    max_value=None,
                    avg_value=None,
                    median_value=None,
                    p95_value=None,
                    p99_value=None,
                    sum_value=0.0,
                    unit=None,
                    first_seen_at=None,
                    last_seen_at=None,
                )

        return summaries

    def evaluate_slo(
        self,
        slo: SLODefinition,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> SLOEvaluation:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=slo.evaluation_window_minutes)

        effective_tenant_id = tenant_id if tenant_id is not None else slo.tenant_id
        effective_domain = domain if domain is not None else slo.domain

        metrics = self.query(
            name=slo.metric_name,
            component=slo.component,
            tenant_id=effective_tenant_id,
            domain=effective_domain,
            from_time=window_start,
            to_time=now,
        )

        if not metrics:
            return SLOEvaluation(
                slo_id=slo.slo_id,
                name=slo.name,
                status=SLOStatus.UNKNOWN,
                metric_name=slo.metric_name,
                current_value=None,
                threshold=slo.threshold,
                comparison=slo.comparison,
                evaluated_at=now,
                window_start=window_start,
                window_end=now,
                breach=False,
                reason="Nenhuma métrica encontrada na janela de avaliação.",
            )

        values = [metric.value for metric in metrics]
        current_value = values[-1]

        breach = self._compare(current_value, slo.threshold, slo.comparison)

        warning_threshold = self._warning_threshold(slo)

        warning = self._compare(current_value, warning_threshold, slo.comparison)

        if breach:
            status = SLOStatus.BREACHED
            reason = (
                f"SLO violado: {current_value} {slo.comparison} "
                f"{slo.threshold}"
            )
        elif warning:
            status = SLOStatus.WARNING
            reason = (
                f"SLO em zona de alerta: {current_value} próximo de "
                f"{slo.threshold}"
            )
        else:
            status = SLOStatus.HEALTHY
            reason = "SLO saudável."

        return SLOEvaluation(
            slo_id=slo.slo_id,
            name=slo.name,
            status=status,
            metric_name=slo.metric_name,
            current_value=current_value,
            threshold=slo.threshold,
            comparison=slo.comparison,
            evaluated_at=now,
            window_start=window_start,
            window_end=now,
            breach=breach,
            reason=reason,
            metadata={
                "samples": len(values),
                "unit": slo.unit.value,
                "tenant_id": effective_tenant_id,
                "domain": effective_domain,
            },
        )

    def evaluate_all_slos(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> List[SLOEvaluation]:
        return [
            self.evaluate_slo(slo, tenant_id=tenant_id, domain=domain)
            for slo in self.list_slos(enabled_only=True)
        ]

    def component_health(
        self,
        component: OrchestrationComponent,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> ComponentHealth:
        slo_evaluations = [
            evaluation
            for evaluation in self.evaluate_all_slos(tenant_id=tenant_id, domain=domain)
            if self._slos[evaluation.slo_id].component == component
        ]

        issues: List[str] = []

        score = 100.0

        for evaluation in slo_evaluations:
            if evaluation.status == SLOStatus.BREACHED:
                score -= 35.0
                issues.append(evaluation.reason)

            elif evaluation.status == SLOStatus.WARNING:
                score -= 15.0
                issues.append(evaluation.reason)

            elif evaluation.status == SLOStatus.UNKNOWN:
                score -= 5.0

        score = max(0.0, min(100.0, score))

        if score >= 85:
            status = HealthStatus.HEALTHY
        elif score >= 50:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.CRITICAL

        recent_metrics = self.query(
            component=component,
            tenant_id=tenant_id,
            domain=domain,
            from_time=datetime.now(timezone.utc) - timedelta(minutes=60),
        )

        summaries = self.summarize(recent_metrics)

        return ComponentHealth(
            component=component,
            status=status,
            score=score,
            evaluated_at=datetime.now(timezone.utc),
            metrics={
                name: summary.to_dict()
                for name, summary in summaries.items()
            },
            slo_evaluations=slo_evaluations,
            issues=issues,
        )

    def snapshot(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        window_minutes: int = 60,
    ) -> OrchestrationMetricsSnapshot:
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(minutes=window_minutes)

        metrics = self.query(
            tenant_id=tenant_id,
            domain=domain,
            from_time=from_time,
            to_time=now,
        )

        summaries = self.summarize(metrics)
        slo_evaluations = self.evaluate_all_slos(tenant_id=tenant_id, domain=domain)

        components = sorted(
            {metric.component for metric in metrics}
            | {slo.component for slo in self.list_slos()},
            key=lambda item: item.value,
        )

        health = [
            self.component_health(
                component=component,
                tenant_id=tenant_id,
                domain=domain,
            )
            for component in components
        ]

        if health:
            global_score = sum(item.score for item in health) / len(health)
        else:
            global_score = 100.0

        if global_score >= 85:
            global_status = HealthStatus.HEALTHY
        elif global_score >= 50:
            global_status = HealthStatus.DEGRADED
        else:
            global_status = HealthStatus.CRITICAL

        return OrchestrationMetricsSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=now,
            tenant_id=tenant_id,
            domain=domain,
            total_metrics=len(metrics),
            summaries=summaries,
            slo_evaluations=slo_evaluations,
            component_health=health,
            global_health_status=global_status,
            global_health_score=round(global_score, 2),
        )

    def export_metrics_json(
        self,
        metrics: Optional[Iterable[OrchestrationMetric]] = None,
    ) -> str:
        selected = list(metrics or self.query())

        return json.dumps(
            [metric.to_dict() for metric in selected],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_snapshot_json(self, snapshot: OrchestrationMetricsSnapshot) -> str:
        return json.dumps(
            snapshot.to_dict(),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def clear(self) -> None:
        self.store.clear()

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> Optional[float]:
        if not values:
            return None

        k = (len(values) - 1) * percentile / 100
        floor = math.floor(k)
        ceil = math.ceil(k)

        if floor == ceil:
            return values[int(k)]

        return values[floor] * (ceil - k) + values[ceil] * (k - floor)

    @staticmethod
    def _compare(value: float, threshold: float, comparison: str) -> bool:
        if comparison == ">":
            return value > threshold
        if comparison == ">=":
            return value >= threshold
        if comparison == "<":
            return value < threshold
        if comparison == "<=":
            return value <= threshold
        if comparison == "==":
            return value == threshold
        if comparison == "!=":
            return value != threshold

        raise MetricValidationError(f"comparison inválido: {comparison}")

    @staticmethod
    def _warning_threshold(slo: SLODefinition) -> float:
        if slo.comparison in {">", ">="}:
            return slo.threshold * slo.warning_ratio

        if slo.comparison in {"<", "<="}:
            if slo.warning_ratio == 0:
                return slo.threshold
            return slo.threshold / slo.warning_ratio

        return slo.threshold

    @staticmethod
    def slo_to_dict(evaluation: SLOEvaluation) -> Dict[str, Any]:
        data = asdict(evaluation)
        data["status"] = evaluation.status.value
        data["evaluated_at"] = evaluation.evaluated_at.isoformat()
        data["window_start"] = evaluation.window_start.isoformat()
        data["window_end"] = evaluation.window_end.isoformat()
        return data

    @staticmethod
    def health_to_dict(health: ComponentHealth) -> Dict[str, Any]:
        data = asdict(health)
        data["component"] = health.component.value
        data["status"] = health.status.value
        data["evaluated_at"] = health.evaluated_at.isoformat()
        data["slo_evaluations"] = [
            OrchestrationMetricsEngine.slo_to_dict(item)
            for item in health.slo_evaluations
        ]
        return data


# =============================================================================
# Convenience Facade
# =============================================================================

class OrchestrationMetricsReporter:
    def __init__(self, engine: Optional[OrchestrationMetricsEngine] = None) -> None:
        self.engine = engine or create_default_orchestration_metrics()

    def workflow_started(
        self,
        workflow_id: str,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        self.engine.increment(
            "workflow.started.total",
            component=OrchestrationComponent.WORKFLOW,
            context=MetricsContext(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                run_id=run_id,
            ),
            tags={"workflow_id": workflow_id},
        )

    def workflow_finished(
        self,
        workflow_id: str,
        status: str,
        duration_ms: float,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        context = MetricsContext(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            run_id=run_id,
        )

        self.engine.increment(
            "workflow.finished.total",
            component=OrchestrationComponent.WORKFLOW,
            context=context,
            tags={
                "workflow_id": workflow_id,
                "status": status,
            },
        )

        self.engine.timing(
            "workflow.duration_ms",
            duration_ms,
            component=OrchestrationComponent.WORKFLOW,
            context=context,
            tags={
                "workflow_id": workflow_id,
                "status": status,
            },
        )

    def dag_finished(
        self,
        dag_id: str,
        status: str,
        duration_ms: float,
        tenant_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        context = MetricsContext(
            tenant_id=tenant_id,
            dag_id=dag_id,
            run_id=run_id,
        )

        self.engine.increment(
            "dag.finished.total",
            component=OrchestrationComponent.DAG,
            context=context,
            tags={
                "dag_id": dag_id,
                "status": status,
            },
        )

        self.engine.timing(
            "dag.duration_ms",
            duration_ms,
            component=OrchestrationComponent.DAG,
            context=context,
            tags={
                "dag_id": dag_id,
                "status": status,
            },
        )

    def task_finished(
        self,
        task_id: str,
        task_type: str,
        status: str,
        duration_ms: float,
        tenant_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        context = MetricsContext(
            tenant_id=tenant_id,
            task_id=task_id,
            worker_id=worker_id,
            run_id=run_id,
        )

        self.engine.increment(
            "task.finished.total",
            component=OrchestrationComponent.TASK,
            context=context,
            tags={
                "task_type": task_type,
                "status": status,
            },
        )

        self.engine.timing(
            "task.duration_ms",
            duration_ms,
            component=OrchestrationComponent.TASK,
            context=context,
            tags={
                "task_type": task_type,
                "status": status,
            },
        )

    def queue_depth(
        self,
        queue_id: str,
        depth: int,
        tenant_id: Optional[str] = None,
    ) -> None:
        self.engine.gauge(
            "queue.depth",
            float(depth),
            component=OrchestrationComponent.QUEUE,
            unit=MetricUnit.TASKS,
            context=MetricsContext(
                tenant_id=tenant_id,
                queue_id=queue_id,
            ),
            tags={"queue_id": queue_id},
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        active_tasks: int,
        capacity: int,
        tenant_id: Optional[str] = None,
    ) -> None:
        context = MetricsContext(
            tenant_id=tenant_id,
            worker_id=worker_id,
        )

        self.engine.gauge(
            "worker.active_tasks",
            float(active_tasks),
            component=OrchestrationComponent.WORKER,
            unit=MetricUnit.TASKS,
            context=context,
            tags={
                "worker_id": worker_id,
                "status": status,
            },
        )

        self.engine.gauge(
            "worker.capacity",
            float(capacity),
            component=OrchestrationComponent.WORKER,
            unit=MetricUnit.TASKS,
            context=context,
            tags={
                "worker_id": worker_id,
                "status": status,
            },
        )


# =============================================================================
# Default SLOs
# =============================================================================

def build_default_orchestration_slos() -> List[SLODefinition]:
    return [
        SLODefinition(
            slo_id="slo-workflow-duration",
            name="Workflow duration below 5 minutes",
            metric_name="workflow.duration_ms",
            component=OrchestrationComponent.WORKFLOW,
            threshold=300_000.0,
            comparison="<=",
            unit=MetricUnit.MILLISECONDS,
            evaluation_window_minutes=60,
            warning_ratio=0.8,
            tags={"slo": "duration"},
        ),
        SLODefinition(
            slo_id="slo-task-duration",
            name="Task duration below 1 minute",
            metric_name="task.duration_ms",
            component=OrchestrationComponent.TASK,
            threshold=60_000.0,
            comparison="<=",
            unit=MetricUnit.MILLISECONDS,
            evaluation_window_minutes=60,
            warning_ratio=0.8,
            tags={"slo": "duration"},
        ),
        SLODefinition(
            slo_id="slo-queue-depth",
            name="Queue depth below 1000 tasks",
            metric_name="queue.depth",
            component=OrchestrationComponent.QUEUE,
            threshold=1000.0,
            comparison="<=",
            unit=MetricUnit.TASKS,
            evaluation_window_minutes=15,
            warning_ratio=0.8,
            tags={"slo": "backlog"},
        ),
        SLODefinition(
            slo_id="slo-worker-active-tasks",
            name="Worker active tasks below overload threshold",
            metric_name="worker.active_tasks",
            component=OrchestrationComponent.WORKER,
            threshold=100.0,
            comparison="<=",
            unit=MetricUnit.TASKS,
            evaluation_window_minutes=15,
            warning_ratio=0.8,
            tags={"slo": "worker"},
        ),
    ]


def create_default_orchestration_metrics() -> OrchestrationMetricsEngine:
    return OrchestrationMetricsEngine(
        slo_definitions=build_default_orchestration_slos()
    )


# =============================================================================
# Compatibility Aliases
# =============================================================================

WorkflowMetric = OrchestrationMetric
QueueMetric = OrchestrationMetric


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_orchestration_metrics()
    reporter = OrchestrationMetricsReporter(engine)

    reporter.workflow_started(
        workflow_id="daily-sales-workflow",
        tenant_id="tenant-default",
        run_id="run-001",
    )

    reporter.task_finished(
        task_id="extract-sales",
        task_type="extract",
        status="success",
        duration_ms=1200.5,
        tenant_id="tenant-default",
        worker_id="worker-001",
        run_id="run-001",
    )

    reporter.task_finished(
        task_id="transform-sales",
        task_type="transform",
        status="success",
        duration_ms=2200.0,
        tenant_id="tenant-default",
        worker_id="worker-001",
        run_id="run-001",
    )

    reporter.queue_depth(
        queue_id="default",
        depth=12,
        tenant_id="tenant-default",
    )

    reporter.worker_heartbeat(
        worker_id="worker-001",
        status="idle",
        active_tasks=0,
        capacity=4,
        tenant_id="tenant-default",
    )

    reporter.workflow_finished(
        workflow_id="daily-sales-workflow",
        status="success",
        duration_ms=7800.0,
        tenant_id="tenant-default",
        run_id="run-001",
    )

    snapshot = engine.snapshot(
        tenant_id="tenant-default",
        window_minutes=60,
    )

    print(engine.export_snapshot_json(snapshot))


if __name__ == "__main__":
    example_usage()