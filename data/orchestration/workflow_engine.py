"""
data/orchestration/workflow_engine.py

Enterprise Workflow Engine.

Recursos:
- Definição e execução de workflows
- Steps com dependências
- Execução sequencial e por níveis
- Trigger rules
- Retry por step
- Timeout opcional
- Hooks de rollback/compensação
- Estado de execução
- Auditoria e métricas
- Multi-tenant
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class WorkflowExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class WorkflowStepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class WorkflowPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class WorkflowExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    LEVEL_BASED = "level_based"


class WorkflowTriggerRule(str, Enum):
    ALL_SUCCESS = "all_success"
    ALL_DONE = "all_done"
    ONE_SUCCESS = "one_success"
    NONE_FAILED = "none_failed"


class RetryStrategy(str, Enum):
    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class WorkflowEngineError(Exception):
    """Erro base do workflow engine."""


class WorkflowValidationError(WorkflowEngineError):
    """Erro de validação."""


class WorkflowNotFoundError(WorkflowEngineError):
    """Workflow não encontrado."""


class WorkflowExecutionError(WorkflowEngineError):
    """Erro durante execução."""


class WorkflowCycleError(WorkflowEngineError):
    """Ciclo detectado."""


class WorkflowStepHandlerNotFound(WorkflowEngineError):
    """Handler de step não encontrado."""


# =============================================================================
# Protocols
# =============================================================================

class WorkflowStepHandler(Protocol):
    def __call__(
        self,
        step: "WorkflowStep",
        context: "WorkflowContext",
        upstream_results: Dict[str, Any],
    ) -> Any:
        ...


class WorkflowRollbackHandler(Protocol):
    def __call__(
        self,
        step: "WorkflowStep",
        context: "WorkflowContext",
        step_result: Any,
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
        logger.info("workflow_audit=%s", json.dumps(event, ensure_ascii=False, default=str))


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
            raise WorkflowValidationError("max_attempts precisa ser >= 1")

        if self.delay_seconds < 0:
            raise WorkflowValidationError("delay_seconds não pode ser negativo")

        if self.backoff_multiplier < 1:
            raise WorkflowValidationError("backoff_multiplier precisa ser >= 1")

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
class WorkflowContext:
    workflow_id: str
    execution_id: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    triggered_by: Optional[str] = None
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    name: str
    handler_name: str
    dependencies: List[str] = field(default_factory=list)
    trigger_rule: WorkflowTriggerRule = WorkflowTriggerRule.ALL_SUCCESS
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_seconds: Optional[float] = None
    rollback_handler_name: Optional[str] = None
    enabled: bool = True
    required: bool = True
    config: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.step_id:
            raise WorkflowValidationError("step_id é obrigatório")

        if not self.name:
            raise WorkflowValidationError(f"name é obrigatório no step {self.step_id}")

        if not self.handler_name:
            raise WorkflowValidationError(f"handler_name é obrigatório no step {self.step_id}")

        if self.step_id in self.dependencies:
            raise WorkflowValidationError(f"Step {self.step_id} depende de si mesmo")

        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise WorkflowValidationError("timeout_seconds precisa ser maior que zero")

        self.retry_policy.validate()


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    name: str
    steps: List[WorkflowStep]
    status: WorkflowStatus = WorkflowStatus.ACTIVE
    version: str = "1.0.0"
    priority: WorkflowPriority = WorkflowPriority.NORMAL
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    owner: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.workflow_id:
            raise WorkflowValidationError("workflow_id é obrigatório")

        if not self.name:
            raise WorkflowValidationError("name é obrigatório")

        if not self.steps:
            raise WorkflowValidationError("Workflow precisa ter pelo menos um step")

        step_ids: Set[str] = set()

        for step in self.steps:
            step.validate()

            if step.step_id in step_ids:
                raise WorkflowValidationError(f"step_id duplicado: {step.step_id}")

            step_ids.add(step.step_id)

        for step in self.steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    raise WorkflowValidationError(
                        f"Step {step.step_id} depende de step inexistente: {dep}"
                    )


@dataclass
class WorkflowStepExecution:
    step_id: str
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    attempts: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None
    rollback_result: Any = None
    rollback_error: Optional[str] = None


@dataclass
class WorkflowExecution:
    execution_id: str
    workflow_id: str
    status: WorkflowExecutionStatus
    context: WorkflowContext
    step_executions: Dict[str, WorkflowStepExecution]
    started_at: datetime
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# Compatibility aliases
WorkflowTask = WorkflowStep


# =============================================================================
# Registries
# =============================================================================

class WorkflowHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[str, WorkflowStepHandler] = {}
        self._rollback_handlers: Dict[str, WorkflowRollbackHandler] = {}

    def register_handler(self, name: str, handler: WorkflowStepHandler) -> None:
        if not name:
            raise WorkflowValidationError("Nome do handler é obrigatório")
        self._handlers[name] = handler

    def register_rollback_handler(self, name: str, handler: WorkflowRollbackHandler) -> None:
        if not name:
            raise WorkflowValidationError("Nome do rollback handler é obrigatório")
        self._rollback_handlers[name] = handler

    def get_handler(self, name: str) -> WorkflowStepHandler:
        handler = self._handlers.get(name)
        if not handler:
            raise WorkflowStepHandlerNotFound(name)
        return handler

    def get_rollback_handler(self, name: str) -> WorkflowRollbackHandler:
        handler = self._rollback_handlers.get(name)
        if not handler:
            raise WorkflowStepHandlerNotFound(name)
        return handler


class WorkflowRepository:
    def __init__(self, workflows: Optional[List[WorkflowDefinition]] = None) -> None:
        self._workflows: Dict[str, WorkflowDefinition] = {}
        self._lock = threading.RLock()

        for workflow in workflows or []:
            self.save(workflow)

    def save(self, workflow: WorkflowDefinition) -> None:
        workflow.validate()
        WorkflowGraph.validate_acyclic(workflow)

        with self._lock:
            self._workflows[workflow.workflow_id] = workflow

    def get(self, workflow_id: str) -> WorkflowDefinition:
        with self._lock:
            workflow = self._workflows.get(workflow_id)

        if not workflow:
            raise WorkflowNotFoundError(workflow_id)

        return workflow

    def list_all(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        status: Optional[WorkflowStatus] = None,
    ) -> List[WorkflowDefinition]:
        with self._lock:
            workflows = list(self._workflows.values())

        if tenant_id is not None:
            workflows = [item for item in workflows if item.tenant_id == tenant_id]

        if domain is not None:
            workflows = [item for item in workflows if item.domain == domain]

        if status is not None:
            workflows = [item for item in workflows if item.status == status]

        return workflows


class WorkflowExecutionStore:
    def __init__(self) -> None:
        self._executions: Dict[str, WorkflowExecution] = {}
        self._lock = threading.RLock()

    def save(self, execution: WorkflowExecution) -> None:
        with self._lock:
            self._executions[execution.execution_id] = execution

    def get(self, execution_id: str) -> WorkflowExecution:
        with self._lock:
            execution = self._executions.get(execution_id)

        if not execution:
            raise WorkflowExecutionError(f"Execução não encontrada: {execution_id}")

        return execution

    def list_all(
        self,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowExecutionStatus] = None,
    ) -> List[WorkflowExecution]:
        with self._lock:
            executions = list(self._executions.values())

        if workflow_id is not None:
            executions = [item for item in executions if item.workflow_id == workflow_id]

        if status is not None:
            executions = [item for item in executions if item.status == status]

        return sorted(executions, key=lambda item: item.started_at, reverse=True)


# =============================================================================
# Graph
# =============================================================================

class WorkflowGraph:
    @staticmethod
    def dependency_map(workflow: WorkflowDefinition) -> Dict[str, List[str]]:
        return {
            step.step_id: list(step.dependencies)
            for step in workflow.steps
        }

    @staticmethod
    def adjacency(workflow: WorkflowDefinition) -> Dict[str, List[str]]:
        graph = {step.step_id: [] for step in workflow.steps}

        for step in workflow.steps:
            for dep in step.dependencies:
                graph[dep].append(step.step_id)

        return graph

    @staticmethod
    def validate_acyclic(workflow: WorkflowDefinition) -> None:
        graph = WorkflowGraph.adjacency(workflow)
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise WorkflowCycleError(f"Ciclo detectado envolvendo step {step_id}")

            if step_id in visited:
                return

            visiting.add(step_id)

            for child in graph[step_id]:
                visit(child)

            visiting.remove(step_id)
            visited.add(step_id)

        for step in workflow.steps:
            visit(step.step_id)

    @staticmethod
    def topological_sort(workflow: WorkflowDefinition) -> List[str]:
        graph = WorkflowGraph.adjacency(workflow)
        indegree = {step.step_id: 0 for step in workflow.steps}

        for children in graph.values():
            for child in children:
                indegree[child] += 1

        queue = deque([step_id for step_id, degree in indegree.items() if degree == 0])
        ordered: List[str] = []

        while queue:
            step_id = queue.popleft()
            ordered.append(step_id)

            for child in graph[step_id]:
                indegree[child] -= 1

                if indegree[child] == 0:
                    queue.append(child)

        if len(ordered) != len(workflow.steps):
            raise WorkflowCycleError("Workflow contém ciclo")

        return ordered

    @staticmethod
    def execution_levels(workflow: WorkflowDefinition) -> List[List[str]]:
        dependencies = WorkflowGraph.dependency_map(workflow)
        remaining = set(dependencies.keys())
        completed: Set[str] = set()
        levels: List[List[str]] = []

        while remaining:
            level = sorted([
                step_id for step_id in remaining
                if set(dependencies[step_id]).issubset(completed)
            ])

            if not level:
                raise WorkflowCycleError("Não foi possível calcular níveis de execução")

            levels.append(level)
            completed.update(level)
            remaining.difference_update(level)

        return levels


# =============================================================================
# Engine
# =============================================================================

class WorkflowEngine:
    def __init__(
        self,
        repository: Optional[WorkflowRepository] = None,
        execution_store: Optional[WorkflowExecutionStore] = None,
        registry: Optional[WorkflowHandlerRegistry] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.repository = repository or WorkflowRepository()
        self.execution_store = execution_store or WorkflowExecutionStore()
        self.registry = registry or WorkflowHandlerRegistry()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def register_handler(self, name: str, handler: WorkflowStepHandler) -> None:
        self.registry.register_handler(name, handler)

    def register_rollback_handler(self, name: str, handler: WorkflowRollbackHandler) -> None:
        self.registry.register_rollback_handler(name, handler)

    def register_workflow(self, workflow: WorkflowDefinition) -> None:
        self.repository.save(workflow)

        self._audit(
            "workflow.registered",
            AuditSeverity.INFO,
            workflow_id=workflow.workflow_id,
            details={
                "name": workflow.name,
                "version": workflow.version,
                "steps": len(workflow.steps),
                "tenant_id": workflow.tenant_id,
                "domain": workflow.domain,
            },
        )

    def validate_workflow(self, workflow_id: str) -> Dict[str, Any]:
        workflow = self.repository.get(workflow_id)
        workflow.validate()
        WorkflowGraph.validate_acyclic(workflow)

        return {
            "workflow_id": workflow.workflow_id,
            "valid": True,
            "topological_order": WorkflowGraph.topological_sort(workflow),
            "execution_levels": WorkflowGraph.execution_levels(workflow),
            "step_count": len(workflow.steps),
            "edge_count": sum(len(step.dependencies) for step in workflow.steps),
        }

    def run(
        self,
        workflow_id: str,
        context: Optional[WorkflowContext] = None,
        mode: WorkflowExecutionMode = WorkflowExecutionMode.SEQUENTIAL,
        fail_fast: bool = True,
        rollback_on_failure: bool = False,
    ) -> WorkflowExecution:
        workflow = self.repository.get(workflow_id)

        if workflow.status != WorkflowStatus.ACTIVE:
            raise WorkflowExecutionError(
                f"Workflow {workflow_id} não está ativo: {workflow.status.value}"
            )

        execution_id = str(uuid.uuid4())

        context = context or WorkflowContext(
            workflow_id=workflow.workflow_id,
            execution_id=execution_id,
            tenant_id=workflow.tenant_id,
            domain=workflow.domain,
            correlation_id=str(uuid.uuid4()),
        )

        step_executions = {
            step.step_id: WorkflowStepExecution(step_id=step.step_id)
            for step in workflow.steps
        }

        execution = WorkflowExecution(
            execution_id=context.execution_id,
            workflow_id=workflow.workflow_id,
            status=WorkflowExecutionStatus.RUNNING,
            context=context,
            step_executions=step_executions,
            started_at=datetime.now(timezone.utc),
            metadata={
                "mode": mode.value,
                "fail_fast": fail_fast,
                "rollback_on_failure": rollback_on_failure,
            },
        )

        self.execution_store.save(execution)

        self._audit(
            "workflow.execution.started",
            AuditSeverity.INFO,
            workflow_id=workflow.workflow_id,
            execution_id=execution.execution_id,
            details={
                "mode": mode.value,
                "priority": workflow.priority.value,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
            },
        )

        started = time.perf_counter()

        try:
            if mode == WorkflowExecutionMode.SEQUENTIAL:
                self._run_sequential(workflow, execution, fail_fast)
            elif mode == WorkflowExecutionMode.LEVEL_BASED:
                self._run_level_based(workflow, execution, fail_fast)
            else:
                raise WorkflowExecutionError(f"Modo inválido: {mode}")

            self._finalize_execution(execution)

            if (
                rollback_on_failure
                and execution.status in {WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.PARTIAL}
            ):
                self.rollback(execution.execution_id)

        except Exception as exc:
            execution.status = WorkflowExecutionStatus.FAILED
            execution.error = str(exc)

            self._audit(
                "workflow.execution.failed",
                AuditSeverity.ERROR,
                workflow_id=workflow.workflow_id,
                execution_id=execution.execution_id,
                details={
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

            if rollback_on_failure:
                self.rollback(execution.execution_id)

        finally:
            execution.finished_at = datetime.now(timezone.utc)
            execution.duration_ms = (time.perf_counter() - started) * 1000
            self.execution_store.save(execution)

            self.metrics_backend.increment(
                "workflow.execution.total",
                tags={
                    "workflow_id": workflow.workflow_id,
                    "status": execution.status.value,
                    "tenant_id": context.tenant_id or "-",
                },
            )

            self.metrics_backend.timing(
                "workflow.execution.duration_ms",
                execution.duration_ms,
                tags={
                    "workflow_id": workflow.workflow_id,
                    "status": execution.status.value,
                },
            )

            self._audit(
                "workflow.execution.finished",
                (
                    AuditSeverity.INFO
                    if execution.status == WorkflowExecutionStatus.SUCCESS
                    else AuditSeverity.ERROR
                ),
                workflow_id=workflow.workflow_id,
                execution_id=execution.execution_id,
                details={
                    "status": execution.status.value,
                    "duration_ms": execution.duration_ms,
                    "error": execution.error,
                },
            )

        return execution

    def _run_sequential(
        self,
        workflow: WorkflowDefinition,
        execution: WorkflowExecution,
        fail_fast: bool,
    ) -> None:
        step_by_id = {step.step_id: step for step in workflow.steps}

        for step_id in WorkflowGraph.topological_sort(workflow):
            step = step_by_id[step_id]

            if not step.enabled:
                self._skip_step(execution, step, "Step desabilitado")
                continue

            if not self._trigger_rule_satisfied(step, execution):
                self._skip_step(execution, step, "Trigger rule não satisfeita")
                if fail_fast and step.required:
                    break
                continue

            self._execute_step(step, execution)

            if (
                execution.step_executions[step.step_id].status == WorkflowStepStatus.FAILED
                and fail_fast
                and step.required
            ):
                break

    def _run_level_based(
        self,
        workflow: WorkflowDefinition,
        execution: WorkflowExecution,
        fail_fast: bool,
    ) -> None:
        step_by_id = {step.step_id: step for step in workflow.steps}

        for level in WorkflowGraph.execution_levels(workflow):
            level_failed = False

            for step_id in level:
                step = step_by_id[step_id]

                if not step.enabled:
                    self._skip_step(execution, step, "Step desabilitado")
                    continue

                if not self._trigger_rule_satisfied(step, execution):
                    self._skip_step(execution, step, "Trigger rule não satisfeita")
                    continue

                self._execute_step(step, execution)

                if execution.step_executions[step.step_id].status == WorkflowStepStatus.FAILED:
                    level_failed = True

            if level_failed and fail_fast:
                break

    def _execute_step(self, step: WorkflowStep, execution: WorkflowExecution) -> None:
        step_execution = execution.step_executions[step.step_id]
        step_execution.status = WorkflowStepStatus.RUNNING
        step_execution.started_at = datetime.now(timezone.utc)

        self._audit(
            "workflow.step.started",
            AuditSeverity.INFO,
            workflow_id=execution.workflow_id,
            execution_id=execution.execution_id,
            step_id=step.step_id,
            details={
                "handler_name": step.handler_name,
                "max_attempts": step.retry_policy.max_attempts,
            },
        )

        started = time.perf_counter()
        handler = self.registry.get_handler(step.handler_name)
        upstream_results = self._upstream_results(step, execution)

        last_error: Optional[BaseException] = None

        for attempt in range(1, step.retry_policy.max_attempts + 1):
            step_execution.attempts = attempt

            try:
                result = self._execute_with_timeout(
                    handler=handler,
                    step=step,
                    context=execution.context,
                    upstream_results=upstream_results,
                )

                step_execution.status = WorkflowStepStatus.SUCCESS
                step_execution.result = result
                step_execution.error = None
                step_execution.traceback_text = None
                break

            except BaseException as exc:
                last_error = exc
                step_execution.error = str(exc)
                step_execution.traceback_text = traceback.format_exc()

                self._audit(
                    "workflow.step.attempt_failed",
                    AuditSeverity.WARNING,
                    workflow_id=execution.workflow_id,
                    execution_id=execution.execution_id,
                    step_id=step.step_id,
                    details={
                        "attempt": attempt,
                        "max_attempts": step.retry_policy.max_attempts,
                        "error": str(exc),
                    },
                )

                if attempt < step.retry_policy.max_attempts:
                    delay = step.retry_policy.delay_for_attempt(attempt)
                    if delay > 0:
                        time.sleep(delay)

        if step_execution.status != WorkflowStepStatus.SUCCESS:
            step_execution.status = WorkflowStepStatus.FAILED
            step_execution.error = str(last_error) if last_error else "Erro desconhecido"

        step_execution.finished_at = datetime.now(timezone.utc)
        step_execution.duration_ms = (time.perf_counter() - started) * 1000

        self.metrics_backend.increment(
            "workflow.step.execution.total",
            tags={
                "workflow_id": execution.workflow_id,
                "step_id": step.step_id,
                "status": step_execution.status.value,
            },
        )

        self.metrics_backend.timing(
            "workflow.step.duration_ms",
            step_execution.duration_ms,
            tags={
                "workflow_id": execution.workflow_id,
                "step_id": step.step_id,
                "status": step_execution.status.value,
            },
        )

        self._audit(
            (
                "workflow.step.succeeded"
                if step_execution.status == WorkflowStepStatus.SUCCESS
                else "workflow.step.failed"
            ),
            (
                AuditSeverity.INFO
                if step_execution.status == WorkflowStepStatus.SUCCESS
                else AuditSeverity.ERROR
            ),
            workflow_id=execution.workflow_id,
            execution_id=execution.execution_id,
            step_id=step.step_id,
            details={
                "status": step_execution.status.value,
                "attempts": step_execution.attempts,
                "duration_ms": step_execution.duration_ms,
                "error": step_execution.error,
            },
        )

    def _execute_with_timeout(
        self,
        handler: WorkflowStepHandler,
        step: WorkflowStep,
        context: WorkflowContext,
        upstream_results: Dict[str, Any],
    ) -> Any:
        if not step.timeout_seconds:
            return handler(step, context, upstream_results)

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, BaseException] = {}

        def target() -> None:
            try:
                result_holder["result"] = handler(step, context, upstream_results)
            except BaseException as exc:
                error_holder["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=step.timeout_seconds)

        if thread.is_alive():
            raise TimeoutError(f"Step {step.step_id} excedeu timeout de {step.timeout_seconds}s")

        if "error" in error_holder:
            raise error_holder["error"]

        return result_holder.get("result")

    def rollback(self, execution_id: str) -> WorkflowExecution:
        execution = self.execution_store.get(execution_id)
        workflow = self.repository.get(execution.workflow_id)

        execution.status = WorkflowExecutionStatus.ROLLING_BACK
        self.execution_store.save(execution)

        self._audit(
            "workflow.rollback.started",
            AuditSeverity.WARNING,
            workflow_id=execution.workflow_id,
            execution_id=execution.execution_id,
        )

        step_by_id = {step.step_id: step for step in workflow.steps}
        order = list(reversed(WorkflowGraph.topological_sort(workflow)))

        for step_id in order:
            step = step_by_id[step_id]
            step_execution = execution.step_executions[step_id]

            if step_execution.status != WorkflowStepStatus.SUCCESS:
                continue

            if not step.rollback_handler_name:
                continue

            try:
                rollback_handler = self.registry.get_rollback_handler(step.rollback_handler_name)
                step_execution.rollback_result = rollback_handler(
                    step,
                    execution.context,
                    step_execution.result,
                )
                step_execution.status = WorkflowStepStatus.ROLLED_BACK

                self._audit(
                    "workflow.step.rolled_back",
                    AuditSeverity.INFO,
                    workflow_id=execution.workflow_id,
                    execution_id=execution.execution_id,
                    step_id=step.step_id,
                )

            except Exception as exc:
                step_execution.rollback_error = str(exc)

                self._audit(
                    "workflow.step.rollback_failed",
                    AuditSeverity.ERROR,
                    workflow_id=execution.workflow_id,
                    execution_id=execution.execution_id,
                    step_id=step.step_id,
                    details={
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )

        execution.status = WorkflowExecutionStatus.ROLLED_BACK
        self.execution_store.save(execution)

        self._audit(
            "workflow.rollback.finished",
            AuditSeverity.WARNING,
            workflow_id=execution.workflow_id,
            execution_id=execution.execution_id,
        )

        return execution

    @staticmethod
    def _skip_step(
        execution: WorkflowExecution,
        step: WorkflowStep,
        reason: str,
    ) -> None:
        step_execution = execution.step_executions[step.step_id]
        step_execution.status = WorkflowStepStatus.SKIPPED
        step_execution.started_at = datetime.now(timezone.utc)
        step_execution.finished_at = datetime.now(timezone.utc)
        step_execution.duration_ms = 0.0
        step_execution.error = reason

    @staticmethod
    def _upstream_results(
        step: WorkflowStep,
        execution: WorkflowExecution,
    ) -> Dict[str, Any]:
        return {
            dep: execution.step_executions[dep].result
            for dep in step.dependencies
            if dep in execution.step_executions
        }

    @staticmethod
    def _trigger_rule_satisfied(
        step: WorkflowStep,
        execution: WorkflowExecution,
    ) -> bool:
        if not step.dependencies:
            return True

        statuses = [
            execution.step_executions[dep].status
            for dep in step.dependencies
            if dep in execution.step_executions
        ]

        if step.trigger_rule == WorkflowTriggerRule.ALL_SUCCESS:
            return all(status == WorkflowStepStatus.SUCCESS for status in statuses)

        if step.trigger_rule == WorkflowTriggerRule.ALL_DONE:
            return all(
                status in {
                    WorkflowStepStatus.SUCCESS,
                    WorkflowStepStatus.FAILED,
                    WorkflowStepStatus.SKIPPED,
                    WorkflowStepStatus.CANCELLED,
                }
                for status in statuses
            )

        if step.trigger_rule == WorkflowTriggerRule.ONE_SUCCESS:
            return any(status == WorkflowStepStatus.SUCCESS for status in statuses)

        if step.trigger_rule == WorkflowTriggerRule.NONE_FAILED:
            return all(status != WorkflowStepStatus.FAILED for status in statuses)

        return False

    @staticmethod
    def _finalize_execution(execution: WorkflowExecution) -> None:
        statuses = [step.status for step in execution.step_executions.values()]

        if all(status in {WorkflowStepStatus.SUCCESS, WorkflowStepStatus.SKIPPED} for status in statuses):
            execution.status = WorkflowExecutionStatus.SUCCESS
            return

        if any(status == WorkflowStepStatus.SUCCESS for status in statuses):
            execution.status = WorkflowExecutionStatus.PARTIAL
            return

        execution.status = WorkflowExecutionStatus.FAILED

    def get_execution(self, execution_id: str) -> WorkflowExecution:
        return self.execution_store.get(execution_id)

    def list_executions(
        self,
        workflow_id: Optional[str] = None,
        status: Optional[WorkflowExecutionStatus] = None,
    ) -> List[WorkflowExecution]:
        return self.execution_store.list_all(workflow_id=workflow_id, status=status)

    def export_workflow_json(self, workflow_id: str) -> str:
        workflow = self.repository.get(workflow_id)
        return json.dumps(
            self._workflow_to_dict(workflow),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_execution_json(self, execution_id: str) -> str:
        execution = self.execution_store.get(execution_id)
        return json.dumps(
            self._execution_to_dict(execution),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        workflow_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        step_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "workflow_id": workflow_id,
                "execution_id": execution_id,
                "step_id": step_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _workflow_to_dict(workflow: WorkflowDefinition) -> Dict[str, Any]:
        data = asdict(workflow)
        data["status"] = workflow.status.value
        data["priority"] = workflow.priority.value
        data["created_at"] = workflow.created_at.isoformat()
        data["updated_at"] = workflow.updated_at.isoformat() if workflow.updated_at else None

        for step in data["steps"]:
            step["trigger_rule"] = step["trigger_rule"].value
            step["retry_policy"]["strategy"] = step["retry_policy"]["strategy"].value

        return data

    @staticmethod
    def _execution_to_dict(execution: WorkflowExecution) -> Dict[str, Any]:
        data = asdict(execution)
        data["status"] = execution.status.value
        data["started_at"] = execution.started_at.isoformat()
        data["finished_at"] = execution.finished_at.isoformat() if execution.finished_at else None
        data["context"]["started_at"] = execution.context.started_at.isoformat()

        for step_execution in data["step_executions"].values():
            step_execution["status"] = step_execution["status"].value
            step_execution["started_at"] = (
                step_execution["started_at"].isoformat()
                if step_execution["started_at"]
                else None
            )
            step_execution["finished_at"] = (
                step_execution["finished_at"].isoformat()
                if step_execution["finished_at"]
                else None
            )

        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_workflow_engine() -> WorkflowEngine:
    engine = WorkflowEngine()

    def echo_handler(
        step: WorkflowStep,
        context: WorkflowContext,
        upstream_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "step_id": step.step_id,
            "message": step.config.get("message", "executed"),
            "config": step.config,
            "parameters": context.parameters,
            "upstream_results": upstream_results,
        }

    def rollback_echo(
        step: WorkflowStep,
        context: WorkflowContext,
        step_result: Any,
    ) -> Dict[str, Any]:
        return {
            "step_id": step.step_id,
            "rolled_back": True,
            "previous_result": step_result,
        }

    engine.register_handler("echo", echo_handler)
    engine.register_rollback_handler("rollback_echo", rollback_echo)

    return engine


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_workflow_engine()

    workflow = WorkflowDefinition(
        workflow_id="daily-sales-workflow",
        name="Daily Sales Workflow",
        tenant_id="tenant-default",
        domain="sales",
        owner="data-platform",
        priority=WorkflowPriority.HIGH,
        steps=[
            WorkflowStep(
                step_id="extract",
                name="Extract Sales",
                handler_name="echo",
                rollback_handler_name="rollback_echo",
                config={"message": "extract sales"},
                retry_policy=RetryPolicy(
                    strategy=RetryStrategy.FIXED,
                    max_attempts=2,
                    delay_seconds=0.2,
                ),
            ),
            WorkflowStep(
                step_id="transform",
                name="Transform Sales",
                handler_name="echo",
                rollback_handler_name="rollback_echo",
                dependencies=["extract"],
                config={"message": "transform sales"},
            ),
            WorkflowStep(
                step_id="publish",
                name="Publish Sales",
                handler_name="echo",
                rollback_handler_name="rollback_echo",
                dependencies=["transform"],
                config={"message": "publish sales"},
            ),
        ],
    )

    engine.register_workflow(workflow)

    execution = engine.run(
        workflow_id="daily-sales-workflow",
        context=WorkflowContext(
            workflow_id="daily-sales-workflow",
            execution_id=str(uuid.uuid4()),
            tenant_id="tenant-default",
            domain="sales",
            triggered_by="scheduler",
            correlation_id="corr-workflow-001",
            parameters={"business_date": "2026-05-13"},
        ),
        mode=WorkflowExecutionMode.SEQUENTIAL,
        rollback_on_failure=False,
    )

    print(engine.export_execution_json(execution.execution_id))


if __name__ == "__main__":
    example_usage()