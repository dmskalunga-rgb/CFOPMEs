"""
data/governance/retention_policy.py

Enterprise Retention Policy Engine.

Recursos:
- Políticas de retenção por domínio, dataset, classificação e tenant
- Suporte a expiração, arquivamento, anonimização e deleção
- Dry-run seguro
- Auditoria estruturada
- Métricas operacionais
- Integração opcional com catálogo de metadados, lineage e masking
- Suporte multi-tenant
- Pronto para uso em pipelines batch, lakehouse, data warehouse ou data platform
"""

from __future__ import annotations

import json
import logging
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class RetentionAction(str, Enum):
    KEEP = "keep"
    ARCHIVE = "archive"
    DELETE = "delete"
    ANONYMIZE = "anonymize"
    MASK = "mask"
    REVIEW = "review"


class RetentionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    ARCHIVED = "archived"
    DELETED = "deleted"
    ANONYMIZED = "anonymized"
    REVIEW_REQUIRED = "review_required"
    ERROR = "error"


class DataSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PII = "pii"
    FINANCIAL = "financial"
    HEALTH = "health"
    LEGAL = "legal"


class LegalHoldStatus(str, Enum):
    NONE = "none"
    ACTIVE = "active"
    RELEASED = "released"


# =============================================================================
# Exceptions
# =============================================================================

class RetentionPolicyError(Exception):
    """Erro base do módulo de retenção."""


class PolicyNotFoundError(RetentionPolicyError):
    """Nenhuma política aplicável encontrada."""


class LegalHoldViolationError(RetentionPolicyError):
    """Tentativa de aplicar retenção destrutiva em dado sob legal hold."""


class RetentionExecutionError(RetentionPolicyError):
    """Erro durante execução da ação de retenção."""


# =============================================================================
# Protocols
# =============================================================================

class RetentionStorageBackend(Protocol):
    def archive(self, record: "DataRecord", policy: "RetentionPolicy") -> None:
        ...

    def delete(self, record: "DataRecord", policy: "RetentionPolicy") -> None:
        ...

    def anonymize(self, record: "DataRecord", policy: "RetentionPolicy") -> None:
        ...

    def mask(self, record: "DataRecord", policy: "RetentionPolicy") -> None:
        ...


class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class RetentionPolicy:
    policy_id: str
    name: str
    domain: str
    retention_days: int
    action_on_expiry: RetentionAction
    enabled: bool = True
    priority: int = 100
    tenant_id: Optional[str] = None
    dataset_name: Optional[str] = None
    classification: Optional[DataSensitivity] = None
    requires_review_before_delete: bool = False
    archive_before_delete: bool = True
    immutable: bool = False
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    def expires_at(self, created_at: datetime) -> datetime:
        return created_at + timedelta(days=self.retention_days)


@dataclass
class DataRecord:
    record_id: str
    dataset_name: str
    domain: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    tenant_id: Optional[str] = None
    classification: DataSensitivity = DataSensitivity.INTERNAL
    legal_hold: LegalHoldStatus = LegalHoldStatus.NONE
    owner: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def age_days(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        return max(0, (now - self.created_at).days)


@dataclass
class RetentionDecision:
    record_id: str
    policy_id: str
    status: RetentionStatus
    action: RetentionAction
    reason: str
    expires_at: datetime
    record_age_days: int
    dry_run: bool
    legal_hold: LegalHoldStatus
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetentionExecutionResult:
    decision: RetentionDecision
    success: bool
    executed_at: datetime
    error: Optional[str] = None


# =============================================================================
# Default Backends
# =============================================================================

class InMemoryAuditBackend:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def write_event(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        logger.info("Retention audit event: %s", json.dumps(event, default=str))


class LoggingMetricsBackend:
    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        logger.info("metric increment | %s=%s | tags=%s", metric_name, value, tags or {})

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        logger.info("metric gauge | %s=%s | tags=%s", metric_name, value, tags or {})


class NoopRetentionStorageBackend:
    def archive(self, record: DataRecord, policy: RetentionPolicy) -> None:
        logger.info("Archive noop: record=%s policy=%s", record.record_id, policy.policy_id)

    def delete(self, record: DataRecord, policy: RetentionPolicy) -> None:
        logger.info("Delete noop: record=%s policy=%s", record.record_id, policy.policy_id)

    def anonymize(self, record: DataRecord, policy: RetentionPolicy) -> None:
        logger.info("Anonymize noop: record=%s policy=%s", record.record_id, policy.policy_id)

    def mask(self, record: DataRecord, policy: RetentionPolicy) -> None:
        logger.info("Mask noop: record=%s policy=%s", record.record_id, policy.policy_id)


# =============================================================================
# Policy Repository
# =============================================================================

class RetentionPolicyRepository:
    def __init__(self, policies: Optional[List[RetentionPolicy]] = None) -> None:
        self._policies: Dict[str, RetentionPolicy] = {}
        for policy in policies or []:
            self.add(policy)

    def add(self, policy: RetentionPolicy) -> None:
        if not policy.policy_id:
            raise ValueError("policy_id é obrigatório")

        if policy.retention_days < 0:
            raise ValueError("retention_days não pode ser negativo")

        self._policies[policy.policy_id] = policy

    def get(self, policy_id: str) -> Optional[RetentionPolicy]:
        return self._policies.get(policy_id)

    def list_enabled(self) -> List[RetentionPolicy]:
        return [p for p in self._policies.values() if p.enabled]

    def resolve_for_record(self, record: DataRecord) -> RetentionPolicy:
        candidates: List[Tuple[int, int, RetentionPolicy]] = []

        for policy in self.list_enabled():
            score = self._match_score(policy, record)
            if score > 0:
                candidates.append((policy.priority, -score, policy))

        if not candidates:
            raise PolicyNotFoundError(
                f"Nenhuma política de retenção encontrada para "
                f"dataset={record.dataset_name}, domain={record.domain}, tenant={record.tenant_id}"
            )

        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    @staticmethod
    def _match_score(policy: RetentionPolicy, record: DataRecord) -> int:
        score = 0

        if policy.domain != record.domain:
            return 0
        score += 1

        if policy.tenant_id:
            if policy.tenant_id != record.tenant_id:
                return 0
            score += 4

        if policy.dataset_name:
            if policy.dataset_name != record.dataset_name:
                return 0
            score += 8

        if policy.classification:
            if policy.classification != record.classification:
                return 0
            score += 6

        return score


# =============================================================================
# Engine
# =============================================================================

class RetentionPolicyEngine:
    def __init__(
        self,
        repository: RetentionPolicyRepository,
        storage_backend: Optional[RetentionStorageBackend] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.repository = repository
        self.storage_backend = storage_backend or NoopRetentionStorageBackend()
        self.audit_backend = audit_backend or InMemoryAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def evaluate(self, record: DataRecord, dry_run: bool = True) -> RetentionDecision:
        now = self.clock()
        policy = self.repository.resolve_for_record(record)
        expires_at = policy.expires_at(record.created_at)
        age_days = record.age_days(now)

        if record.legal_hold == LegalHoldStatus.ACTIVE:
            return RetentionDecision(
                record_id=record.record_id,
                policy_id=policy.policy_id,
                status=RetentionStatus.REVIEW_REQUIRED,
                action=RetentionAction.REVIEW,
                reason="Registro sob legal hold ativo. Ação destrutiva bloqueada.",
                expires_at=expires_at,
                record_age_days=age_days,
                dry_run=dry_run,
                legal_hold=record.legal_hold,
                metadata=self._decision_metadata(record, policy),
            )

        if now < expires_at:
            return RetentionDecision(
                record_id=record.record_id,
                policy_id=policy.policy_id,
                status=RetentionStatus.ACTIVE,
                action=RetentionAction.KEEP,
                reason="Registro ainda dentro do período de retenção.",
                expires_at=expires_at,
                record_age_days=age_days,
                dry_run=dry_run,
                legal_hold=record.legal_hold,
                metadata=self._decision_metadata(record, policy),
            )

        action = policy.action_on_expiry

        if action == RetentionAction.DELETE and policy.requires_review_before_delete:
            return RetentionDecision(
                record_id=record.record_id,
                policy_id=policy.policy_id,
                status=RetentionStatus.REVIEW_REQUIRED,
                action=RetentionAction.REVIEW,
                reason="Registro expirado, mas política exige revisão antes da deleção.",
                expires_at=expires_at,
                record_age_days=age_days,
                dry_run=dry_run,
                legal_hold=record.legal_hold,
                metadata=self._decision_metadata(record, policy),
            )

        status_map = {
            RetentionAction.ARCHIVE: RetentionStatus.EXPIRED,
            RetentionAction.DELETE: RetentionStatus.EXPIRED,
            RetentionAction.ANONYMIZE: RetentionStatus.EXPIRED,
            RetentionAction.MASK: RetentionStatus.EXPIRED,
            RetentionAction.REVIEW: RetentionStatus.REVIEW_REQUIRED,
            RetentionAction.KEEP: RetentionStatus.ACTIVE,
        }

        return RetentionDecision(
            record_id=record.record_id,
            policy_id=policy.policy_id,
            status=status_map[action],
            action=action,
            reason=f"Registro expirado conforme política {policy.name}.",
            expires_at=expires_at,
            record_age_days=age_days,
            dry_run=dry_run,
            legal_hold=record.legal_hold,
            metadata=self._decision_metadata(record, policy),
        )

    def execute(self, record: DataRecord, dry_run: bool = True) -> RetentionExecutionResult:
        decision = self.evaluate(record, dry_run=dry_run)
        executed_at = self.clock()

        try:
            self._audit("retention.decision.created", record, decision)

            if decision.action in {RetentionAction.KEEP, RetentionAction.REVIEW}:
                self._metric_decision(decision)
                return RetentionExecutionResult(decision, True, executed_at)

            if dry_run:
                self._audit("retention.execution.dry_run", record, decision)
                self._metric_decision(decision)
                return RetentionExecutionResult(decision, True, executed_at)

            self._guard_legal_hold(record, decision)

            policy = self.repository.get(decision.policy_id)
            if not policy:
                raise PolicyNotFoundError(f"Política {decision.policy_id} não encontrada")

            self._apply_action(record, policy, decision)
            self._audit("retention.execution.success", record, decision)
            self._metric_decision(decision)

            return RetentionExecutionResult(decision, True, executed_at)

        except Exception as exc:
            logger.exception("Erro ao executar política de retenção")
            self.metrics_backend.increment(
                "retention.execution.error",
                tags={
                    "record_id": record.record_id,
                    "action": decision.action.value,
                    "policy_id": decision.policy_id,
                },
            )
            self._audit(
                "retention.execution.error",
                record,
                decision,
                extra={"error": str(exc)},
            )
            return RetentionExecutionResult(
                decision=decision,
                success=False,
                executed_at=executed_at,
                error=str(exc),
            )

    def execute_batch(
        self,
        records: Iterable[DataRecord],
        dry_run: bool = True,
        stop_on_error: bool = False,
    ) -> List[RetentionExecutionResult]:
        results: List[RetentionExecutionResult] = []

        for record in records:
            result = self.execute(record, dry_run=dry_run)
            results.append(result)

            if stop_on_error and not result.success:
                break

        self.metrics_backend.gauge("retention.batch.total", len(results))
        self.metrics_backend.gauge(
            "retention.batch.success",
            sum(1 for r in results if r.success),
        )
        self.metrics_backend.gauge(
            "retention.batch.failed",
            sum(1 for r in results if not r.success),
        )

        return results

    def _apply_action(
        self,
        record: DataRecord,
        policy: RetentionPolicy,
        decision: RetentionDecision,
    ) -> None:
        if decision.action == RetentionAction.ARCHIVE:
            self.storage_backend.archive(record, policy)

        elif decision.action == RetentionAction.DELETE:
            if policy.archive_before_delete:
                self.storage_backend.archive(record, policy)
            self.storage_backend.delete(record, policy)

        elif decision.action == RetentionAction.ANONYMIZE:
            self.storage_backend.anonymize(record, policy)

        elif decision.action == RetentionAction.MASK:
            self.storage_backend.mask(record, policy)

        else:
            raise RetentionExecutionError(f"Ação não executável: {decision.action}")

    @staticmethod
    def _guard_legal_hold(record: DataRecord, decision: RetentionDecision) -> None:
        destructive_actions = {
            RetentionAction.DELETE,
            RetentionAction.ANONYMIZE,
            RetentionAction.MASK,
        }

        if record.legal_hold == LegalHoldStatus.ACTIVE and decision.action in destructive_actions:
            raise LegalHoldViolationError(
                f"Registro {record.record_id} está sob legal hold ativo."
            )

    def _audit(
        self,
        event_type: str,
        record: DataRecord,
        decision: RetentionDecision,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = {
            "event_id": self._event_id(event_type, record.record_id, decision.policy_id),
            "event_type": event_type,
            "occurred_at": self.clock().isoformat(),
            "record": {
                "record_id": record.record_id,
                "dataset_name": record.dataset_name,
                "domain": record.domain,
                "tenant_id": record.tenant_id,
                "classification": record.classification.value,
                "legal_hold": record.legal_hold.value,
            },
            "decision": self._serialize_decision(decision),
            "extra": extra or {},
        }

        self.audit_backend.write_event(event)

    def _metric_decision(self, decision: RetentionDecision) -> None:
        tags = {
            "policy_id": decision.policy_id,
            "action": decision.action.value,
            "status": decision.status.value,
            "dry_run": str(decision.dry_run).lower(),
        }
        self.metrics_backend.increment("retention.decision.total", tags=tags)

    @staticmethod
    def _serialize_decision(decision: RetentionDecision) -> Dict[str, Any]:
        data = asdict(decision)
        data["status"] = decision.status.value
        data["action"] = decision.action.value
        data["expires_at"] = decision.expires_at.isoformat()
        data["legal_hold"] = decision.legal_hold.value
        return data

    @staticmethod
    def _decision_metadata(record: DataRecord, policy: RetentionPolicy) -> Dict[str, Any]:
        return {
            "dataset_name": record.dataset_name,
            "domain": record.domain,
            "tenant_id": record.tenant_id,
            "classification": record.classification.value,
            "policy_name": policy.name,
            "policy_priority": policy.priority,
            "retention_days": policy.retention_days,
            "policy_tags": policy.tags,
        }

    @staticmethod
    def _event_id(event_type: str, record_id: str, policy_id: str) -> str:
        raw = f"{event_type}|{record_id}|{policy_id}|{datetime.now(timezone.utc).isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =============================================================================
# Policy Factory
# =============================================================================

class RetentionPolicyFactory:
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> RetentionPolicy:
        return RetentionPolicy(
            policy_id=data["policy_id"],
            name=data["name"],
            domain=data["domain"],
            retention_days=int(data["retention_days"]),
            action_on_expiry=RetentionAction(data["action_on_expiry"]),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 100)),
            tenant_id=data.get("tenant_id"),
            dataset_name=data.get("dataset_name"),
            classification=(
                DataSensitivity(data["classification"])
                if data.get("classification")
                else None
            ),
            requires_review_before_delete=bool(
                data.get("requires_review_before_delete", False)
            ),
            archive_before_delete=bool(data.get("archive_before_delete", True)),
            immutable=bool(data.get("immutable", False)),
            description=data.get("description", ""),
            tags=data.get("tags", {}),
        )

    @staticmethod
    def from_json(json_text: str) -> List[RetentionPolicy]:
        payload = json.loads(json_text)

        if isinstance(payload, dict):
            payload = payload.get("policies", [])

        return [RetentionPolicyFactory.from_dict(item) for item in payload]


# =============================================================================
# Example SQL/Lakehouse Backend
# =============================================================================

class CallbackRetentionStorageBackend:
    """
    Backend plugável para conectar com banco, lakehouse, object storage,
    catálogo ou serviço interno.

    Exemplo de uso:
        backend = CallbackRetentionStorageBackend(
            archive_fn=lambda record, policy: ...,
            delete_fn=lambda record, policy: ...
        )
    """

    def __init__(
        self,
        archive_fn: Optional[Callable[[DataRecord, RetentionPolicy], None]] = None,
        delete_fn: Optional[Callable[[DataRecord, RetentionPolicy], None]] = None,
        anonymize_fn: Optional[Callable[[DataRecord, RetentionPolicy], None]] = None,
        mask_fn: Optional[Callable[[DataRecord, RetentionPolicy], None]] = None,
    ) -> None:
        self.archive_fn = archive_fn
        self.delete_fn = delete_fn
        self.anonymize_fn = anonymize_fn
        self.mask_fn = mask_fn

    def archive(self, record: DataRecord, policy: RetentionPolicy) -> None:
        if self.archive_fn:
            self.archive_fn(record, policy)
        else:
            logger.info("Archive callback não configurado para %s", record.record_id)

    def delete(self, record: DataRecord, policy: RetentionPolicy) -> None:
        if self.delete_fn:
            self.delete_fn(record, policy)
        else:
            logger.info("Delete callback não configurado para %s", record.record_id)

    def anonymize(self, record: DataRecord, policy: RetentionPolicy) -> None:
        if self.anonymize_fn:
            self.anonymize_fn(record, policy)
        else:
            logger.info("Anonymize callback não configurado para %s", record.record_id)

    def mask(self, record: DataRecord, policy: RetentionPolicy) -> None:
        if self.mask_fn:
            self.mask_fn(record, policy)
        else:
            logger.info("Mask callback não configurado para %s", record.record_id)


# =============================================================================
# Default Enterprise Policies
# =============================================================================

def build_default_enterprise_policies() -> List[RetentionPolicy]:
    return [
        RetentionPolicy(
            policy_id="ret-public-3650",
            name="Public data retention 10 years",
            domain="general",
            retention_days=3650,
            action_on_expiry=RetentionAction.ARCHIVE,
            classification=DataSensitivity.PUBLIC,
            priority=200,
            description="Dados públicos podem ser mantidos por longo prazo.",
        ),
        RetentionPolicy(
            policy_id="ret-internal-1825",
            name="Internal data retention 5 years",
            domain="general",
            retention_days=1825,
            action_on_expiry=RetentionAction.ARCHIVE,
            classification=DataSensitivity.INTERNAL,
            priority=180,
        ),
        RetentionPolicy(
            policy_id="ret-pii-730",
            name="PII retention 2 years",
            domain="customer",
            retention_days=730,
            action_on_expiry=RetentionAction.ANONYMIZE,
            classification=DataSensitivity.PII,
            priority=50,
            requires_review_before_delete=False,
            archive_before_delete=True,
            description="Dados pessoais devem ser anonimizados após expiração.",
            tags={"lgpd": "true", "gdpr": "true"},
        ),
        RetentionPolicy(
            policy_id="ret-financial-2555",
            name="Financial data retention 7 years",
            domain="finance",
            retention_days=2555,
            action_on_expiry=RetentionAction.ARCHIVE,
            classification=DataSensitivity.FINANCIAL,
            priority=40,
            tags={"compliance": "financial"},
        ),
        RetentionPolicy(
            policy_id="ret-legal-3650",
            name="Legal data retention 10 years",
            domain="legal",
            retention_days=3650,
            action_on_expiry=RetentionAction.REVIEW,
            classification=DataSensitivity.LEGAL,
            priority=10,
            requires_review_before_delete=True,
            immutable=True,
            tags={"legal_hold_sensitive": "true"},
        ),
    ]


# =============================================================================
# CLI-like Example
# =============================================================================

def example_usage() -> None:
    policies = build_default_enterprise_policies()
    repository = RetentionPolicyRepository(policies)

    engine = RetentionPolicyEngine(
        repository=repository,
        storage_backend=NoopRetentionStorageBackend(),
        audit_backend=InMemoryAuditBackend(),
        metrics_backend=LoggingMetricsBackend(),
    )

    records = [
        DataRecord(
            record_id="customer-001",
            dataset_name="customers",
            domain="customer",
            tenant_id="tenant-a",
            classification=DataSensitivity.PII,
            created_at=datetime.now(timezone.utc) - timedelta(days=900),
            owner="data-platform",
        ),
        DataRecord(
            record_id="invoice-001",
            dataset_name="invoices",
            domain="finance",
            tenant_id="tenant-a",
            classification=DataSensitivity.FINANCIAL,
            created_at=datetime.now(timezone.utc) - timedelta(days=3000),
            owner="finance",
        ),
        DataRecord(
            record_id="contract-001",
            dataset_name="contracts",
            domain="legal",
            tenant_id="tenant-a",
            classification=DataSensitivity.LEGAL,
            created_at=datetime.now(timezone.utc) - timedelta(days=4000),
            legal_hold=LegalHoldStatus.ACTIVE,
            owner="legal",
        ),
    ]

    results = engine.execute_batch(records, dry_run=True)

    for result in results:
        print(json.dumps({
            "record_id": result.decision.record_id,
            "policy_id": result.decision.policy_id,
            "action": result.decision.action.value,
            "status": result.decision.status.value,
            "success": result.success,
            "error": result.error,
        }, indent=2))


if __name__ == "__main__":
    example_usage()