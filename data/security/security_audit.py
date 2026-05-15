"""
data/security/security_audit.py

Enterprise-grade security audit module for Python services, data platforms,
APIs, pipelines, workers and internal governance tooling.

Core capabilities:
- Centralized security audit events
- Immutable-style append-only audit trail abstraction
- Tamper-evident hash chaining
- Structured event taxonomy
- Redaction of sensitive fields
- Event validation and normalization
- Query/filter helpers
- Correlation by request/correlation/session/user/tenant
- Batch ingestion
- Export to JSON/JSONL
- Metrics and summaries
- Pluggable repositories and sinks
- In-memory repository for local development/tests
- Fail-closed/fail-open behavior configuration

Production recommendations:
- Persist audit events in append-only storage or WORM-capable infrastructure.
- Forward audit events to SIEM/SOC platforms.
- Protect audit repositories with strict access control.
- Keep raw secrets, tokens, passwords and private keys out of audit payloads.
- Use hash chaining plus external timestamping/signing for stronger evidence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class SecurityAuditError(Exception):
    """Base security audit error."""


class AuditValidationError(SecurityAuditError):
    """Raised when an audit event is invalid."""


class AuditRepositoryError(SecurityAuditError):
    """Raised when repository operations fail."""


class AuditSinkError(SecurityAuditError):
    """Raised when a sink fails."""


class AuditIntegrityError(SecurityAuditError):
    """Raised when hash chain integrity verification fails."""


class AuditExportError(SecurityAuditError):
    """Raised when audit export fails."""


# =============================================================================
# Enums and configuration
# =============================================================================


class AuditCategory(str, Enum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RBAC = "rbac"
    ABAC = "abac"
    ENCRYPTION = "encryption"
    DECRYPTION = "decryption"
    KEY_MANAGEMENT = "key_management"
    SECRET_MANAGEMENT = "secret_management"
    INTRUSION_DETECTION = "intrusion_detection"
    DATA_ACCESS = "data_access"
    ADMIN_ACTIVITY = "admin_activity"
    CONFIGURATION = "configuration"
    COMPLIANCE = "compliance"
    SYSTEM = "system"
    CUSTOM = "custom"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    ERROR = "error"
    UNKNOWN = "unknown"


class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditAction(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    AUTHORIZE = "authorize"
    ENCRYPT = "encrypt"
    DECRYPT = "decrypt"
    ROTATE = "rotate"
    EXPORT = "export"
    IMPORT = "import"
    ENABLE = "enable"
    DISABLE = "disable"
    ALERT = "alert"
    VALIDATE = "validate"
    EXECUTE = "execute"
    CUSTOM = "custom"


class AuditFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True)
class SecurityAuditConfig:
    """Runtime configuration for audit behavior."""

    enabled: bool = True
    fail_closed: bool = False
    redact_sensitive_fields: bool = True
    enable_hash_chain: bool = True
    require_event_id: bool = True
    max_metadata_bytes: int = 512 * 1024
    max_events_in_memory: int = 500_000
    default_source: str = "security-audit"
    emit_to_sinks: bool = True
    repository_batch_size: int = 1_000
    sensitive_field_terms: Tuple[str, ...] = (
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "session_cookie",
        "cookie",
        "plaintext",
        "ciphertext",
        "key_material",
        "refresh_token",
        "access_token",
    )


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class AuditActor:
    """Actor responsible for an audited action."""

    actor_id: Optional[str] = None
    actor_type: str = "user"
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = dataclasses.asdict(self)
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class AuditResource:
    """Resource targeted by an audited action."""

    resource_id: Optional[str] = None
    resource_type: Optional[str] = None
    name: Optional[str] = None
    tenant_id: Optional[str] = None
    classification: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def canonical(self) -> str:
        return f"{self.resource_type or 'resource'}:{self.resource_id or self.name or '*'}"

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = dataclasses.asdict(self)
        return redact_sensitive(data) if redact else data


@dataclass(frozen=True)
class SecurityAuditEvent:
    """Normalized structured security audit event."""

    event_id: str
    category: AuditCategory
    action: AuditAction
    outcome: AuditOutcome
    severity: AuditSeverity = AuditSeverity.INFO
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "security-audit"
    actor: AuditActor = field(default_factory=AuditActor)
    resource: AuditResource = field(default_factory=AuditResource)
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    event_type: Optional[str] = None
    risk_score: float = 0.0
    tags: Tuple[str, ...] = ()
    metadata: JsonDict = field(default_factory=dict)
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None

    def validate(self, config: Optional[SecurityAuditConfig] = None) -> None:
        config = config or SecurityAuditConfig()
        if config.require_event_id and not self.event_id:
            raise AuditValidationError("event_id is required.")
        if not self.message:
            raise AuditValidationError("message is required.")
        metadata_size = len(json.dumps(self.metadata, default=str).encode("utf-8"))
        if metadata_size > config.max_metadata_bytes:
            raise AuditValidationError("metadata exceeds max_metadata_bytes.")

    def canonical_payload(self, include_hashes: bool = False, redact: bool = False) -> JsonDict:
        payload = {
            "event_id": self.event_id,
            "category": self.category.value,
            "action": self.action.value,
            "outcome": self.outcome.value,
            "severity": self.severity.value,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "actor": self.actor.to_dict(redact=redact),
            "resource": self.resource.to_dict(redact=redact),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "event_type": self.event_type,
            "risk_score": self.risk_score,
            "tags": list(self.tags),
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
        }
        if include_hashes:
            payload["previous_hash"] = self.previous_hash
            payload["event_hash"] = self.event_hash
        return payload

    def to_dict(self, redact: bool = True) -> JsonDict:
        return self.canonical_payload(include_hashes=True, redact=redact)

    def compute_hash(self, previous_hash: Optional[str] = None) -> str:
        payload = self.canonical_payload(include_hashes=False, redact=False)
        payload["previous_hash"] = previous_hash
        raw = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def with_hashes(self, previous_hash: Optional[str]) -> "SecurityAuditEvent":
        event_hash = self.compute_hash(previous_hash)
        return dataclasses.replace(self, previous_hash=previous_hash, event_hash=event_hash)


@dataclass(frozen=True)
class AuditQuery:
    """Query object for searching audit events."""

    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    categories: Tuple[AuditCategory, ...] = ()
    actions: Tuple[AuditAction, ...] = ()
    outcomes: Tuple[AuditOutcome, ...] = ()
    severities: Tuple[AuditSeverity, ...] = ()
    actor_id: Optional[str] = None
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    session_id: Optional[str] = None
    tags_any: Tuple[str, ...] = ()
    tags_all: Tuple[str, ...] = ()
    text: Optional[str] = None
    min_risk_score: Optional[float] = None
    limit: Optional[int] = 1_000


@dataclass(frozen=True)
class AuditSummary:
    """Aggregated audit summary."""

    total_events: int
    by_category: JsonDict
    by_outcome: JsonDict
    by_severity: JsonDict
    by_action: JsonDict
    top_actors: JsonDict
    top_resources: JsonDict
    start_time: Optional[datetime]
    end_time: Optional[datetime]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "total_events": self.total_events,
            "by_category": self.by_category,
            "by_outcome": self.by_outcome,
            "by_severity": self.by_severity,
            "by_action": self.by_action,
            "top_actors": self.top_actors,
            "top_resources": self.top_resources,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class IntegrityVerificationResult:
    """Result of verifying audit hash chain integrity."""

    valid: bool
    checked_events: int
    first_invalid_event_id: Optional[str] = None
    reason: str = ""
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "valid": self.valid,
            "checked_events": self.checked_events,
            "first_invalid_event_id": self.first_invalid_event_id,
            "reason": self.reason,
            "verified_at": self.verified_at.isoformat(),
        }


# =============================================================================
# Repositories and sinks
# =============================================================================


class AuditRepository(ABC):
    """Append-only audit repository abstraction."""

    @abstractmethod
    def append(self, event: SecurityAuditEvent) -> None:
        """Append one audit event."""

    @abstractmethod
    def append_many(self, events: Sequence[SecurityAuditEvent]) -> None:
        """Append multiple audit events."""

    @abstractmethod
    def query(self, query: AuditQuery) -> Sequence[SecurityAuditEvent]:
        """Query audit events."""

    @abstractmethod
    def last_event(self) -> Optional[SecurityAuditEvent]:
        """Return the last appended event."""

    @abstractmethod
    def iter_all(self) -> Iterator[SecurityAuditEvent]:
        """Iterate over all events in append order."""


class InMemoryAuditRepository(AuditRepository):
    """Thread-safe in-memory audit repository for local development/tests."""

    def __init__(self, max_events: int = 500_000) -> None:
        self.max_events = max(1, max_events)
        self._events: List[SecurityAuditEvent] = []
        self._lock = threading.RLock()

    def append(self, event: SecurityAuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]

    def append_many(self, events: Sequence[SecurityAuditEvent]) -> None:
        with self._lock:
            self._events.extend(events)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events :]

    def query(self, query: AuditQuery) -> Sequence[SecurityAuditEvent]:
        with self._lock:
            results = [event for event in self._events if _event_matches_query(event, query)]
            if query.limit is not None:
                results = results[: query.limit]
            return tuple(results)

    def last_event(self) -> Optional[SecurityAuditEvent]:
        with self._lock:
            return self._events[-1] if self._events else None

    def iter_all(self) -> Iterator[SecurityAuditEvent]:
        with self._lock:
            snapshot = tuple(self._events)
        yield from snapshot


class AuditSink(ABC):
    """External audit sink abstraction."""

    @abstractmethod
    def emit(self, event: SecurityAuditEvent) -> None:
        """Emit one event to external system."""

    def emit_many(self, events: Sequence[SecurityAuditEvent]) -> None:
        for event in events:
            self.emit(event)


class LoggingAuditSink(AuditSink):
    """Logging-backed audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.audit")
        self.redact = redact

    def emit(self, event: SecurityAuditEvent) -> None:
        level = _severity_to_logging_level(event.severity)
        self.audit_logger.log(level, "security_audit_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


class CompositeAuditSink(AuditSink):
    """Fan-out audit sink."""

    def __init__(self, sinks: Sequence[AuditSink]) -> None:
        self.sinks = tuple(sinks)

    def emit(self, event: SecurityAuditEvent) -> None:
        errors = []
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                errors.append(exc)
                logger.exception("Audit sink failed: %s", type(sink).__name__)
        if errors:
            raise AuditSinkError(f"{len(errors)} audit sink(s) failed.")

    def emit_many(self, events: Sequence[SecurityAuditEvent]) -> None:
        for sink in self.sinks:
            sink.emit_many(events)


# =============================================================================
# Main audit service
# =============================================================================


class SecurityAuditService:
    """Enterprise security audit service."""

    def __init__(
        self,
        repository: Optional[AuditRepository] = None,
        sink: Optional[AuditSink] = None,
        config: Optional[SecurityAuditConfig] = None,
    ) -> None:
        self.config = config or SecurityAuditConfig()
        self.repository = repository or InMemoryAuditRepository(max_events=self.config.max_events_in_memory)
        self.sink = sink or LoggingAuditSink(redact=self.config.redact_sensitive_fields)
        self._lock = threading.RLock()

    def record(self, event: SecurityAuditEvent) -> SecurityAuditEvent:
        """Validate, hash-chain, persist and emit one audit event."""
        if not self.config.enabled:
            return event
        try:
            normalized = self._normalize_event(event)
            normalized.validate(self.config)
            with self._lock:
                previous_hash = self.repository.last_event().event_hash if self.config.enable_hash_chain and self.repository.last_event() else None
                stored = normalized.with_hashes(previous_hash) if self.config.enable_hash_chain else normalized
                self.repository.append(stored)
            if self.config.emit_to_sinks:
                self.sink.emit(stored)
            return stored
        except Exception as exc:
            logger.exception("Failed to record security audit event. event_id=%s", event.event_id)
            if self.config.fail_closed:
                if isinstance(exc, SecurityAuditError):
                    raise
                raise SecurityAuditError("Failed to record security audit event.") from exc
            return event

    def record_many(self, events: Sequence[SecurityAuditEvent]) -> Tuple[SecurityAuditEvent, ...]:
        """Record multiple audit events preserving hash-chain order."""
        if not self.config.enabled:
            return tuple(events)
        stored_events: List[SecurityAuditEvent] = []
        try:
            with self._lock:
                previous = self.repository.last_event()
                previous_hash = previous.event_hash if previous else None
                for event in events:
                    normalized = self._normalize_event(event)
                    normalized.validate(self.config)
                    stored = normalized.with_hashes(previous_hash) if self.config.enable_hash_chain else normalized
                    stored_events.append(stored)
                    previous_hash = stored.event_hash
                self.repository.append_many(stored_events)
            if self.config.emit_to_sinks:
                self.sink.emit_many(stored_events)
            return tuple(stored_events)
        except Exception as exc:
            logger.exception("Failed to record audit event batch.")
            if self.config.fail_closed:
                if isinstance(exc, SecurityAuditError):
                    raise
                raise SecurityAuditError("Failed to record audit batch.") from exc
            return tuple(stored_events)

    def emit(
        self,
        category: AuditCategory,
        action: AuditAction,
        outcome: AuditOutcome,
        message: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        actor: Optional[AuditActor] = None,
        resource: Optional[AuditResource] = None,
        **kwargs: Any,
    ) -> SecurityAuditEvent:
        """Convenience method to create and record an audit event."""
        event = SecurityAuditEvent(
            event_id=str(kwargs.pop("event_id", uuid.uuid4())),
            category=category,
            action=action,
            outcome=outcome,
            severity=severity,
            message=message,
            source=str(kwargs.pop("source", self.config.default_source)),
            actor=actor or AuditActor(),
            resource=resource or AuditResource(),
            request_id=kwargs.pop("request_id", None),
            correlation_id=kwargs.pop("correlation_id", None),
            session_id=kwargs.pop("session_id", None),
            trace_id=kwargs.pop("trace_id", None),
            span_id=kwargs.pop("span_id", None),
            event_type=kwargs.pop("event_type", None),
            risk_score=float(kwargs.pop("risk_score", 0.0)),
            tags=tuple(kwargs.pop("tags", ())),
            metadata=dict(kwargs.pop("metadata", {})),
        )
        return self.record(event)

    def query(self, query: AuditQuery) -> Sequence[SecurityAuditEvent]:
        return self.repository.query(query)

    def correlate(
        self,
        correlation_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 1_000,
    ) -> Sequence[SecurityAuditEvent]:
        return self.query(AuditQuery(
            correlation_id=correlation_id,
            request_id=request_id,
            session_id=session_id,
            actor_id=actor_id,
            limit=limit,
        ))

    def summarize(self, query: Optional[AuditQuery] = None) -> AuditSummary:
        events = tuple(self.repository.query(query or AuditQuery(limit=None)))
        by_category = Counter(event.category.value for event in events)
        by_outcome = Counter(event.outcome.value for event in events)
        by_severity = Counter(event.severity.value for event in events)
        by_action = Counter(event.action.value for event in events)
        top_actors = Counter(event.actor.actor_id or event.actor.username or "unknown" for event in events)
        top_resources = Counter(event.resource.canonical() for event in events)
        timestamps = [event.timestamp for event in events]
        return AuditSummary(
            total_events=len(events),
            by_category=dict(by_category),
            by_outcome=dict(by_outcome),
            by_severity=dict(by_severity),
            by_action=dict(by_action),
            top_actors=dict(top_actors.most_common(20)),
            top_resources=dict(top_resources.most_common(20)),
            start_time=min(timestamps) if timestamps else None,
            end_time=max(timestamps) if timestamps else None,
        )

    def verify_integrity(self) -> IntegrityVerificationResult:
        """Verify hash chain integrity for all events."""
        if not self.config.enable_hash_chain:
            return IntegrityVerificationResult(valid=True, checked_events=0, reason="Hash chain is disabled.")

        previous_hash: Optional[str] = None
        checked = 0
        for event in self.repository.iter_all():
            checked += 1
            expected = event.compute_hash(previous_hash)
            if event.previous_hash != previous_hash:
                return IntegrityVerificationResult(
                    valid=False,
                    checked_events=checked,
                    first_invalid_event_id=event.event_id,
                    reason="previous_hash does not match expected chain value.",
                )
            if event.event_hash != expected:
                return IntegrityVerificationResult(
                    valid=False,
                    checked_events=checked,
                    first_invalid_event_id=event.event_id,
                    reason="event_hash does not match event payload.",
                )
            previous_hash = event.event_hash
        return IntegrityVerificationResult(valid=True, checked_events=checked, reason="Audit hash chain is valid.")

    def export(self, query: Optional[AuditQuery] = None, fmt: AuditFormat = AuditFormat.JSONL, redact: bool = True) -> str:
        """Export audit events as JSON or JSONL string."""
        try:
            events = tuple(self.repository.query(query or AuditQuery(limit=None)))
            if fmt == AuditFormat.JSON:
                return json.dumps([event.to_dict(redact=redact) for event in events], indent=2, sort_keys=True, default=str)
            if fmt == AuditFormat.JSONL:
                return "\n".join(json.dumps(event.to_dict(redact=redact), sort_keys=True, default=str) for event in events)
            raise AuditExportError(f"Unsupported audit export format: {fmt.value}")
        except Exception as exc:
            if isinstance(exc, AuditExportError):
                raise
            raise AuditExportError("Failed to export audit events.") from exc

    def _normalize_event(self, event: SecurityAuditEvent) -> SecurityAuditEvent:
        timestamp = event.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return dataclasses.replace(
            event,
            event_id=event.event_id or str(uuid.uuid4()),
            source=event.source or self.config.default_source,
            timestamp=timestamp,
            metadata=redact_sensitive(event.metadata, self.config.sensitive_field_terms) if self.config.redact_sensitive_fields else dict(event.metadata),
        )


# =============================================================================
# Adapter helpers for other security modules
# =============================================================================


def audit_from_mapping(payload: Mapping[str, Any], default_category: AuditCategory = AuditCategory.CUSTOM) -> SecurityAuditEvent:
    """Create a SecurityAuditEvent from a generic event mapping."""
    actor_data = payload.get("actor") or {}
    resource_data = payload.get("resource") or {}
    return SecurityAuditEvent(
        event_id=str(payload.get("event_id") or payload.get("id") or uuid.uuid4()),
        category=AuditCategory(str(payload.get("category") or default_category.value)),
        action=AuditAction(str(payload.get("action") or AuditAction.CUSTOM.value)),
        outcome=AuditOutcome(str(payload.get("outcome") or AuditOutcome.UNKNOWN.value)),
        severity=AuditSeverity(str(payload.get("severity") or AuditSeverity.INFO.value)),
        message=str(payload.get("message") or payload.get("reason") or "Audit event"),
        timestamp=parse_datetime(payload.get("timestamp")) if payload.get("timestamp") else datetime.now(timezone.utc),
        source=str(payload.get("source") or "external"),
        actor=AuditActor(
            actor_id=actor_data.get("actor_id") or payload.get("principal_id") or payload.get("user_id"),
            actor_type=str(actor_data.get("actor_type") or "user"),
            username=actor_data.get("username") or payload.get("username"),
            tenant_id=actor_data.get("tenant_id") or payload.get("tenant_id"),
            ip_address=actor_data.get("ip_address") or payload.get("ip_address"),
            user_agent=actor_data.get("user_agent") or payload.get("user_agent"),
            attributes=dict(actor_data.get("attributes") or {}),
        ),
        resource=AuditResource(
            resource_id=resource_data.get("resource_id") or payload.get("resource_id"),
            resource_type=resource_data.get("resource_type") or payload.get("resource_type"),
            name=resource_data.get("name") or payload.get("resource"),
            tenant_id=resource_data.get("tenant_id") or payload.get("tenant_id"),
            attributes=dict(resource_data.get("attributes") or {}),
        ),
        request_id=payload.get("request_id"),
        correlation_id=payload.get("correlation_id"),
        session_id=payload.get("session_id"),
        trace_id=payload.get("trace_id"),
        span_id=payload.get("span_id"),
        event_type=payload.get("event_type"),
        risk_score=float(payload.get("risk_score") or 0.0),
        tags=tuple(payload.get("tags") or ()),
        metadata=dict(payload.get("metadata") or {}),
    )


def audit_authentication_event(
    username: str,
    success: bool,
    message: str,
    actor_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> SecurityAuditEvent:
    return SecurityAuditEvent(
        event_id=str(uuid.uuid4()),
        category=AuditCategory.AUTHENTICATION,
        action=AuditAction.LOGIN,
        outcome=AuditOutcome.SUCCESS if success else AuditOutcome.FAILURE,
        severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
        message=message,
        actor=AuditActor(actor_id=actor_id, username=username, tenant_id=tenant_id, ip_address=ip_address, user_agent=user_agent),
        request_id=request_id,
        correlation_id=correlation_id,
        metadata=dict(metadata or {}),
    )


def audit_authorization_event(
    actor_id: str,
    resource_type: str,
    resource_id: Optional[str],
    action_name: str,
    allowed: bool,
    message: str,
    tenant_id: Optional[str] = None,
    request_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> SecurityAuditEvent:
    return SecurityAuditEvent(
        event_id=str(uuid.uuid4()),
        category=AuditCategory.AUTHORIZATION,
        action=AuditAction.AUTHORIZE,
        outcome=AuditOutcome.SUCCESS if allowed else AuditOutcome.DENIED,
        severity=AuditSeverity.INFO if allowed else AuditSeverity.WARNING,
        message=message,
        actor=AuditActor(actor_id=actor_id, tenant_id=tenant_id),
        resource=AuditResource(resource_id=resource_id, resource_type=resource_type, tenant_id=tenant_id),
        request_id=request_id,
        correlation_id=correlation_id,
        metadata={"requested_action": action_name, **dict(metadata or {})},
    )


# =============================================================================
# Utility functions
# =============================================================================


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def redact_sensitive(data: Mapping[str, Any], sensitive_terms: Optional[Sequence[str]] = None) -> JsonDict:
    terms = tuple(term.lower() for term in (sensitive_terms or SecurityAuditConfig().sensitive_field_terms))

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key).lower()
                if any(term in key_text for term in terms):
                    output[str(key)] = "***REDACTED***"
                else:
                    output[str(key)] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def _event_matches_query(event: SecurityAuditEvent, query: AuditQuery) -> bool:
    if query.start_time and event.timestamp < query.start_time:
        return False
    if query.end_time and event.timestamp > query.end_time:
        return False
    if query.categories and event.category not in query.categories:
        return False
    if query.actions and event.action not in query.actions:
        return False
    if query.outcomes and event.outcome not in query.outcomes:
        return False
    if query.severities and event.severity not in query.severities:
        return False
    if query.actor_id and event.actor.actor_id != query.actor_id:
        return False
    if query.username and event.actor.username != query.username:
        return False
    if query.tenant_id and query.tenant_id not in {event.actor.tenant_id, event.resource.tenant_id}:
        return False
    if query.resource_type and event.resource.resource_type != query.resource_type:
        return False
    if query.resource_id and event.resource.resource_id != query.resource_id:
        return False
    if query.request_id and event.request_id != query.request_id:
        return False
    if query.correlation_id and event.correlation_id != query.correlation_id:
        return False
    if query.session_id and event.session_id != query.session_id:
        return False
    if query.tags_any and not set(query.tags_any).intersection(set(event.tags)):
        return False
    if query.tags_all and not set(query.tags_all).issubset(set(event.tags)):
        return False
    if query.min_risk_score is not None and event.risk_score < query.min_risk_score:
        return False
    if query.text:
        haystack = json.dumps(event.to_dict(redact=False), default=str).lower()
        if query.text.lower() not in haystack:
            return False
    return True


def _severity_to_logging_level(severity: AuditSeverity) -> int:
    return {
        AuditSeverity.DEBUG: logging.DEBUG,
        AuditSeverity.INFO: logging.INFO,
        AuditSeverity.NOTICE: logging.INFO,
        AuditSeverity.WARNING: logging.WARNING,
        AuditSeverity.ERROR: logging.ERROR,
        AuditSeverity.CRITICAL: logging.CRITICAL,
    }[severity]


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple, set)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def create_default_security_audit_service() -> SecurityAuditService:
    return SecurityAuditService()


__all__ = [
    "AuditAction",
    "AuditActor",
    "AuditCategory",
    "AuditExportError",
    "AuditFormat",
    "AuditIntegrityError",
    "AuditOutcome",
    "AuditQuery",
    "AuditRepository",
    "AuditRepositoryError",
    "AuditResource",
    "AuditSeverity",
    "AuditSink",
    "AuditSinkError",
    "AuditSummary",
    "AuditValidationError",
    "CompositeAuditSink",
    "InMemoryAuditRepository",
    "IntegrityVerificationResult",
    "LoggingAuditSink",
    "SecurityAuditConfig",
    "SecurityAuditError",
    "SecurityAuditEvent",
    "SecurityAuditService",
    "audit_authentication_event",
    "audit_authorization_event",
    "audit_from_mapping",
    "create_default_security_audit_service",
    "parse_datetime",
    "redact_sensitive",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    audit = create_default_security_audit_service()
    event = audit.emit(
        category=AuditCategory.AUTHENTICATION,
        action=AuditAction.LOGIN,
        outcome=AuditOutcome.SUCCESS,
        severity=AuditSeverity.INFO,
        message="User login successful.",
        actor=AuditActor(actor_id="user-001", username="admin@example.com", tenant_id="default", ip_address="127.0.0.1"),
        request_id="req-demo",
        correlation_id="corr-demo",
        metadata={"method": "password", "access_token": "should-not-appear"},
    )

    print(json.dumps(event.to_dict(), indent=2, default=str))
    print(json.dumps(audit.summarize().to_dict(), indent=2, default=str))
    print(json.dumps(audit.verify_integrity().to_dict(), indent=2, default=str))
