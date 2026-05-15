"""
data/analytics/analytics_audit.py

Enterprise Analytics Audit Engine.

Recursos:
- Auditoria de métricas, dashboards, queries, pipelines e modelos analíticos
- Eventos estruturados
- Hash chain para trilha imutável
- Multi-tenant
- Severidade e categorias
- Correlação por request/pipeline/job
- Backends plugáveis
- Exportação JSON/JSONL
- Consultas por período, tenant, ator, recurso e categoria
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


class AuditCategory(str, Enum):
    METRIC = "metric"
    DASHBOARD = "dashboard"
    QUERY = "query"
    DATASET = "dataset"
    PIPELINE = "pipeline"
    MODEL = "model"
    ACCESS = "access"
    GOVERNANCE = "governance"
    SECURITY = "security"
    QUALITY = "quality"
    EXPERIMENT = "experiment"
    SYSTEM = "system"


class AuditAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    READ = "read"
    EXECUTED = "executed"
    FAILED = "failed"
    VALIDATED = "validated"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACCESSED = "accessed"
    EXPORTED = "exported"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


# =============================================================================
# Exceptions
# =============================================================================

class AnalyticsAuditError(Exception):
    """Erro base de auditoria analítica."""


class AuditEventNotFound(AnalyticsAuditError):
    """Evento não encontrado."""


class AuditIntegrityError(AnalyticsAuditError):
    """Falha de integridade na cadeia de auditoria."""


# =============================================================================
# Protocols
# =============================================================================

class AuditBackend(Protocol):
    def append(self, event: "AnalyticsAuditEvent") -> None:
        ...

    def list_events(self) -> List["AnalyticsAuditEvent"]:
        ...


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class AnalyticsAuditContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    correlation_id: Optional[str] = None
    request_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    job_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass(frozen=True)
class AnalyticsAuditActor:
    actor_id: str
    actor_type: str = "user"
    actor_name: Optional[str] = None
    service_name: Optional[str] = None
    roles: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalyticsAuditResource:
    resource_id: str
    resource_type: str
    resource_name: Optional[str] = None
    dataset: Optional[str] = None
    metric_id: Optional[str] = None
    dashboard_id: Optional[str] = None
    model_id: Optional[str] = None
    version: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalyticsAuditEvent:
    event_id: str
    occurred_at: datetime
    category: AuditCategory
    action: AuditAction
    severity: AuditSeverity
    outcome: AuditOutcome
    actor: AnalyticsAuditActor
    resource: AnalyticsAuditResource
    context: AnalyticsAuditContext
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

class InMemoryAuditBackend:
    def __init__(self) -> None:
        self._events: List[AnalyticsAuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: AnalyticsAuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_events(self) -> List[AnalyticsAuditEvent]:
        with self._lock:
            return list(self._events)


class JsonlAuditBackend:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._memory = InMemoryAuditBackend()
        self._lock = threading.RLock()

    def append(self, event: AnalyticsAuditEvent) -> None:
        with self._lock:
            self._memory.append(event)
            with open(self.file_path, "a", encoding="utf-8") as file:
                file.write(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
                file.write("\n")

    def list_events(self) -> List[AnalyticsAuditEvent]:
        return self._memory.list_events()


# =============================================================================
# Audit Engine
# =============================================================================

class AnalyticsAuditEngine:
    def __init__(
        self,
        backend: Optional[AuditBackend] = None,
        enable_hash_chain: bool = True,
    ) -> None:
        self.backend = backend or InMemoryAuditBackend()
        self.enable_hash_chain = enable_hash_chain
        self._lock = threading.RLock()
        self._last_hash: Optional[str] = None

    def record(
        self,
        category: AuditCategory,
        action: AuditAction,
        actor: AnalyticsAuditActor,
        resource: AnalyticsAuditResource,
        message: str,
        context: Optional[AnalyticsAuditContext] = None,
        severity: AuditSeverity = AuditSeverity.INFO,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        details: Optional[Dict[str, Any]] = None,
    ) -> AnalyticsAuditEvent:
        with self._lock:
            previous_hash = self._last_hash if self.enable_hash_chain else None

            event = AnalyticsAuditEvent(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(timezone.utc),
                category=category,
                action=action,
                severity=severity,
                outcome=outcome,
                actor=actor,
                resource=resource,
                context=context or AnalyticsAuditContext(),
                message=message,
                details=details or {},
                previous_hash=previous_hash,
                event_hash=None,
            )

            event_hash = self._hash_event(event)

            final_event = AnalyticsAuditEvent(
                **{
                    **event.__dict__,
                    "event_hash": event_hash,
                }
            )

            self.backend.append(final_event)

            if self.enable_hash_chain:
                self._last_hash = event_hash

            logger.info(
                "analytics_audit event_id=%s category=%s action=%s outcome=%s",
                final_event.event_id,
                final_event.category.value,
                final_event.action.value,
                final_event.outcome.value,
            )

            return final_event

    def query(
        self,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        category: Optional[AuditCategory] = None,
        action: Optional[AuditAction] = None,
        outcome: Optional[AuditOutcome] = None,
        resource_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        from_time: Optional[datetime] = None,
        to_time: Optional[datetime] = None,
        min_severity: Optional[AuditSeverity] = None,
    ) -> List[AnalyticsAuditEvent]:
        events = self.backend.list_events()

        if tenant_id is not None:
            events = [e for e in events if e.context.tenant_id == tenant_id]

        if actor_id is not None:
            events = [e for e in events if e.actor.actor_id == actor_id]

        if category is not None:
            events = [e for e in events if e.category == category]

        if action is not None:
            events = [e for e in events if e.action == action]

        if outcome is not None:
            events = [e for e in events if e.outcome == outcome]

        if resource_id is not None:
            events = [e for e in events if e.resource.resource_id == resource_id]

        if resource_type is not None:
            events = [e for e in events if e.resource.resource_type == resource_type]

        if from_time is not None:
            events = [e for e in events if e.occurred_at >= from_time]

        if to_time is not None:
            events = [e for e in events if e.occurred_at <= to_time]

        if min_severity is not None:
            events = [
                e for e in events
                if self._severity_rank(e.severity) >= self._severity_rank(min_severity)
            ]

        return events

    def verify_integrity(self) -> bool:
        events = self.backend.list_events()

        previous_hash: Optional[str] = None

        for event in events:
            if event.previous_hash != previous_hash:
                raise AuditIntegrityError(
                    f"previous_hash inválido no evento {event.event_id}"
                )

            calculated_hash = self._hash_event(
                AnalyticsAuditEvent(
                    **{
                        **event.__dict__,
                        "event_hash": None,
                    }
                )
            )

            if calculated_hash != event.event_hash:
                raise AuditIntegrityError(
                    f"event_hash inválido no evento {event.event_id}"
                )

            previous_hash = event.event_hash

        return True

    def export_jsonl(self, events: Optional[Iterable[AnalyticsAuditEvent]] = None) -> str:
        selected = list(events or self.backend.list_events())
        return "\n".join(
            json.dumps(event.to_dict(), ensure_ascii=False, default=str)
            for event in selected
        )

    def export_json(self, events: Optional[Iterable[AnalyticsAuditEvent]] = None) -> str:
        selected = list(events or self.backend.list_events())
        return json.dumps(
            [event.to_dict() for event in selected],
            ensure_ascii=False,
            indent=2,
            default=str,
        )

    @staticmethod
    def _hash_event(event: AnalyticsAuditEvent) -> str:
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
# Convenience Facade
# =============================================================================

class AnalyticsAuditLogger:
    def __init__(self, engine: Optional[AnalyticsAuditEngine] = None) -> None:
        self.engine = engine or AnalyticsAuditEngine()

    def metric_executed(
        self,
        metric_id: str,
        actor_id: str,
        tenant_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        row_count: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> AnalyticsAuditEvent:
        return self.engine.record(
            category=AuditCategory.METRIC,
            action=AuditAction.EXECUTED,
            actor=AnalyticsAuditActor(actor_id=actor_id),
            resource=AnalyticsAuditResource(
                resource_id=metric_id,
                resource_type="metric",
                metric_id=metric_id,
            ),
            context=AnalyticsAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            ),
            message=f"Métrica executada: {metric_id}",
            details={
                "duration_ms": duration_ms,
                "row_count": row_count,
            },
        )

    def query_failed(
        self,
        query_id: str,
        actor_id: str,
        error: str,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AnalyticsAuditEvent:
        return self.engine.record(
            category=AuditCategory.QUERY,
            action=AuditAction.FAILED,
            actor=AnalyticsAuditActor(actor_id=actor_id),
            resource=AnalyticsAuditResource(
                resource_id=query_id,
                resource_type="query",
            ),
            context=AnalyticsAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            ),
            severity=AuditSeverity.ERROR,
            outcome=AuditOutcome.FAILURE,
            message=f"Falha na query: {query_id}",
            details={"error": error},
        )

    def dashboard_accessed(
        self,
        dashboard_id: str,
        actor_id: str,
        tenant_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AnalyticsAuditEvent:
        return self.engine.record(
            category=AuditCategory.DASHBOARD,
            action=AuditAction.ACCESSED,
            actor=AnalyticsAuditActor(actor_id=actor_id),
            resource=AnalyticsAuditResource(
                resource_id=dashboard_id,
                resource_type="dashboard",
                dashboard_id=dashboard_id,
            ),
            context=AnalyticsAuditContext(
                tenant_id=tenant_id,
                ip_address=ip_address,
                correlation_id=correlation_id,
            ),
            message=f"Dashboard acessado: {dashboard_id}",
        )

    def dataset_exported(
        self,
        dataset_name: str,
        actor_id: str,
        tenant_id: Optional[str] = None,
        row_count: Optional[int] = None,
        destination: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AnalyticsAuditEvent:
        return self.engine.record(
            category=AuditCategory.DATASET,
            action=AuditAction.EXPORTED,
            actor=AnalyticsAuditActor(actor_id=actor_id),
            resource=AnalyticsAuditResource(
                resource_id=dataset_name,
                resource_type="dataset",
                dataset=dataset_name,
            ),
            context=AnalyticsAuditContext(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            ),
            severity=AuditSeverity.WARNING,
            message=f"Dataset exportado: {dataset_name}",
            details={
                "row_count": row_count,
                "destination": destination,
            },
        )


# =============================================================================
# Factory
# =============================================================================

def create_default_audit_engine(
    jsonl_path: Optional[str] = None,
    enable_hash_chain: bool = True,
) -> AnalyticsAuditEngine:
    backend: AuditBackend

    if jsonl_path:
        backend = JsonlAuditBackend(jsonl_path)
    else:
        backend = InMemoryAuditBackend()

    return AnalyticsAuditEngine(
        backend=backend,
        enable_hash_chain=enable_hash_chain,
    )


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    engine = create_default_audit_engine()
    audit = AnalyticsAuditLogger(engine)

    audit.metric_executed(
        metric_id="gross_revenue",
        actor_id="analyst-001",
        tenant_id="tenant-a",
        duration_ms=125.4,
        row_count=10000,
        correlation_id="corr-123",
    )

    audit.dashboard_accessed(
        dashboard_id="sales-executive-dashboard",
        actor_id="manager-001",
        tenant_id="tenant-a",
        ip_address="10.0.0.15",
    )

    audit.query_failed(
        query_id="query-789",
        actor_id="analyst-001",
        tenant_id="tenant-a",
        error="Timeout while scanning partition",
    )

    engine.verify_integrity()

    print(engine.export_json())


if __name__ == "__main__":
    example_usage()