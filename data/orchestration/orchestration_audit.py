"""
data/orchestration/orchestration_audit.py

Enterprise Orchestration Audit Engine.

Recursos:
- Auditoria de workflows, DAGs, tasks, workers, schedulers, filas e pipelines
- Eventos estruturados
- Hash chain para trilha imutável
- Multi-tenant
- Severidade, categoria, ação e outcome
- Consultas por período, ator, recurso, tenant e correlação
- Verificação de integridade
- Exportação JSON e JSONL
- Backends plugáveis
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class OrchestrationAuditCategory(str, Enum):
    WORKFLOW = "workflow"
    DAG = "dag"
    TASK = "task"
    SCHEDULER = "scheduler"
    WORKER = "worker"
    QUEUE = "queue"
    PIPELINE = "pipeline"
    EVENT_BUS = "event_bus"
    STATE = "state"
    RETRY = "retry"
    RESOURCE = "resource"
    SECURITY = "security"
    SYSTEM = "system"


class OrchestrationAuditAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    RESUMED = "resumed"
    RETRIED = "retried"
    SKIPPED = "skipped"
    CLAIMED = "claimed"
    RELEASED = "released"
    HEARTBEAT = "heartbeat"
    SCHEDULED = "scheduled"
    TRIGGERED = "triggered"
    VALIDATED = "validated"
    REGISTERED = "registered"
    DEREGISTERED = "deregistered"
    ACCESS_DENIED = "access_denied"


# =============================================================================
# Exceptions
# =============================================================================

class OrchestrationAuditError(Exception):
    """Erro base do módulo de auditoria de orquestração."""


class AuditIntegrityError(OrchestrationAuditError):
    """Falha na integridade da hash chain."""


class AuditEventNotFound(OrchestrationAuditError):
    """Evento de auditoria não encontrado."""


# =============================================================================
# Protocols
# =============================================================================

class AuditBackend(Protocol):
    def append(self, event: "OrchestrationAuditEvent") -> None:
        ...

    def list_events(self) -> List["OrchestrationAuditEvent"]:
        ...


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class OrchestrationAuditContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None
    run_id: Optional[str] = None
    workflow_id: Optional[str] = None
    dag_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    task_id: Optional[str] = None
    worker_id: Optional[str] = None
    scheduler_id: Optional[str] = None
    queue_id: Optional[str] = None


@dataclass(frozen=True)
class OrchestrationAuditActor:
    actor_id: str
    actor_type: str = "system"
    actor_name: Optional[str] = None
    service_name: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass(frozen=True)
class OrchestrationAuditResource:
    resource_id: str
    resource_type: str
    resource_name: Optional[str] = None
    version: Optional[str] = None
    owner: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationAuditEvent:
    event_id: str
    occurred_at: datetime
    category: OrchestrationAuditCategory
    action: OrchestrationAuditAction
    severity: AuditSeverity
    outcome: AuditOutcome
    actor: OrchestrationAuditActor
    resource: OrchestrationAuditResource
    context: OrchestrationAuditContext
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None

    def canonical_payload(self) -> Dict[str, Any]:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["category"] = self.category.value
        data["action"] = self.action.value
        data["severity"] = self.severity.value
        data["outcome"] = self.outcome.value
        data.pop("event_hash", None)
        return data

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        data["category"] = self.category.value
        data["action"] = self.action.value
        data["severity"] = self.severity.value
        data["outcome"] = self.outcome.value
        return data


# =============================================================================
# Backends
# =============================================================================

class InMemoryOrchestrationAuditBackend:
    def __init__(self) -> None:
        self._events: List[OrchestrationAuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: OrchestrationAuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_events(self) -> List[OrchestrationAuditEvent]:
        with self._lock:
            return list(self._events)


class JsonlOrchestrationAuditBackend:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._memory = InMemoryOrchestrationAuditBackend()
        self._lock = threading.RLock()

    def append(self, event: OrchestrationAuditEvent) -> None:
        with self._lock:
            self._memory.append(event)

            with open(self.file_path, "a", encoding="utf-8") as file:
                file.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
                file.write("\n")

    def list_events(self) -> List[OrchestrationAuditEvent]:
        return self._memory.list_events()


# =============================================================================
# Audit Engine
# =============================================================================

class OrchestrationAuditEngine:
    def __init__(
        self,
        backend: Optional[AuditBackend] = None,
        enable_hash_chain: bool = True,
    ) -> None:
        self.backend = backend or InMemoryOrchestrationAuditBackend()
        self.enable_hash_chain = enable_hash_chain
        self._lock = threading.RLock()
        self._last_hash: Optional[str] = None

    def record(
        self,
        category: OrchestrationAuditCategory,
        action: OrchestrationAuditAction,
        actor: OrchestrationAuditActor,
        resource: OrchestrationAuditResource,
        message: str,
        context: Optional[OrchestrationAuditContext] = None,
        severity: AuditSeverity = AuditSeverity.INFO,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        details: Optional[Dict[str, Any]] = None,
    ) -> OrchestrationAuditEvent:
        with self._lock:
            previous_hash = self._last_hash if self.enable_hash_chain else None

            event = OrchestrationAuditEvent(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(timezone.utc),
                category=category,
                action=action,
                severity=severity,
                outcome=outcome,
                actor=actor,
                resource=resource,
                context=context or OrchestrationAuditContext(),
                message=message,
                details=details or {},
                previous_hash=previous_hash,
                event_hash=None,
            )

            event_hash = self._hash_event(event)

            final_event = OrchestrationAuditEvent(
                **{
                    **event.__dict__,
                    "event_hash": event_hash,
                }
            )

            self.backend.append(final_event)

            if self.enable_hash_chain:
                self._last_hash = event_hash

            logger.info(
                "orchestration_audit event_id=%s category=%s action=%s outcome=%s",
                final_event.event_id,
                final_event.category.value,
                final_event.action.value,
                final_event.outcome.value,
            )

            return final_event

    def query(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        actor_id: Optional[str] = None,
        category: Optional[OrchestrationAuditCategory] = None,
        action: Optional[OrchestrationAuditAction] = None,
        outcome: Optional[AuditOutcome] = None,
        severity: Optional[AuditSeverity] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        correlation_id: Optional[str] = None,
        run_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        dag_id: Optional[str] = None,
        task_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
    ) -> List[OrchestrationAuditEvent]:
        events = self.backend.list_events()

        if tenant_id is not None:
            events = [event for event in events if event.context.tenant_id == tenant_id]

        if domain is not None:
            events = [event for event in events if event.context.domain == domain]

        if actor_id is not None:
            events = [event for event in events if event.actor.actor_id == actor_id]

        if category is not None:
            events = [event for event in events if event.category == category]

        if action is not None:
            events = [event for event in events if event.action == action]

        if outcome is not None:
            events = [event for event in events if event.outcome == outcome]

        if severity is not None:
            events = [
                event for event in events
                if self._severity_rank(event.severity) >= self._severity_rank(severity)
            ]

        if resource_id is not None:
            events = [event for event in events if event.resource.resource_id == resource_id]

        if resource_type is not None:
            events = [event for event in events if event.resource.resource_type == resource_type]

        if correlation_id is not None:
            events = [event for event in events if event.context.correlation_id == correlation_id]

        if run_id is not None:
            events = [event for event in events if event.context.run_id == run_id]

        if workflow_id is not None:
            events = [event for event in events if event.context.workflow_id == workflow_id]

        if dag_id is not None:
            events = [event for event in events if event.context.dag_id == dag_id]

        if task_id is not None:
            events = [event for event in events if event.context.task_id == task_id]

        if worker_id is not None:
            events = [event for event in events if event.context.worker_id == worker_id]

        if from_time is not None:
            events = [event for event in events if event.occurred_at >= from_time]

        if to_time is not None:
            events = [event for event in events if event.occurred_at <= to_time]

        return sorted(events, key=lambda event: event.occurred_at)

    def verify_integrity(self) -> bool:
        events = self.backend.list_events()
        previous_hash: Optional[str] = None

        for event in events:
            if event.previous_hash != previous_hash:
                raise AuditIntegrityError(
                    f"previous_hash inválido no evento {event.event_id}"
                )

            event_without_hash = OrchestrationAuditEvent(
                **{
                    **event.__dict__,
                    "event_hash": None,
                }
            )

            calculated_hash = self._hash_event(event_without_hash)

            if calculated_hash != event.event_hash:
                raise AuditIntegrityError(
                    f"event_hash inválido no evento {event.event_id}"
                )

            previous_hash = event.event_hash

        return True

    def export_json(
        self,
        events: Optional[Iterable[OrchestrationAuditEvent]] = None,
    ) -> str:
        selected = list(events or self.backend.list_events())

        return json.dumps(
            [event.to_dict() for event in selected],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    def export_jsonl(
        self,
        events: Optional[Iterable[OrchestrationAuditEvent]] = None,
    ) -> str:
        selected = list(events or self.backend.list_events())

        return "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=False, default=str)
            for event in selected
        )

    def compliance_summary(
        self,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        events = self.query(
            tenant_id=tenant_id,
            from_time=from_time,
            to_time=to_time,
        )

        by_category: Dict[str, int] = {}
        by_outcome: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}

        for event in events:
            by_category[event.category.value] = by_category.get(event.category.value, 0) + 1
            by_outcome[event.outcome.value] = by_outcome.get(event.outcome.value, 0) + 1
            by_severity[event.severity.value] = by_severity.get(event.severity.value, 0) + 1

        failures = [
            event for event in events
            if event.outcome in {AuditOutcome.FAILURE, AuditOutcome.DENIED}
        ]

        critical = [
            event for event in events
            if event.severity == AuditSeverity.CRITICAL
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "from_time": from_time.isoformat() if from_time else None,
            "to_time": to_time.isoformat() if to_time else None,
            "total_events": len(events),
            "failures": len(failures),
            "critical_events": len(critical),
            "by_category": by_category,
            "by_outcome": by_outcome,
            "by_severity": by_severity,
            "integrity_verified": self.verify_integrity(),
        }

    @staticmethod
    def _hash_event(event: OrchestrationAuditEvent) -> str:
        payload = json.dumps(
            event.canonical_payload(),
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _severity_rank(severity: AuditSeverity) -> int:
        ranking = {
            AuditSeverity.DEBUG: 10,
            AuditSeverity.INFO: 20,
            AuditSeverity.WARNING: 30,
            AuditSeverity.ERROR: 40,
            AuditSeverity.CRITICAL: 50,
        }
        return ranking[severity]


# =============================================================================
# Convenience Logger
# =============================================================================

class OrchestrationAuditLogger:
    def __init__(self, engine: Optional[OrchestrationAuditEngine] = None) -> None:
        self.engine = engine or OrchestrationAuditEngine()

    def workflow_started(
        self,
        workflow_id: str,
        run_id: str,
        actor_id: str = "system",
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.WORKFLOW,
            action=OrchestrationAuditAction.STARTED,
            actor=OrchestrationAuditActor(actor_id=actor_id),
            resource=OrchestrationAuditResource(
                resource_id=workflow_id,
                resource_type="workflow",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                run_id=run_id,
            ),
            message=f"Workflow iniciado: {workflow_id}",
        )

    def workflow_failed(
        self,
        workflow_id: str,
        run_id: str,
        error: str,
        actor_id: str = "system",
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.WORKFLOW,
            action=OrchestrationAuditAction.FAILED,
            actor=OrchestrationAuditActor(actor_id=actor_id),
            resource=OrchestrationAuditResource(
                resource_id=workflow_id,
                resource_type="workflow",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                workflow_id=workflow_id,
                run_id=run_id,
            ),
            severity=AuditSeverity.ERROR,
            outcome=AuditOutcome.FAILURE,
            message=f"Workflow falhou: {workflow_id}",
            details={"error": error},
        )

    def dag_started(
        self,
        dag_id: str,
        run_id: str,
        actor_id: str = "system",
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.DAG,
            action=OrchestrationAuditAction.STARTED,
            actor=OrchestrationAuditActor(actor_id=actor_id),
            resource=OrchestrationAuditResource(
                resource_id=dag_id,
                resource_type="dag",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                dag_id=dag_id,
                run_id=run_id,
            ),
            message=f"DAG iniciado: {dag_id}",
        )

    def task_finished(
        self,
        task_id: str,
        run_id: str,
        success: bool,
        actor_id: str = "worker",
        worker_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.TASK,
            action=(
                OrchestrationAuditAction.FINISHED
                if success
                else OrchestrationAuditAction.FAILED
            ),
            actor=OrchestrationAuditActor(actor_id=actor_id, actor_type="worker"),
            resource=OrchestrationAuditResource(
                resource_id=task_id,
                resource_type="task",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                task_id=task_id,
                worker_id=worker_id,
                run_id=run_id,
            ),
            severity=AuditSeverity.INFO if success else AuditSeverity.ERROR,
            outcome=AuditOutcome.SUCCESS if success else AuditOutcome.FAILURE,
            message=(
                f"Task finalizada: {task_id}"
                if success
                else f"Task falhou: {task_id}"
            ),
            details={
                "duration_ms": duration_ms,
                "error": error,
            },
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        status: str,
        tenant_id: Optional[str] = None,
        active_tasks: Optional[int] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.WORKER,
            action=OrchestrationAuditAction.HEARTBEAT,
            actor=OrchestrationAuditActor(
                actor_id=worker_id,
                actor_type="worker",
            ),
            resource=OrchestrationAuditResource(
                resource_id=worker_id,
                resource_type="worker",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                worker_id=worker_id,
            ),
            severity=AuditSeverity.DEBUG,
            message=f"Heartbeat recebido do worker: {worker_id}",
            details={
                "status": status,
                "active_tasks": active_tasks,
            },
        )

    def schedule_triggered(
        self,
        scheduler_id: str,
        resource_id: str,
        run_id: str,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.SCHEDULER,
            action=OrchestrationAuditAction.TRIGGERED,
            actor=OrchestrationAuditActor(
                actor_id=scheduler_id,
                actor_type="scheduler",
            ),
            resource=OrchestrationAuditResource(
                resource_id=resource_id,
                resource_type="scheduled_resource",
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                scheduler_id=scheduler_id,
                run_id=run_id,
                correlation_id=correlation_id,
            ),
            message=f"Scheduler disparou execução: {resource_id}",
        )

    def access_denied(
        self,
        resource_id: str,
        resource_type: str,
        actor_id: str,
        reason: str,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> OrchestrationAuditEvent:
        return self.engine.record(
            category=OrchestrationAuditCategory.SECURITY,
            action=OrchestrationAuditAction.ACCESS_DENIED,
            actor=OrchestrationAuditActor(actor_id=actor_id, actor_type="user"),
            resource=OrchestrationAuditResource(
                resource_id=resource_id,
                resource_type=resource_type,
            ),
            context=OrchestrationAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            ),
            severity=AuditSeverity.WARNING,
            outcome=AuditOutcome.DENIED,
            message=f"Acesso negado ao recurso {resource_type}:{resource_id}",
            details={"reason": reason},
        )


# =============================================================================
# Factory
# =============================================================================

def create_default_orchestration_audit(
    jsonl_path: Optional[str] = None,
    enable_hash_chain: bool = True,
) -> OrchestrationAuditEngine:
    backend: AuditBackend

    if jsonl_path:
        backend = JsonlOrchestrationAuditBackend(jsonl_path)
    else:
        backend = InMemoryOrchestrationAuditBackend()

    return OrchestrationAuditEngine(
        backend=backend,
        enable_hash_chain=enable_hash_chain,
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_orchestration_audit()
    audit = OrchestrationAuditLogger(engine)

    audit.workflow_started(
        workflow_id="daily-sales-workflow",
        run_id="run-001",
        actor_id="scheduler",
        tenant_id="tenant-default",
        correlation_id="corr-orch-001",
    )

    audit.dag_started(
        dag_id="daily-sales-dag",
        run_id="run-001",
        actor_id="scheduler",
        tenant_id="tenant-default",
        correlation_id="corr-orch-001",
    )

    audit.task_finished(
        task_id="extract-sales",
        run_id="run-001",
        success=True,
        worker_id="worker-001",
        tenant_id="tenant-default",
        duration_ms=1200.5,
        correlation_id="corr-orch-001",
    )

    audit.task_finished(
        task_id="publish-sales",
        run_id="run-001",
        success=False,
        worker_id="worker-002",
        tenant_id="tenant-default",
        duration_ms=320.0,
        error="Timeout publishing dataset",
        correlation_id="corr-orch-001",
    )

    engine.verify_integrity()

    print(engine.export_json())

    print(
        json.dumps(
            engine.compliance_summary(tenant_id="tenant-default"),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    example_usage()