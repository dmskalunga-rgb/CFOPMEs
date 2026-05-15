"""
data/orchestration/dag_manager.py

Enterprise DAG Manager.

Recursos:
- Definição e gerenciamento de DAGs
- Validação estrutural
- Detecção de ciclos
- Ordenação topológica
- Execução sequencial ou por níveis
- Retry por nó
- Controle de estado
- Auditoria
- Métricas
- Multi-tenant
- Serialização JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class DAGStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class DAGRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class DAGNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class DAGExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    LEVEL_BASED = "level_based"


class RetryStrategy(str, Enum):
    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class NodeTriggerRule(str, Enum):
    ALL_SUCCESS = "all_success"
    ALL_DONE = "all_done"
    ONE_SUCCESS = "one_success"
    NONE_FAILED = "none_failed"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class DAGManagerError(Exception):
    """Erro base do DAG Manager."""


class DAGValidationError(DAGManagerError):
    """Erro de validação do DAG."""


class DAGNotFoundError(DAGManagerError):
    """DAG não encontrado."""


class DAGNodeNotFoundError(DAGManagerError):
    """Nó do DAG não encontrado."""


class DAGCycleError(DAGManagerError):
    """Ciclo detectado no DAG."""


class DAGExecutionError(DAGManagerError):
    """Erro durante execução do DAG."""


# =============================================================================
# Protocols
# =============================================================================

class DAGTaskExecutor(Protocol):
    def execute(
        self,
        node: "DAGNode",
        context: "DAGExecutionContext",
        upstream_results: Dict[str, Any],
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
# Default Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("dag_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


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


class FunctionTaskExecutor:
    """
    Executor baseado em registry de funções.

    Cada DAGNode pode ter:
    - task_name: nome da função registrada
    - config: parâmetros da task
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Callable[..., Any]] = {}

    def register(self, task_name: str, fn: Callable[..., Any]) -> None:
        if not task_name:
            raise ValueError("task_name é obrigatório")
        self._registry[task_name] = fn

    def execute(
        self,
        node: "DAGNode",
        context: "DAGExecutionContext",
        upstream_results: Dict[str, Any],
    ) -> Any:
        if node.task_name not in self._registry:
            raise DAGExecutionError(f"Task não registrada: {node.task_name}")

        fn = self._registry[node.task_name]

        return fn(
            node=node,
            context=context,
            upstream_results=upstream_results,
            **node.config,
        )


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class RetryPolicy:
    strategy: RetryStrategy = RetryStrategy.NONE
    max_attempts: int = 1
    delay_seconds: float = 0.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    def validate(self) -> None:
        if self.max_attempts < 1:
            raise DAGValidationError("max_attempts precisa ser >= 1")

        if self.delay_seconds < 0:
            raise DAGValidationError("delay_seconds não pode ser negativo")

        if self.backoff_multiplier < 1:
            raise DAGValidationError("backoff_multiplier precisa ser >= 1")

    def delay_for_attempt(self, attempt: int) -> float:
        if self.strategy == RetryStrategy.NONE:
            return 0.0

        if self.strategy == RetryStrategy.FIXED:
            return self.delay_seconds

        if self.strategy == RetryStrategy.EXPONENTIAL:
            return min(
                self.delay_seconds * (self.backoff_multiplier ** max(0, attempt - 1)),
                self.max_delay_seconds,
            )

        return 0.0


@dataclass(frozen=True)
class DAGExecutionContext:
    dag_id: str
    run_id: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    triggered_by: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class DAGNode:
    node_id: str
    task_name: str
    dependencies: List[str] = field(default_factory=list)
    trigger_rule: NodeTriggerRule = NodeTriggerRule.ALL_SUCCESS
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: Optional[float] = None
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.node_id:
            raise DAGValidationError("node_id é obrigatório")

        if not self.task_name:
            raise DAGValidationError(f"task_name é obrigatório no node {self.node_id}")

        if self.node_id in self.dependencies:
            raise DAGValidationError(f"Nó {self.node_id} depende de si mesmo")

        self.retry_policy.validate()


@dataclass(frozen=True)
class DAGDefinition:
    dag_id: str
    name: str
    nodes: List[DAGNode]
    status: DAGStatus = DAGStatus.ACTIVE
    version: str = "1.0.0"
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    owner: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.dag_id:
            raise DAGValidationError("dag_id é obrigatório")

        if not self.name:
            raise DAGValidationError("name é obrigatório")

        if not self.nodes:
            raise DAGValidationError("DAG precisa ter pelo menos um nó")

        node_ids = set()

        for node in self.nodes:
            node.validate()

            if node.node_id in node_ids:
                raise DAGValidationError(f"node_id duplicado: {node.node_id}")

            node_ids.add(node.node_id)

        for node in self.nodes:
            for dependency in node.dependencies:
                if dependency not in node_ids:
                    raise DAGValidationError(
                        f"Nó {node.node_id} depende de nó inexistente: {dependency}"
                    )


@dataclass
class DAGNodeExecution:
    node_id: str
    status: DAGNodeStatus = DAGNodeStatus.PENDING
    attempts: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None


@dataclass
class DAGRun:
    run_id: str
    dag_id: str
    status: DAGRunStatus
    context: DAGExecutionContext
    node_executions: Dict[str, DAGNodeExecution]
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Repository
# =============================================================================

class DAGRepository:
    def __init__(self, dags: Optional[List[DAGDefinition]] = None) -> None:
        self._dags: Dict[str, DAGDefinition] = {}

        for dag in dags or []:
            self.save(dag)

    def save(self, dag: DAGDefinition) -> None:
        dag.validate()
        DAGGraphValidator.validate_acyclic(dag)
        self._dags[dag.dag_id] = dag

    def get(self, dag_id: str) -> DAGDefinition:
        dag = self._dags.get(dag_id)
        if not dag:
            raise DAGNotFoundError(dag_id)
        return dag

    def list_all(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[DAGStatus] = None,
    ) -> List[DAGDefinition]:
        dags = list(self._dags.values())

        if tenant_id is not None:
            dags = [dag for dag in dags if dag.tenant_id == tenant_id]

        if domain is not None:
            dags = [dag for dag in dags if dag.domain == domain]

        if status is not None:
            dags = [dag for dag in dags if dag.status == status]

        return dags

    def delete(self, dag_id: str) -> None:
        if dag_id not in self._dags:
            raise DAGNotFoundError(dag_id)
        del self._dags[dag_id]


# =============================================================================
# Graph Utilities
# =============================================================================

class DAGGraphValidator:
    @staticmethod
    def validate_acyclic(dag: DAGDefinition) -> None:
        graph = DAGGraphValidator.adjacency_list(dag)
        visited: Set[str] = set()
        visiting: Set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise DAGCycleError(f"Ciclo detectado envolvendo o nó {node_id}")

            if node_id in visited:
                return

            visiting.add(node_id)

            for child in graph[node_id]:
                visit(child)

            visiting.remove(node_id)
            visited.add(node_id)

        for node in dag.nodes:
            visit(node.node_id)

    @staticmethod
    def adjacency_list(dag: DAGDefinition) -> Dict[str, List[str]]:
        graph: Dict[str, List[str]] = {node.node_id: [] for node in dag.nodes}

        for node in dag.nodes:
            for dependency in node.dependencies:
                graph[dependency].append(node.node_id)

        return graph

    @staticmethod
    def dependency_map(dag: DAGDefinition) -> Dict[str, List[str]]:
        return {
            node.node_id: list(node.dependencies)
            for node in dag.nodes
        }

    @staticmethod
    def topological_sort(dag: DAGDefinition) -> List[str]:
        graph = DAGGraphValidator.adjacency_list(dag)
        indegree = {node.node_id: 0 for node in dag.nodes}

        for children in graph.values():
            for child in children:
                indegree[child] += 1

        queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
        ordered: List[str] = []

        while queue:
            node_id = queue.popleft()
            ordered.append(node_id)

            for child in graph[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if len(ordered) != len(dag.nodes):
            raise DAGCycleError("DAG contém ciclo ou dependência inválida")

        return ordered

    @staticmethod
    def execution_levels(dag: DAGDefinition) -> List[List[str]]:
        dependency_map = DAGGraphValidator.dependency_map(dag)
        remaining = set(dependency_map.keys())
        completed: Set[str] = set()
        levels: List[List[str]] = []

        while remaining:
            current_level = sorted([
                node_id for node_id in remaining
                if set(dependency_map[node_id]).issubset(completed)
            ])

            if not current_level:
                raise DAGCycleError("Não foi possível calcular níveis de execução")

            levels.append(current_level)
            completed.update(current_level)
            remaining.difference_update(current_level)

        return levels


# =============================================================================
# State Store
# =============================================================================

class DAGRunStore:
    def __init__(self) -> None:
        self._runs: Dict[str, DAGRun] = {}

    def save(self, run: DAGRun) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> DAGRun:
        run = self._runs.get(run_id)
        if not run:
            raise DAGExecutionError(f"Run não encontrada: {run_id}")
        return run

    def list_runs(
        self,
        dag_id: Optional[str] = None,
        status: Optional[DAGRunStatus] = None,
    ) -> List[DAGRun]:
        runs = list(self._runs.values())

        if dag_id is not None:
            runs = [run for run in runs if run.dag_id == dag_id]

        if status is not None:
            runs = [run for run in runs if run.status == status]

        return sorted(runs, key=lambda item: item.started_at, reverse=True)


# =============================================================================
# DAG Manager
# =============================================================================

class DAGManager:
    def __init__(
        self,
        repository: Optional[DAGRepository] = None,
        executor: Optional[DAGTaskExecutor] = None,
        run_store: Optional[DAGRunStore] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.repository = repository or DAGRepository()
        self.executor = executor or FunctionTaskExecutor()
        self.run_store = run_store or DAGRunStore()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def register_dag(self, dag: DAGDefinition) -> None:
        self.repository.save(dag)

        self._audit(
            event_type="dag.registered",
            severity=AuditSeverity.INFO,
            dag_id=dag.dag_id,
            details={
                "name": dag.name,
                "version": dag.version,
                "nodes": len(dag.nodes),
                "tenant_id": dag.tenant_id,
                "domain": dag.domain,
            },
        )

    def validate_dag(self, dag_id: str) -> Dict[str, Any]:
        dag = self.repository.get(dag_id)
        dag.validate()
        DAGGraphValidator.validate_acyclic(dag)

        topological_order = DAGGraphValidator.topological_sort(dag)
        levels = DAGGraphValidator.execution_levels(dag)

        return {
            "dag_id": dag.dag_id,
            "valid": True,
            "topological_order": topological_order,
            "execution_levels": levels,
            "node_count": len(dag.nodes),
            "edge_count": sum(len(node.dependencies) for node in dag.nodes),
        }

    def run(
        self,
        dag_id: str,
        context: Optional[DAGExecutionContext] = None,
        mode: DAGExecutionMode = DAGExecutionMode.SEQUENTIAL,
        fail_fast: bool = True,
    ) -> DAGRun:
        dag = self.repository.get(dag_id)

        if dag.status != DAGStatus.ACTIVE:
            raise DAGExecutionError(f"DAG {dag_id} não está ativa: {dag.status.value}")

        run_id = str(uuid.uuid4())

        context = context or DAGExecutionContext(
            dag_id=dag_id,
            run_id=run_id,
            tenant_id=dag.tenant_id,
            domain=dag.domain,
        )

        node_executions = {
            node.node_id: DAGNodeExecution(node_id=node.node_id)
            for node in dag.nodes
        }

        run = DAGRun(
            run_id=context.run_id,
            dag_id=dag_id,
            status=DAGRunStatus.RUNNING,
            context=context,
            node_executions=node_executions,
            started_at=datetime.now(timezone.utc),
        )

        self.run_store.save(run)

        self._audit(
            event_type="dag.run.started",
            severity=AuditSeverity.INFO,
            dag_id=dag_id,
            run_id=run.run_id,
            details={
                "mode": mode.value,
                "fail_fast": fail_fast,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
            },
        )

        started = time.perf_counter()

        try:
            if mode == DAGExecutionMode.SEQUENTIAL:
                self._run_sequential(dag, run, fail_fast=fail_fast)
            elif mode == DAGExecutionMode.LEVEL_BASED:
                self._run_level_based(dag, run, fail_fast=fail_fast)
            else:
                raise DAGExecutionError(f"Modo de execução inválido: {mode}")

            self._finalize_run(run)

        except Exception as exc:
            run.status = DAGRunStatus.FAILED
            run.error = str(exc)
            self._audit(
                event_type="dag.run.failed",
                severity=AuditSeverity.ERROR,
                dag_id=dag_id,
                run_id=run.run_id,
                details={
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

        finally:
            run.finished_at = datetime.now(timezone.utc)
            run.duration_ms = (time.perf_counter() - started) * 1000
            self.run_store.save(run)

            self.metrics_backend.timing(
                "dag.run.duration_ms",
                run.duration_ms,
                tags={
                    "dag_id": dag_id,
                    "status": run.status.value,
                    "tenant_id": context.tenant_id or "-",
                },
            )

            self.metrics_backend.increment(
                "dag.run.total",
                tags={
                    "dag_id": dag_id,
                    "status": run.status.value,
                    "tenant_id": context.tenant_id or "-",
                },
            )

        return run

    def _run_sequential(
        self,
        dag: DAGDefinition,
        run: DAGRun,
        fail_fast: bool,
    ) -> None:
        node_by_id = {node.node_id: node for node in dag.nodes}
        order = DAGGraphValidator.topological_sort(dag)

        for node_id in order:
            node = node_by_id[node_id]

            if not node.enabled:
                self._skip_node(run, node_id, "Nó desabilitado")
                continue

            if not self._trigger_rule_satisfied(node, run):
                self._skip_node(run, node_id, "Trigger rule não satisfeita")
                if fail_fast:
                    break
                continue

            self._execute_node(node, run)

            if run.node_executions[node_id].status == DAGNodeStatus.FAILED and fail_fast:
                break

    def _run_level_based(
        self,
        dag: DAGDefinition,
        run: DAGRun,
        fail_fast: bool,
    ) -> None:
        node_by_id = {node.node_id: node for node in dag.nodes}
        levels = DAGGraphValidator.execution_levels(dag)

        for level in levels:
            level_failed = False

            for node_id in level:
                node = node_by_id[node_id]

                if not node.enabled:
                    self._skip_node(run, node_id, "Nó desabilitado")
                    continue

                if not self._trigger_rule_satisfied(node, run):
                    self._skip_node(run, node_id, "Trigger rule não satisfeita")
                    continue

                self._execute_node(node, run)

                if run.node_executions[node_id].status == DAGNodeStatus.FAILED:
                    level_failed = True

            if level_failed and fail_fast:
                break

    def _execute_node(self, node: DAGNode, run: DAGRun) -> None:
        execution = run.node_executions[node.node_id]
        execution.status = DAGNodeStatus.RUNNING
        execution.started_at = datetime.now(timezone.utc)

        self._audit(
            event_type="dag.node.started",
            severity=AuditSeverity.INFO,
            dag_id=run.dag_id,
            run_id=run.run_id,
            node_id=node.node_id,
            details={
                "task_name": node.task_name,
                "attempts_max": node.retry_policy.max_attempts,
            },
        )

        started = time.perf_counter()
        upstream_results = self._upstream_results(node, run)

        last_error: Optional[Exception] = None

        for attempt in range(1, node.retry_policy.max_attempts + 1):
            execution.attempts = attempt

            try:
                result = self.executor.execute(
                    node=node,
                    context=run.context,
                    upstream_results=upstream_results,
                )

                execution.status = DAGNodeStatus.SUCCESS
                execution.result = result
                execution.error = None
                execution.traceback_text = None
                break

            except Exception as exc:
                last_error = exc
                execution.error = str(exc)
                execution.traceback_text = traceback.format_exc()

                self._audit(
                    event_type="dag.node.attempt_failed",
                    severity=AuditSeverity.WARNING,
                    dag_id=run.dag_id,
                    run_id=run.run_id,
                    node_id=node.node_id,
                    details={
                        "attempt": attempt,
                        "max_attempts": node.retry_policy.max_attempts,
                        "error": str(exc),
                    },
                )

                if attempt < node.retry_policy.max_attempts:
                    delay = node.retry_policy.delay_for_attempt(attempt)
                    if delay > 0:
                        time.sleep(delay)

        if execution.status != DAGNodeStatus.SUCCESS:
            execution.status = DAGNodeStatus.FAILED
            execution.error = str(last_error) if last_error else "Erro desconhecido"

        execution.finished_at = datetime.now(timezone.utc)
        execution.duration_ms = (time.perf_counter() - started) * 1000

        self.metrics_backend.timing(
            "dag.node.duration_ms",
            execution.duration_ms,
            tags={
                "dag_id": run.dag_id,
                "node_id": node.node_id,
                "status": execution.status.value,
            },
        )

        self.metrics_backend.increment(
            "dag.node.execution.total",
            tags={
                "dag_id": run.dag_id,
                "node_id": node.node_id,
                "status": execution.status.value,
            },
        )

        self._audit(
            event_type=(
                "dag.node.succeeded"
                if execution.status == DAGNodeStatus.SUCCESS
                else "dag.node.failed"
            ),
            severity=(
                AuditSeverity.INFO
                if execution.status == DAGNodeStatus.SUCCESS
                else AuditSeverity.ERROR
            ),
            dag_id=run.dag_id,
            run_id=run.run_id,
            node_id=node.node_id,
            details={
                "status": execution.status.value,
                "attempts": execution.attempts,
                "duration_ms": execution.duration_ms,
                "error": execution.error,
            },
        )

    def _skip_node(self, run: DAGRun, node_id: str, reason: str) -> None:
        execution = run.node_executions[node_id]
        execution.status = DAGNodeStatus.SKIPPED
        execution.started_at = datetime.now(timezone.utc)
        execution.finished_at = datetime.now(timezone.utc)
        execution.duration_ms = 0
        execution.error = reason

        self._audit(
            event_type="dag.node.skipped",
            severity=AuditSeverity.WARNING,
            dag_id=run.dag_id,
            run_id=run.run_id,
            node_id=node_id,
            details={"reason": reason},
        )

    @staticmethod
    def _upstream_results(node: DAGNode, run: DAGRun) -> Dict[str, Any]:
        return {
            dependency: run.node_executions[dependency].result
            for dependency in node.dependencies
            if dependency in run.node_executions
        }

    @staticmethod
    def _trigger_rule_satisfied(node: DAGNode, run: DAGRun) -> bool:
        if not node.dependencies:
            return True

        statuses = [
            run.node_executions[dependency].status
            for dependency in node.dependencies
            if dependency in run.node_executions
        ]

        if node.trigger_rule == NodeTriggerRule.ALL_SUCCESS:
            return all(status == DAGNodeStatus.SUCCESS for status in statuses)

        if node.trigger_rule == NodeTriggerRule.ALL_DONE:
            return all(
                status in {
                    DAGNodeStatus.SUCCESS,
                    DAGNodeStatus.FAILED,
                    DAGNodeStatus.SKIPPED,
                    DAGNodeStatus.CANCELLED,
                }
                for status in statuses
            )

        if node.trigger_rule == NodeTriggerRule.ONE_SUCCESS:
            return any(status == DAGNodeStatus.SUCCESS for status in statuses)

        if node.trigger_rule == NodeTriggerRule.NONE_FAILED:
            return all(status != DAGNodeStatus.FAILED for status in statuses)

        return False

    @staticmethod
    def _finalize_run(run: DAGRun) -> None:
        statuses = [execution.status for execution in run.node_executions.values()]

        if all(status in {DAGNodeStatus.SUCCESS, DAGNodeStatus.SKIPPED} for status in statuses):
            run.status = DAGRunStatus.SUCCESS
            return

        if any(status == DAGNodeStatus.SUCCESS for status in statuses):
            run.status = DAGRunStatus.PARTIAL
            return

        run.status = DAGRunStatus.FAILED

    def get_run(self, run_id: str) -> DAGRun:
        return self.run_store.get(run_id)

    def list_runs(
        self,
        dag_id: Optional[str] = None,
        status: Optional[DAGRunStatus] = None,
    ) -> List[DAGRun]:
        return self.run_store.list_runs(dag_id=dag_id, status=status)

    def export_dag_json(self, dag_id: str) -> str:
        dag = self.repository.get(dag_id)
        return json.dumps(
            self._dag_to_dict(dag),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_run_json(self, run_id: str) -> str:
        run = self.run_store.get(run_id)
        return json.dumps(
            self._run_to_dict(run),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        dag_id: Optional[str] = None,
        run_id: Optional[str] = None,
        node_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "dag_id": dag_id,
                "run_id": run_id,
                "node_id": node_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _dag_to_dict(dag: DAGDefinition) -> Dict[str, Any]:
        data = asdict(dag)
        data["status"] = dag.status.value
        data["created_at"] = dag.created_at.isoformat()
        data["updated_at"] = dag.updated_at.isoformat() if dag.updated_at else None

        for node in data["nodes"]:
            node["trigger_rule"] = node["trigger_rule"].value
            node["retry_policy"]["strategy"] = node["retry_policy"]["strategy"].value

        return data

    @staticmethod
    def _run_to_dict(run: DAGRun) -> Dict[str, Any]:
        data = asdict(run)
        data["status"] = run.status.value
        data["started_at"] = run.started_at.isoformat()
        data["finished_at"] = run.finished_at.isoformat() if run.finished_at else None
        data["context"]["started_at"] = run.context.started_at.isoformat()

        for execution in data["node_executions"].values():
            execution["status"] = execution["status"].value
            execution["started_at"] = (
                execution["started_at"].isoformat()
                if execution["started_at"]
                else None
            )
            execution["finished_at"] = (
                execution["finished_at"].isoformat()
                if execution["finished_at"]
                else None
            )

        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_dag_manager() -> DAGManager:
    executor = FunctionTaskExecutor()

    def print_task(
        node: DAGNode,
        context: DAGExecutionContext,
        upstream_results: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return {
            "node_id": node.node_id,
            "message": kwargs.get("message", "executed"),
            "upstream_results": upstream_results,
            "parameters": context.parameters,
        }

    executor.register("print_task", print_task)

    return DAGManager(executor=executor)


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    manager = create_default_dag_manager()

    dag = DAGDefinition(
        dag_id="daily-sales-pipeline",
        name="Daily Sales Pipeline",
        domain="sales",
        tenant_id="tenant-default",
        owner="data-platform",
        nodes=[
            DAGNode(
                node_id="extract",
                task_name="print_task",
                config={"message": "Extract sales data"},
                retry_policy=RetryPolicy(
                    strategy=RetryStrategy.FIXED,
                    max_attempts=2,
                    delay_seconds=1,
                ),
            ),
            DAGNode(
                node_id="transform",
                task_name="print_task",
                dependencies=["extract"],
                config={"message": "Transform sales data"},
            ),
            DAGNode(
                node_id="quality",
                task_name="print_task",
                dependencies=["transform"],
                config={"message": "Validate quality"},
            ),
            DAGNode(
                node_id="publish",
                task_name="print_task",
                dependencies=["quality"],
                config={"message": "Publish mart"},
            ),
        ],
    )

    manager.register_dag(dag)

    validation = manager.validate_dag("daily-sales-pipeline")
    print(json.dumps(validation, indent=2, ensure_ascii=False))

    run = manager.run(
        dag_id="daily-sales-pipeline",
        context=DAGExecutionContext(
            dag_id="daily-sales-pipeline",
            run_id=str(uuid.uuid4()),
            tenant_id="tenant-default",
            domain="sales",
            triggered_by="scheduler",
            correlation_id="corr-dag-001",
            parameters={"date": "2026-05-13"},
        ),
        mode=DAGExecutionMode.SEQUENTIAL,
    )

    print(manager.export_run_json(run.run_id))


if __name__ == "__main__":
    example_usage()