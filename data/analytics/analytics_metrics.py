"""
data/analytics/analytics_metrics.py

Enterprise Analytics Metrics Engine.

Recursos:
- Catálogo de métricas enterprise
- Métricas simples, compostas e derivadas
- Agregações, filtros, dimensões e janelas temporais
- Validação de definições
- Execução plugável via query executor
- Cache com TTL
- Auditoria e observabilidade
- Multi-tenant
- Versionamento e status de métricas
- Exportação JSON
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import math
import operator
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class MetricStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class MetricType(str, Enum):
    SIMPLE = "simple"
    DERIVED = "derived"
    COMPOSITE = "composite"
    RATIO = "ratio"
    KPI = "kpi"


class AggregationType(str, Enum):
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    MEDIAN = "median"
    PERCENTILE = "percentile"
    FIRST = "first"
    LAST = "last"


class TimeGrain(str, Enum):
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class FilterOperator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    CONTAINS = "contains"


class MetricSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class AnalyticsMetricError(Exception):
    """Erro base de métricas analíticas."""


class MetricNotFound(AnalyticsMetricError):
    """Métrica não encontrada."""


class MetricValidationError(AnalyticsMetricError):
    """Erro de validação da métrica."""


class MetricExecutionError(AnalyticsMetricError):
    """Erro durante execução da métrica."""


class UnsafeExpressionError(AnalyticsMetricError):
    """Expressão derivada insegura."""


# =============================================================================
# Protocols
# =============================================================================

class MetricQueryExecutor(Protocol):
    def execute(
        self,
        source: str,
        measure: Optional[str],
        aggregation: AggregationType,
        dimensions: List[str],
        filters: List["MetricFilter"],
        time_window: Optional["TimeWindow"],
        context: "MetricExecutionContext",
    ) -> Any:
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

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("analytics_metric_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def timing(
        self,
        metric_name: str,
        value_ms: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("timing=%s value_ms=%s tags=%s", metric_name, value_ms, tags or {})


class InMemoryMetricQueryExecutor:
    """
    Executor simples para testes locais.

    Em produção, substitua por executor SQL/Spark/Trino/BigQuery/Snowflake.
    """

    def __init__(self, datasets: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self.datasets = datasets or {}

    def execute(
        self,
        source: str,
        measure: Optional[str],
        aggregation: AggregationType,
        dimensions: List[str],
        filters: List["MetricFilter"],
        time_window: Optional["TimeWindow"],
        context: "MetricExecutionContext",
    ) -> Any:
        rows = list(self.datasets.get(source, []))
        rows = self._apply_filters(rows, filters)

        if dimensions:
            grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}

            for row in rows:
                key = tuple(row.get(dim) for dim in dimensions)
                grouped.setdefault(key, []).append(row)

            return [
                {
                    **{dim: key[index] for index, dim in enumerate(dimensions)},
                    "value": self._aggregate(group_rows, measure, aggregation),
                }
                for key, group_rows in grouped.items()
            ]

        return self._aggregate(rows, measure, aggregation)

    @staticmethod
    def _apply_filters(
        rows: List[Dict[str, Any]],
        filters: List["MetricFilter"],
    ) -> List[Dict[str, Any]]:
        result = rows

        for metric_filter in filters:
            result = [
                row for row in result
                if metric_filter.matches(row.get(metric_filter.field_name))
            ]

        return result

    @staticmethod
    def _aggregate(
        rows: List[Dict[str, Any]],
        measure: Optional[str],
        aggregation: AggregationType,
    ) -> Any:
        if aggregation == AggregationType.COUNT:
            return len(rows)

        values = [
            row.get(measure)
            for row in rows
            if measure is not None and row.get(measure) is not None
        ]

        numeric_values = [
            float(value)
            for value in values
            if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit()
        ]

        if aggregation == AggregationType.SUM:
            return sum(numeric_values)

        if aggregation == AggregationType.AVG:
            return sum(numeric_values) / len(numeric_values) if numeric_values else 0

        if aggregation == AggregationType.MIN:
            return min(numeric_values) if numeric_values else None

        if aggregation == AggregationType.MAX:
            return max(numeric_values) if numeric_values else None

        if aggregation == AggregationType.MEDIAN:
            if not numeric_values:
                return None
            sorted_values = sorted(numeric_values)
            mid = len(sorted_values) // 2
            if len(sorted_values) % 2 == 0:
                return (sorted_values[mid - 1] + sorted_values[mid]) / 2
            return sorted_values[mid]

        if aggregation == AggregationType.FIRST:
            return values[0] if values else None

        if aggregation == AggregationType.LAST:
            return values[-1] if values else None

        if aggregation == AggregationType.COUNT_DISTINCT:
            return len(set(values))

        raise MetricExecutionError(f"Agregação não suportada no executor em memória: {aggregation}")


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime
    grain: Optional[TimeGrain] = None
    timezone_name: str = "UTC"

    def validate(self) -> None:
        if self.start >= self.end:
            raise MetricValidationError("TimeWindow inválida: start deve ser menor que end")


@dataclass(frozen=True)
class MetricFilter:
    field_name: str
    operator: FilterOperator
    value: Any

    def matches(self, candidate: Any) -> bool:
        if self.operator == FilterOperator.EQ:
            return candidate == self.value
        if self.operator == FilterOperator.NE:
            return candidate != self.value
        if self.operator == FilterOperator.GT:
            return candidate > self.value
        if self.operator == FilterOperator.GTE:
            return candidate >= self.value
        if self.operator == FilterOperator.LT:
            return candidate < self.value
        if self.operator == FilterOperator.LTE:
            return candidate <= self.value
        if self.operator == FilterOperator.IN:
            return candidate in self.value
        if self.operator == FilterOperator.NOT_IN:
            return candidate not in self.value
        if self.operator == FilterOperator.BETWEEN:
            return self.value[0] <= candidate <= self.value[1]
        if self.operator == FilterOperator.CONTAINS:
            return str(self.value) in str(candidate)
        return False


@dataclass(frozen=True)
class MetricOwner:
    owner_id: str
    name: Optional[str] = None
    team: Optional[str] = None
    email: Optional[str] = None


@dataclass(frozen=True)
class MetricLineage:
    source_tables: List[str] = field(default_factory=list)
    upstream_metrics: List[str] = field(default_factory=list)
    downstream_assets: List[str] = field(default_factory=list)
    transformation: Optional[str] = None


@dataclass(frozen=True)
class MetricSLO:
    freshness_minutes: Optional[int] = None
    max_latency_ms: Optional[int] = None
    min_success_rate: Optional[float] = None
    expected_min_value: Optional[float] = None
    expected_max_value: Optional[float] = None


@dataclass(frozen=True)
class MetricDefinition:
    metric_id: str
    name: str
    metric_type: MetricType
    source: Optional[str] = None
    measure: Optional[str] = None
    aggregation: Optional[AggregationType] = None
    expression: Optional[str] = None
    dimensions: List[str] = field(default_factory=list)
    filters: List[MetricFilter] = field(default_factory=list)
    owner: Optional[MetricOwner] = None
    lineage: MetricLineage = field(default_factory=MetricLineage)
    slo: MetricSLO = field(default_factory=MetricSLO)
    status: MetricStatus = MetricStatus.ACTIVE
    version: str = "1.0.0"
    domain: Optional[str] = None
    tenant_id: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    cache_ttl_seconds: Optional[int] = 300
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.metric_id:
            raise MetricValidationError("metric_id é obrigatório")

        if not self.name:
            raise MetricValidationError("name é obrigatório")

        if self.metric_type in {MetricType.SIMPLE, MetricType.KPI}:
            if not self.source:
                raise MetricValidationError("source é obrigatório para métrica simples/KPI")
            if not self.aggregation:
                raise MetricValidationError("aggregation é obrigatória para métrica simples/KPI")

        if self.metric_type in {MetricType.DERIVED, MetricType.RATIO, MetricType.COMPOSITE}:
            if not self.expression:
                raise MetricValidationError("expression é obrigatória para métrica derivada/ratio/composite")


@dataclass(frozen=True)
class MetricExecutionContext:
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class MetricResult:
    metric_id: str
    name: str
    value: Any
    computed_at: datetime
    dimensions: Dict[str, Any] = field(default_factory=dict)
    time_window: Optional[TimeWindow] = None
    status: str = "success"
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Cache
# =============================================================================

@dataclass
class CacheEntry:
    value: MetricResult
    expires_at: datetime


class MetricCache:
    def __init__(self) -> None:
        self._items: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[MetricResult]:
        with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None

            if datetime.now(timezone.utc) > entry.expires_at:
                self._items.pop(key, None)
                return None

            return entry.value

    def set(self, key: str, value: MetricResult, ttl_seconds: int) -> None:
        with self._lock:
            self._items[key] = CacheEntry(
                value=value,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


# =============================================================================
# Repository
# =============================================================================

class MetricRepository:
    def __init__(self, metrics: Optional[List[MetricDefinition]] = None) -> None:
        self._metrics: Dict[str, MetricDefinition] = {}

        for metric in metrics or []:
            self.save(metric)

    def save(self, metric: MetricDefinition) -> None:
        metric.validate()
        self._metrics[metric.metric_id] = metric

    def get(self, metric_id: str) -> MetricDefinition:
        metric = self._metrics.get(metric_id)
        if not metric:
            raise MetricNotFound(metric_id)
        return metric

    def list_all(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[MetricStatus] = None,
    ) -> List[MetricDefinition]:
        items = list(self._metrics.values())

        if tenant_id is not None:
            items = [m for m in items if m.tenant_id == tenant_id]

        if domain is not None:
            items = [m for m in items if m.domain == domain]

        if status is not None:
            items = [m for m in items if m.status == status]

        return items

    def delete(self, metric_id: str) -> None:
        if metric_id not in self._metrics:
            raise MetricNotFound(metric_id)
        del self._metrics[metric_id]

    def dependency_graph(self) -> Dict[str, List[str]]:
        return {
            metric.metric_id: list(metric.lineage.upstream_metrics)
            for metric in self._metrics.values()
        }


# =============================================================================
# Safe Expression Evaluator
# =============================================================================

class SafeExpressionEvaluator:
    """
    Avaliador seguro para expressões derivadas.

    Permitido:
    - operações matemáticas básicas
    - nomes de métricas resolvidas
    - funções whitelisted: min, max, abs, round, sqrt, log
    """

    ALLOWED_OPERATORS: Dict[type, Callable[..., Any]] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.Mod: operator.mod,
    }

    ALLOWED_FUNCTIONS: Dict[str, Callable[..., Any]] = {
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "sqrt": math.sqrt,
        "log": math.log,
    }

    def evaluate(self, expression: str, variables: Dict[str, Any]) -> Any:
        try:
            tree = ast.parse(expression, mode="eval")
            return self._eval(tree.body, variables)
        except ZeroDivisionError:
            return None
        except Exception as exc:
            raise UnsafeExpressionError(str(exc)) from exc

    def _eval(self, node: ast.AST, variables: Dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise UnsafeExpressionError("Constante não numérica não permitida")

        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise UnsafeExpressionError(f"Variável desconhecida: {node.id}")
            return variables[node.id]

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in self.ALLOWED_OPERATORS:
                raise UnsafeExpressionError(f"Operador não permitido: {op_type}")
            return self.ALLOWED_OPERATORS[op_type](
                self._eval(node.left, variables),
                self._eval(node.right, variables),
            )

        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in self.ALLOWED_OPERATORS:
                raise UnsafeExpressionError(f"Operador unário não permitido: {op_type}")
            return self.ALLOWED_OPERATORS[op_type](self._eval(node.operand, variables))

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise UnsafeExpressionError("Chamada inválida")

            func_name = node.func.id
            if func_name not in self.ALLOWED_FUNCTIONS:
                raise UnsafeExpressionError(f"Função não permitida: {func_name}")

            args = [self._eval(arg, variables) for arg in node.args]
            return self.ALLOWED_FUNCTIONS[func_name](*args)

        raise UnsafeExpressionError(f"Nó AST não permitido: {type(node)}")


# =============================================================================
# Engine
# =============================================================================

class AnalyticsMetricsEngine:
    def __init__(
        self,
        repository: MetricRepository,
        query_executor: Optional[MetricQueryExecutor] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        cache: Optional[MetricCache] = None,
        expression_evaluator: Optional[SafeExpressionEvaluator] = None,
    ) -> None:
        self.repository = repository
        self.query_executor = query_executor or InMemoryMetricQueryExecutor()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.cache = cache or MetricCache()
        self.expression_evaluator = expression_evaluator or SafeExpressionEvaluator()

    def compute(
        self,
        metric_id: str,
        context: Optional[MetricExecutionContext] = None,
        time_window: Optional[TimeWindow] = None,
        filters: Optional[List[MetricFilter]] = None,
        dimensions: Optional[List[str]] = None,
        use_cache: bool = True,
    ) -> MetricResult:
        context = context or MetricExecutionContext()
        metric = self.repository.get(metric_id)

        if metric.status != MetricStatus.ACTIVE:
            raise MetricValidationError(f"Métrica não está ativa: {metric_id}")

        if metric.tenant_id and context.tenant_id and metric.tenant_id != context.tenant_id:
            raise MetricValidationError("Tenant inválido para métrica")

        if time_window:
            time_window.validate()

        started_at = datetime.now(timezone.utc)
        cache_key = self._cache_key(metric, context, time_window, filters or [], dimensions or [])

        cached = self.cache.get(cache_key) if use_cache and metric.cache_ttl_seconds else None
        if cached:
            self.metrics_backend.increment(
                "analytics.metrics.cache_hit",
                tags={"metric_id": metric.metric_id},
            )
            return cached

        try:
            if metric.metric_type in {MetricType.SIMPLE, MetricType.KPI}:
                result = self._compute_simple(
                    metric,
                    context,
                    time_window,
                    filters or [],
                    dimensions,
                )
            else:
                result = self._compute_derived(
                    metric,
                    context,
                    time_window,
                    filters or [],
                    dimensions,
                )

            duration_ms = (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
            result.metadata["duration_ms"] = duration_ms
            result.metadata["version"] = metric.version
            result.metadata["domain"] = metric.domain

            if metric.cache_ttl_seconds and use_cache:
                self.cache.set(cache_key, result, metric.cache_ttl_seconds)

            self._audit("metric.computed", metric, context, result)
            self.metrics_backend.timing(
                "analytics.metrics.compute.duration_ms",
                duration_ms,
                tags={"metric_id": metric.metric_id},
            )
            self.metrics_backend.increment(
                "analytics.metrics.compute.success",
                tags={"metric_id": metric.metric_id},
            )

            self._check_slo(metric, result, duration_ms)

            return result

        except Exception as exc:
            logger.exception("Erro ao computar métrica %s", metric_id)
            self.metrics_backend.increment(
                "analytics.metrics.compute.error",
                tags={"metric_id": metric_id},
            )
            self._audit(
                "metric.compute_failed",
                metric,
                context,
                None,
                extra={"error": str(exc)},
            )
            return MetricResult(
                metric_id=metric.metric_id,
                name=metric.name,
                value=None,
                computed_at=datetime.now(timezone.utc),
                time_window=time_window,
                status="error",
                error=str(exc),
            )

    def compute_many(
        self,
        metric_ids: Iterable[str],
        context: Optional[MetricExecutionContext] = None,
        time_window: Optional[TimeWindow] = None,
        filters: Optional[List[MetricFilter]] = None,
    ) -> Dict[str, MetricResult]:
        return {
            metric_id: self.compute(
                metric_id=metric_id,
                context=context,
                time_window=time_window,
                filters=filters,
            )
            for metric_id in metric_ids
        }

    def _compute_simple(
        self,
        metric: MetricDefinition,
        context: MetricExecutionContext,
        time_window: Optional[TimeWindow],
        filters: List[MetricFilter],
        dimensions: Optional[List[str]],
    ) -> MetricResult:
        effective_filters = list(metric.filters) + list(filters)
        effective_dimensions = dimensions if dimensions is not None else metric.dimensions

        value = self.query_executor.execute(
            source=metric.source or "",
            measure=metric.measure,
            aggregation=metric.aggregation or AggregationType.SUM,
            dimensions=effective_dimensions,
            filters=effective_filters,
            time_window=time_window,
            context=context,
        )

        return MetricResult(
            metric_id=metric.metric_id,
            name=metric.name,
            value=value,
            computed_at=datetime.now(timezone.utc),
            time_window=time_window,
            metadata={
                "source": metric.source,
                "measure": metric.measure,
                "aggregation": metric.aggregation.value if metric.aggregation else None,
                "dimensions": effective_dimensions,
            },
        )

    def _compute_derived(
        self,
        metric: MetricDefinition,
        context: MetricExecutionContext,
        time_window: Optional[TimeWindow],
        filters: List[MetricFilter],
        dimensions: Optional[List[str]],
    ) -> MetricResult:
        variables: Dict[str, Any] = {}

        for upstream_id in metric.lineage.upstream_metrics:
            upstream_result = self.compute(
                upstream_id,
                context=context,
                time_window=time_window,
                filters=filters,
                dimensions=dimensions,
            )
            variables[upstream_id] = upstream_result.value

        value = self.expression_evaluator.evaluate(
            metric.expression or "",
            variables,
        )

        return MetricResult(
            metric_id=metric.metric_id,
            name=metric.name,
            value=value,
            computed_at=datetime.now(timezone.utc),
            time_window=time_window,
            metadata={
                "expression": metric.expression,
                "variables": variables,
            },
        )

    def validate_metric(self, metric: MetricDefinition) -> List[str]:
        errors: List[str] = []

        try:
            metric.validate()
        except Exception as exc:
            errors.append(str(exc))

        if metric.metric_type in {MetricType.DERIVED, MetricType.RATIO, MetricType.COMPOSITE}:
            if not metric.lineage.upstream_metrics:
                errors.append("Métrica derivada precisa de upstream_metrics")

            if metric.expression:
                try:
                    self.expression_evaluator.evaluate(
                        metric.expression,
                        {item: 1 for item in metric.lineage.upstream_metrics},
                    )
                except Exception as exc:
                    errors.append(f"Expressão inválida: {exc}")

        return errors

    def export_catalog_json(self) -> str:
        return json.dumps(
            [self._metric_to_dict(metric) for metric in self.repository.list_all()],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def lineage_graph(self) -> Dict[str, List[str]]:
        return self.repository.dependency_graph()

    def _check_slo(
        self,
        metric: MetricDefinition,
        result: MetricResult,
        duration_ms: float,
    ) -> None:
        if metric.slo.max_latency_ms and duration_ms > metric.slo.max_latency_ms:
            self.metrics_backend.increment(
                "analytics.metrics.slo.latency_violation",
                tags={"metric_id": metric.metric_id},
            )

        if isinstance(result.value, (int, float)):
            if (
                metric.slo.expected_min_value is not None
                and result.value < metric.slo.expected_min_value
            ):
                self.metrics_backend.increment(
                    "analytics.metrics.slo.min_value_violation",
                    tags={"metric_id": metric.metric_id},
                )

            if (
                metric.slo.expected_max_value is not None
                and result.value > metric.slo.expected_max_value
            ):
                self.metrics_backend.increment(
                    "analytics.metrics.slo.max_value_violation",
                    tags={"metric_id": metric.metric_id},
                )

    def _audit(
        self,
        event_type: str,
        metric: MetricDefinition,
        context: MetricExecutionContext,
        result: Optional[MetricResult],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "metric_id": metric.metric_id,
                "metric_name": metric.name,
                "tenant_id": context.tenant_id,
                "domain": metric.domain,
                "user_id": context.user_id,
                "correlation_id": context.correlation_id,
                "status": result.status if result else "error",
                "value_hash": self._hash_value(result.value) if result else None,
                "extra": extra or {},
            }
        )

    @staticmethod
    def _cache_key(
        metric: MetricDefinition,
        context: MetricExecutionContext,
        time_window: Optional[TimeWindow],
        filters: List[MetricFilter],
        dimensions: List[str],
    ) -> str:
        payload = {
            "metric_id": metric.metric_id,
            "tenant_id": context.tenant_id,
            "domain": context.domain,
            "parameters": context.parameters,
            "time_window": asdict(time_window) if time_window else None,
            "filters": [asdict(f) for f in filters],
            "dimensions": dimensions,
        }

        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_value(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _metric_to_dict(metric: MetricDefinition) -> Dict[str, Any]:
        data = asdict(metric)
        data["metric_type"] = metric.metric_type.value
        data["status"] = metric.status.value
        data["aggregation"] = metric.aggregation.value if metric.aggregation else None
        data["created_at"] = metric.created_at.isoformat()
        data["updated_at"] = metric.updated_at.isoformat() if metric.updated_at else None

        for item in data["filters"]:
            item["operator"] = item["operator"].value

        return data


# =============================================================================
# Factory
# =============================================================================

def build_default_metric_catalog() -> List[MetricDefinition]:
    return [
        MetricDefinition(
            metric_id="gross_revenue",
            name="Receita Bruta",
            metric_type=MetricType.KPI,
            source="sales_orders",
            measure="gross_amount",
            aggregation=AggregationType.SUM,
            domain="sales",
            dimensions=["store_id"],
            owner=MetricOwner(
                owner_id="analytics-admin",
                team="analytics",
                email="analytics@example.com",
            ),
            lineage=MetricLineage(
                source_tables=["sales_orders"],
                downstream_assets=["sales-executive-dashboard"],
            ),
            slo=MetricSLO(
                max_latency_ms=2000,
                expected_min_value=0,
            ),
            tags={"finance": "true", "executive": "true"},
        ),
        MetricDefinition(
            metric_id="orders_count",
            name="Quantidade de Pedidos",
            metric_type=MetricType.SIMPLE,
            source="sales_orders",
            measure="order_id",
            aggregation=AggregationType.COUNT_DISTINCT,
            domain="sales",
            dimensions=["store_id"],
            lineage=MetricLineage(
                source_tables=["sales_orders"],
                downstream_assets=["sales-executive-dashboard"],
            ),
        ),
        MetricDefinition(
            metric_id="net_revenue",
            name="Receita Líquida",
            metric_type=MetricType.KPI,
            source="sales_orders",
            measure="net_amount",
            aggregation=AggregationType.SUM,
            domain="sales",
            dimensions=["store_id"],
            lineage=MetricLineage(
                source_tables=["sales_orders"],
            ),
        ),
        MetricDefinition(
            metric_id="average_ticket",
            name="Ticket Médio",
            metric_type=MetricType.RATIO,
            expression="net_revenue / orders_count",
            domain="sales",
            lineage=MetricLineage(
                upstream_metrics=["net_revenue", "orders_count"],
                transformation="net_revenue / orders_count",
                downstream_assets=["sales-executive-dashboard"],
            ),
            tags={"kpi": "true"},
        ),
    ]


def create_default_metrics_engine(
    datasets: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> AnalyticsMetricsEngine:
    repository = MetricRepository(build_default_metric_catalog())
    executor = InMemoryMetricQueryExecutor(datasets or {})
    return AnalyticsMetricsEngine(
        repository=repository,
        query_executor=executor,
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_metrics_engine(
        datasets={
            "sales_orders": [
                {
                    "order_id": "o1",
                    "store_id": "store-a",
                    "gross_amount": 120.0,
                    "net_amount": 100.0,
                },
                {
                    "order_id": "o2",
                    "store_id": "store-a",
                    "gross_amount": 240.0,
                    "net_amount": 200.0,
                },
                {
                    "order_id": "o3",
                    "store_id": "store-b",
                    "gross_amount": 180.0,
                    "net_amount": 150.0,
                },
            ]
        }
    )

    context = MetricExecutionContext(
        user_id="analytics-admin",
        tenant_id="tenant-default",
        domain="sales",
        correlation_id="corr-metrics-001",
    )

    result = engine.compute(
        metric_id="average_ticket",
        context=context,
        time_window=TimeWindow(
            start=datetime.now(timezone.utc) - timedelta(days=30),
            end=datetime.now(timezone.utc),
            grain=TimeGrain.DAY,
        ),
    )

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    example_usage()