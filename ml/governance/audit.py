# ml/governance/audit.py
"""
Enterprise ML Governance Audit.

Recursos:
- auditoria imutável de eventos ML
- hash chain para integridade
- trilha de decisão de modelos
- auditoria de datasets, features, prompts e inferências
- mascaramento de campos sensíveis
- exportação JSONL
- consulta por entidade, modelo, usuário e correlação
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


class AuditSeverity(str, Enum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AuditAction(str, Enum):
    MODEL_REGISTERED = "model_registered"
    MODEL_DEPLOYED = "model_deployed"
    MODEL_ROLLED_BACK = "model_rolled_back"
    MODEL_EVALUATED = "model_evaluated"
    MODEL_PREDICTED = "model_predicted"

    DATASET_CREATED = "dataset_created"
    DATASET_VALIDATED = "dataset_validated"
    DATASET_ACCESSED = "dataset_accessed"

    FEATURE_CREATED = "feature_created"
    FEATURE_USED = "feature_used"

    PROMPT_EXECUTED = "prompt_executed"
    POLICY_EVALUATED = "policy_evaluated"
    DRIFT_DETECTED = "drift_detected"
    BIAS_DETECTED = "bias_detected"

    HUMAN_APPROVAL = "human_approval"
    HUMAN_REJECTION = "human_rejection"

    SECURITY_EVENT = "security_event"
    CONFIG_CHANGED = "config_changed"


class AuditEntityType(str, Enum):
    MODEL = "model"
    DATASET = "dataset"
    FEATURE = "feature"
    PIPELINE = "pipeline"
    PROMPT = "prompt"
    USER = "user"
    TENANT = "tenant"
    PREDICTION = "prediction"
    POLICY = "policy"
    SYSTEM = "system"


@dataclass(frozen=True)
class AuditActor:
    actor_id: str
    actor_type: str = "user"
    display_name: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: Sequence[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditEntity:
    entity_id: str
    entity_type: AuditEntityType
    name: Optional[str] = None
    version: Optional[str] = None


@dataclass(frozen=True)
class AuditContext:
    correlation_id: str
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    environment: str = "production"
    region: Optional[str] = None


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: str
    action: AuditAction
    severity: AuditSeverity
    actor: AuditActor
    entity: AuditEntity
    context: AuditContext
    message: str
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    previous_hash: Optional[str] = None
    event_hash: Optional[str] = None

    def canonical_payload(self, include_event_hash: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_event_hash:
            data["event_hash"] = None
        return data

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class SensitiveDataMasker:
    DEFAULT_PATTERNS: Mapping[str, str] = {
        "email": r"([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        "cpf": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
        "credit_card": r"\b(?:\d[ -]*?){13,19}\b",
        "api_key": r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([^'\"\s,}]+)",
    }

    SENSITIVE_KEYS = {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "private_key",
        "cpf",
        "credit_card",
    }

    def __init__(self, replacement: str = "***MASKED***") -> None:
        self.replacement = replacement

    def mask(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(k): self.replacement if self._is_sensitive_key(str(k)) else self.mask(v)
                for k, v in value.items()
            }

        if isinstance(value, list):
            return [self.mask(v) for v in value]

        if isinstance(value, tuple):
            return tuple(self.mask(v) for v in value)

        if isinstance(value, str):
            return self._mask_string(value)

        return value

    def _mask_string(self, value: str) -> str:
        masked = value

        for name, pattern in self.DEFAULT_PATTERNS.items():
            if name == "email":
                masked = re.sub(pattern, r"\1@***", masked)
            elif name == "api_key":
                masked = re.sub(pattern, r"\1: ***MASKED***", masked)
            else:
                masked = re.sub(pattern, self.replacement, masked)

        return masked

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return normalized in self.SENSITIVE_KEYS or any(s in normalized for s in self.SENSITIVE_KEYS)


class AuditIntegrity:
    @staticmethod
    def compute_hash(event: AuditEvent) -> str:
        payload = event.canonical_payload(include_event_hash=False)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def verify_chain(events: Sequence[AuditEvent]) -> Dict[str, Any]:
        broken: List[str] = []
        previous_hash: Optional[str] = None

        for event in events:
            expected_hash = AuditIntegrity.compute_hash(event)

            if event.event_hash != expected_hash:
                broken.append(event.event_id)

            if event.previous_hash != previous_hash:
                broken.append(event.event_id)

            previous_hash = event.event_hash

        return {
            "valid": len(broken) == 0,
            "broken_event_ids": broken,
            "checked_events": len(events),
        }


class AuditStore:
    def append(self, event: AuditEvent) -> None:
        raise NotImplementedError

    def list_events(self) -> List[AuditEvent]:
        raise NotImplementedError


class InMemoryAuditStore(AuditStore):
    def __init__(self) -> None:
        self._events: List[AuditEvent] = []
        self._lock = Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_events(self) -> List[AuditEvent]:
        with self._lock:
            return list(self._events)


class JsonlAuditStore(AuditStore):
    def __init__(self, path: str | Path = "artifacts/audit/ml_audit.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(event.to_json() + "\n")

    def list_events(self) -> List[AuditEvent]:
        if not self.path.exists():
            return []

        events: List[AuditEvent] = []

        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                raw = json.loads(line)
                events.append(AuditEventFactory.from_dict(raw))

        return events


class AuditEventFactory:
    @staticmethod
    def from_dict(raw: Mapping[str, Any]) -> AuditEvent:
        return AuditEvent(
            event_id=raw["event_id"],
            timestamp=raw["timestamp"],
            action=AuditAction(raw["action"]),
            severity=AuditSeverity(raw["severity"]),
            actor=AuditActor(**raw["actor"]),
            entity=AuditEntity(
                entity_id=raw["entity"]["entity_id"],
                entity_type=AuditEntityType(raw["entity"]["entity_type"]),
                name=raw["entity"].get("name"),
                version=raw["entity"].get("version"),
            ),
            context=AuditContext(**raw["context"]),
            message=raw["message"],
            before=raw.get("before"),
            after=raw.get("after"),
            metadata=raw.get("metadata", {}),
            previous_hash=raw.get("previous_hash"),
            event_hash=raw.get("event_hash"),
        )


class MLAuditLogger:
    def __init__(
        self,
        store: Optional[AuditStore] = None,
        masker: Optional[SensitiveDataMasker] = None,
    ) -> None:
        self.store = store or JsonlAuditStore()
        self.masker = masker or SensitiveDataMasker()
        self._lock = Lock()

    def log(
        self,
        *,
        action: AuditAction,
        actor: AuditActor,
        entity: AuditEntity,
        context: AuditContext,
        message: str,
        severity: AuditSeverity = AuditSeverity.INFO,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        with self._lock:
            existing = self.store.list_events()
            previous_hash = existing[-1].event_hash if existing else None

            event = AuditEvent(
                event_id=str(uuid.uuid4()),
                timestamp=datetime.now(timezone.utc).isoformat(),
                action=action,
                severity=severity,
                actor=actor,
                entity=entity,
                context=context,
                message=self.masker.mask(message),
                before=self.masker.mask(before) if before else None,
                after=self.masker.mask(after) if after else None,
                metadata=self.masker.mask(metadata or {}),
                previous_hash=previous_hash,
                event_hash=None,
            )

            event_hash = AuditIntegrity.compute_hash(event)

            signed_event = AuditEvent(
                **{
                    **asdict(event),
                    "actor": event.actor,
                    "entity": event.entity,
                    "context": event.context,
                    "action": event.action,
                    "severity": event.severity,
                    "event_hash": event_hash,
                }
            )

            self.store.append(signed_event)
            return signed_event

    def model_deployed(
        self,
        *,
        actor: AuditActor,
        context: AuditContext,
        model_id: str,
        model_name: str,
        version: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        return self.log(
            action=AuditAction.MODEL_DEPLOYED,
            actor=actor,
            entity=AuditEntity(model_id, AuditEntityType.MODEL, model_name, version),
            context=context,
            message=f"Modelo {model_name}:{version} implantado.",
            severity=AuditSeverity.NOTICE,
            metadata=metadata,
        )

    def prediction_logged(
        self,
        *,
        actor: AuditActor,
        context: AuditContext,
        prediction_id: str,
        model_id: str,
        model_version: str,
        input_summary: Dict[str, Any],
        output_summary: Dict[str, Any],
        explanation: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        return self.log(
            action=AuditAction.MODEL_PREDICTED,
            actor=actor,
            entity=AuditEntity(prediction_id, AuditEntityType.PREDICTION, version=model_version),
            context=context,
            message="Inferência ML registrada.",
            severity=AuditSeverity.INFO,
            metadata={
                "model_id": model_id,
                "model_version": model_version,
                "input_summary": input_summary,
                "output_summary": output_summary,
                "explanation": explanation or {},
            },
        )

    def policy_evaluated(
        self,
        *,
        actor: AuditActor,
        context: AuditContext,
        policy_id: str,
        decision: str,
        reasons: Sequence[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditEvent:
        severity = AuditSeverity.WARNING if decision.lower() in {"deny", "blocked", "rejected"} else AuditSeverity.INFO

        return self.log(
            action=AuditAction.POLICY_EVALUATED,
            actor=actor,
            entity=AuditEntity(policy_id, AuditEntityType.POLICY),
            context=context,
            message=f"Política avaliada com decisão: {decision}.",
            severity=severity,
            metadata={
                "decision": decision,
                "reasons": list(reasons),
                **(metadata or {}),
            },
        )

    def drift_detected(
        self,
        *,
        actor: AuditActor,
        context: AuditContext,
        model_id: str,
        drift_report: Mapping[str, Any],
    ) -> AuditEvent:
        severity = AuditSeverity.HIGH

        if str(drift_report.get("overall_severity", "")).lower() == "critical":
            severity = AuditSeverity.CRITICAL
        elif str(drift_report.get("overall_severity", "")).lower() in {"none", "low"}:
            severity = AuditSeverity.NOTICE

        return self.log(
            action=AuditAction.DRIFT_DETECTED,
            actor=actor,
            entity=AuditEntity(model_id, AuditEntityType.MODEL),
            context=context,
            message="Drift detectado no modelo.",
            severity=severity,
            metadata={"drift_report": dict(drift_report)},
        )

    def verify_integrity(self) -> Dict[str, Any]:
        return AuditIntegrity.verify_chain(self.store.list_events())

    def query(
        self,
        *,
        entity_id: Optional[str] = None,
        entity_type: Optional[AuditEntityType] = None,
        action: Optional[AuditAction] = None,
        actor_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        min_severity: Optional[AuditSeverity] = None,
    ) -> List[AuditEvent]:
        events = self.store.list_events()

        severity_rank = {
            AuditSeverity.INFO: 1,
            AuditSeverity.NOTICE: 2,
            AuditSeverity.WARNING: 3,
            AuditSeverity.HIGH: 4,
            AuditSeverity.CRITICAL: 5,
        }

        filtered: List[AuditEvent] = []

        for event in events:
            if entity_id and event.entity.entity_id != entity_id:
                continue
            if entity_type and event.entity.entity_type != entity_type:
                continue
            if action and event.action != action:
                continue
            if actor_id and event.actor.actor_id != actor_id:
                continue
            if correlation_id and event.context.correlation_id != correlation_id:
                continue
            if min_severity and severity_rank[event.severity] < severity_rank[min_severity]:
                continue

            filtered.append(event)

        return filtered


class AuditExporter:
    @staticmethod
    def to_json(events: Sequence[AuditEvent], indent: int = 2) -> str:
        return json.dumps([event.to_dict() for event in events], ensure_ascii=False, indent=indent, default=str)

    @staticmethod
    def to_markdown(events: Sequence[AuditEvent]) -> str:
        lines = [
            "# ML Audit Report",
            "",
            "| Timestamp | Severity | Action | Entity | Actor | Message |",
            "|---|---|---|---|---|---|",
        ]

        for event in events:
            lines.append(
                f"| {event.timestamp} | {event.severity.value} | {event.action.value} | "
                f"{event.entity.entity_type.value}:{event.entity.entity_id} | "
                f"{event.actor.actor_id} | {event.message} |"
            )

        return "\n".join(lines)


if __name__ == "__main__":
    audit = MLAuditLogger(store=InMemoryAuditStore())

    actor = AuditActor(
        actor_id="thiago",
        display_name="Thiago Sousa",
        tenant_id="digital-meta",
        roles=["ml_admin"],
    )

    context = AuditContext(
        correlation_id="corr-001",
        request_id="req-001",
        environment="production",
        source_ip="127.0.0.1",
    )

    audit.model_deployed(
        actor=actor,
        context=context,
        model_id="document-router",
        model_name="Document Router",
        version="1.0.0",
        metadata={"approval_ticket": "GOV-123"},
    )

    audit.prediction_logged(
        actor=actor,
        context=context,
        prediction_id="pred-001",
        model_id="document-router",
        model_version="1.0.0",
        input_summary={"document_type": "invoice", "email": "cliente@email.com"},
        output_summary={"class": "financeiro", "confidence": 0.94},
        explanation={"top_features": ["vendor", "amount", "due_date"]},
    )

    print(AuditExporter.to_markdown(audit.query()))
    print(audit.verify_integrity())