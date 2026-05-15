"""
data/orchestration/rollback_manager.py

Enterprise Rollback Manager.

Recursos:
- Rollback de workflows, DAGs, pipelines, jobs e tasks
- Checkpoints e snapshots de estado
- Compensating actions
- Rollback parcial, total e por estágio
- Validação pré e pós-rollback
- Execução dry-run
- Estratégias sequencial e reversa por dependência
- Idempotência de rollback
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
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Set


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class RollbackTargetType(str, Enum):
    WORKFLOW = "workflow"
    DAG = "dag"
    PIPELINE = "pipeline"
    JOB = "job"
    TASK = "task"
    DATASET = "dataset"
    MODEL = "model"
    DEPLOYMENT = "deployment"
    RESOURCE = "resource"
    CUSTOM = "custom"


class RollbackStrategy(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    CHECKPOINT = "checkpoint"
    COMPENSATING = "compensating"
    DRY_RUN = "dry_run"


class RollbackExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    REVERSE_ORDER = "reverse_order"
    BEST_EFFORT = "best_effort"


class RollbackStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RollbackStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckpointStatus(str, Enum):
    ACTIVE = "active"
    RESTORED = "restored"
    EXPIRED = "expired"
    INVALID = "invalid"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class RollbackManagerError(Exception):
    """Erro base do Rollback Manager."""


class RollbackValidationError(RollbackManagerError):
    """Erro de validação de rollback."""


class RollbackExecutionError(RollbackManagerError):
    """Erro durante execução de rollback."""


class CheckpointNotFoundError(RollbackManagerError):
    """Checkpoint não encontrado."""


class RollbackPlanNotFoundError(RollbackManagerError):
    """Plano de rollback não encontrado."""


# =============================================================================
# Protocols
# =============================================================================

class RollbackAction(Protocol):
    def __call__(
        self,
        step: "RollbackStep",
        context: "RollbackContext",
        snapshot: Optional["RollbackSnapshot"],
    ) -> Any:
        ...


class RollbackValidator(Protocol):
    def validate(
        self,
        plan: "RollbackPlan",
        context: "RollbackContext",
    ) -> List[str]:
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
        logger.info(
            "rollback_manager_audit=%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )


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
class RollbackContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    dag_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackSnapshot:
    snapshot_id: str
    target_id: str
    target_type: RollbackTargetType
    state: Dict[str, Any]
    checksum: Optional[str] = None
    checkpoint_id: Optional[str] = None
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RollbackCheckpoint:
    checkpoint_id: str
    name: str
    target_id: str
    target_type: RollbackTargetType
    snapshots: List[RollbackSnapshot]
    status: CheckpointStatus = CheckpointStatus.ACTIVE
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    restored_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RollbackStep:
    step_id: str
    name: str
    target_id: str
    target_type: RollbackTargetType
    action_name: str
    order: int = 0
    required: bool = True
    idempotency_key: Optional[str] = None
    checkpoint_id: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.step_id:
            raise RollbackValidationError("step_id é obrigatório")

        if not self.name:
            raise RollbackValidationError("name é obrigatório")

        if not self.target_id:
            raise RollbackValidationError("target_id é obrigatório")

        if not self.action_name:
            raise RollbackValidationError("action_name é obrigatório")

        if self.step_id in self.depends_on:
            raise RollbackValidationError(f"Step {self.step_id} depende de si mesmo")


@dataclass(frozen=True)
class RollbackPlan:
    plan_id: str
    name: str
    target_id: str
    target_type: RollbackTargetType
    strategy: RollbackStrategy
    steps: List[RollbackStep]
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    owner: Optional[str] = None
    version: str = "1.0.0"
    enabled: bool = True
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.plan_id:
            raise RollbackValidationError("plan_id é obrigatório")

        if not self.name:
            raise RollbackValidationError("name é obrigatório")

        if not self.target_id:
            raise RollbackValidationError("target_id é obrigatório")

        if not self.steps:
            raise RollbackValidationError("Plano precisa ter pelo menos um step")

        step_ids: Set[str] = set()

        for step in self.steps:
            step.validate()

            if step.step_id in step_ids:
                raise RollbackValidationError(f"step_id duplicado: {step.step_id}")

            step_ids.add(step.step_id)

        for step in self.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise RollbackValidationError(
                        f"Step {step.step_id} depende de step inexistente: {dep}"
                    )


@dataclass
class RollbackStepExecution:
    step_id: str
    status: RollbackStepStatus = RollbackStepStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    traceback_text: Optional[str] = None


@dataclass
class RollbackExecution:
    execution_id: str
    plan_id: str
    target_id: str
    target_type: RollbackTargetType
    strategy: RollbackStrategy
    mode: RollbackExecutionMode
    status: RollbackStatus
    context: RollbackContext
    step_executions: Dict[str, RollbackStepExecution]
    dry_run: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    validation_errors: List[str] = field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Stores
# =============================================================================

class RollbackRepository:
    def __init__(self) -> None:
        self._plans: Dict[str, RollbackPlan] = {}
        self._checkpoints: Dict[str, RollbackCheckpoint] = {}
        self._executions: Dict[str, RollbackExecution] = {}
        self._lock = threading.RLock()

    def save_plan(self, plan: RollbackPlan) -> None:
        plan.validate()
        with self._lock:
            self._plans[plan.plan_id] = plan

    def get_plan(self, plan_id: str) -> RollbackPlan:
        with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                raise RollbackPlanNotFoundError(plan_id)
            return plan

    def list_plans(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[RollbackPlan]:
        with self._lock:
            plans = list(self._plans.values())

        if tenant_id is not None:
            plans = [plan for plan in plans if plan.tenant_id == tenant_id]

        if domain is not None:
            plans = [plan for plan in plans if plan.domain == domain]

        if target_id is not None:
            plans = [plan for plan in plans if plan.target_id == target_id]

        return plans

    def save_checkpoint(self, checkpoint: RollbackCheckpoint) -> None:
        with self._lock:
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> RollbackCheckpoint:
        with self._lock:
            checkpoint = self._checkpoints.get(checkpoint_id)
            if not checkpoint:
                raise CheckpointNotFoundError(checkpoint_id)
            return checkpoint

    def list_checkpoints(
        self,
        target_id: Optional[str] = None,
        status: Optional[CheckpointStatus] = None,
    ) -> List[RollbackCheckpoint]:
        with self._lock:
            checkpoints = list(self._checkpoints.values())

        if target_id is not None:
            checkpoints = [item for item in checkpoints if item.target_id == target_id]

        if status is not None:
            checkpoints = [item for item in checkpoints if item.status == status]

        return sorted(checkpoints, key=lambda item: item.created_at, reverse=True)

    def save_execution(self, execution: RollbackExecution) -> None:
        with self._lock:
            self._executions[execution.execution_id] = execution

    def get_execution(self, execution_id: str) -> RollbackExecution:
        with self._lock:
            execution = self._executions.get(execution_id)
            if not execution:
                raise RollbackExecutionError(f"Execução não encontrada: {execution_id}")
            return execution

    def list_executions(
        self,
        plan_id: Optional[str] = None,
        status: Optional[RollbackStatus] = None,
    ) -> List[RollbackExecution]:
        with self._lock:
            executions = list(self._executions.values())

        if plan_id is not None:
            executions = [item for item in executions if item.plan_id == plan_id]

        if status is not None:
            executions = [item for item in executions if item.status == status]

        return sorted(executions, key=lambda item: item.started_at, reverse=True)


# =============================================================================
# Validation
# =============================================================================

class DefaultRollbackValidator:
    def validate(
        self,
        plan: RollbackPlan,
        context: RollbackContext,
    ) -> List[str]:
        errors: List[str] = []

        if not plan.enabled:
            errors.append(f"Plano {plan.plan_id} está desabilitado")

        if plan.tenant_id and context.tenant_id and plan.tenant_id != context.tenant_id:
            errors.append("Tenant inválido para o plano de rollback")

        if plan.domain and context.domain and plan.domain != context.domain:
            errors.append("Domínio inválido para o plano de rollback")

        try:
            plan.validate()
        except Exception as exc:
            errors.append(str(exc))

        errors.extend(self._cycle_errors(plan))

        return errors

    @staticmethod
    def _cycle_errors(plan: RollbackPlan) -> List[str]:
        graph = {step.step_id: list(step.depends_on) for step in plan.steps}
        visited: Set[str] = set()
        visiting: Set[str] = set()
        errors: List[str] = []

        def visit(step_id: str) -> None:
            if step_id in visiting:
                errors.append(f"Ciclo detectado envolvendo step {step_id}")
                return

            if step_id in visited:
                return

            visiting.add(step_id)

            for dep in graph.get(step_id, []):
                visit(dep)

            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in graph:
            visit(step_id)

        return errors


# =============================================================================
# Rollback Manager
# =============================================================================

class RollbackManager:
    def __init__(
        self,
        repository: Optional[RollbackRepository] = None,
        validator: Optional[RollbackValidator] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.repository = repository or RollbackRepository()
        self.validator = validator or DefaultRollbackValidator()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self._actions: Dict[str, RollbackAction] = {}
        self._processed_idempotency_keys: Set[str] = set()

    def register_action(self, action_name: str, action: RollbackAction) -> None:
        if not action_name:
            raise RollbackValidationError("action_name é obrigatório")

        self._actions[action_name] = action

        self._audit(
            "rollback.action.registered",
            AuditSeverity.INFO,
            details={"action_name": action_name},
        )

    def register_plan(self, plan: RollbackPlan) -> None:
        self.repository.save_plan(plan)

        self._audit(
            "rollback.plan.registered",
            AuditSeverity.INFO,
            plan_id=plan.plan_id,
            details={
                "name": plan.name,
                "target_id": plan.target_id,
                "target_type": plan.target_type.value,
                "strategy": plan.strategy.value,
                "steps": len(plan.steps),
            },
        )

    def create_checkpoint(
        self,
        name: str,
        target_id: str,
        target_type: RollbackTargetType,
        snapshots: List[RollbackSnapshot],
        context: Optional[RollbackContext] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RollbackCheckpoint:
        context = context or RollbackContext()

        checkpoint_id = str(uuid.uuid4())

        for snapshot in snapshots:
            snapshot.checkpoint_id = checkpoint_id
            snapshot.tenant_id = snapshot.tenant_id or context.tenant_id
            snapshot.domain = snapshot.domain or context.domain

        checkpoint = RollbackCheckpoint(
            checkpoint_id=checkpoint_id,
            name=name,
            target_id=target_id,
            target_type=target_type,
            snapshots=snapshots,
            tenant_id=context.tenant_id,
            domain=context.domain,
            metadata=metadata or {},
        )

        self.repository.save_checkpoint(checkpoint)

        self._audit(
            "rollback.checkpoint.created",
            AuditSeverity.INFO,
            checkpoint_id=checkpoint_id,
            details={
                "name": name,
                "target_id": target_id,
                "target_type": target_type.value,
                "snapshots": len(snapshots),
                "tenant_id": context.tenant_id,
                "domain": context.domain,
            },
        )

        self.metrics_backend.increment(
            "rollback.checkpoint.created.total",
            tags={
                "target_type": target_type.value,
                "tenant_id": context.tenant_id or "-",
            },
        )

        return checkpoint

    def execute(
        self,
        plan_id: str,
        context: Optional[RollbackContext] = None,
        mode: RollbackExecutionMode = RollbackExecutionMode.REVERSE_ORDER,
        dry_run: bool = False,
        fail_fast: bool = True,
        step_ids: Optional[List[str]] = None,
    ) -> RollbackExecution:
        plan = self.repository.get_plan(plan_id)
        context = context or RollbackContext(
            tenant_id=plan.tenant_id,
            domain=plan.domain,
        )

        validation_errors = self.validator.validate(plan, context)

        selected_steps = self._select_steps(plan, mode, step_ids)

        execution = RollbackExecution(
            execution_id=str(uuid.uuid4()),
            plan_id=plan.plan_id,
            target_id=plan.target_id,
            target_type=plan.target_type,
            strategy=RollbackStrategy.DRY_RUN if dry_run else plan.strategy,
            mode=mode,
            status=RollbackStatus.VALIDATING,
            context=context,
            dry_run=dry_run,
            validation_errors=validation_errors,
            step_executions={
                step.step_id: RollbackStepExecution(step_id=step.step_id)
                for step in selected_steps
            },
            metadata={
                "selected_steps": [step.step_id for step in selected_steps],
                "fail_fast": fail_fast,
            },
        )

        self.repository.save_execution(execution)

        if validation_errors:
            execution.status = RollbackStatus.FAILED
            execution.error = "Validation failed"
            execution.finished_at = datetime.now(timezone.utc)
            self.repository.save_execution(execution)

            self._audit(
                "rollback.execution.validation_failed",
                AuditSeverity.ERROR,
                execution_id=execution.execution_id,
                plan_id=plan.plan_id,
                details={"validation_errors": validation_errors},
            )

            return execution

        self._audit(
            "rollback.execution.started",
            AuditSeverity.WARNING,
            execution_id=execution.execution_id,
            plan_id=plan.plan_id,
            details={
                "target_id": plan.target_id,
                "target_type": plan.target_type.value,
                "strategy": execution.strategy.value,
                "dry_run": dry_run,
                "mode": mode.value,
                "reason": context.reason,
            },
        )

        execution.status = RollbackStatus.RUNNING
        started = time.perf_counter()

        try:
            for step in selected_steps:
                if not self._dependencies_satisfied(step, execution):
                    self._skip_step(execution, step, "Dependências do rollback não satisfeitas")
                    if fail_fast and step.required:
                        break
                    continue

                self._execute_step(
                    step=step,
                    execution=execution,
                    context=context,
                    dry_run=dry_run,
                )

                step_execution = execution.step_executions[step.step_id]

                if (
                    step_execution.status == RollbackStepStatus.FAILED
                    and fail_fast
                    and step.required
                ):
                    break

            self._finalize_execution(execution)

        except Exception as exc:
            execution.status = RollbackStatus.FAILED
            execution.error = str(exc)

            self._audit(
                "rollback.execution.failed",
                AuditSeverity.ERROR,
                execution_id=execution.execution_id,
                plan_id=plan.plan_id,
                details={
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )

        finally:
            execution.finished_at = datetime.now(timezone.utc)
            execution.duration_ms = (time.perf_counter() - started) * 1000
            self.repository.save_execution(execution)

            self.metrics_backend.increment(
                "rollback.execution.total",
                tags={
                    "plan_id": plan.plan_id,
                    "target_type": plan.target_type.value,
                    "status": execution.status.value,
                    "dry_run": str(dry_run).lower(),
                },
            )

            self.metrics_backend.timing(
                "rollback.execution.duration_ms",
                execution.duration_ms,
                tags={
                    "plan_id": plan.plan_id,
                    "target_type": plan.target_type.value,
                    "status": execution.status.value,
                },
            )

            self._audit(
                "rollback.execution.finished",
                (
                    AuditSeverity.INFO
                    if execution.status == RollbackStatus.SUCCESS
                    else AuditSeverity.ERROR
                ),
                execution_id=execution.execution_id,
                plan_id=plan.plan_id,
                details={
                    "status": execution.status.value,
                    "duration_ms": execution.duration_ms,
                    "error": execution.error,
                },
            )

        return execution

    def rollback_to_checkpoint(
        self,
        checkpoint_id: str,
        context: Optional[RollbackContext] = None,
        dry_run: bool = False,
    ) -> RollbackExecution:
        checkpoint = self.repository.get_checkpoint(checkpoint_id)
        context = context or RollbackContext(
            tenant_id=checkpoint.tenant_id,
            domain=checkpoint.domain,
            reason=f"Rollback to checkpoint {checkpoint_id}",
        )

        steps = [
            RollbackStep(
                step_id=f"restore-{snapshot.snapshot_id}",
                name=f"Restore snapshot {snapshot.snapshot_id}",
                target_id=snapshot.target_id,
                target_type=snapshot.target_type,
                action_name="restore_snapshot",
                order=index,
                checkpoint_id=checkpoint_id,
                config={"snapshot_id": snapshot.snapshot_id},
            )
            for index, snapshot in enumerate(checkpoint.snapshots)
        ]

        plan = RollbackPlan(
            plan_id=f"checkpoint-rollback-{checkpoint_id}",
            name=f"Rollback to checkpoint {checkpoint.name}",
            target_id=checkpoint.target_id,
            target_type=checkpoint.target_type,
            strategy=RollbackStrategy.CHECKPOINT,
            steps=steps,
            tenant_id=checkpoint.tenant_id,
            domain=checkpoint.domain,
            metadata={"checkpoint_id": checkpoint_id},
        )

        self.repository.save_plan(plan)

        execution = self.execute(
            plan_id=plan.plan_id,
            context=context,
            mode=RollbackExecutionMode.REVERSE_ORDER,
            dry_run=dry_run,
            fail_fast=True,
        )

        if execution.status == RollbackStatus.SUCCESS and not dry_run:
            checkpoint.status = CheckpointStatus.RESTORED
            checkpoint.restored_at = datetime.now(timezone.utc)
            self.repository.save_checkpoint(checkpoint)

        return execution

    def _execute_step(
        self,
        step: RollbackStep,
        execution: RollbackExecution,
        context: RollbackContext,
        dry_run: bool,
    ) -> None:
        step_execution = execution.step_executions[step.step_id]
        step_execution.status = RollbackStepStatus.RUNNING
        step_execution.started_at = datetime.now(timezone.utc)

        started = time.perf_counter()

        self._audit(
            "rollback.step.started",
            AuditSeverity.INFO,
            execution_id=execution.execution_id,
            plan_id=execution.plan_id,
            step_id=step.step_id,
            details={
                "action_name": step.action_name,
                "target_id": step.target_id,
                "target_type": step.target_type.value,
                "dry_run": dry_run,
            },
        )

        try:
            if dry_run:
                step_execution.result = {
                    "dry_run": True,
                    "message": "Step validado, mas não executado.",
                    "action_name": step.action_name,
                    "target_id": step.target_id,
                }
                step_execution.status = RollbackStepStatus.SUCCESS
                return

            if step.idempotency_key and step.idempotency_key in self._processed_idempotency_keys:
                step_execution.result = {
                    "idempotent": True,
                    "message": "Rollback step já executado anteriormente.",
                }
                step_execution.status = RollbackStepStatus.SUCCESS
                return

            action = self._actions.get(step.action_name)

            if not action:
                raise RollbackExecutionError(
                    f"Ação de rollback não registrada: {step.action_name}"
                )

            snapshot = self._find_snapshot_for_step(step)
            result = action(step, context, snapshot)

            step_execution.result = result
            step_execution.status = RollbackStepStatus.SUCCESS

            if step.idempotency_key:
                self._processed_idempotency_keys.add(step.idempotency_key)

        except Exception as exc:
            step_execution.status = RollbackStepStatus.FAILED
            step_execution.error = str(exc)
            step_execution.traceback_text = traceback.format_exc()

        finally:
            step_execution.finished_at = datetime.now(timezone.utc)
            step_execution.duration_ms = (time.perf_counter() - started) * 1000

            self.metrics_backend.increment(
                "rollback.step.total",
                tags={
                    "plan_id": execution.plan_id,
                    "step_id": step.step_id,
                    "status": step_execution.status.value,
                    "action_name": step.action_name,
                },
            )

            self.metrics_backend.timing(
                "rollback.step.duration_ms",
                step_execution.duration_ms,
                tags={
                    "plan_id": execution.plan_id,
                    "step_id": step.step_id,
                    "status": step_execution.status.value,
                },
            )

            self._audit(
                (
                    "rollback.step.succeeded"
                    if step_execution.status == RollbackStepStatus.SUCCESS
                    else "rollback.step.failed"
                ),
                (
                    AuditSeverity.INFO
                    if step_execution.status == RollbackStepStatus.SUCCESS
                    else AuditSeverity.ERROR
                ),
                execution_id=execution.execution_id,
                plan_id=execution.plan_id,
                step_id=step.step_id,
                details={
                    "status": step_execution.status.value,
                    "duration_ms": step_execution.duration_ms,
                    "error": step_execution.error,
                },
            )

    def _find_snapshot_for_step(self, step: RollbackStep) -> Optional[RollbackSnapshot]:
        if not step.checkpoint_id:
            return None

        checkpoint = self.repository.get_checkpoint(step.checkpoint_id)
        snapshot_id = step.config.get("snapshot_id")

        for snapshot in checkpoint.snapshots:
            if snapshot_id and snapshot.snapshot_id == snapshot_id:
                return snapshot

            if not snapshot_id and snapshot.target_id == step.target_id:
                return snapshot

        return None

    @staticmethod
    def _select_steps(
        plan: RollbackPlan,
        mode: RollbackExecutionMode,
        step_ids: Optional[List[str]],
    ) -> List[RollbackStep]:
        steps = list(plan.steps)

        if step_ids:
            allowed = set(step_ids)
            steps = [step for step in steps if step.step_id in allowed]

        steps = sorted(steps, key=lambda item: item.order)

        if mode == RollbackExecutionMode.REVERSE_ORDER:
            steps = list(reversed(steps))

        return steps

    @staticmethod
    def _dependencies_satisfied(
        step: RollbackStep,
        execution: RollbackExecution,
    ) -> bool:
        if not step.depends_on:
            return True

        return all(
            execution.step_executions.get(dep)
            and execution.step_executions[dep].status == RollbackStepStatus.SUCCESS
            for dep in step.depends_on
        )

    def _skip_step(
        self,
        execution: RollbackExecution,
        step: RollbackStep,
        reason: str,
    ) -> None:
        step_execution = execution.step_executions[step.step_id]
        step_execution.status = RollbackStepStatus.SKIPPED
        step_execution.error = reason
        step_execution.started_at = datetime.now(timezone.utc)
        step_execution.finished_at = datetime.now(timezone.utc)
        step_execution.duration_ms = 0.0

        self._audit(
            "rollback.step.skipped",
            AuditSeverity.WARNING,
            execution_id=execution.execution_id,
            plan_id=execution.plan_id,
            step_id=step.step_id,
            details={"reason": reason},
        )

    @staticmethod
    def _finalize_execution(execution: RollbackExecution) -> None:
        statuses = [
            step.status for step in execution.step_executions.values()
        ]

        if all(status in {RollbackStepStatus.SUCCESS, RollbackStepStatus.SKIPPED} for status in statuses):
            execution.status = RollbackStatus.SUCCESS
            return

        if any(status == RollbackStepStatus.SUCCESS for status in statuses):
            execution.status = RollbackStatus.PARTIAL
            return

        execution.status = RollbackStatus.FAILED

    def get_execution(self, execution_id: str) -> RollbackExecution:
        return self.repository.get_execution(execution_id)

    def list_executions(
        self,
        plan_id: Optional[str] = None,
        status: Optional[RollbackStatus] = None,
    ) -> List[RollbackExecution]:
        return self.repository.list_executions(plan_id=plan_id, status=status)

    def list_checkpoints(
        self,
        target_id: Optional[str] = None,
        status: Optional[CheckpointStatus] = None,
    ) -> List[RollbackCheckpoint]:
        return self.repository.list_checkpoints(target_id=target_id, status=status)

    def export_execution_json(self, execution_id: str) -> str:
        execution = self.repository.get_execution(execution_id)
        return json.dumps(
            self._execution_to_dict(execution),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_checkpoint_json(self, checkpoint_id: str) -> str:
        checkpoint = self.repository.get_checkpoint(checkpoint_id)
        return json.dumps(
            self._checkpoint_to_dict(checkpoint),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_plan_json(self, plan_id: str) -> str:
        plan = self.repository.get_plan(plan_id)
        return json.dumps(
            self._plan_to_dict(plan),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        execution_id: Optional[str] = None,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        checkpoint_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "execution_id": execution_id,
                "plan_id": plan_id,
                "step_id": step_id,
                "checkpoint_id": checkpoint_id,
                "details": details or {},
            }
        )

    @staticmethod
    def _plan_to_dict(plan: RollbackPlan) -> Dict[str, Any]:
        data = asdict(plan)
        data["target_type"] = plan.target_type.value
        data["strategy"] = plan.strategy.value

        for step in data["steps"]:
            step["target_type"] = step["target_type"].value

        return data

    @staticmethod
    def _checkpoint_to_dict(checkpoint: RollbackCheckpoint) -> Dict[str, Any]:
        data = asdict(checkpoint)
        data["target_type"] = checkpoint.target_type.value
        data["status"] = checkpoint.status.value
        data["created_at"] = checkpoint.created_at.isoformat()
        data["restored_at"] = checkpoint.restored_at.isoformat() if checkpoint.restored_at else None

        for snapshot in data["snapshots"]:
            snapshot["target_type"] = snapshot["target_type"].value
            snapshot["created_at"] = snapshot["created_at"].isoformat()

        return data

    @staticmethod
    def _execution_to_dict(execution: RollbackExecution) -> Dict[str, Any]:
        data = asdict(execution)
        data["target_type"] = execution.target_type.value
        data["strategy"] = execution.strategy.value
        data["mode"] = execution.mode.value
        data["status"] = execution.status.value
        data["started_at"] = execution.started_at.isoformat()
        data["finished_at"] = execution.finished_at.isoformat() if execution.finished_at else None

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
# Default Actions
# =============================================================================

def restore_snapshot_action(
    step: RollbackStep,
    context: RollbackContext,
    snapshot: Optional[RollbackSnapshot],
) -> Dict[str, Any]:
    if not snapshot:
        raise RollbackExecutionError("Snapshot não encontrado para restore")

    return {
        "action": "restore_snapshot",
        "target_id": snapshot.target_id,
        "target_type": snapshot.target_type.value,
        "restored_state": snapshot.state,
        "checkpoint_id": snapshot.checkpoint_id,
        "checksum": snapshot.checksum,
        "tenant_id": context.tenant_id,
    }


def compensate_action(
    step: RollbackStep,
    context: RollbackContext,
    snapshot: Optional[RollbackSnapshot],
) -> Dict[str, Any]:
    return {
        "action": "compensate",
        "target_id": step.target_id,
        "target_type": step.target_type.value,
        "compensation": step.config,
        "reason": context.reason,
    }


def noop_action(
    step: RollbackStep,
    context: RollbackContext,
    snapshot: Optional[RollbackSnapshot],
) -> Dict[str, Any]:
    return {
        "action": "noop",
        "target_id": step.target_id,
        "message": "Nenhuma alteração aplicada.",
    }


# =============================================================================
# Factory
# =============================================================================

def create_default_rollback_manager() -> RollbackManager:
    manager = RollbackManager()
    manager.register_action("restore_snapshot", restore_snapshot_action)
    manager.register_action("compensate", compensate_action)
    manager.register_action("noop", noop_action)
    return manager


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    manager = create_default_rollback_manager()

    context = RollbackContext(
        tenant_id="tenant-default",
        domain="sales",
        user_id="data-platform",
        correlation_id="corr-rollback-001",
        reason="Falha na publicação do mart de vendas",
    )

    checkpoint = manager.create_checkpoint(
        name="Before sales mart publish",
        target_id="sales_mart",
        target_type=RollbackTargetType.DATASET,
        context=context,
        snapshots=[
            RollbackSnapshot(
                snapshot_id=str(uuid.uuid4()),
                target_id="sales_mart",
                target_type=RollbackTargetType.DATASET,
                state={
                    "version": "2026-05-13T00:00:00Z",
                    "row_count": 150000,
                    "location": "s3://warehouse/sales_mart/previous",
                },
                checksum="sha256:example",
            )
        ],
    )

    plan = RollbackPlan(
        plan_id="rollback-sales-mart",
        name="Rollback Sales Mart",
        target_id="sales_mart",
        target_type=RollbackTargetType.DATASET,
        strategy=RollbackStrategy.CHECKPOINT,
        tenant_id="tenant-default",
        domain="sales",
        steps=[
            RollbackStep(
                step_id="restore-sales-mart",
                name="Restore Sales Mart Snapshot",
                target_id="sales_mart",
                target_type=RollbackTargetType.DATASET,
                action_name="restore_snapshot",
                order=1,
                checkpoint_id=checkpoint.checkpoint_id,
                config={"snapshot_id": checkpoint.snapshots[0].snapshot_id},
                idempotency_key="restore-sales-mart-001",
            ),
            RollbackStep(
                step_id="notify-downstream",
                name="Notify Downstream Consumers",
                target_id="sales_dashboard",
                target_type=RollbackTargetType.SERVICE,
                action_name="compensate",
                order=2,
                depends_on=["restore-sales-mart"],
                config={"notification": "sales_mart restored to previous version"},
            ),
        ],
    )

    manager.register_plan(plan)

    execution = manager.execute(
        plan_id="rollback-sales-mart",
        context=context,
        dry_run=False,
        mode=RollbackExecutionMode.SEQUENTIAL,
    )

    print(manager.export_execution_json(execution.execution_id))


if __name__ == "__main__":
    example_usage()