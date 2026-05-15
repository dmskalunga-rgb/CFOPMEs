"""
data/ai/ai_audit.py

Módulo enterprise de auditoria para operações de IA/ML/LLM.

Objetivos:
- Registrar trilhas auditáveis de inferência, treinamento, avaliação, prompts,
  embeddings, uso de modelos, decisões automatizadas e erros.
- Padronizar eventos de auditoria com correlation_id, trace_id, tenant_id,
  user_id, modelo, provider, latência, status e custos.
- Proteger dados sensíveis com mascaramento/redaction configurável.
- Persistir auditoria em JSONL local ou sinks plugáveis.
- Fornecer integridade básica com hash encadeado opcional.
- Expor métricas internas de auditoria.
- Permitir integração simples com pipelines enterprise.

Dependências recomendadas:
    pip install pydantic

Exemplo rápido:
    audit = AIAuditLogger.from_env()
    audit.log_inference(
        model_name="gpt-enterprise",
        provider="custom",
        input_payload={"prompt": "..."},
        output_payload={"answer": "..."},
        status=AuditStatus.SUCCEEDED,
    )
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, Union

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependência ausente: instale com `pip install pydantic`.") from exc


# =============================================================================
# Logging
# =============================================================================

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(message)s | service=%(service)s host=%(host)s"
)


class ContextFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.host = socket.gethostname()

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service_name
        record.host = self.host
        return True


def build_logger(name: str = "data.ai.ai_audit") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "ai-audit")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums
# =============================================================================


class AuditEventType(str, Enum):
    INFERENCE_REQUEST = "inference_request"
    INFERENCE_RESPONSE = "inference_response"
    INFERENCE_ERROR = "inference_error"
    PROMPT_RENDERED = "prompt_rendered"
    PROMPT_REJECTED = "prompt_rejected"
    EMBEDDING_REQUEST = "embedding_request"
    EMBEDDING_RESPONSE = "embedding_response"
    MODEL_SELECTED = "model_selected"
    MODEL_DEPLOYED = "model_deployed"
    MODEL_DEPRECATED = "model_deprecated"
    EVALUATION_STARTED = "evaluation_started"
    EVALUATION_COMPLETED = "evaluation_completed"
    GUARDRAIL_TRIGGERED = "guardrail_triggered"
    POLICY_VIOLATION = "policy_violation"
    HUMAN_REVIEW_REQUESTED = "human_review_requested"
    HUMAN_REVIEW_COMPLETED = "human_review_completed"
    DATA_ACCESS = "data_access"
    FEATURE_GENERATED = "feature_generated"
    TRAINING_STARTED = "training_started"
    TRAINING_COMPLETED = "training_completed"
    CUSTOM = "custom"


class AuditStatus(str, Enum):
    CREATED = "created"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    WARNING = "warning"


class AuditSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RedactionMode(str, Enum):
    NONE = "none"
    MASK = "mask"
    HASH = "hash"
    REMOVE = "remove"


class AuditSinkType(str, Enum):
    JSONL = "jsonl"
    LOGGING = "logging"
    CALLBACK = "callback"
    MEMORY = "memory"


# =============================================================================
# Exceptions
# =============================================================================


class AIAuditError(Exception):
    """Erro base de auditoria de IA."""


class AIAuditConfigurationError(AIAuditError):
    """Erro de configuração de auditoria."""


class AIAuditSinkError(AIAuditError):
    """Erro em sink de auditoria."""


class AIAuditValidationError(AIAuditError):
    """Erro de validação de evento de auditoria."""


# =============================================================================
# Models
# =============================================================================


class AIUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None
    latency_ms: Optional[float] = None


class AIModelAuditRef(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    version: Optional[str] = None
    deployment_id: Optional[str] = None
    task_type: Optional[str] = None
    stage: Optional[str] = None


class AIAuditEvent(BaseModel):
    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: AuditEventType = AuditEventType.CUSTOM
    status: AuditStatus = AuditStatus.CREATED
    severity: AuditSeverity = AuditSeverity.INFO

    timestamp: str = Field(default_factory=lambda: utc_now_iso())
    service_name: str = Field(default_factory=lambda: os.getenv("SERVICE_NAME", "ai-audit"))
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    host: str = Field(default_factory=socket.gethostname)

    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = None
    span_id: Optional[str] = None

    model: Optional[AIModelAuditRef] = None
    usage: Optional[AIUsage] = None

    input_payload: Optional[Dict[str, Any]] = None
    output_payload: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    error_type: Optional[str] = None
    error_message: Optional[str] = None

    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None


@dataclass(frozen=True)
class RedactionRule:
    field_pattern: str
    mode: RedactionMode = RedactionMode.MASK
    mask_value: str = "***REDACTED***"
    hash_salt: Optional[str] = None
    regex: bool = False

    def matches(self, field_path: str) -> bool:
        if self.regex:
            return re.search(self.field_pattern, field_path, flags=re.IGNORECASE) is not None
        return self.field_pattern.lower() == field_path.lower() or self.field_pattern.lower() in field_path.lower()


@dataclass(frozen=True)
class AIAuditConfig:
    enabled: bool = True
    sink_type: AuditSinkType = AuditSinkType.JSONL
    jsonl_path: Optional[Path] = Path("data/audit/ai_audit.jsonl")

    redact_enabled: bool = True
    redaction_mode: RedactionMode = RedactionMode.MASK
    redaction_rules: Tuple[RedactionRule, ...] = field(default_factory=tuple)

    include_input_payload: bool = True
    include_output_payload: bool = True
    include_metadata: bool = True
    max_payload_chars: Optional[int] = 100_000

    enable_hash_chain: bool = True
    hash_secret: Optional[str] = None
    hash_state_path: Optional[Path] = Path("data/audit/ai_audit_hash_state.json")

    fail_silently: bool = True
    flush_every_event: bool = True

    @staticmethod
    def from_env() -> "AIAuditConfig":
        jsonl_raw = os.getenv("AI_AUDIT_JSONL_PATH", "data/audit/ai_audit.jsonl")
        hash_state_raw = os.getenv("AI_AUDIT_HASH_STATE_PATH", "data/audit/ai_audit_hash_state.json")

        return AIAuditConfig(
            enabled=env_bool("AI_AUDIT_ENABLED", True),
            sink_type=AuditSinkType(os.getenv("AI_AUDIT_SINK_TYPE", AuditSinkType.JSONL.value)),
            jsonl_path=Path(jsonl_raw) if jsonl_raw else None,
            redact_enabled=env_bool("AI_AUDIT_REDACT_ENABLED", True),
            redaction_mode=RedactionMode(os.getenv("AI_AUDIT_REDACTION_MODE", RedactionMode.MASK.value)),
            redaction_rules=tuple(default_redaction_rules(os.getenv("AI_AUDIT_HASH_SALT"))),
            include_input_payload=env_bool("AI_AUDIT_INCLUDE_INPUT", True),
            include_output_payload=env_bool("AI_AUDIT_INCLUDE_OUTPUT", True),
            include_metadata=env_bool("AI_AUDIT_INCLUDE_METADATA", True),
            max_payload_chars=int(os.getenv("AI_AUDIT_MAX_PAYLOAD_CHARS"))
            if os.getenv("AI_AUDIT_MAX_PAYLOAD_CHARS")
            else 100_000,
            enable_hash_chain=env_bool("AI_AUDIT_ENABLE_HASH_CHAIN", True),
            hash_secret=os.getenv("AI_AUDIT_HASH_SECRET") or None,
            hash_state_path=Path(hash_state_raw) if hash_state_raw else None,
            fail_silently=env_bool("AI_AUDIT_FAIL_SILENTLY", True),
            flush_every_event=env_bool("AI_AUDIT_FLUSH_EVERY_EVENT", True),
        )


@dataclass
class AIAuditMetrics:
    events_received: int = 0
    events_written: int = 0
    events_failed: int = 0
    events_redacted: int = 0
    bytes_written: int = 0
    last_event_at: Optional[str] = None
    total_write_seconds: float = 0.0

    def snapshot(self) -> Dict[str, Any]:
        avg = self.total_write_seconds / self.events_written if self.events_written else 0.0
        return {
            "events_received": self.events_received,
            "events_written": self.events_written,
            "events_failed": self.events_failed,
            "events_redacted": self.events_redacted,
            "bytes_written": self.bytes_written,
            "last_event_at": self.last_event_at,
            "average_write_seconds": round(avg, 6),
            "total_write_seconds": round(self.total_write_seconds, 6),
        }


# =============================================================================
# Protocols
# =============================================================================


class AIAuditSink(Protocol):
    def write(self, event: AIAuditEvent) -> int:
        """Persiste evento e retorna bytes escritos ou aproximados."""

    def close(self) -> None:
        """Fecha recursos do sink."""


class AIAuditObserver(Protocol):
    def on_event(self, event: AIAuditEvent) -> None:
        """Hook chamado após evento ser preparado."""


# =============================================================================
# Sinks
# =============================================================================


class JsonlAIAuditSink:
    def __init__(self, path: Union[str, Path], flush_every_event: bool = True) -> None:
        self.path = Path(path)
        self.flush_every_event = flush_every_event
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, event: AIAuditEvent) -> int:
        line = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default) + "\n"
        with self._lock:
            self._handle.write(line)
            if self.flush_every_event:
                self._handle.flush()
        return len(line.encode("utf-8"))

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                self._handle.close()


class LoggingAIAuditSink:
    def write(self, event: AIAuditEvent) -> int:
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        logger.info("AI_AUDIT_EVENT %s", payload)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


class MemoryAIAuditSink:
    def __init__(self) -> None:
        self.events: List[AIAuditEvent] = []
        self._lock = threading.Lock()

    def write(self, event: AIAuditEvent) -> int:
        with self._lock:
            self.events.append(event)
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


class CallbackAIAuditSink:
    def __init__(self, callback: Callable[[AIAuditEvent], None]) -> None:
        self.callback = callback

    def write(self, event: AIAuditEvent) -> int:
        self.callback(event)
        payload = json.dumps(model_to_dict(event), ensure_ascii=False, default=json_default)
        return len(payload.encode("utf-8"))

    def close(self) -> None:
        return None


# =============================================================================
# Redaction
# =============================================================================


class PayloadRedactor:
    def __init__(
        self,
        rules: Sequence[RedactionRule],
        default_mode: RedactionMode = RedactionMode.MASK,
        enabled: bool = True,
    ) -> None:
        self.rules = list(rules)
        self.default_mode = default_mode
        self.enabled = enabled

    def redact(self, payload: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        if payload is None:
            return None
        if not self.enabled:
            return dict(payload)
        return self._redact_value(payload, path="")  # type: ignore[return-value]

    def _redact_value(self, value: Any, path: str) -> Any:
        if isinstance(value, Mapping):
            output: Dict[str, Any] = {}
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                rule = self._find_rule(child_path)
                if rule:
                    redacted = self._apply_rule(child, rule)
                    if redacted is not _REMOVE:
                        output[str(key)] = redacted
                else:
                    output[str(key)] = self._redact_value(child, child_path)
            return output

        if isinstance(value, list):
            return [self._redact_value(item, f"{path}[]") for item in value]

        return value

    def _find_rule(self, path: str) -> Optional[RedactionRule]:
        for rule in self.rules:
            if rule.matches(path):
                return rule
        return None

    def _apply_rule(self, value: Any, rule: RedactionRule) -> Any:
        if rule.mode == RedactionMode.REMOVE:
            return _REMOVE
        if rule.mode == RedactionMode.NONE:
            return value
        if rule.mode == RedactionMode.HASH:
            return hash_value(value, salt=rule.hash_salt)
        return rule.mask_value


class _RemoveSentinel:
    pass


_REMOVE = _RemoveSentinel()


# =============================================================================
# Hash chain
# =============================================================================


class AuditHashChain:
    def __init__(self, state_path: Optional[Path], secret: Optional[str] = None) -> None:
        self.state_path = state_path
        self.secret = secret
        self._lock = threading.Lock()
        self.previous_hash = self._load_previous_hash()

    def apply(self, event: AIAuditEvent) -> AIAuditEvent:
        with self._lock:
            event.previous_hash = self.previous_hash
            event.event_hash = self._compute_event_hash(event)
            self.previous_hash = event.event_hash
            self._save_previous_hash(self.previous_hash)
        return event

    def _compute_event_hash(self, event: AIAuditEvent) -> str:
        event_dict = model_to_dict(event)
        event_dict.pop("event_hash", None)
        canonical = json.dumps(event_dict, sort_keys=True, ensure_ascii=False, default=json_default)

        if self.secret:
            return hmac.new(
                self.secret.encode("utf-8"),
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _load_previous_hash(self) -> Optional[str]:
        if not self.state_path or not self.state_path.exists():
            return None
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload.get("previous_hash")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Falha ao carregar hash state de auditoria. error=%s", exc)
            return None

    def _save_previous_hash(self, value: Optional[str]) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump({"previous_hash": value, "updated_at": utc_now_iso()}, handle, ensure_ascii=False, indent=2)


# =============================================================================
# Auditor principal
# =============================================================================


class AIAuditLogger:
    def __init__(
        self,
        config: Optional[AIAuditConfig] = None,
        sink: Optional[AIAuditSink] = None,
        observers: Optional[Sequence[AIAuditObserver]] = None,
    ) -> None:
        self.config = config or AIAuditConfig.from_env()
        self.metrics = AIAuditMetrics()
        self.redactor = PayloadRedactor(
            rules=self.config.redaction_rules,
            default_mode=self.config.redaction_mode,
            enabled=self.config.redact_enabled,
        )
        self.hash_chain = AuditHashChain(
            self.config.hash_state_path,
            secret=self.config.hash_secret,
        ) if self.config.enable_hash_chain else None
        self.sink = sink or self._build_sink()
        self.observers = list(observers or [])

    @classmethod
    def from_env(cls) -> "AIAuditLogger":
        return cls(config=AIAuditConfig.from_env())

    def log_event(self, event: AIAuditEvent) -> Optional[AIAuditEvent]:
        if not self.config.enabled:
            return None

        started = time.perf_counter()
        self.metrics.events_received += 1

        try:
            prepared = self._prepare_event(event)
            bytes_written = self.sink.write(prepared)
            self.metrics.events_written += 1
            self.metrics.bytes_written += bytes_written
            self.metrics.last_event_at = prepared.timestamp
            self.metrics.total_write_seconds += time.perf_counter() - started
            self._notify_observers(prepared)
            return prepared

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.events_failed += 1
            logger.exception("Falha ao registrar auditoria de IA. error=%s", exc)
            if not self.config.fail_silently:
                raise AIAuditSinkError(str(exc)) from exc
            return None

    def log_inference(
        self,
        model_name: str,
        provider: Optional[str] = None,
        input_payload: Optional[Mapping[str, Any]] = None,
        output_payload: Optional[Mapping[str, Any]] = None,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        usage: Optional[Union[AIUsage, Mapping[str, Any]]] = None,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> Optional[AIAuditEvent]:
        event_type = AuditEventType.INFERENCE_ERROR if error else AuditEventType.INFERENCE_RESPONSE
        severity = AuditSeverity.ERROR if error or status == AuditStatus.FAILED else AuditSeverity.INFO

        event = AIAuditEvent(
            event_type=event_type,
            status=status,
            severity=severity,
            request_id=request_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            model=AIModelAuditRef(name=model_name, provider=provider),
            usage=self._coerce_usage(usage),
            input_payload=dict(input_payload or {}) if input_payload is not None else None,
            output_payload=dict(output_payload or {}) if output_payload is not None else None,
            metadata=dict(metadata or {}),
            error_type=error.__class__.__name__ if error else None,
            error_message=str(error) if error else None,
        )
        return self.log_event(event)

    def log_prompt(
        self,
        prompt_template: Optional[str],
        rendered_prompt: Optional[str],
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        variables: Optional[Mapping[str, Any]] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[AIAuditEvent]:
        event = AIAuditEvent(
            event_type=AuditEventType.PROMPT_RENDERED if status == AuditStatus.SUCCEEDED else AuditEventType.PROMPT_REJECTED,
            status=status,
            severity=AuditSeverity.INFO if status == AuditStatus.SUCCEEDED else AuditSeverity.WARNING,
            correlation_id=correlation_id or str(uuid.uuid4()),
            model=AIModelAuditRef(name=model_name, provider=provider) if model_name or provider else None,
            input_payload={
                "prompt_template": prompt_template,
                "variables": dict(variables or {}),
            },
            output_payload={"rendered_prompt": rendered_prompt} if rendered_prompt is not None else None,
            metadata=dict(metadata or {}),
        )
        return self.log_event(event)

    def log_embedding(
        self,
        model_name: str,
        provider: Optional[str],
        texts_count: int,
        dimensions: Optional[int] = None,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        usage: Optional[Union[AIUsage, Mapping[str, Any]]] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        error: Optional[Exception] = None,
    ) -> Optional[AIAuditEvent]:
        event = AIAuditEvent(
            event_type=AuditEventType.EMBEDDING_RESPONSE if not error else AuditEventType.INFERENCE_ERROR,
            status=status,
            severity=AuditSeverity.ERROR if error else AuditSeverity.INFO,
            correlation_id=correlation_id or str(uuid.uuid4()),
            model=AIModelAuditRef(name=model_name, provider=provider, task_type="embedding"),
            usage=self._coerce_usage(usage),
            input_payload={"texts_count": texts_count},
            output_payload={"dimensions": dimensions} if dimensions is not None else None,
            metadata=dict(metadata or {}),
            error_type=error.__class__.__name__ if error else None,
            error_message=str(error) if error else None,
        )
        return self.log_event(event)

    def log_guardrail(
        self,
        policy_name: str,
        triggered: bool,
        reason: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[AIAuditEvent]:
        event = AIAuditEvent(
            event_type=AuditEventType.GUARDRAIL_TRIGGERED if triggered else AuditEventType.CUSTOM,
            status=AuditStatus.BLOCKED if triggered else AuditStatus.SUCCEEDED,
            severity=AuditSeverity.WARNING if triggered else AuditSeverity.INFO,
            correlation_id=correlation_id or str(uuid.uuid4()),
            input_payload=dict(payload or {}) if payload is not None else None,
            metadata={
                "policy_name": policy_name,
                "triggered": triggered,
                "reason": reason,
                **dict(metadata or {}),
            },
        )
        return self.log_event(event)

    def log_evaluation(
        self,
        evaluation_name: str,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        metrics: Optional[Mapping[str, Any]] = None,
        status: AuditStatus = AuditStatus.SUCCEEDED,
        dataset_ref: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Optional[AIAuditEvent]:
        event = AIAuditEvent(
            event_type=AuditEventType.EVALUATION_COMPLETED,
            status=status,
            severity=AuditSeverity.INFO if status == AuditStatus.SUCCEEDED else AuditSeverity.ERROR,
            correlation_id=correlation_id or str(uuid.uuid4()),
            model=AIModelAuditRef(name=model_name, provider=provider) if model_name or provider else None,
            output_payload={"metrics": dict(metrics or {})},
            metadata={
                "evaluation_name": evaluation_name,
                "dataset_ref": dataset_ref,
                **dict(metadata or {}),
            },
        )
        return self.log_event(event)

    def close(self) -> None:
        self.sink.close()
        logger.info("AI audit logger encerrado. metrics=%s", json.dumps(self.metrics.snapshot()))

    def _prepare_event(self, event: AIAuditEvent) -> AIAuditEvent:
        if not self.config.include_input_payload:
            event.input_payload = None
        if not self.config.include_output_payload:
            event.output_payload = None
        if not self.config.include_metadata:
            event.metadata = {}

        if self.config.redact_enabled:
            event.input_payload = self.redactor.redact(event.input_payload)
            event.output_payload = self.redactor.redact(event.output_payload)
            event.metadata = self.redactor.redact(event.metadata) or {}
            self.metrics.events_redacted += 1

        if self.config.max_payload_chars:
            event.input_payload = truncate_payload(event.input_payload, self.config.max_payload_chars)
            event.output_payload = truncate_payload(event.output_payload, self.config.max_payload_chars)
            event.metadata = truncate_payload(event.metadata, self.config.max_payload_chars) or {}

        if self.hash_chain:
            event = self.hash_chain.apply(event)

        return event

    def _build_sink(self) -> AIAuditSink:
        if self.config.sink_type == AuditSinkType.LOGGING:
            return LoggingAIAuditSink()
        if self.config.sink_type == AuditSinkType.MEMORY:
            return MemoryAIAuditSink()
        if self.config.sink_type == AuditSinkType.JSONL:
            if not self.config.jsonl_path:
                raise AIAuditConfigurationError("jsonl_path é obrigatório para sink JSONL.")
            return JsonlAIAuditSink(self.config.jsonl_path, flush_every_event=self.config.flush_every_event)
        raise AIAuditConfigurationError("Sink CALLBACK exige passar sink explicitamente no construtor.")

    def _notify_observers(self, event: AIAuditEvent) -> None:
        for observer in self.observers:
            try:
                observer.on_event(event)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observer de auditoria falhou. error=%s", exc)

    @staticmethod
    def _coerce_usage(usage: Optional[Union[AIUsage, Mapping[str, Any]]]) -> Optional[AIUsage]:
        if usage is None:
            return None
        if isinstance(usage, AIUsage):
            return usage
        if hasattr(AIUsage, "model_validate"):
            return AIUsage.model_validate(usage)
        return AIUsage.parse_obj(usage)  # type: ignore[attr-defined]

    def __enter__(self) -> "AIAuditLogger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


# =============================================================================
# Decorators/context helpers
# =============================================================================


def audit_function(
    audit_logger: AIAuditLogger,
    event_type: AuditEventType = AuditEventType.CUSTOM,
    model_name: Optional[str] = None,
    provider: Optional[str] = None,
    capture_args: bool = False,
    capture_result: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator simples para auditar funções síncronas."""

    def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            correlation_id = str(uuid.uuid4())
            started = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                latency_ms = (time.perf_counter() - started) * 1000
                audit_logger.log_event(
                    AIAuditEvent(
                        event_type=event_type,
                        status=AuditStatus.SUCCEEDED,
                        severity=AuditSeverity.INFO,
                        correlation_id=correlation_id,
                        model=AIModelAuditRef(name=model_name, provider=provider) if model_name or provider else None,
                        input_payload={"args": args, "kwargs": kwargs} if capture_args else None,
                        output_payload={"result": result} if capture_result else None,
                        usage=AIUsage(latency_ms=latency_ms),
                        metadata={"function": fn.__name__},
                    )
                )
                return result
            except Exception as exc:  # pylint: disable=broad-exception-caught
                latency_ms = (time.perf_counter() - started) * 1000
                audit_logger.log_event(
                    AIAuditEvent(
                        event_type=AuditEventType.INFERENCE_ERROR,
                        status=AuditStatus.FAILED,
                        severity=AuditSeverity.ERROR,
                        correlation_id=correlation_id,
                        model=AIModelAuditRef(name=model_name, provider=provider) if model_name or provider else None,
                        input_payload={"args": args, "kwargs": kwargs} if capture_args else None,
                        usage=AIUsage(latency_ms=latency_ms),
                        metadata={"function": fn.__name__},
                        error_type=exc.__class__.__name__,
                        error_message=str(exc),
                    )
                )
                raise

        return _wrapped

    return _decorator


# =============================================================================
# Utilitários
# =============================================================================


def default_redaction_rules(hash_salt: Optional[str] = None) -> List[RedactionRule]:
    sensitive_fields = [
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "access_key",
        "refresh_token",
        "private_key",
        "cpf",
        "cnpj",
        "ssn",
        "email",
        "phone",
        "telefone",
        "address",
        "endereco",
        "credit_card",
        "card_number",
    ]
    return [RedactionRule(field_pattern=field, mode=RedactionMode.MASK, hash_salt=hash_salt) for field in sensitive_fields]


def hash_value(value: Any, salt: Optional[str] = None) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)
    payload = f"{salt or ''}:{raw}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def truncate_payload(payload: Optional[Dict[str, Any]], max_chars: int) -> Optional[Dict[str, Any]]:
    if payload is None:
        return None

    serialized = json.dumps(payload, ensure_ascii=False, default=json_default)
    if len(serialized) <= max_chars:
        return payload

    truncated = serialized[:max_chars]
    return {
        "_truncated": True,
        "_max_chars": max_chars,
        "_preview": truncated,
    }


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Bootstrap CLI simples
# =============================================================================


def main() -> None:
    audit = AIAuditLogger.from_env()
    audit.log_inference(
        model_name=os.getenv("AI_AUDIT_EXAMPLE_MODEL", "example-model"),
        provider=os.getenv("AI_AUDIT_EXAMPLE_PROVIDER", "custom"),
        input_payload={"prompt": "Olá, explique auditoria de IA.", "api_key": "secret-example"},
        output_payload={"answer": "Auditoria de IA registra eventos importantes com segurança."},
        status=AuditStatus.SUCCEEDED,
        usage=AIUsage(input_tokens=10, output_tokens=12, total_tokens=22, latency_ms=123.4),
        metadata={"example": True},
    )
    audit.close()


if __name__ == "__main__":
    main()
