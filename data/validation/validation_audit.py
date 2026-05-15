"""
data/validation/validation_audit.py

Enterprise-grade validation audit module.

Este módulo centraliza a auditoria de validações de dados, contratos, qualidade,
PII, integridade, consistência, compliance e drift.

Capacidades principais:
- Eventos de auditoria estruturados, serializáveis e rastreáveis.
- Correlação por run_id, correlation_id, dataset, pipeline, tenant e ambiente.
- Severidade, categoria, status, origem, destino e evidências seguras.
- Trilha de auditoria em memória, arquivo JSONL e sinks plugáveis.
- Sanitização automática para evitar vazamento de PII/segredos nos logs.
- Hash determinístico de payload para rastreabilidade e deduplicação.
- Agregação de estatísticas por execução, dataset, regra e severidade.
- Exportação/importação JSON/JSONL para integração com SIEM, lakehouse e observabilidade.
- Design tipado, defensivo e pronto para uso enterprise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import statistics
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class AuditSeverity(str, Enum):
    """Severidade do evento de auditoria."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AuditStatus(str, Enum):
    """Status de um evento ou operação auditada."""

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class AuditCategory(str, Enum):
    """Categoria funcional do evento."""

    VALIDATION = "VALIDATION"
    QUALITY = "QUALITY"
    SCHEMA = "SCHEMA"
    INTEGRITY = "INTEGRITY"
    PII = "PII"
    COMPLIANCE = "COMPLIANCE"
    CONSISTENCY = "CONSISTENCY"
    CONTRACT = "CONTRACT"
    DRIFT = "DRIFT"
    SECURITY = "SECURITY"
    OBSERVABILITY = "OBSERVABILITY"
    SYSTEM = "SYSTEM"
    CUSTOM = "CUSTOM"


class AuditAction(str, Enum):
    """Tipo de ação auditável."""

    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_FINISHED = "VALIDATION_FINISHED"
    RULE_EVALUATED = "RULE_EVALUATED"
    ISSUE_DETECTED = "ISSUE_DETECTED"
    VIOLATION_DETECTED = "VIOLATION_DETECTED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    CONTRACT_EVALUATED = "CONTRACT_EVALUATED"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    EXPORT_CREATED = "EXPORT_CREATED"
    CONFIG_LOADED = "CONFIG_LOADED"
    ERROR_RAISED = "ERROR_RAISED"
    CUSTOM = "CUSTOM"


class AuditWriteError(Exception):
    """Erro ao persistir evento de auditoria."""


class AuditConfigurationError(Exception):
    """Erro de configuração de auditoria."""


class AuditSink(Protocol):
    """Contrato de destino de auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Persiste ou encaminha um evento de auditoria."""


@dataclass(frozen=True)
class AuditIdentity:
    """Identidade operacional da origem do evento."""

    service_name: str = "data-validation"
    service_version: Optional[str] = None
    host: Optional[str] = None
    user: Optional[str] = None
    process_id: Optional[int] = None
    runtime: str = "python"

    def to_dict(self) -> JsonDict:
        return {
            "service_name": self.service_name,
            "service_version": self.service_version,
            "host": self.host or os.getenv("HOSTNAME") or os.getenv("COMPUTERNAME"),
            "user": self.user or os.getenv("USER") or os.getenv("USERNAME"),
            "process_id": self.process_id or os.getpid(),
            "runtime": self.runtime,
        }


@dataclass(frozen=True)
class AuditContext:
    """Contexto de correlação para auditoria de validações."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    data_product: Optional[str] = None
    data_owner: Optional[str] = None
    rule_id: Optional[str] = None
    rule_type: Optional[str] = None
    validation_type: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self) -> Dict[str, str]:
        return {
            "dataset": self.dataset_name,
            "pipeline": self.pipeline_name or "unknown",
            "environment": self.environment,
            "tenant": self.tenant_id or "default",
            "source": self.source_system or "unknown",
            "product": self.data_product or "unknown",
            "owner": self.data_owner or "unknown",
            "validation_type": self.validation_type or "unknown",
        }

    def to_dict(self) -> JsonDict:
        return {
            "dataset_name": self.dataset_name,
            "pipeline_name": self.pipeline_name,
            "environment": self.environment,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "source_system": self.source_system,
            "data_product": self.data_product,
            "data_owner": self.data_owner,
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "validation_type": self.validation_type,
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class AuditEvent:
    """Evento enterprise de auditoria de validação."""

    event_name: str
    category: AuditCategory
    action: AuditAction
    status: AuditStatus
    severity: AuditSeverity
    context: AuditContext
    payload: Mapping[str, Any] = field(default_factory=dict)
    identity: AuditIdentity = field(default_factory=AuditIdentity)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = None
    schema_version: str = "1.0"

    @property
    def payload_hash(self) -> str:
        return hash_payload(self.payload)

    def to_dict(self, *, sanitize: bool = True) -> JsonDict:
        payload = sanitize_payload(self.payload) if sanitize else safe_json_value(dict(self.payload))
        return {
            "event_id": self.event_id,
            "event_name": self.event_name,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at.isoformat(),
            "category": self.category.value,
            "action": self.action.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "context": self.context.to_dict(),
            "identity": self.identity.to_dict(),
            "duration_ms": self.duration_ms,
            "payload_hash": hash_payload(payload),
            "payload": payload,
        }

    def to_json(self, *, sanitize: bool = True) -> str:
        return json.dumps(self.to_dict(sanitize=sanitize), ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class AuditSummary:
    """Resumo agregado de eventos auditados."""

    total_events: int
    by_status: Mapping[str, int]
    by_severity: Mapping[str, int]
    by_category: Mapping[str, int]
    by_dataset: Mapping[str, int]
    first_event_at: Optional[datetime]
    last_event_at: Optional[datetime]
    duration_ms_avg: Optional[float]
    duration_ms_p95: Optional[float]

    def to_dict(self) -> JsonDict:
        return {
            "total_events": self.total_events,
            "by_status": dict(self.by_status),
            "by_severity": dict(self.by_severity),
            "by_category": dict(self.by_category),
            "by_dataset": dict(self.by_dataset),
            "first_event_at": self.first_event_at.isoformat() if self.first_event_at else None,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "duration_ms_avg": self.duration_ms_avg,
            "duration_ms_p95": self.duration_ms_p95,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class ValidationAuditLogger:
    """Logger central para trilhas de auditoria de validação."""

    def __init__(
        self,
        *,
        sinks: Optional[Sequence[AuditSink]] = None,
        identity: Optional[AuditIdentity] = None,
        sanitize: bool = True,
        fail_on_sink_error: bool = False,
        keep_memory: bool = True,
        max_memory_events: int = 10_000,
    ) -> None:
        self.sinks = list(sinks or [])
        self.identity = identity or AuditIdentity()
        self.sanitize = sanitize
        self.fail_on_sink_error = fail_on_sink_error
        self.keep_memory = keep_memory
        self.max_memory_events = max_memory_events
        self._events: List[AuditEvent] = []
        self._lock = threading.RLock()

    @property
    def events(self) -> Tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def add_sink(self, sink: AuditSink) -> None:
        with self._lock:
            self.sinks.append(sink)

    def emit(
        self,
        *,
        event_name: str,
        category: AuditCategory,
        action: AuditAction,
        status: AuditStatus,
        severity: AuditSeverity,
        context: AuditContext,
        payload: Optional[Mapping[str, Any]] = None,
        duration_ms: Optional[float] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_name=event_name,
            category=category,
            action=action,
            status=status,
            severity=severity,
            context=context,
            payload=payload or {},
            identity=self.identity,
            duration_ms=duration_ms,
        )
        self.write(event)
        return event

    def write(self, event: AuditEvent) -> None:
        event_payload = event.to_dict(sanitize=self.sanitize)
        errors: List[str] = []

        with self._lock:
            if self.keep_memory:
                self._events.append(event)
                if len(self._events) > self.max_memory_events:
                    self._events = self._events[-self.max_memory_events :]

            for sink in self.sinks:
                try:
                    sink.emit(event_payload)
                except Exception as exc:
                    logger.exception("Audit sink failed")
                    errors.append(str(exc))

        if errors and self.fail_on_sink_error:
            raise AuditWriteError("One or more audit sinks failed: " + "; ".join(errors))

    def validation_started(
        self,
        context: AuditContext,
        *,
        category: AuditCategory = AuditCategory.VALIDATION,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> AuditEvent:
        return self.emit(
            event_name="validation_started",
            category=category,
            action=AuditAction.VALIDATION_STARTED,
            status=AuditStatus.STARTED,
            severity=AuditSeverity.INFO,
            context=context,
            payload=payload,
        )

    def validation_finished(
        self,
        context: AuditContext,
        *,
        category: AuditCategory = AuditCategory.VALIDATION,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        severity: AuditSeverity = AuditSeverity.INFO,
        payload: Optional[Mapping[str, Any]] = None,
        duration_ms: Optional[float] = None,
    ) -> AuditEvent:
        return self.emit(
            event_name="validation_finished",
            category=category,
            action=AuditAction.VALIDATION_FINISHED,
            status=status,
            severity=severity,
            context=context,
            payload=payload,
            duration_ms=duration_ms,
        )

    def rule_evaluated(
        self,
        context: AuditContext,
        *,
        status: AuditStatus,
        severity: AuditSeverity,
        payload: Optional[Mapping[str, Any]] = None,
        duration_ms: Optional[float] = None,
    ) -> AuditEvent:
        return self.emit(
            event_name="rule_evaluated",
            category=AuditCategory.VALIDATION,
            action=AuditAction.RULE_EVALUATED,
            status=status,
            severity=severity,
            context=context,
            payload=payload,
            duration_ms=duration_ms,
        )

    def issue_detected(
        self,
        context: AuditContext,
        *,
        category: AuditCategory,
        severity: AuditSeverity,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> AuditEvent:
        return self.emit(
            event_name="issue_detected",
            category=category,
            action=AuditAction.ISSUE_DETECTED,
            status=AuditStatus.WARNING if severity in {AuditSeverity.INFO, AuditSeverity.WARNING} else AuditStatus.FAILED,
            severity=severity,
            context=context,
            payload=payload,
        )

    def error(
        self,
        context: AuditContext,
        *,
        category: AuditCategory = AuditCategory.SYSTEM,
        error: Union[str, Exception],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> AuditEvent:
        merged_payload = dict(payload or {})
        merged_payload["error"] = str(error)
        merged_payload["error_type"] = type(error).__name__ if isinstance(error, Exception) else "Error"
        return self.emit(
            event_name="validation_error",
            category=category,
            action=AuditAction.ERROR_RAISED,
            status=AuditStatus.ERROR,
            severity=AuditSeverity.CRITICAL,
            context=context,
            payload=merged_payload,
        )

    def summary(self, events: Optional[Sequence[AuditEvent]] = None) -> AuditSummary:
        selected = list(events) if events is not None else list(self.events)
        durations = [event.duration_ms for event in selected if event.duration_ms is not None]
        occurred = [event.occurred_at for event in selected]
        return AuditSummary(
            total_events=len(selected),
            by_status=Counter(event.status.value for event in selected),
            by_severity=Counter(event.severity.value for event in selected),
            by_category=Counter(event.category.value for event in selected),
            by_dataset=Counter(event.context.dataset_name for event in selected),
            first_event_at=min(occurred) if occurred else None,
            last_event_at=max(occurred) if occurred else None,
            duration_ms_avg=statistics.mean(durations) if durations else None,
            duration_ms_p95=percentile(durations, 0.95) if durations else None,
        )

    def export_jsonl(self, path: Union[str, Path], *, sanitize: Optional[bool] = None) -> Path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        use_sanitize = self.sanitize if sanitize is None else sanitize
        with output_path.open("w", encoding="utf-8") as file:
            for event in self.events:
                file.write(event.to_json(sanitize=use_sanitize) + "\n")
        return output_path

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


class InMemoryAuditSink:
    """Sink em memória para testes, desenvolvimento local e inspeção."""

    def __init__(self, max_events: int = 100_000) -> None:
        self.max_events = max_events
        self.events: List[Mapping[str, Any]] = []
        self._lock = threading.RLock()

    def emit(self, event: Mapping[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))
            if len(self.events) > self.max_events:
                self.events = self.events[-self.max_events :]

    def query(
        self,
        *,
        dataset_name: Optional[str] = None,
        category: Optional[AuditCategory] = None,
        severity: Optional[AuditSeverity] = None,
        status: Optional[AuditStatus] = None,
    ) -> List[Mapping[str, Any]]:
        with self._lock:
            result = list(self.events)
        if dataset_name is not None:
            result = [e for e in result if e.get("context", {}).get("dataset_name") == dataset_name]
        if category is not None:
            result = [e for e in result if e.get("category") == category.value]
        if severity is not None:
            result = [e for e in result if e.get("severity") == severity.value]
        if status is not None:
            result = [e for e in result if e.get("status") == status.value]
        return result


class JsonLineAuditSink:
    """Sink que persiste eventos em arquivo JSONL."""

    def __init__(self, path: Union[str, Path], *, flush: bool = True) -> None:
        self.path = Path(path)
        self.flush = flush
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def emit(self, event: Mapping[str, Any]) -> None:
        try:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(safe_json_value(dict(event)), ensure_ascii=False, sort_keys=True, default=str) + "\n")
                    if self.flush:
                        file.flush()
        except Exception as exc:
            raise AuditWriteError(f"Failed to write audit event to {self.path}: {exc}") from exc


class LoggingAuditSink:
    """Sink que encaminha eventos para o logging padrão Python."""

    def __init__(self, logger_name: str = "data.validation.audit") -> None:
        self.logger = logging.getLogger(logger_name)

    def emit(self, event: Mapping[str, Any]) -> None:
        severity = str(event.get("severity", "INFO"))
        message = json.dumps(safe_json_value(dict(event)), ensure_ascii=False, sort_keys=True, default=str)
        if severity == AuditSeverity.CRITICAL.value:
            self.logger.critical(message)
        elif severity == AuditSeverity.ERROR.value:
            self.logger.error(message)
        elif severity == AuditSeverity.WARNING.value:
            self.logger.warning(message)
        elif severity == AuditSeverity.DEBUG.value:
            self.logger.debug(message)
        else:
            self.logger.info(message)


class CompositeAuditSink:
    """Sink composto para fan-out controlado."""

    def __init__(self, sinks: Sequence[AuditSink], *, fail_fast: bool = False) -> None:
        self.sinks = list(sinks)
        self.fail_fast = fail_fast

    def emit(self, event: Mapping[str, Any]) -> None:
        errors: List[str] = []
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                errors.append(str(exc))
                if self.fail_fast:
                    raise
        if errors:
            logger.warning("CompositeAuditSink completed with sink errors: %s", errors)


class AuditTimer:
    """Context manager para medir duração de operações auditáveis."""

    def __init__(self) -> None:
        self.started_perf: Optional[float] = None
        self.finished_perf: Optional[float] = None

    def __enter__(self) -> "AuditTimer":
        self.started_perf = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finished_perf = time.perf_counter()

    @property
    def duration_ms(self) -> float:
        if self.started_perf is None:
            return 0.0
        end = self.finished_perf if self.finished_perf is not None else time.perf_counter()
        return max(0.0, (end - self.started_perf) * 1000.0)


SECRET_KEY_PATTERNS = (
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"passwd", re.IGNORECASE),
    re.compile(r"senha", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"bearer", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
)

PII_VALUE_PATTERNS = (
    re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE),
    re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
)


def sanitize_payload(payload: Mapping[str, Any]) -> JsonDict:
    """Sanitiza payload recursivamente removendo segredos e mascarando PII comum."""
    return safe_json_value(_sanitize_value(payload, key_path=""))


def _sanitize_value(value: Any, *, key_path: str) -> Any:
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{key_path}.{key_text}" if key_path else key_text
            if _is_secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = _sanitize_value(item, key_path=next_path)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item, key_path=key_path) for item in value]
    if isinstance(value, str):
        return mask_pii_text(value)
    return value


def mask_pii_text(value: str) -> str:
    """Mascara PII comum em texto livre."""
    text = value
    text = re.sub(r"([A-Z0-9._%+-])[A-Z0-9._%+-]*(@[A-Z0-9.-]+\.[A-Z]{2,})", r"\1***\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b", "***.***.***-**", text)
    text = re.sub(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b", "**.***.***/****-**", text)

    def _mask_card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            return "**** **** **** " + digits[-4:]
        return match.group(0)

    text = re.sub(r"\b(?:\d[ -]*?){13,19}\b", _mask_card, text)
    return text


def _is_secret_key(key: str) -> bool:
    return any(pattern.search(key) for pattern in SECRET_KEY_PATTERNS)


def hash_payload(payload: Mapping[str, Any]) -> str:
    """Calcula hash SHA-256 determinístico do payload."""
    canonical = json.dumps(safe_json_value(dict(payload)), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários em estrutura JSON segura."""
    if isinstance(value, Mapping):
        return {str(k): safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Calcula percentil simples sem dependências externas."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    weight = pos - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def load_jsonl_events(path: Union[str, Path]) -> List[Mapping[str, Any]]:
    """Carrega eventos de auditoria a partir de arquivo JSONL."""
    input_path = Path(path)
    if not input_path.exists():
        raise AuditConfigurationError(f"Audit JSONL file does not exist: {input_path}")
    events: List[Mapping[str, Any]] = []
    with input_path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                events.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise AuditConfigurationError(f"Invalid JSONL audit file at line {line_number}: {exc}") from exc
    return events


def summarize_event_dicts(events: Sequence[Mapping[str, Any]]) -> AuditSummary:
    """Gera resumo a partir de eventos já serializados em dicionário."""
    durations = [float(e["duration_ms"]) for e in events if e.get("duration_ms") is not None]
    occurred: List[datetime] = []
    for event in events:
        raw_ts = event.get("occurred_at")
        if raw_ts:
            try:
                occurred.append(datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")))
            except Exception:
                pass
    return AuditSummary(
        total_events=len(events),
        by_status=Counter(str(e.get("status", "unknown")) for e in events),
        by_severity=Counter(str(e.get("severity", "unknown")) for e in events),
        by_category=Counter(str(e.get("category", "unknown")) for e in events),
        by_dataset=Counter(str(e.get("context", {}).get("dataset_name", "unknown")) for e in events),
        first_event_at=min(occurred) if occurred else None,
        last_event_at=max(occurred) if occurred else None,
        duration_ms_avg=statistics.mean(durations) if durations else None,
        duration_ms_p95=percentile(durations, 0.95) if durations else None,
    )


def build_default_audit_logger(
    *,
    jsonl_path: Optional[Union[str, Path]] = None,
    enable_logging_sink: bool = True,
    keep_memory: bool = True,
    service_name: str = "data-validation",
    service_version: Optional[str] = None,
) -> ValidationAuditLogger:
    """Factory para logger de auditoria padrão enterprise."""
    sinks: List[AuditSink] = []
    if jsonl_path is not None:
        sinks.append(JsonLineAuditSink(jsonl_path))
    if enable_logging_sink:
        sinks.append(LoggingAuditSink())
    return ValidationAuditLogger(
        sinks=sinks,
        identity=AuditIdentity(service_name=service_name, service_version=service_version),
        keep_memory=keep_memory,
        sanitize=True,
    )


__all__ = [
    "AuditAction",
    "AuditCategory",
    "AuditConfigurationError",
    "AuditContext",
    "AuditEvent",
    "AuditIdentity",
    "AuditSeverity",
    "AuditSink",
    "AuditStatus",
    "AuditSummary",
    "AuditTimer",
    "AuditWriteError",
    "CompositeAuditSink",
    "InMemoryAuditSink",
    "JsonLineAuditSink",
    "LoggingAuditSink",
    "ValidationAuditLogger",
    "build_default_audit_logger",
    "hash_payload",
    "load_jsonl_events",
    "mask_pii_text",
    "percentile",
    "safe_json_value",
    "sanitize_payload",
    "summarize_event_dicts",
]
