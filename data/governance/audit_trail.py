"""
audit_trail.py
==============

Enterprise-grade audit trail module for data governance platforms.

Core capabilities
-----------------
- Immutable audit event model with actor, action, resource and context.
- Tamper-evident hash chaining per stream/tenant/domain.
- Correlation IDs, trace IDs and request IDs for distributed systems.
- Severity, category, outcome and compliance-control tagging.
- Pluggable audit stores/sinks: memory by default, extensible to SIEM, Kafka,
  object storage, database, OpenSearch, Splunk, Sentinel or lakehouse tables.
- Query API with filters, pagination, time windows and export helpers.
- Integrity verification and chain gap detection.
- Retention planning and legal-hold metadata.
- Privacy-aware redaction helpers for sensitive payloads.
- Batch ingestion and structured audit reports.

This module is vendor-neutral and dependency-light. It can be used directly by
other governance modules such as access governance, policy engines, catalog
management, privacy workflows and compliance evidence collectors.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import gzip
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Redactor = Callable[[Any], Any]


class AuditTrailError(Exception):
    """Base exception for audit trail failures."""


class AuditIntegrityError(AuditTrailError):
    """Raised when audit chain integrity verification fails."""


class AuditWriteError(AuditTrailError):
    """Raised when audit event persistence fails."""


class AuditEventSeverity(str, enum.Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditOutcome(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    ERROR = "error"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class AuditCategory(str, enum.Enum):
    ACCESS = "access"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    DATA_CHANGE = "data_change"
    SCHEMA_CHANGE = "schema_change"
    POLICY = "policy"
    PRIVACY = "privacy"
    GOVERNANCE = "governance"
    COMPLIANCE = "compliance"
    SECURITY = "security"
    SYSTEM = "system"
    PIPELINE = "pipeline"
    QUALITY = "quality"
    LINEAGE = "lineage"
    METADATA = "metadata"


class AuditAction(str, enum.Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXPORT = "export"
    IMPORT = "import"
    LOGIN = "login"
    LOGOUT = "logout"
    GRANT = "grant"
    REVOKE = "revoke"
    APPROVE = "approve"
    REJECT = "reject"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    VALIDATE = "validate"
    CLASSIFY = "classify"
    RETAIN = "retain"
    DISPOSE = "dispose"


class RetentionClass(str, enum.Enum):
    STANDARD = "standard"
    EXTENDED = "extended"
    REGULATORY = "regulatory"
    LEGAL_HOLD = "legal_hold"


class ExportFormat(str, enum.Enum):
    JSONL = "jsonl"
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class AuditActor:
    actor_id: str
    actor_type: str = "user"
    display_name: Optional[str] = None
    email: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "display_name": self.display_name,
            "email": self.email,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class AuditResource:
    resource_id: str
    resource_type: str
    name: Optional[str] = None
    domain: Optional[str] = None
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "name": self.name,
            "domain": self.domain,
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "classifications": list(self.classifications),
            "tags": list(self.tags),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class AuditContext:
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    request_id: Optional[str] = None
    job_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    environment: str = "prod"
    service_name: Optional[str] = None
    service_version: Optional[str] = None
    source_system: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "request_id": self.request_id,
            "job_id": self.job_id,
            "pipeline_id": self.pipeline_id,
            "environment": self.environment,
            "service_name": self.service_name,
            "service_version": self.service_version,
            "source_system": self.source_system,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: dt.datetime
    category: AuditCategory
    action: Union[AuditAction, str]
    outcome: AuditOutcome
    severity: AuditEventSeverity
    actor: AuditActor
    resource: AuditResource
    context: AuditContext
    message: str = ""
    before: Optional[JsonDict] = None
    after: Optional[JsonDict] = None
    changes: Optional[JsonDict] = None
    reason: Optional[str] = None
    policy_ids: Tuple[str, ...] = field(default_factory=tuple)
    control_ids: Tuple[str, ...] = field(default_factory=tuple)
    risk_score: Optional[float] = None
    retention_class: RetentionClass = RetentionClass.STANDARD
    legal_hold: bool = False
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None
    signature: Optional[str] = None
    schema_version: str = "1.0"
    metadata: JsonDict = field(default_factory=dict)

    def canonical_payload(self, *, include_hashes: bool = False, include_signature: bool = False) -> JsonDict:
        payload = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "category": enum_value(self.category),
            "action": enum_value(self.action),
            "outcome": enum_value(self.outcome),
            "severity": enum_value(self.severity),
            "actor": self.actor.to_dict(),
            "resource": self.resource.to_dict(),
            "context": self.context.to_dict(),
            "message": self.message,
            "before": self.before,
            "after": self.after,
            "changes": self.changes,
            "reason": self.reason,
            "policy_ids": list(self.policy_ids),
            "control_ids": list(self.control_ids),
            "risk_score": self.risk_score,
            "retention_class": enum_value(self.retention_class),
            "legal_hold": self.legal_hold,
            "schema_version": self.schema_version,
            "metadata": self.metadata,
        }
        if include_hashes:
            payload["previous_hash"] = self.previous_hash
            payload["event_hash"] = self.event_hash
        if include_signature:
            payload["signature"] = self.signature
        return to_json_safe(payload)

    def to_dict(self) -> JsonDict:
        payload = self.canonical_payload(include_hashes=True, include_signature=True)
        return payload


@dataclass(frozen=True)
class AuditEventInput:
    category: AuditCategory
    action: Union[AuditAction, str]
    outcome: AuditOutcome
    actor: AuditActor
    resource: AuditResource
    context: AuditContext = field(default_factory=AuditContext)
    severity: AuditEventSeverity = AuditEventSeverity.INFO
    message: str = ""
    before: Optional[JsonDict] = None
    after: Optional[JsonDict] = None
    changes: Optional[JsonDict] = None
    reason: Optional[str] = None
    policy_ids: Sequence[str] = field(default_factory=tuple)
    control_ids: Sequence[str] = field(default_factory=tuple)
    risk_score: Optional[float] = None
    retention_class: RetentionClass = RetentionClass.STANDARD
    legal_hold: bool = False
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class AuditQuery:
    start_time: Optional[dt.datetime] = None
    end_time: Optional[dt.datetime] = None
    actor_id: Optional[str] = None
    actor_type: Optional[str] = None
    resource_id: Optional[str] = None
    resource_type: Optional[str] = None
    tenant_id: Optional[str] = None
    category: Optional[AuditCategory] = None
    action: Optional[Union[AuditAction, str]] = None
    outcome: Optional[AuditOutcome] = None
    severity_at_least: Optional[AuditEventSeverity] = None
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    policy_id: Optional[str] = None
    control_id: Optional[str] = None
    text: Optional[str] = None
    legal_hold: Optional[bool] = None
    limit: int = 100
    offset: int = 0


@dataclass
class AuditQueryResult:
    events: List[AuditEvent]
    total_matched: int
    limit: int
    offset: int

    def to_dict(self) -> JsonDict:
        return {
            "total_matched": self.total_matched,
            "limit": self.limit,
            "offset": self.offset,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class IntegrityIssue:
    code: str
    message: str
    event_id: Optional[str] = None
    stream_id: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return dataclasses.asdict(self)


@dataclass
class IntegrityReport:
    verified: bool
    total_events: int
    streams_checked: int
    issues: List[IntegrityIssue] = field(default_factory=list)
    checked_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "verified": self.verified,
            "total_events": self.total_events,
            "streams_checked": self.streams_checked,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class AuditWriteReport:
    total_input: int = 0
    written: int = 0
    failed: int = 0
    categories: Counter = field(default_factory=Counter)
    outcomes: Counter = field(default_factory=Counter)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    errors: List[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "total_input": self.total_input,
            "written": self.written,
            "failed": self.failed,
            "categories": dict(self.categories),
            "outcomes": dict(self.outcomes),
            "duration_ms": self.duration_ms,
            "errors": list(self.errors),
        }


@runtime_checkable
class AuditStore(Protocol):
    def append(self, event: AuditEvent) -> None:
        ...

    def append_many(self, events: Sequence[AuditEvent]) -> None:
        ...

    def query(self, query: AuditQuery) -> AuditQueryResult:
        ...

    def all_events(self) -> List[AuditEvent]:
        ...

    def latest_event_hash(self, stream_id: str) -> Optional[str]:
        ...

    def close(self) -> None:
        ...


class InMemoryAuditStore(AuditStore):
    """In-memory audit store for tests, local runs and fallback mode."""

    def __init__(self) -> None:
        self._events: List[AuditEvent] = []
        self._latest_hash_by_stream: Dict[str, str] = {}

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)
        self._latest_hash_by_stream[stream_id_for_event(event)] = event.event_hash or ""

    def append_many(self, events: Sequence[AuditEvent]) -> None:
        for event in events:
            self.append(event)

    def query(self, query: AuditQuery) -> AuditQueryResult:
        matched = [event for event in self._events if matches_query(event, query)]
        sliced = matched[query.offset : query.offset + query.limit]
        return AuditQueryResult(events=sliced, total_matched=len(matched), limit=query.limit, offset=query.offset)

    def all_events(self) -> List[AuditEvent]:
        return list(self._events)

    def latest_event_hash(self, stream_id: str) -> Optional[str]:
        return self._latest_hash_by_stream.get(stream_id)

    def close(self) -> None:
        return None


class CompositeAuditStore(AuditStore):
    """Fan-out audit writes to multiple stores while reading from a primary store."""

    def __init__(self, primary: AuditStore, replicas: Sequence[AuditStore] = ()) -> None:
        self.primary = primary
        self.replicas = list(replicas)

    def append(self, event: AuditEvent) -> None:
        self.primary.append(event)
        for replica in self.replicas:
            replica.append(event)

    def append_many(self, events: Sequence[AuditEvent]) -> None:
        self.primary.append_many(events)
        for replica in self.replicas:
            replica.append_many(events)

    def query(self, query: AuditQuery) -> AuditQueryResult:
        return self.primary.query(query)

    def all_events(self) -> List[AuditEvent]:
        return self.primary.all_events()

    def latest_event_hash(self, stream_id: str) -> Optional[str]:
        return self.primary.latest_event_hash(stream_id)

    def close(self) -> None:
        self.primary.close()
        for replica in self.replicas:
            replica.close()


class LoggingAuditStore(AuditStore):
    """Audit store that writes JSON events to Python logging."""

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger
        self._memory = InMemoryAuditStore()

    def append(self, event: AuditEvent) -> None:
        self._memory.append(event)
        self.log.info("audit_event", extra={"audit_event": event.to_dict()})

    def append_many(self, events: Sequence[AuditEvent]) -> None:
        for event in events:
            self.append(event)

    def query(self, query: AuditQuery) -> AuditQueryResult:
        return self._memory.query(query)

    def all_events(self) -> List[AuditEvent]:
        return self._memory.all_events()

    def latest_event_hash(self, stream_id: str) -> Optional[str]:
        return self._memory.latest_event_hash(stream_id)

    def close(self) -> None:
        return None


class AuditRedactor:
    """Privacy-aware redaction utility for audit payloads."""

    DEFAULT_SENSITIVE_KEYS = {
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "session",
        "ssn",
        "cpf",
        "credit_card",
        "card_number",
    }

    EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

    def __init__(self, sensitive_keys: Optional[Iterable[str]] = None, mask: str = "***REDACTED***") -> None:
        self.sensitive_keys = {key.lower() for key in (sensitive_keys or self.DEFAULT_SENSITIVE_KEYS)}
        self.mask = mask

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                if str(key).lower() in self.sensitive_keys or any(part in str(key).lower() for part in self.sensitive_keys):
                    output[str(key)] = self.mask
                else:
                    output[str(key)] = self.redact(item)
            return output
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, str):
            return self.EMAIL_PATTERN.sub(self.mask, value)
        return value


@dataclass(frozen=True)
class AuditTrailConfig:
    hash_algorithm: str = "sha256"
    signing_secret: Optional[str] = None
    enable_hash_chain: bool = True
    enable_signature: bool = False
    redact_sensitive_data: bool = True
    default_retention_days: int = 365
    extended_retention_days: int = 2555
    regulatory_retention_days: int = 3650
    service_name: str = "data-governance"
    environment: str = "prod"
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.hash_algorithm not in hashlib.algorithms_available:
            raise ValueError(f"Unsupported hash algorithm: {self.hash_algorithm}")
        if self.default_retention_days <= 0:
            raise ValueError("default_retention_days must be > 0")


class AuditTrail:
    """Main enterprise audit trail service."""

    def __init__(
        self,
        store: Optional[AuditStore] = None,
        *,
        config: Optional[AuditTrailConfig] = None,
        redactor: Optional[AuditRedactor] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AuditTrailConfig()
        self.store = store or InMemoryAuditStore()
        self.redactor = redactor or AuditRedactor()
        self.log = log or logger

    def record(self, event_input: AuditEventInput) -> AuditEvent:
        """Create, seal and persist one audit event."""
        event = self._build_event(event_input)
        try:
            self.store.append(event)
        except Exception as exc:
            raise AuditWriteError(f"Failed to write audit event {event.event_id}: {exc}") from exc
        return event

    def record_many(self, event_inputs: Iterable[AuditEventInput]) -> AuditWriteReport:
        """Batch-write audit events and return a structured write report."""
        report = AuditWriteReport()
        events: List[AuditEvent] = []

        for item in event_inputs:
            report.total_input += 1
            try:
                event = self._build_event(item)
                events.append(event)
                report.categories[event.category.value] += 1
                report.outcomes[event.outcome.value] += 1
            except Exception as exc:
                report.failed += 1
                report.errors.append(str(exc))

        if events:
            try:
                self.store.append_many(events)
                report.written = len(events)
            except Exception as exc:
                report.failed += len(events)
                report.errors.append(str(exc))
                raise AuditWriteError(f"Failed to batch-write audit events: {exc}") from exc

        report.finish()
        return report

    def query(self, query: AuditQuery) -> AuditQueryResult:
        return self.store.query(query)

    def verify_integrity(self, *, raise_on_error: bool = False) -> IntegrityReport:
        """Verify event hashes and hash-chain continuity by stream."""
        events = sorted(self.store.all_events(), key=lambda event: (stream_id_for_event(event), event.timestamp, event.event_id))
        issues: List[IntegrityIssue] = []
        streams: Dict[str, List[AuditEvent]] = defaultdict(list)
        for event in events:
            streams[stream_id_for_event(event)].append(event)

        for stream_id, stream_events in streams.items():
            previous_hash: Optional[str] = None
            for event in stream_events:
                expected_hash = self._compute_event_hash(dataclasses.replace(event, event_hash=None, signature=None))
                if event.event_hash != expected_hash:
                    issues.append(
                        IntegrityIssue(
                            code="EVENT_HASH_MISMATCH",
                            message="Audit event hash does not match canonical payload",
                            event_id=event.event_id,
                            stream_id=stream_id,
                            expected=expected_hash,
                            actual=event.event_hash,
                        )
                    )
                if self.config.enable_hash_chain and event.previous_hash != previous_hash:
                    issues.append(
                        IntegrityIssue(
                            code="CHAIN_PREVIOUS_HASH_MISMATCH",
                            message="Audit chain previous_hash does not match prior event hash",
                            event_id=event.event_id,
                            stream_id=stream_id,
                            expected=previous_hash,
                            actual=event.previous_hash,
                        )
                    )
                if self.config.enable_signature and event.signature:
                    expected_signature = self._sign_hash(event.event_hash or "")
                    if not hmac.compare_digest(event.signature, expected_signature):
                        issues.append(
                            IntegrityIssue(
                                code="SIGNATURE_MISMATCH",
                                message="Audit event signature is invalid",
                                event_id=event.event_id,
                                stream_id=stream_id,
                                expected=expected_signature,
                                actual=event.signature,
                            )
                        )
                previous_hash = event.event_hash

        report = IntegrityReport(
            verified=not issues,
            total_events=len(events),
            streams_checked=len(streams),
            issues=issues,
        )
        if raise_on_error and issues:
            raise AuditIntegrityError(json.dumps(report.to_dict(), ensure_ascii=False))
        return report

    def export(self, query: Optional[AuditQuery] = None, *, fmt: ExportFormat = ExportFormat.JSONL, compress: bool = False) -> bytes:
        """Export matching events as bytes in JSONL, JSON or CSV format."""
        query = query or AuditQuery(limit=10_000)
        result = self.query(query)
        events = [event.to_dict() for event in result.events]

        if fmt == ExportFormat.JSONL:
            content = "\n".join(json.dumps(event, ensure_ascii=False, default=str) for event in events).encode("utf-8")
        elif fmt == ExportFormat.JSON:
            content = json.dumps(events, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        elif fmt == ExportFormat.CSV:
            content = events_to_csv(events).encode("utf-8")
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

        return gzip.compress(content) if compress else content

    def retention_plan(self, now: Optional[dt.datetime] = None) -> JsonDict:
        """Return retention eligibility counts without deleting anything."""
        now = now or dt.datetime.now(dt.timezone.utc)
        eligible: List[str] = []
        protected: List[str] = []

        for event in self.store.all_events():
            if event.legal_hold or event.retention_class == RetentionClass.LEGAL_HOLD:
                protected.append(event.event_id)
                continue
            retain_until = self.retention_until(event)
            if retain_until <= now:
                eligible.append(event.event_id)
            else:
                protected.append(event.event_id)

        return {
            "eligible_for_disposal": len(eligible),
            "protected": len(protected),
            "eligible_event_ids": eligible,
            "generated_at": now.isoformat(),
        }

    def retention_until(self, event: AuditEvent) -> dt.datetime:
        if event.retention_class == RetentionClass.EXTENDED:
            days = self.config.extended_retention_days
        elif event.retention_class == RetentionClass.REGULATORY:
            days = self.config.regulatory_retention_days
        elif event.retention_class == RetentionClass.LEGAL_HOLD or event.legal_hold:
            days = 365 * 100
        else:
            days = self.config.default_retention_days
        return event.timestamp + dt.timedelta(days=days)

    def summarize(self, query: Optional[AuditQuery] = None) -> JsonDict:
        query = query or AuditQuery(limit=100_000)
        result = self.query(query)
        by_category = Counter(event.category.value for event in result.events)
        by_outcome = Counter(event.outcome.value for event in result.events)
        by_action = Counter(enum_value(event.action) for event in result.events)
        by_actor = Counter(event.actor.actor_id for event in result.events)
        by_resource_type = Counter(event.resource.resource_type for event in result.events)
        return {
            "total": result.total_matched,
            "returned": len(result.events),
            "by_category": dict(by_category),
            "by_outcome": dict(by_outcome),
            "by_action": dict(by_action),
            "top_actors": dict(by_actor.most_common(20)),
            "by_resource_type": dict(by_resource_type),
        }

    def _build_event(self, event_input: AuditEventInput) -> AuditEvent:
        event_id = str(uuid.uuid4())
        timestamp = dt.datetime.now(dt.timezone.utc)

        before = self._sanitize(event_input.before)
        after = self._sanitize(event_input.after)
        changes = self._sanitize(event_input.changes if event_input.changes is not None else diff_dicts(before, after))
        metadata = self._sanitize({**self.config.metadata, **event_input.metadata})

        context = event_input.context
        if not context.service_name or not context.environment:
            context = dataclasses.replace(
                context,
                service_name=context.service_name or self.config.service_name,
                environment=context.environment or self.config.environment,
            )

        stream_id = stream_id_for_parts(
            tenant_id=event_input.resource.tenant_id,
            resource_id=event_input.resource.resource_id,
            category=event_input.category,
        )
        previous_hash = self.store.latest_event_hash(stream_id) if self.config.enable_hash_chain else None

        event = AuditEvent(
            event_id=event_id,
            timestamp=timestamp,
            category=event_input.category,
            action=event_input.action,
            outcome=event_input.outcome,
            severity=event_input.severity,
            actor=event_input.actor,
            resource=event_input.resource,
            context=context,
            message=event_input.message,
            before=before,
            after=after,
            changes=changes,
            reason=event_input.reason,
            policy_ids=tuple(event_input.policy_ids),
            control_ids=tuple(event_input.control_ids),
            risk_score=event_input.risk_score,
            retention_class=event_input.retention_class,
            legal_hold=event_input.legal_hold,
            previous_hash=previous_hash,
            metadata=metadata,
        )
        event_hash = self._compute_event_hash(event)
        signature = self._sign_hash(event_hash) if self.config.enable_signature else None
        return dataclasses.replace(event, event_hash=event_hash, signature=signature)

    def _compute_event_hash(self, event: AuditEvent) -> str:
        payload = event.canonical_payload(include_hashes=False, include_signature=False)
        payload["previous_hash"] = event.previous_hash
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        hasher = hashlib.new(self.config.hash_algorithm)
        hasher.update(raw.encode("utf-8"))
        return hasher.hexdigest()

    def _sign_hash(self, event_hash: str) -> str:
        if not self.config.signing_secret:
            raise AuditTrailError("Signing secret is required when signature is enabled")
        return hmac.new(self.config.signing_secret.encode("utf-8"), event_hash.encode("utf-8"), hashlib.sha256).hexdigest()

    def _sanitize(self, value: Any) -> Any:
        safe = to_json_safe(value)
        if self.config.redact_sensitive_data:
            return self.redactor.redact(safe)
        return safe

    def close(self) -> None:
        self.store.close()


# -----------------------------------------------------------------------------
# Query helpers
# -----------------------------------------------------------------------------


_SEVERITY_ORDER = {
    AuditEventSeverity.DEBUG: 10,
    AuditEventSeverity.INFO: 20,
    AuditEventSeverity.WARNING: 30,
    AuditEventSeverity.ERROR: 40,
    AuditEventSeverity.CRITICAL: 50,
}


def matches_query(event: AuditEvent, query: AuditQuery) -> bool:
    if query.start_time and event.timestamp < query.start_time:
        return False
    if query.end_time and event.timestamp > query.end_time:
        return False
    if query.actor_id and event.actor.actor_id != query.actor_id:
        return False
    if query.actor_type and event.actor.actor_type != query.actor_type:
        return False
    if query.resource_id and event.resource.resource_id != query.resource_id:
        return False
    if query.resource_type and event.resource.resource_type != query.resource_type:
        return False
    if query.tenant_id and event.resource.tenant_id != query.tenant_id:
        return False
    if query.category and event.category != query.category:
        return False
    if query.action and enum_value(event.action) != enum_value(query.action):
        return False
    if query.outcome and event.outcome != query.outcome:
        return False
    if query.severity_at_least and _SEVERITY_ORDER[event.severity] < _SEVERITY_ORDER[query.severity_at_least]:
        return False
    if query.correlation_id and event.context.correlation_id != query.correlation_id:
        return False
    if query.trace_id and event.context.trace_id != query.trace_id:
        return False
    if query.policy_id and query.policy_id not in event.policy_ids:
        return False
    if query.control_id and query.control_id not in event.control_ids:
        return False
    if query.legal_hold is not None and event.legal_hold != query.legal_hold:
        return False
    if query.text:
        needle = query.text.lower()
        haystack = json.dumps(event.to_dict(), ensure_ascii=False, default=str).lower()
        if needle not in haystack:
            return False
    return True


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


def enum_value(value: Any) -> Any:
    return value.value if isinstance(value, enum.Enum) else value


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


def diff_dicts(before: Optional[Mapping[str, Any]], after: Optional[Mapping[str, Any]]) -> Optional[JsonDict]:
    if before is None or after is None:
        return None
    changes: JsonDict = {"added": {}, "removed": {}, "changed": {}}
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    for key in sorted(after_keys - before_keys):
        changes["added"][key] = after[key]
    for key in sorted(before_keys - after_keys):
        changes["removed"][key] = before[key]
    for key in sorted(before_keys & after_keys):
        if before[key] != after[key]:
            changes["changed"][key] = {"before": before[key], "after": after[key]}
    if not changes["added"] and not changes["removed"] and not changes["changed"]:
        return None
    return changes


def stream_id_for_parts(*, tenant_id: Optional[str], resource_id: str, category: AuditCategory) -> str:
    return f"{tenant_id or 'global'}:{category.value}:{resource_id}"


def stream_id_for_event(event: AuditEvent) -> str:
    return stream_id_for_parts(tenant_id=event.resource.tenant_id, resource_id=event.resource.resource_id, category=event.category)


def events_to_csv(events: Sequence[Mapping[str, Any]]) -> str:
    columns = [
        "event_id",
        "timestamp",
        "category",
        "action",
        "outcome",
        "severity",
        "actor_id",
        "actor_type",
        "resource_id",
        "resource_type",
        "tenant_id",
        "correlation_id",
        "message",
        "event_hash",
    ]
    lines = [",".join(columns)]
    for event in events:
        actor = event.get("actor") or {}
        resource = event.get("resource") or {}
        context = event.get("context") or {}
        row = {
            "event_id": event.get("event_id"),
            "timestamp": event.get("timestamp"),
            "category": event.get("category"),
            "action": event.get("action"),
            "outcome": event.get("outcome"),
            "severity": event.get("severity"),
            "actor_id": actor.get("actor_id"),
            "actor_type": actor.get("actor_type"),
            "resource_id": resource.get("resource_id"),
            "resource_type": resource.get("resource_type"),
            "tenant_id": resource.get("tenant_id"),
            "correlation_id": context.get("correlation_id"),
            "message": event.get("message"),
            "event_hash": event.get("event_hash"),
        }
        lines.append(",".join(csv_escape(row.get(column)) for column in columns))
    return "\n".join(lines)


def csv_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace('"', '""')
    if any(char in text for char in [",", "\n", "\r", '"']):
        return f'"{text}"'
    return text


# -----------------------------------------------------------------------------
# Convenience builders
# -----------------------------------------------------------------------------


def build_audit_event(
    *,
    actor_id: str,
    resource_id: str,
    resource_type: str,
    action: Union[AuditAction, str],
    category: AuditCategory = AuditCategory.GOVERNANCE,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    message: str = "",
    tenant_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    **metadata: Any,
) -> AuditEventInput:
    actor = AuditActor(actor_id=actor_id)
    resource = AuditResource(resource_id=resource_id, resource_type=resource_type, tenant_id=tenant_id)
    context = AuditContext(correlation_id=correlation_id or str(uuid.uuid4()))
    return AuditEventInput(
        category=category,
        action=action,
        outcome=outcome,
        actor=actor,
        resource=resource,
        context=context,
        message=message,
        metadata=metadata,
    )


def build_default_audit_trail(*, signing_secret: Optional[str] = None) -> AuditTrail:
    config = AuditTrailConfig(
        signing_secret=signing_secret,
        enable_signature=bool(signing_secret),
        enable_hash_chain=True,
        redact_sensitive_data=True,
        service_name="data-governance",
        environment="prod",
    )
    return AuditTrail(store=InMemoryAuditStore(), config=config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    audit = build_default_audit_trail(signing_secret="dev-secret")
    actor = AuditActor(actor_id="u-100", display_name="Ana Silva", email="ana@example.com", ip_address="10.0.0.10")
    resource = AuditResource(
        resource_id="sales_daily",
        resource_type="dataset",
        name="Daily Sales",
        tenant_id="tenant-a",
        classifications=("financial_sensitive",),
        tags=("gold", "sales"),
    )
    context = AuditContext(correlation_id="corr-123", service_name="governance-api", environment="dev")

    audit.record(
        AuditEventInput(
            category=AuditCategory.ACCESS,
            action=AuditAction.GRANT,
            outcome=AuditOutcome.SUCCESS,
            severity=AuditEventSeverity.INFO,
            actor=actor,
            resource=resource,
            context=context,
            message="Access granted to dataset",
            after={"role": "reader", "token": "secret-token-that-will-be-redacted"},
            policy_ids=("analyst_read_internal",),
            control_ids=("AC-01", "DG-ACCESS-REVIEW"),
            risk_score=35,
            retention_class=RetentionClass.REGULATORY,
        )
    )
    audit.record(
        AuditEventInput(
            category=AuditCategory.ACCESS,
            action=AuditAction.READ,
            outcome=AuditOutcome.SUCCESS,
            actor=actor,
            resource=resource,
            context=context,
            message="Dataset read by analyst",
        )
    )

    print(json.dumps(audit.summarize(), indent=2, ensure_ascii=False))
    print(json.dumps(audit.verify_integrity().to_dict(), indent=2, ensure_ascii=False))
    print(audit.export(fmt=ExportFormat.JSONL).decode("utf-8"))
