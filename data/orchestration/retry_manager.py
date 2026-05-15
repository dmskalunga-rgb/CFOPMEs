"""
data/orchestration/retry_manager.py

Enterprise Retry Manager.

Recursos:
- Retry reutilizável para workflows, DAGs, tasks, filas e integrações
- Estratégias: none, fixed, linear, exponential, exponential com jitter
- Circuit breaker simples
- Políticas por operação
- Classificação de erros retryable/non-retryable
- Timeout opcional por tentativa
- Hooks before/after attempt
- Auditoria
- Métricas
- Multi-tenant
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Type


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class RetryStrategy(str, Enum):
    NONE = "none"
    FIXED = "fixed"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"


class RetryOutcome(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"


class RetryAttemptStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    NON_RETRYABLE = "non_retryable"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class RetryManagerError(Exception):
    """Erro base do Retry Manager."""


class RetryPolicyError(RetryManagerError):
    """Erro na política de retry."""


class RetryExecutionError(RetryManagerError):
    """Erro durante execução com retry."""


class RetryTimeoutError(RetryManagerError):
    """Timeout em tentativa de execução."""


class CircuitOpenError(RetryManagerError):
    """Circuit breaker aberto."""


# =============================================================================
# Protocols
# =============================================================================

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
            "retry_manager_audit=%s",
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
class RetryContext:
    operation_name: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    dag_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 60.0
    half_open_max_calls: int = 1

    def validate(self) -> None:
        if self.failure_threshold < 1:
            raise RetryPolicyError("failure_threshold precisa ser >= 1")

        if self.recovery_timeout_seconds <= 0:
            raise RetryPolicyError("recovery_timeout_seconds precisa ser > 0")

        if self.half_open_max_calls < 1:
            raise RetryPolicyError("half_open_max_calls precisa ser >= 1")


@dataclass(frozen=True)
class RetryPolicy:
    policy_id: str
    name: str
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_JITTER
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.25
    attempt_timeout_seconds: Optional[float] = None
    retryable_exceptions: List[Type[BaseException]] = field(default_factory=lambda: [Exception])
    non_retryable_exceptions: List[Type[BaseException]] = field(default_factory=list)
    circuit_breaker: CircuitBreakerPolicy = field(default_factory=CircuitBreakerPolicy)
    enabled: bool = True
    tags: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.policy_id:
            raise RetryPolicyError("policy_id é obrigatório")

        if not self.name:
            raise RetryPolicyError("name é obrigatório")

        if self.max_attempts < 1:
            raise RetryPolicyError("max_attempts precisa ser >= 1")

        if self.base_delay_seconds < 0:
            raise RetryPolicyError("base_delay_seconds não pode ser negativo")

        if self.max_delay_seconds < 0:
            raise RetryPolicyError("max_delay_seconds não pode ser negativo")

        if self.multiplier < 1:
            raise RetryPolicyError("multiplier precisa ser >= 1")

        if not 0 <= self.jitter_ratio <= 1:
            raise RetryPolicyError("jitter_ratio precisa estar entre 0 e 1")

        if self.attempt_timeout_seconds is not None and self.attempt_timeout_seconds <= 0:
            raise RetryPolicyError("attempt_timeout_seconds precisa ser > 0")

        self.circuit_breaker.validate()

    def delay_for_attempt(self, attempt: int) -> float:
        if self.strategy == RetryStrategy.NONE:
            return 0.0

        if self.strategy == RetryStrategy.FIXED:
            delay = self.base_delay_seconds

        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.base_delay_seconds * attempt

        elif self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.base_delay_seconds * (self.multiplier ** max(0, attempt - 1))

        elif self.strategy == RetryStrategy.EXPONENTIAL_JITTER:
            base = self.base_delay_seconds * (self.multiplier ** max(0, attempt - 1))
            jitter = base * self.jitter_ratio
            delay = random.uniform(max(0.0, base - jitter), base + jitter)

        else:
            delay = self.base_delay_seconds

        return min(delay, self.max_delay_seconds)


@dataclass
class RetryAttempt:
    attempt_number: int
    status: RetryAttemptStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    delay_before_seconds: float = 0.0
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    traceback_text: Optional[str] = None


@dataclass
class RetryExecution:
    execution_id: str
    policy_id: str
    operation_name: str
    outcome: RetryOutcome
    attempts: List[RetryAttempt]
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    result: Any = None
    error: Optional[str] = None
    context: Optional[RetryContext] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CircuitBreakerState:
    operation_name: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: Optional[datetime] = None
    half_open_calls: int = 0
    last_error: Optional[str] = None


# =============================================================================
# Repository
# =============================================================================

class RetryPolicyRepository:
    def __init__(self, policies: Optional[List[RetryPolicy]] = None) -> None:
        self._policies: Dict[str, RetryPolicy] = {}

        for policy in policies or []:
            self.save(policy)

    def save(self, policy: RetryPolicy) -> None:
        policy.validate()
        self._policies[policy.policy_id] = policy

    def get(self, policy_id: str) -> RetryPolicy:
        policy = self._policies.get(policy_id)
        if not policy:
            raise RetryPolicyError(f"Política não encontrada: {policy_id}")
        return policy

    def list_all(self, enabled_only: bool = True) -> List[RetryPolicy]:
        policies = list(self._policies.values())

        if enabled_only:
            policies = [policy for policy in policies if policy.enabled]

        return policies


# =============================================================================
# Circuit Breaker
# =============================================================================

class CircuitBreakerRegistry:
    def __init__(self) -> None:
        self._states: Dict[str, CircuitBreakerState] = {}
        self._lock = threading.RLock()

    def before_call(
        self,
        operation_name: str,
        policy: CircuitBreakerPolicy,
    ) -> None:
        if not policy.enabled:
            return

        with self._lock:
            state = self._states.setdefault(
                operation_name,
                CircuitBreakerState(operation_name=operation_name),
            )

            now = datetime.now(timezone.utc)

            if state.state == CircuitState.OPEN:
                if (
                    state.opened_at
                    and now - state.opened_at >= timedelta(seconds=policy.recovery_timeout_seconds)
                ):
                    state.state = CircuitState.HALF_OPEN
                    state.half_open_calls = 0
                else:
                    raise CircuitOpenError(f"Circuit breaker aberto para {operation_name}")

            if state.state == CircuitState.HALF_OPEN:
                if state.half_open_calls >= policy.half_open_max_calls:
                    raise CircuitOpenError(
                        f"Circuit breaker half-open sem capacidade para {operation_name}"
                    )

                state.half_open_calls += 1

    def record_success(
        self,
        operation_name: str,
        policy: CircuitBreakerPolicy,
    ) -> None:
        if not policy.enabled:
            return

        with self._lock:
            state = self._states.setdefault(
                operation_name,
                CircuitBreakerState(operation_name=operation_name),
            )

            state.state = CircuitState.CLOSED
            state.failure_count = 0
            state.opened_at = None
            state.half_open_calls = 0
            state.last_error = None

    def record_failure(
        self,
        operation_name: str,
        policy: CircuitBreakerPolicy,
        error: str,
    ) -> None:
        if not policy.enabled:
            return

        with self._lock:
            state = self._states.setdefault(
                operation_name,
                CircuitBreakerState(operation_name=operation_name),
            )

            state.failure_count += 1
            state.last_error = error

            if state.state == CircuitState.HALF_OPEN:
                state.state = CircuitState.OPEN
                state.opened_at = datetime.now(timezone.utc)
                return

            if state.failure_count >= policy.failure_threshold:
                state.state = CircuitState.OPEN
                state.opened_at = datetime.now(timezone.utc)

    def get_state(self, operation_name: str) -> CircuitBreakerState:
        with self._lock:
            return self._states.setdefault(
                operation_name,
                CircuitBreakerState(operation_name=operation_name),
            )

    def list_states(self) -> List[CircuitBreakerState]:
        with self._lock:
            return list(self._states.values())


# =============================================================================
# Retry Manager
# =============================================================================

BeforeAttemptHook = Callable[[int, RetryContext], None]
AfterAttemptHook = Callable[[RetryAttempt, RetryContext], None]


class RetryManager:
    def __init__(
        self,
        repository: Optional[RetryPolicyRepository] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        circuit_breakers: Optional[CircuitBreakerRegistry] = None,
    ) -> None:
        self.repository = repository or RetryPolicyRepository(build_default_retry_policies())
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.circuit_breakers = circuit_breakers or CircuitBreakerRegistry()
        self._before_attempt_hooks: List[BeforeAttemptHook] = []
        self._after_attempt_hooks: List[AfterAttemptHook] = []

    def add_before_attempt_hook(self, hook: BeforeAttemptHook) -> None:
        self._before_attempt_hooks.append(hook)

    def add_after_attempt_hook(self, hook: AfterAttemptHook) -> None:
        self._after_attempt_hooks.append(hook)

    def execute(
        self,
        policy_id: str,
        operation: Callable[[], Any],
        context: RetryContext,
    ) -> RetryExecution:
        policy = self.repository.get(policy_id)

        if not policy.enabled:
            started = datetime.now(timezone.utc)
            return RetryExecution(
                execution_id=str(uuid.uuid4()),
                policy_id=policy.policy_id,
                operation_name=context.operation_name,
                outcome=RetryOutcome.SKIPPED,
                attempts=[],
                started_at=started,
                finished_at=started,
                duration_ms=0.0,
                error="Retry policy disabled",
                context=context,
            )

        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()
        attempts: List[RetryAttempt] = []

        try:
            self.circuit_breakers.before_call(
                context.operation_name,
                policy.circuit_breaker,
            )
        except CircuitOpenError as exc:
            finished_at = datetime.now(timezone.utc)
            execution = RetryExecution(
                execution_id=str(uuid.uuid4()),
                policy_id=policy.policy_id,
                operation_name=context.operation_name,
                outcome=RetryOutcome.CIRCUIT_OPEN,
                attempts=[],
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=(time.perf_counter() - started_perf) * 1000,
                error=str(exc),
                context=context,
            )

            self._audit_execution("retry.circuit_open", execution, AuditSeverity.WARNING)
            self._emit_execution_metrics(execution)
            return execution

        final_error: Optional[BaseException] = None
        result: Any = None

        for attempt_number in range(1, policy.max_attempts + 1):
            delay = 0.0

            if attempt_number > 1:
                delay = policy.delay_for_attempt(attempt_number - 1)

                if delay > 0:
                    time.sleep(delay)

            for hook in self._before_attempt_hooks:
                hook(attempt_number, context)

            attempt_started_at = datetime.now(timezone.utc)
            attempt_perf = time.perf_counter()

            try:
                result = self._execute_attempt(operation, policy)

                attempt = RetryAttempt(
                    attempt_number=attempt_number,
                    status=RetryAttemptStatus.SUCCESS,
                    started_at=attempt_started_at,
                    finished_at=datetime.now(timezone.utc),
                    duration_ms=(time.perf_counter() - attempt_perf) * 1000,
                    delay_before_seconds=delay,
                )

                attempts.append(attempt)

                for hook in self._after_attempt_hooks:
                    hook(attempt, context)

                self.circuit_breakers.record_success(
                    context.operation_name,
                    policy.circuit_breaker,
                )

                finished_at = datetime.now(timezone.utc)

                execution = RetryExecution(
                    execution_id=str(uuid.uuid4()),
                    policy_id=policy.policy_id,
                    operation_name=context.operation_name,
                    outcome=RetryOutcome.SUCCESS,
                    attempts=attempts,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=(time.perf_counter() - started_perf) * 1000,
                    result=result,
                    context=context,
                    metadata={
                        "attempts_used": attempt_number,
                    },
                )

                self._audit_execution("retry.execution.success", execution, AuditSeverity.INFO)
                self._emit_execution_metrics(execution)

                return execution

            except BaseException as exc:
                final_error = exc
                attempt_status = self._attempt_status_for_error(exc, policy)

                attempt = RetryAttempt(
                    attempt_number=attempt_number,
                    status=attempt_status,
                    started_at=attempt_started_at,
                    finished_at=datetime.now(timezone.utc),
                    duration_ms=(time.perf_counter() - attempt_perf) * 1000,
                    delay_before_seconds=delay,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    traceback_text=traceback.format_exc(),
                )

                attempts.append(attempt)

                for hook in self._after_attempt_hooks:
                    hook(attempt, context)

                self._audit_attempt(policy, context, attempt)

                if attempt_status == RetryAttemptStatus.NON_RETRYABLE:
                    break

                if policy.strategy == RetryStrategy.NONE:
                    break

        error_message = str(final_error) if final_error else "Unknown retry failure"

        self.circuit_breakers.record_failure(
            context.operation_name,
            policy.circuit_breaker,
            error_message,
        )

        finished_at = datetime.now(timezone.utc)

        outcome = (
            RetryOutcome.TIMEOUT
            if any(attempt.status == RetryAttemptStatus.TIMEOUT for attempt in attempts)
            else RetryOutcome.FAILED
        )

        execution = RetryExecution(
            execution_id=str(uuid.uuid4()),
            policy_id=policy.policy_id,
            operation_name=context.operation_name,
            outcome=outcome,
            attempts=attempts,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=(time.perf_counter() - started_perf) * 1000,
            error=error_message,
            context=context,
            metadata={
                "attempts_used": len(attempts),
                "final_error_type": type(final_error).__name__ if final_error else None,
            },
        )

        self._audit_execution("retry.execution.failed", execution, AuditSeverity.ERROR)
        self._emit_execution_metrics(execution)

        return execution

    def execute_or_raise(
        self,
        policy_id: str,
        operation: Callable[[], Any],
        context: RetryContext,
    ) -> Any:
        execution = self.execute(policy_id, operation, context)

        if execution.outcome == RetryOutcome.SUCCESS:
            return execution.result

        raise RetryExecutionError(
            f"Operação {context.operation_name} falhou após retry: {execution.error}"
        )

    def get_circuit_state(self, operation_name: str) -> CircuitBreakerState:
        return self.circuit_breakers.get_state(operation_name)

    def export_execution_json(self, execution: RetryExecution) -> str:
        return json.dumps(
            self._execution_to_dict(execution),
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_circuit_states_json(self) -> str:
        return json.dumps(
            [self._circuit_state_to_dict(state) for state in self.circuit_breakers.list_states()],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def _execute_attempt(
        self,
        operation: Callable[[], Any],
        policy: RetryPolicy,
    ) -> Any:
        if not policy.attempt_timeout_seconds:
            return operation()

        result_holder: Dict[str, Any] = {}
        error_holder: Dict[str, BaseException] = {}

        def target() -> None:
            try:
                result_holder["result"] = operation()
            except BaseException as exc:
                error_holder["error"] = exc

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(timeout=policy.attempt_timeout_seconds)

        if thread.is_alive():
            raise RetryTimeoutError(
                f"Tentativa excedeu timeout de {policy.attempt_timeout_seconds}s"
            )

        if "error" in error_holder:
            raise error_holder["error"]

        return result_holder.get("result")

    @staticmethod
    def _attempt_status_for_error(
        exc: BaseException,
        policy: RetryPolicy,
    ) -> RetryAttemptStatus:
        if isinstance(exc, RetryTimeoutError):
            return RetryAttemptStatus.TIMEOUT

        if any(isinstance(exc, non_retryable) for non_retryable in policy.non_retryable_exceptions):
            return RetryAttemptStatus.NON_RETRYABLE

        if any(isinstance(exc, retryable) for retryable in policy.retryable_exceptions):
            return RetryAttemptStatus.FAILED

        return RetryAttemptStatus.NON_RETRYABLE

    def _audit_attempt(
        self,
        policy: RetryPolicy,
        context: RetryContext,
        attempt: RetryAttempt,
    ) -> None:
        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "retry.attempt.failed",
                "severity": AuditSeverity.WARNING.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "policy_id": policy.policy_id,
                "operation_name": context.operation_name,
                "attempt_number": attempt.attempt_number,
                "status": attempt.status.value,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
                "run_id": context.run_id,
                "workflow_id": context.workflow_id,
                "dag_id": context.dag_id,
                "task_id": context.task_id,
                "worker_id": context.worker_id,
                "error_type": attempt.error_type,
                "error_message": attempt.error_message,
                "duration_ms": attempt.duration_ms,
            }
        )

    def _audit_execution(
        self,
        event_type: str,
        execution: RetryExecution,
        severity: AuditSeverity,
    ) -> None:
        context = execution.context or RetryContext(operation_name=execution.operation_name)

        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "execution_id": execution.execution_id,
                "policy_id": execution.policy_id,
                "operation_name": execution.operation_name,
                "outcome": execution.outcome.value,
                "attempts": len(execution.attempts),
                "duration_ms": execution.duration_ms,
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "correlation_id": context.correlation_id,
                "run_id": context.run_id,
                "workflow_id": context.workflow_id,
                "dag_id": context.dag_id,
                "task_id": context.task_id,
                "worker_id": context.worker_id,
                "error": execution.error,
            }
        )

    def _emit_execution_metrics(self, execution: RetryExecution) -> None:
        context = execution.context or RetryContext(operation_name=execution.operation_name)

        tags = {
            "policy_id": execution.policy_id,
            "operation_name": execution.operation_name,
            "outcome": execution.outcome.value,
            "tenant_id": context.tenant_id or "-",
        }

        self.metrics_backend.increment("retry.execution.total", tags=tags)
        self.metrics_backend.timing(
            "retry.execution.duration_ms",
            execution.duration_ms,
            tags=tags,
        )
        self.metrics_backend.increment(
            "retry.execution.attempts.total",
            value=len(execution.attempts),
            tags=tags,
        )

    @staticmethod
    def _execution_to_dict(execution: RetryExecution) -> Dict[str, Any]:
        data = asdict(execution)
        data["outcome"] = execution.outcome.value
        data["started_at"] = execution.started_at.isoformat()
        data["finished_at"] = execution.finished_at.isoformat()

        for attempt in data["attempts"]:
            attempt["status"] = attempt["status"].value
            attempt["started_at"] = attempt["started_at"].isoformat()
            attempt["finished_at"] = attempt["finished_at"].isoformat()

        return data

    @staticmethod
    def _circuit_state_to_dict(state: CircuitBreakerState) -> Dict[str, Any]:
        data = asdict(state)
        data["state"] = state.state.value
        data["opened_at"] = state.opened_at.isoformat() if state.opened_at else None
        return data


# =============================================================================
# Default Policies
# =============================================================================

def build_default_retry_policies() -> List[RetryPolicy]:
    return [
        RetryPolicy(
            policy_id="default",
            name="Default Exponential Jitter Retry",
            strategy=RetryStrategy.EXPONENTIAL_JITTER,
            max_attempts=3,
            base_delay_seconds=1.0,
            max_delay_seconds=30.0,
            multiplier=2.0,
            jitter_ratio=0.25,
            circuit_breaker=CircuitBreakerPolicy(
                enabled=False,
            ),
            tags={"default": "true"},
        ),
        RetryPolicy(
            policy_id="critical_io",
            name="Critical IO Retry",
            strategy=RetryStrategy.EXPONENTIAL_JITTER,
            max_attempts=5,
            base_delay_seconds=2.0,
            max_delay_seconds=120.0,
            multiplier=2.0,
            jitter_ratio=0.30,
            attempt_timeout_seconds=60.0,
            circuit_breaker=CircuitBreakerPolicy(
                enabled=True,
                failure_threshold=5,
                recovery_timeout_seconds=60.0,
                half_open_max_calls=1,
            ),
            tags={"io": "true", "critical": "true"},
        ),
        RetryPolicy(
            policy_id="fast_fixed",
            name="Fast Fixed Retry",
            strategy=RetryStrategy.FIXED,
            max_attempts=3,
            base_delay_seconds=0.5,
            max_delay_seconds=2.0,
            multiplier=1.0,
            jitter_ratio=0.0,
            tags={"fast": "true"},
        ),
        RetryPolicy(
            policy_id="no_retry",
            name="No Retry",
            strategy=RetryStrategy.NONE,
            max_attempts=1,
            base_delay_seconds=0.0,
            tags={"retry": "false"},
        ),
    ]


def create_default_retry_manager() -> RetryManager:
    return RetryManager(
        repository=RetryPolicyRepository(build_default_retry_policies())
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    manager = create_default_retry_manager()

    state = {"attempts": 0}

    def unstable_operation() -> Dict[str, Any]:
        state["attempts"] += 1

        if state["attempts"] < 3:
            raise RuntimeError("Falha transitória simulada")

        return {
            "ok": True,
            "attempts": state["attempts"],
        }

    execution = manager.execute(
        policy_id="default",
        operation=unstable_operation,
        context=RetryContext(
            operation_name="load_sales_dataset",
            tenant_id="tenant-default",
            domain="sales",
            correlation_id="corr-retry-001",
            task_id="task-load-sales",
        ),
    )

    print(manager.export_execution_json(execution))
    print(manager.export_circuit_states_json())


if __name__ == "__main__":
    example_usage()