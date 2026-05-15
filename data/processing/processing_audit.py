"""
data/processing/processing_audit.py

Enterprise-grade processing audit trail for data platforms.

Purpose
-------
Provides a robust, dependency-light audit module for ETL/ELT, batch,
streaming, enrichment, cleaning, validation, feature engineering and operational
processing workflows.

Core capabilities
-----------------
- Immutable audit event model.
- Event severity, category, status and actor metadata.
- Processing lineage: dataset, pipeline, job, run, stage, input/output refs.
- Tamper-evident hash chaining.
- JSONL append-only audit sink.
- In-memory sink for tests and embedded workflows.
- Optional composite sink.
- PII/secret redaction and safe metadata handling.
- Query/filter helper over local audit events.
- Decorators and context managers for audited operations.
- Optional telemetry integration.
- Standard library only.

Example
-------
auditor = ProcessingAuditor()

with auditor.audit_operation("daily_sales.clean", pipeline="sales", dataset="orders"):
    run_cleaning()

auditor.record(
    event_type=AuditEventType.DATA_QUALITY_CHECK,
    message="Null check completed",
    status=AuditStatus.SUCCEEDED,
    metrics={"null_count": 0},
)
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import functools
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
MAX_TEXT_LENGTH = 50_000

_CTX_RUN_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_run_id", default=None)
_CTX_PIPELINE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_pipeline", default=None)
_CTX_DATASET: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_dataset", default=None)
_CTX_STAGE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_stage", default=None)
_CTX_CORRELATION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_correlation_id", default=None)
_CTX_ACTOR: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("processing_audit_actor", default=None)


class AuditEventType(str, Enum):
    PROCESS_STARTED = "process_started"
    PROCESS_COMPLETED = "process_completed"
    PROCESS_FAILED = "process_failed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    RECORD_PROCESSED = "record_processed"
    BATCH_PROCESSED = "batch_processed"
    DATA_QUALITY_CHECK = "data_quality_check"
    VALIDATION = "validation"
    CLEANING = "cleaning"
    NORMALIZATION = "normalization"
    DEDUPLICATION = "deduplication"
    ENRICHMENT = "enrichment"
    AGGREGATION = "aggregation"
    FEATURE_ENGINEERING = "feature_engineering"
    OUTLIER_DETECTION = "outlier_detection"
    ANOMALY_DETECTION = "anomaly_detection"
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    DEAD_LETTER_WRITTEN = "dead_letter_written"
    CONFIG_CHANGED = "config_changed"
    ACCESS = "access"
    CUSTOM = "custom"


class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AuditCategory(str, Enum):
    OPERATIONAL = "operational"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    DATA_QUALITY = "data_quality"
    LINEAGE = "lineage"
    PERFORMANCE = "performance"
    CONFIGURATION = "configuration"
    CUSTOM = "custom"


@dataclass(frozen=True)
class AuditIdentity:
    actor: Optional[str] = None
    actor_type: Optional[str] = None
    service: Optional[str] = None
    host: Optional[str] = None
    environment: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AuditIdentity":
        return cls(
            actor=os.getenv("PROCESSING_AUDIT_ACTOR") or os.getenv("USER") or os.getenv("USERNAME"),
            actor_type=os.getenv("PROCESSING_AUDIT_ACTOR_TYPE", "service"),
            service=os.getenv("SERVICE_NAME", os.getenv("PROCESSING_SERVICE_NAME", "data-platform")),
            host=os.getenv("HOSTNAME"),
            environment=os.getenv("ENVIRONMENT", os.getenv("PROCESSING_ENVIRONMENT", "development")),
            tenant_id=os.getenv("TENANT_ID"),
            user_id=os.getenv("USER_ID"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class AuditLineage:
    pipeline: Optional[str] = None
    dataset: Optional[str] = None
    job_id: Optional[str] = None
    run_id: Optional[str] = None
    stage: Optional[str] = None
    input_refs: Tuple[str, ...] = field(default_factory=tuple)
    output_refs: Tuple[str, ...] = field(default_factory=tuple)
    schema_version: Optional[str] = None
    code_version: Optional[str] = None
    config_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class AuditEvent:
    id: str
    timestamp: str
    event_type: AuditEventType
    severity: AuditSeverity
    status: AuditStatus
    category: AuditCategory
    message: str
    identity: AuditIdentity
    lineage: AuditLineage
    correlation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    duration_ms: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    attributes: Dict[str, Any] = field(default_factory=dict)
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None

    def canonical_payload(self, *, include_hash: bool = False) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "status": self.status.value,
            "category": self.category.value,
            "message": self.message,
            "identity": self.identity.to_dict(),
            "lineage": self.lineage.to_dict(),
            "correlation_id": self.correlation_id,
            "parent_event_id": self.parent_event_id,
            "duration_ms": self.duration_ms,
            "metrics": sanitize_mapping(self.metrics),
            "attributes": sanitize_mapping(self.attributes),
            "previous_hash": self.previous_hash,
        }
        if include_hash:
            payload["event_hash"] = self.event_hash
        return payload

    def compute_hash(self) -> str:
        raw = json.dumps(self.canonical_payload(include_hash=False), ensure_ascii=False, sort_keys=True, default=safe_json_default)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def with_hash(self, previous_hash: Optional[str]) -> "AuditEvent":
        event = dataclasses.replace(self, previous_hash=previous_hash, event_hash=None)
        return dataclasses.replace(event, event_hash=event.compute_hash())

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(self.canonical_payload(include_hash=True))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=safe_json_default)


@dataclass(frozen=True)
class AuditQuery:
    event_types: Optional[Tuple[AuditEventType, ...]] = None
    severities: Optional[Tuple[AuditSeverity, ...]] = None
    statuses: Optional[Tuple[AuditStatus, ...]] = None
    pipeline: Optional[str] = None
    dataset: Optional[str] = None
    run_id: Optional[str] = None
    stage: Optional[str] = None
    correlation_id: Optional[str] = None
    text: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = None


@dataclass(frozen=True)
class AuditSummary:
    total_events: int
    by_type: Dict[str, int]
    by_status: Dict[str, int]
    by_severity: Dict[str, int]
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]
    chain_valid: bool
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None:
        ...

    def flush(self) -> None:
        ...


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: List[AuditEvent] = []
        self._lock = threading.RLock()

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            self.events.append(event)

    def flush(self) -> None:
        return None

    def query(self, query: Optional[AuditQuery] = None) -> List[AuditEvent]:
        with self._lock:
            events = list(self.events)
        return filter_events(events, query)


class JsonlAuditSink:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_json() + "\n")

    def flush(self) -> None:
        return None

    def read_all(self) -> List[AuditEvent]:
        if not self.path.exists():
            return []
        events: List[AuditEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                events.append(event_from_dict(json.loads(line)))
        return events


class CompositeAuditSink:
    def __init__(self, sinks: Sequence[AuditSink]) -> None:
        self.sinks = list(sinks)

    def write(self, event: AuditEvent) -> None:
        for sink in self.sinks:
            try:
                sink.write(event)
            except Exception:
                logger.exception("Audit sink failed: %s", sink.__class__.__name__)

    def flush(self) -> None:
        for sink in self.sinks:
            with contextlib.suppress(Exception):
                sink.flush()


@dataclass(frozen=True)
class ProcessingAuditConfig:
    enabled: bool = True
    audit_path: Optional[str] = None
    hash_chain_enabled: bool = True
    telemetry_enabled: bool = True
    service_name: str = "data-platform"
    environment: str = "development"
    fail_open: bool = True

    @classmethod
    def from_env(cls) -> "ProcessingAuditConfig":
        return cls(
            enabled=bool_env("PROCESSING_AUDIT_ENABLED", True),
            audit_path=os.getenv("PROCESSING_AUDIT_PATH"),
            hash_chain_enabled=bool_env("PROCESSING_AUDIT_HASH_CHAIN_ENABLED", True),
            telemetry_enabled=bool_env("PROCESSING_AUDIT_TELEMETRY_ENABLED", True),
            service_name=os.getenv("SERVICE_NAME", os.getenv("PROCESSING_SERVICE_NAME", "data-platform")),
            environment=os.getenv("ENVIRONMENT", os.getenv("PROCESSING_ENVIRONMENT", "development")),
            fail_open=bool_env("PROCESSING_AUDIT_FAIL_OPEN", True),
        )


class ProcessingAuditError(Exception):
    """Base processing audit error."""


class AuditChainError(ProcessingAuditError):
    """Audit hash chain validation failed."""


class ProcessingAuditor:
    """Enterprise processing auditor."""

    def __init__(
        self,
        config: Optional[ProcessingAuditConfig] = None,
        sink: Optional[AuditSink] = None,
        identity: Optional[AuditIdentity] = None,
    ) -> None:
        self.config = config or ProcessingAuditConfig.from_env()
        self.identity = identity or AuditIdentity.from_env()
        if sink is not None:
            self.sink = sink
        elif self.config.audit_path:
            self.sink = JsonlAuditSink(self.config.audit_path)
        else:
            self.sink = InMemoryAuditSink()
        self._lock = threading.RLock()
        self._last_hash: Optional[str] = None

    def record(
        self,
        *,
        event_type: AuditEventType = AuditEventType.CUSTOM,
        message: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        status: AuditStatus = AuditStatus.UNKNOWN,
        category: AuditCategory = AuditCategory.OPERATIONAL,
        lineage: Optional[AuditLineage] = None,
        identity: Optional[AuditIdentity] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            timestamp=utc_now_iso(),
            event_type=event_type,
            severity=severity,
            status=status,
            category=category,
            message=truncate_text(message, MAX_TEXT_LENGTH),
            identity=identity or self._current_identity(),
            lineage=lineage or self._current_lineage(),
            correlation_id=correlation_id or _CTX_CORRELATION_ID.get(),
            parent_event_id=parent_event_id,
            duration_ms=round(duration_ms, 3) if duration_ms is not None else None,
            metrics=sanitize_mapping(dict(metrics or {})),
            attributes=sanitize_mapping(dict(attributes or {})),
        )
        if not self.config.enabled:
            return event
        with self._lock:
            final_event = event.with_hash(self._last_hash) if self.config.hash_chain_enabled else event
            try:
                self.sink.write(final_event)
                self._last_hash = final_event.event_hash or self._last_hash
                telemetry_metric("processing_audit.events_total", 1, self.config.telemetry_enabled)
                return final_event
            except Exception:
                logger.exception("Failed to write processing audit event")
                if not self.config.fail_open:
                    raise
                return final_event

    @contextlib.contextmanager
    def context(
        self,
        *,
        pipeline: Optional[str] = None,
        dataset: Optional[str] = None,
        run_id: Optional[str] = None,
        stage: Optional[str] = None,
        correlation_id: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> Iterator[AuditLineage]:
        tokens: List[Tuple[contextvars.ContextVar[Any], contextvars.Token[Any]]] = []

        def set_if_present(var: contextvars.ContextVar[Any], value: Any) -> None:
            if value is not None:
                tokens.append((var, var.set(value)))

        set_if_present(_CTX_PIPELINE, pipeline)
        set_if_present(_CTX_DATASET, dataset)
        set_if_present(_CTX_RUN_ID, run_id or _CTX_RUN_ID.get() or str(uuid.uuid4()))
        set_if_present(_CTX_STAGE, stage)
        set_if_present(_CTX_CORRELATION_ID, correlation_id or _CTX_CORRELATION_ID.get() or str(uuid.uuid4()))
        set_if_present(_CTX_ACTOR, actor)
        try:
            yield self._current_lineage()
        finally:
            for var, token in reversed(tokens):
                var.reset(token)

    @contextlib.contextmanager
    def audit_operation(
        self,
        name: str,
        *,
        pipeline: Optional[str] = None,
        dataset: Optional[str] = None,
        stage: Optional[str] = None,
        event_type: AuditEventType = AuditEventType.STAGE_STARTED,
        success_event_type: AuditEventType = AuditEventType.STAGE_COMPLETED,
        failure_event_type: AuditEventType = AuditEventType.STAGE_FAILED,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[AuditEvent]:
        started = time.perf_counter()
        with self.context(pipeline=pipeline, dataset=dataset, stage=stage or name):
            start_event = self.record(
                event_type=event_type,
                message=f"Processing operation started: {name}",
                status=AuditStatus.STARTED,
                category=AuditCategory.OPERATIONAL,
                attributes=attributes,
            )
            try:
                yield start_event
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000.0
                self.record(
                    event_type=failure_event_type,
                    message=f"Processing operation failed: {name}",
                    severity=AuditSeverity.ERROR,
                    status=AuditStatus.FAILED,
                    category=AuditCategory.OPERATIONAL,
                    parent_event_id=start_event.id,
                    duration_ms=duration_ms,
                    attributes={"error_type": exc.__class__.__name__, "error_message": str(exc), **dict(attributes or {})},
                )
                raise
            else:
                duration_ms = (time.perf_counter() - started) * 1000.0
                self.record(
                    event_type=success_event_type,
                    message=f"Processing operation completed: {name}",
                    status=AuditStatus.SUCCEEDED,
                    category=AuditCategory.OPERATIONAL,
                    parent_event_id=start_event.id,
                    duration_ms=duration_ms,
                    attributes=attributes,
                )

    def audited(
        self,
        name: Optional[str] = None,
        *,
        event_type: AuditEventType = AuditEventType.STAGE_STARTED,
        category: AuditCategory = AuditCategory.OPERATIONAL,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            operation_name = name or f"{func.__module__}.{func.__qualname__}"

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.audit_operation(operation_name, event_type=event_type, attributes={"function": func.__qualname__, "category": category.value}):
                    return func(*args, **kwargs)

            return cast(F, wrapper)

        return decorator

    def query(self, query: Optional[AuditQuery] = None) -> List[AuditEvent]:
        if isinstance(self.sink, InMemoryAuditSink):
            return self.sink.query(query)
        if isinstance(self.sink, JsonlAuditSink):
            return filter_events(self.sink.read_all(), query)
        return []

    def summarize(self, events: Optional[Sequence[AuditEvent]] = None) -> AuditSummary:
        data = list(events) if events is not None else self.query()
        by_type: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}
        for event in data:
            by_type[event.event_type.value] = by_type.get(event.event_type.value, 0) + 1
            by_status[event.status.value] = by_status.get(event.status.value, 0) + 1
            by_severity[event.severity.value] = by_severity.get(event.severity.value, 0) + 1
        chain_valid, issues = validate_hash_chain(data)
        timestamps = [event.timestamp for event in data]
        return AuditSummary(
            total_events=len(data),
            by_type=by_type,
            by_status=by_status,
            by_severity=by_severity,
            first_timestamp=min(timestamps) if timestamps else None,
            last_timestamp=max(timestamps) if timestamps else None,
            chain_valid=chain_valid,
            issues=issues,
        )

    def export_json(self, path: str | os.PathLike[str], events: Optional[Sequence[AuditEvent]] = None) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": utc_now_iso(),
            "summary": self.summarize(events).to_dict(),
            "events": [event.to_dict() for event in (events if events is not None else self.query())],
        }
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=safe_json_default), encoding="utf-8")
        return target

    def flush(self) -> None:
        self.sink.flush()

    def _current_identity(self) -> AuditIdentity:
        actor = _CTX_ACTOR.get() or self.identity.actor
        return dataclasses.replace(self.identity, actor=actor, service=self.config.service_name, environment=self.config.environment)

    def _current_lineage(self) -> AuditLineage:
        return AuditLineage(
            pipeline=_CTX_PIPELINE.get(),
            dataset=_CTX_DATASET.get(),
            run_id=_CTX_RUN_ID.get(),
            stage=_CTX_STAGE.get(),
        )


def filter_events(events: Sequence[AuditEvent], query: Optional[AuditQuery]) -> List[AuditEvent]:
    if query is None:
        return list(events)
    result: List[AuditEvent] = []
    for event in events:
        if query.event_types and event.event_type not in query.event_types:
            continue
        if query.severities and event.severity not in query.severities:
            continue
        if query.statuses and event.status not in query.statuses:
            continue
        if query.pipeline and event.lineage.pipeline != query.pipeline:
            continue
        if query.dataset and event.lineage.dataset != query.dataset:
            continue
        if query.run_id and event.lineage.run_id != query.run_id:
            continue
        if query.stage and event.lineage.stage != query.stage:
            continue
        if query.correlation_id and event.correlation_id != query.correlation_id:
            continue
        if query.text and query.text.lower() not in event.message.lower() and query.text.lower() not in json.dumps(event.attributes, default=str).lower():
            continue
        event_ts = parse_datetime(event.timestamp)
        if query.start_time and event_ts < ensure_tz(query.start_time):
            continue
        if query.end_time and event_ts > ensure_tz(query.end_time):
            continue
        result.append(event)
        if query.limit is not None and len(result) >= query.limit:
            break
    return result


def validate_hash_chain(events: Sequence[AuditEvent]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    previous_hash: Optional[str] = None
    for index, event in enumerate(events):
        if event.previous_hash != previous_hash:
            issues.append(f"event {index} previous_hash mismatch")
        expected = dataclasses.replace(event, event_hash=None).compute_hash()
        if event.event_hash and event.event_hash != expected:
            issues.append(f"event {index} event_hash mismatch")
        previous_hash = event.event_hash or expected
    return not issues, issues


def event_from_dict(data: Mapping[str, Any]) -> AuditEvent:
    identity_raw = dict(data.get("identity") or {})
    lineage_raw = dict(data.get("lineage") or {})
    return AuditEvent(
        id=str(data["id"]),
        timestamp=str(data["timestamp"]),
        event_type=AuditEventType(data.get("event_type", AuditEventType.CUSTOM.value)),
        severity=AuditSeverity(data.get("severity", AuditSeverity.INFO.value)),
        status=AuditStatus(data.get("status", AuditStatus.UNKNOWN.value)),
        category=AuditCategory(data.get("category", AuditCategory.OPERATIONAL.value)),
        message=str(data.get("message", "")),
        identity=AuditIdentity(**{k: identity_raw.get(k) for k in AuditIdentity.__dataclass_fields__}),
        lineage=AuditLineage(
            pipeline=lineage_raw.get("pipeline"),
            dataset=lineage_raw.get("dataset"),
            job_id=lineage_raw.get("job_id"),
            run_id=lineage_raw.get("run_id"),
            stage=lineage_raw.get("stage"),
            input_refs=tuple(lineage_raw.get("input_refs", []) or []),
            output_refs=tuple(lineage_raw.get("output_refs", []) or []),
            schema_version=lineage_raw.get("schema_version"),
            code_version=lineage_raw.get("code_version"),
            config_hash=lineage_raw.get("config_hash"),
        ),
        correlation_id=data.get("correlation_id"),
        parent_event_id=data.get("parent_event_id"),
        duration_ms=float(data["duration_ms"]) if data.get("duration_ms") is not None else None,
        metrics=dict(data.get("metrics") or {}),
        attributes=dict(data.get("attributes") or {}),
        previous_hash=data.get("previous_hash"),
        event_hash=data.get("event_hash"),
    )


def config_hash(config: Mapping[str, Any]) -> str:
    raw = json.dumps(sanitize_mapping(config), ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_datetime(value: str) -> datetime:
    return ensure_tz(datetime.fromisoformat(value.replace("Z", "+00:00")))


def ensure_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    output: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            output[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            output[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set)):
            output[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            output[key_str] = sanitize_value(value, depth=depth)
    return output


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (value != value):
            return None
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    return truncate_text(text, MAX_TEXT_LENGTH)


def truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 15] + "...[truncated]"


def telemetry_metric(name: str, value: float, enabled: bool) -> None:
    if not enabled:
        return
    try:
        from data.observability.telemetry import get_telemetry
        get_telemetry().gauge(name, value)
    except Exception:
        logger.debug("Processing audit telemetry metric failed", exc_info=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_default_auditor: Optional[ProcessingAuditor] = None
_default_lock = threading.RLock()


def get_default_auditor() -> ProcessingAuditor:
    global _default_auditor
    with _default_lock:
        if _default_auditor is None:
            _default_auditor = ProcessingAuditor()
        return _default_auditor


def configure_default_auditor(
    *,
    config: Optional[ProcessingAuditConfig] = None,
    sink: Optional[AuditSink] = None,
    identity: Optional[AuditIdentity] = None,
) -> ProcessingAuditor:
    global _default_auditor
    with _default_lock:
        _default_auditor = ProcessingAuditor(config=config, sink=sink, identity=identity)
        return _default_auditor


def audit_event(**kwargs: Any) -> AuditEvent:
    return get_default_auditor().record(**kwargs)


def audit_operation(name: str, **kwargs: Any) -> contextlib.AbstractContextManager[AuditEvent]:
    return get_default_auditor().audit_operation(name, **kwargs)


__all__ = [
    "AuditCategory",
    "AuditChainError",
    "AuditEvent",
    "AuditEventType",
    "AuditIdentity",
    "AuditLineage",
    "AuditQuery",
    "AuditSeverity",
    "AuditSink",
    "AuditStatus",
    "AuditSummary",
    "CompositeAuditSink",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "ProcessingAuditConfig",
    "ProcessingAuditError",
    "ProcessingAuditor",
    "audit_event",
    "audit_operation",
    "config_hash",
    "configure_default_auditor",
    "event_from_dict",
    "filter_events",
    "get_default_auditor",
    "validate_hash_chain",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    auditor = ProcessingAuditor(ProcessingAuditConfig(telemetry_enabled=False), sink=InMemoryAuditSink())
    with auditor.audit_operation("example.clean", pipeline="sales", dataset="orders"):
        auditor.record(
            event_type=AuditEventType.DATA_QUALITY_CHECK,
            message="Checked nulls",
            status=AuditStatus.SUCCEEDED,
            category=AuditCategory.DATA_QUALITY,
            metrics={"null_count": 0},
        )
    print(json.dumps(auditor.summarize().to_dict(), indent=2, ensure_ascii=False))
