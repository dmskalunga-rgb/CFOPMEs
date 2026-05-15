"""
data/quality/quality_audit.py

Enterprise-grade Quality Audit module.

This module centralizes auditability for data quality operations, including
checks, profiling, validation runs, findings, remediation actions, approvals,
SLA violations, and evidence packages.

Main capabilities:
- Structured audit events for data quality processes
- Tamper-evident hash chain for audit logs
- Event severity and status tracking
- Evidence attachment metadata
- Run/session lifecycle auditing
- Finding/remediation/approval audit helpers
- In-memory and JSONL audit sinks
- Query/filter utilities
- Audit summary reporting
- Metrics sink integration
- Safe JSON serialization

Designed for enterprise data quality platforms, governance workflows,
compliance evidence, lakehouse quality gates, orchestration pipelines, and
regulated audit trails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class QualityAuditError(Exception):
    """Base exception for quality audit failures."""


class QualityAuditConfigurationError(QualityAuditError):
    """Raised when audit configuration is invalid."""


class QualityAuditWriteError(QualityAuditError):
    """Raised when an audit event cannot be written."""


class QualityAuditIntegrityError(QualityAuditError):
    """Raised when audit integrity verification fails."""


# =============================================================================
# Enums
# =============================================================================


class AuditSeverity(str, Enum):
    """Severity level for audit events."""

    DEBUG = "debug"
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditStatus(str, Enum):
    """Status of an audited operation."""

    STARTED = "started"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    ERROR = "error"


class AuditEventType(str, Enum):
    """Standard data quality audit event types."""

    QUALITY_RUN_STARTED = "quality_run_started"
    QUALITY_RUN_COMPLETED = "quality_run_completed"
    QUALITY_RUN_FAILED = "quality_run_failed"
    CHECK_STARTED = "check_started"
    CHECK_COMPLETED = "check_completed"
    CHECK_FAILED = "check_failed"
    PROFILING_STARTED = "profiling_started"
    PROFILING_COMPLETED = "profiling_completed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_COMPLETED = "validation_completed"
    FINDING_CREATED = "finding_created"
    FINDING_ACKNOWLEDGED = "finding_acknowledged"
    FINDING_RESOLVED = "finding_resolved"
    REMEDIATION_CREATED = "remediation_created"
    REMEDIATION_EXECUTED = "remediation_executed"
    REMEDIATION_FAILED = "remediation_failed"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    SLA_VIOLATION = "sla_violation"
    POLICY_VIOLATION = "policy_violation"
    CONTRACT_VIOLATION = "contract_violation"
    EVIDENCE_ATTACHED = "evidence_attached"
    CONFIG_CHANGED = "config_changed"
    ACCESS_GRANTED = "access_granted"
    ACCESS_DENIED = "access_denied"
    CUSTOM = "custom"


class EvidenceType(str, Enum):
    """Types of evidence attached to audit events."""

    REPORT = "report"
    DATA_SAMPLE = "data_sample"
    SQL_QUERY = "sql_query"
    CONFIG = "config"
    LOG = "log"
    METRIC = "metric"
    SCREENSHOT = "screenshot"
    FILE = "file"
    URI = "uri"
    CUSTOM = "custom"


class AuditActorType(str, Enum):
    """Actor category that produced an event."""

    SYSTEM = "system"
    USER = "user"
    SERVICE = "service"
    PIPELINE = "pipeline"
    SCHEDULER = "scheduler"
    API = "api"
    UNKNOWN = "unknown"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing audit metrics."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    """Protocol for audit event persistence."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


class AuditRepository(Protocol):
    """Protocol for queryable audit repositories."""

    def append(self, event: "QualityAuditEvent") -> None:
        ...

    def list_events(self) -> List["QualityAuditEvent"]:
        ...


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class AuditActor:
    """Actor responsible for an audit event."""

    actor_id: str = "system"
    actor_type: AuditActorType = AuditActorType.SYSTEM
    display_name: Optional[str] = None
    email: Optional[str] = None
    service_name: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["actor_type"] = self.actor_type.value
        return _json_safe(data)


@dataclass(frozen=True)
class QualityAuditContext:
    """Context shared by audit events in a quality run."""

    tenant_id: Optional[str] = None
    environment: str = "default"
    system: str = "data-quality"
    domain: Optional[str] = None
    dataset_name: Optional[str] = None
    dataset_version: Optional[str] = None
    pipeline_name: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    job_id: Optional[str] = None
    workflow_id: Optional[str] = None
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    source_system: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "QualityAuditContext":
        payload = asdict(self)
        payload.update(updates)
        if isinstance(payload.get("tags"), dict):
            payload["tags"] = dict(payload["tags"])
        if isinstance(payload.get("metadata"), dict):
            payload["metadata"] = dict(payload["metadata"])
        return QualityAuditContext(**payload)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class AuditEvidence:
    """Evidence metadata attached to an audit event."""

    evidence_id: str
    evidence_type: EvidenceType
    name: str
    uri: Optional[str] = None
    content_hash: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_payload(
        name: str,
        payload: Any,
        *,
        evidence_type: EvidenceType = EvidenceType.CUSTOM,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AuditEvidence":
        encoded = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return AuditEvidence(
            evidence_id=str(uuid.uuid4()),
            evidence_type=evidence_type,
            name=name,
            content_hash=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            description=description,
            metadata=metadata or {},
        )

    @staticmethod
    def from_uri(
        name: str,
        uri: str,
        *,
        evidence_type: EvidenceType = EvidenceType.URI,
        content_hash: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "AuditEvidence":
        return AuditEvidence(
            evidence_id=str(uuid.uuid4()),
            evidence_type=evidence_type,
            name=name,
            uri=uri,
            content_hash=content_hash,
            description=description,
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evidence_type"] = self.evidence_type.value
        return _json_safe(data)


@dataclass
class QualityAuditEvent:
    """Single immutable quality audit event."""

    event_id: str
    event_type: AuditEventType
    timestamp: str
    severity: AuditSeverity
    status: AuditStatus
    message: str
    actor: AuditActor
    context: QualityAuditContext
    payload: Dict[str, Any] = field(default_factory=dict)
    evidence: List[AuditEvidence] = field(default_factory=list)
    parent_event_id: Optional[str] = None
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None
    schema_version: str = "1.0"

    def canonical_payload(self, *, include_event_hash: bool = False) -> Dict[str, Any]:
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "actor": self.actor.to_dict(),
            "context": self.context.to_dict(),
            "payload": _json_safe(self.payload),
            "evidence": [item.to_dict() for item in self.evidence],
            "parent_event_id": self.parent_event_id,
            "previous_hash": self.previous_hash,
            "schema_version": self.schema_version,
        }
        if include_event_hash:
            data["event_hash"] = self.event_hash
        return data

    def compute_hash(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(include_event_hash=False),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def seal(self, previous_hash: Optional[str]) -> "QualityAuditEvent":
        self.previous_hash = previous_hash
        self.event_hash = self.compute_hash()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(self.canonical_payload(include_event_hash=True))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class AuditQuery:
    """Query filters for audit events."""

    dataset_name: Optional[str] = None
    event_types: Optional[Sequence[AuditEventType]] = None
    statuses: Optional[Sequence[AuditStatus]] = None
    severities: Optional[Sequence[AuditSeverity]] = None
    actor_id: Optional[str] = None
    correlation_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    limit: Optional[int] = None


@dataclass
class AuditSummary:
    """Aggregated audit summary."""

    summary_id: str
    generated_at: str
    total_events: int
    events_by_type: Dict[str, int]
    events_by_status: Dict[str, int]
    events_by_severity: Dict[str, int]
    datasets: List[str]
    first_event_at: Optional[str]
    last_event_at: Optional[str]
    failed_events: int
    critical_events: int
    integrity_verified: bool
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _counter(values: Iterable[str]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


# =============================================================================
# Sinks / Repositories
# =============================================================================


class NoopMetricsSink:
    """Default metrics sink that intentionally does nothing."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditRepository:
    """Thread-safe in-memory audit repository."""

    def __init__(self) -> None:
        self._events: List[QualityAuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: QualityAuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_events(self) -> List[QualityAuditEvent]:
        with self._lock:
            return list(self._events)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class JsonLinesAuditRepository:
    """Append-only JSONL audit repository with process-level locking."""

    def __init__(self, file_path: str | os.PathLike[str]) -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(self, event: QualityAuditEvent) -> None:
        try:
            with self._lock:
                with self.file_path.open("a", encoding="utf-8") as fp:
                    fp.write(event.to_json())
                    fp.write("\n")
        except Exception as exc:  # noqa: BLE001
            raise QualityAuditWriteError(f"Failed to write audit event to JSONL: {exc}") from exc

    def list_events(self) -> List[QualityAuditEvent]:
        if not self.file_path.exists():
            return []
        events: List[QualityAuditEvent] = []
        with self._lock:
            with self.file_path.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    events.append(event_from_dict(payload))
        return events


class RepositoryAuditSink:
    """Adapter exposing repository as a generic AuditSink."""

    def __init__(self, repository: AuditRepository) -> None:
        self.repository = repository
        self._previous_hash: Optional[str] = None
        existing = repository.list_events()
        if existing:
            self._previous_hash = existing[-1].event_hash

    def write_event(self, event: Mapping[str, Any]) -> None:
        if isinstance(event, QualityAuditEvent):
            audit_event = event
        else:
            audit_event = event_from_dict(dict(event))
        audit_event.seal(self._previous_hash)
        self.repository.append(audit_event)
        self._previous_hash = audit_event.event_hash


# =============================================================================
# Serialization
# =============================================================================


def event_from_dict(payload: Mapping[str, Any]) -> QualityAuditEvent:
    """Deserialize a QualityAuditEvent from a dictionary."""
    actor_payload = payload.get("actor") or {}
    context_payload = payload.get("context") or {}
    evidence_payload = payload.get("evidence") or []

    actor = AuditActor(
        actor_id=actor_payload.get("actor_id", "system"),
        actor_type=AuditActorType(actor_payload.get("actor_type", AuditActorType.SYSTEM.value)),
        display_name=actor_payload.get("display_name"),
        email=actor_payload.get("email"),
        service_name=actor_payload.get("service_name"),
        ip_address=actor_payload.get("ip_address"),
        user_agent=actor_payload.get("user_agent"),
        metadata=dict(actor_payload.get("metadata") or {}),
    )

    context = QualityAuditContext(
        tenant_id=context_payload.get("tenant_id"),
        environment=context_payload.get("environment", "default"),
        system=context_payload.get("system", "data-quality"),
        domain=context_payload.get("domain"),
        dataset_name=context_payload.get("dataset_name"),
        dataset_version=context_payload.get("dataset_version"),
        pipeline_name=context_payload.get("pipeline_name"),
        pipeline_run_id=context_payload.get("pipeline_run_id"),
        job_id=context_payload.get("job_id"),
        workflow_id=context_payload.get("workflow_id"),
        correlation_id=context_payload.get("correlation_id"),
        trace_id=context_payload.get("trace_id"),
        source_system=context_payload.get("source_system"),
        tags=dict(context_payload.get("tags") or {}),
        metadata=dict(context_payload.get("metadata") or {}),
    )

    evidence = [
        AuditEvidence(
            evidence_id=item.get("evidence_id", str(uuid.uuid4())),
            evidence_type=EvidenceType(item.get("evidence_type", EvidenceType.CUSTOM.value)),
            name=item.get("name", "evidence"),
            uri=item.get("uri"),
            content_hash=item.get("content_hash"),
            mime_type=item.get("mime_type"),
            size_bytes=item.get("size_bytes"),
            description=item.get("description"),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in evidence_payload
    ]

    return QualityAuditEvent(
        event_id=payload.get("event_id", str(uuid.uuid4())),
        event_type=AuditEventType(payload.get("event_type", AuditEventType.CUSTOM.value)),
        timestamp=payload.get("timestamp", utc_now_iso()),
        severity=AuditSeverity(payload.get("severity", AuditSeverity.INFO.value)),
        status=AuditStatus(payload.get("status", AuditStatus.RUNNING.value)),
        message=payload.get("message", ""),
        actor=actor,
        context=context,
        payload=dict(payload.get("payload") or {}),
        evidence=evidence,
        parent_event_id=payload.get("parent_event_id"),
        previous_hash=payload.get("previous_hash"),
        event_hash=payload.get("event_hash"),
        schema_version=payload.get("schema_version", "1.0"),
    )


# =============================================================================
# Auditor
# =============================================================================


class QualityAuditor:
    """
    Enterprise quality audit service.

    Example:
        repository = InMemoryAuditRepository()
        auditor = QualityAuditor(repository)
        run_id = auditor.start_quality_run(dataset_name="customers")
        auditor.audit_finding_created(...)
        auditor.complete_quality_run(run_id, status=AuditStatus.SUCCEEDED)
    """

    def __init__(
        self,
        repository: Optional[AuditRepository] = None,
        *,
        default_actor: Optional[AuditActor] = None,
        default_context: Optional[QualityAuditContext] = None,
        metrics_sink: Optional[MetricsSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryAuditRepository()
        self.default_actor = default_actor or AuditActor()
        self.default_context = default_context or QualityAuditContext()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.logger = logger_ or logger
        self._lock = threading.RLock()
        self._previous_hash = self._load_previous_hash()

    def _load_previous_hash(self) -> Optional[str]:
        events = self.repository.list_events()
        if not events:
            return None
        return events[-1].event_hash

    def emit(
        self,
        event_type: AuditEventType,
        message: str,
        *,
        severity: AuditSeverity = AuditSeverity.INFO,
        status: AuditStatus = AuditStatus.RUNNING,
        actor: Optional[AuditActor] = None,
        context: Optional[QualityAuditContext] = None,
        payload: Optional[Dict[str, Any]] = None,
        evidence: Optional[Sequence[AuditEvidence]] = None,
        parent_event_id: Optional[str] = None,
    ) -> QualityAuditEvent:
        """Create, seal, persist, and return an audit event."""
        started = time.perf_counter()
        audit_event = QualityAuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=utc_now_iso(),
            severity=severity,
            status=status,
            message=message,
            actor=actor or self.default_actor,
            context=context or self.default_context,
            payload=_json_safe(payload or {}),
            evidence=list(evidence or []),
            parent_event_id=parent_event_id,
        )

        with self._lock:
            audit_event.seal(self._previous_hash)
            self.repository.append(audit_event)
            self._previous_hash = audit_event.event_hash

        duration_ms = (time.perf_counter() - started) * 1000
        self._publish_event_metrics(audit_event, duration_ms)
        self.logger.debug(
            "Audit event emitted type=%s status=%s severity=%s event_id=%s",
            audit_event.event_type.value,
            audit_event.status.value,
            audit_event.severity.value,
            audit_event.event_id,
        )
        return audit_event

    def start_quality_run(
        self,
        *,
        dataset_name: str,
        run_id: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Audit the beginning of a quality run and return its run id."""
        run_id = run_id or str(uuid.uuid4())
        base_context = context or self.default_context
        run_context = base_context.with_updates(dataset_name=dataset_name, correlation_id=base_context.correlation_id or run_id)
        event = self.emit(
            AuditEventType.QUALITY_RUN_STARTED,
            f"Quality run started for dataset '{dataset_name}'.",
            severity=AuditSeverity.INFO,
            status=AuditStatus.STARTED,
            actor=actor,
            context=run_context,
            payload={"run_id": run_id, **(payload or {})},
        )
        return run_id

    def complete_quality_run(
        self,
        run_id: str,
        *,
        dataset_name: Optional[str] = None,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        quality_score: Optional[float] = None,
        summary: Optional[Mapping[str, Any]] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        evidence: Optional[Sequence[AuditEvidence]] = None,
    ) -> QualityAuditEvent:
        """Audit the completion of a quality run."""
        event_type = AuditEventType.QUALITY_RUN_COMPLETED if status != AuditStatus.FAILED else AuditEventType.QUALITY_RUN_FAILED
        severity = AuditSeverity.INFO if status == AuditStatus.SUCCEEDED else AuditSeverity.HIGH
        base_context = context or self.default_context
        if dataset_name:
            base_context = base_context.with_updates(dataset_name=dataset_name)
        return self.emit(
            event_type,
            f"Quality run completed with status '{status.value}'.",
            severity=severity,
            status=status,
            actor=actor,
            context=base_context,
            payload={"run_id": run_id, "quality_score": quality_score, "summary": _json_safe(summary or {})},
            evidence=evidence,
        )

    def audit_check_started(
        self,
        check_name: str,
        *,
        check_type: Optional[str] = None,
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.CHECK_STARTED,
            f"Quality check started: {check_name}.",
            status=AuditStatus.STARTED,
            actor=actor,
            context=ctx,
            payload={"check_name": check_name, "check_type": check_type},
        )

    def audit_check_completed(
        self,
        check_name: str,
        *,
        status: AuditStatus,
        score: Optional[float] = None,
        result: Optional[Mapping[str, Any]] = None,
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        parent_event_id: Optional[str] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        event_type = AuditEventType.CHECK_COMPLETED if status != AuditStatus.FAILED else AuditEventType.CHECK_FAILED
        severity = AuditSeverity.INFO if status == AuditStatus.SUCCEEDED else AuditSeverity.HIGH
        return self.emit(
            event_type,
            f"Quality check completed: {check_name}.",
            severity=severity,
            status=status,
            actor=actor,
            context=ctx,
            payload={"check_name": check_name, "score": score, "result": _json_safe(result or {})},
            parent_event_id=parent_event_id,
        )

    def audit_finding_created(
        self,
        finding_id: str,
        message: str,
        *,
        severity: AuditSeverity,
        dataset_name: Optional[str] = None,
        rule_name: Optional[str] = None,
        column: Optional[str] = None,
        metric_name: Optional[str] = None,
        metric_value: Optional[float] = None,
        threshold: Optional[float] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        evidence: Optional[Sequence[AuditEvidence]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.FINDING_CREATED,
            message,
            severity=severity,
            status=AuditStatus.WARNING if severity in {AuditSeverity.LOW, AuditSeverity.MEDIUM} else AuditStatus.FAILED,
            actor=actor,
            context=ctx,
            payload={
                "finding_id": finding_id,
                "rule_name": rule_name,
                "column": column,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "threshold": threshold,
                "metadata": metadata or {},
            },
            evidence=evidence,
        )

    def audit_finding_resolved(
        self,
        finding_id: str,
        *,
        resolution: str,
        resolver: Optional[AuditActor] = None,
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        evidence: Optional[Sequence[AuditEvidence]] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.FINDING_RESOLVED,
            f"Finding resolved: {finding_id}.",
            severity=AuditSeverity.INFO,
            status=AuditStatus.SUCCEEDED,
            actor=resolver,
            context=ctx,
            payload={"finding_id": finding_id, "resolution": resolution},
            evidence=evidence,
        )

    def audit_policy_violation(
        self,
        policy_name: str,
        message: str,
        *,
        severity: AuditSeverity = AuditSeverity.HIGH,
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.POLICY_VIOLATION,
            message,
            severity=severity,
            status=AuditStatus.FAILED,
            actor=actor,
            context=ctx,
            payload={"policy_name": policy_name, **(payload or {})},
        )

    def audit_sla_violation(
        self,
        sla_name: str,
        message: str,
        *,
        observed_value: Optional[Any] = None,
        expected_value: Optional[Any] = None,
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.SLA_VIOLATION,
            message,
            severity=AuditSeverity.HIGH,
            status=AuditStatus.FAILED,
            actor=actor,
            context=ctx,
            payload={"sla_name": sla_name, "observed_value": observed_value, "expected_value": expected_value},
        )

    def audit_evidence_attached(
        self,
        evidence: Sequence[AuditEvidence],
        *,
        message: str = "Evidence attached to quality audit.",
        dataset_name: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        parent_event_id: Optional[str] = None,
    ) -> QualityAuditEvent:
        ctx = context or self.default_context
        if dataset_name:
            ctx = ctx.with_updates(dataset_name=dataset_name)
        return self.emit(
            AuditEventType.EVIDENCE_ATTACHED,
            message,
            severity=AuditSeverity.INFO,
            status=AuditStatus.SUCCEEDED,
            actor=actor,
            context=ctx,
            payload={"evidence_count": len(evidence)},
            evidence=evidence,
            parent_event_id=parent_event_id,
        )

    def query(self, query: Optional[AuditQuery] = None) -> List[QualityAuditEvent]:
        """Query events from the repository."""
        query = query or AuditQuery()
        events = self.repository.list_events()
        started_at = _parse_iso(query.started_at)
        finished_at = _parse_iso(query.finished_at)
        event_types = {item.value for item in query.event_types} if query.event_types else None
        statuses = {item.value for item in query.statuses} if query.statuses else None
        severities = {item.value for item in query.severities} if query.severities else None

        filtered: List[QualityAuditEvent] = []
        for event in events:
            event_dt = _parse_iso(event.timestamp)
            if query.dataset_name and event.context.dataset_name != query.dataset_name:
                continue
            if event_types and event.event_type.value not in event_types:
                continue
            if statuses and event.status.value not in statuses:
                continue
            if severities and event.severity.value not in severities:
                continue
            if query.actor_id and event.actor.actor_id != query.actor_id:
                continue
            if query.correlation_id and event.context.correlation_id != query.correlation_id:
                continue
            if query.pipeline_run_id and event.context.pipeline_run_id != query.pipeline_run_id:
                continue
            if started_at and event_dt and event_dt < started_at:
                continue
            if finished_at and event_dt and event_dt > finished_at:
                continue
            if query.tags:
                if any(event.context.tags.get(k) != v for k, v in query.tags.items()):
                    continue
            filtered.append(event)

        filtered.sort(key=lambda e: e.timestamp)
        if query.limit is not None:
            return filtered[: query.limit]
        return filtered

    def verify_integrity(self, events: Optional[Sequence[QualityAuditEvent]] = None) -> bool:
        """Verify hash chain integrity for audit events."""
        events = list(events or self.repository.list_events())
        previous_hash: Optional[str] = None
        for event in events:
            if event.previous_hash != previous_hash:
                return False
            expected_hash = event.compute_hash()
            if event.event_hash != expected_hash:
                return False
            previous_hash = event.event_hash
        return True

    def assert_integrity(self, events: Optional[Sequence[QualityAuditEvent]] = None) -> None:
        if not self.verify_integrity(events):
            raise QualityAuditIntegrityError("Audit hash-chain integrity verification failed.")

    def summarize(self, query: Optional[AuditQuery] = None) -> AuditSummary:
        """Generate an aggregated audit summary."""
        events = self.query(query)
        timestamps = [event.timestamp for event in events]
        datasets = sorted({event.context.dataset_name for event in events if event.context.dataset_name})
        return AuditSummary(
            summary_id=str(uuid.uuid4()),
            generated_at=utc_now_iso(),
            total_events=len(events),
            events_by_type=_counter(event.event_type.value for event in events),
            events_by_status=_counter(event.status.value for event in events),
            events_by_severity=_counter(event.severity.value for event in events),
            datasets=datasets,
            first_event_at=min(timestamps) if timestamps else None,
            last_event_at=max(timestamps) if timestamps else None,
            failed_events=sum(1 for event in events if event.status in {AuditStatus.FAILED, AuditStatus.ERROR}),
            critical_events=sum(1 for event in events if event.severity == AuditSeverity.CRITICAL),
            integrity_verified=self.verify_integrity(events),
            metadata={"query_applied": query is not None},
        )

    def _publish_event_metrics(self, event: QualityAuditEvent, duration_ms: float) -> None:
        tags = {
            "event_type": event.event_type.value,
            "status": event.status.value,
            "severity": event.severity.value,
            "dataset": event.context.dataset_name or "unknown",
            "environment": event.context.environment,
        }
        self.metrics_sink.increment("data_quality.audit.event_written", tags=tags)
        self.metrics_sink.timing("data_quality.audit.write_duration_ms", duration_ms, tags=tags)


# =============================================================================
# Context Manager
# =============================================================================


class QualityAuditRun:
    """Context manager for auditing quality run lifecycle."""

    def __init__(
        self,
        auditor: QualityAuditor,
        *,
        dataset_name: str,
        run_id: Optional[str] = None,
        context: Optional[QualityAuditContext] = None,
        actor: Optional[AuditActor] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.auditor = auditor
        self.dataset_name = dataset_name
        self.run_id = run_id or str(uuid.uuid4())
        self.context = context
        self.actor = actor
        self.payload = payload or {}
        self.started_at: Optional[float] = None

    def __enter__(self) -> "QualityAuditRun":
        self.started_at = time.perf_counter()
        self.auditor.start_quality_run(
            dataset_name=self.dataset_name,
            run_id=self.run_id,
            context=self.context,
            actor=self.actor,
            payload=self.payload,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        duration_ms = None
        if self.started_at is not None:
            duration_ms = (time.perf_counter() - self.started_at) * 1000
        if exc is None:
            self.auditor.complete_quality_run(
                self.run_id,
                dataset_name=self.dataset_name,
                status=AuditStatus.SUCCEEDED,
                summary={"duration_ms": duration_ms},
                context=self.context,
                actor=self.actor,
            )
            return False
        self.auditor.complete_quality_run(
            self.run_id,
            dataset_name=self.dataset_name,
            status=AuditStatus.FAILED,
            summary={
                "duration_ms": duration_ms,
                "exception_type": getattr(exc_type, "__name__", str(exc_type)),
                "exception_message": str(exc),
            },
            context=self.context,
            actor=self.actor,
        )
        return False


# =============================================================================
# Convenience API
# =============================================================================


def create_jsonl_auditor(
    file_path: str | os.PathLike[str],
    *,
    actor: Optional[AuditActor] = None,
    context: Optional[QualityAuditContext] = None,
) -> QualityAuditor:
    """Create a QualityAuditor backed by an append-only JSONL file."""
    repository = JsonLinesAuditRepository(file_path)
    return QualityAuditor(repository, default_actor=actor, default_context=context)


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    repository = InMemoryAuditRepository()
    auditor = QualityAuditor(
        repository,
        default_actor=AuditActor(
            actor_id="quality-service",
            actor_type=AuditActorType.SERVICE,
            service_name="quality-engine",
        ),
        default_context=QualityAuditContext(
            environment="dev",
            system="data-platform",
            pipeline_name="customer-quality-pipeline",
            tags={"domain": "customers"},
        ),
    )

    with QualityAuditRun(auditor, dataset_name="customers") as run:
        check_started = auditor.audit_check_started(
            "customer_id_completeness",
            check_type="completeness",
            dataset_name="customers",
        )
        auditor.audit_finding_created(
            finding_id="finding_customer_id_nulls",
            message="Critical customer_id field contains null values.",
            severity=AuditSeverity.CRITICAL,
            dataset_name="customers",
            rule_name="customer_id_required",
            column="customer_id",
            metric_name="null_rate",
            metric_value=0.01,
            threshold=0.0,
            parent_event_id=check_started.event_id if hasattr(check_started, "event_id") else None,
        )
        auditor.audit_check_completed(
            "customer_id_completeness",
            status=AuditStatus.FAILED,
            score=0.99,
            dataset_name="customers",
            parent_event_id=check_started.event_id,
        )

    summary = auditor.summarize()
    print(summary.to_json())
    print("Integrity verified:", auditor.verify_integrity())
