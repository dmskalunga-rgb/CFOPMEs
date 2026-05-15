"""
data/ai/ai_governance.py

Módulo enterprise de governança de IA.

Objetivos:
- Centralizar políticas de uso, risco, compliance e aprovação de modelos/pipelines.
- Registrar decisões de governança com justificativa, severidade e trilha auditável.
- Avaliar requests de IA antes da inferência, treinamento, avaliação ou deploy.
- Suportar model cards, classificação de risco, owners, aprovação humana e expiração.
- Integrar com auditoria, logs estruturados e sinks plugáveis.
- Fornecer registry simples em memória/JSONL para políticas e decisões.

Recursos principais:
- Policy engine extensível.
- Regras deny/allow/warn/review.
- Avaliação por contexto, modelo, provider, task, dados e metadata.
- Risk scoring configurável.
- Governance decisions padronizadas.
- Model cards e AI system cards.
- Approval workflow básico.
- Exceções de política com validade.
- Export/import JSON.
- Métricas internas.

Dependências recomendadas:
    pip install pydantic
"""

from __future__ import annotations

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
from datetime import datetime, timedelta, timezone
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


def build_logger(name: str = "data.ai.ai_governance") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter(service_name=os.getenv("SERVICE_NAME", "ai-governance")))

    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = build_logger()


# =============================================================================
# Enums
# =============================================================================


class GovernanceAction(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REQUIRE_REVIEW = "require_review"
    BLOCK = "block"


class GovernanceStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    NOT_REQUIRED = "not_required"


class RiskLevel(str, Enum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicySeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    WARN = "warn"
    REVIEW = "review"
    DENY = "deny"


class PolicyScope(str, Enum):
    GLOBAL = "global"
    PROVIDER = "provider"
    MODEL = "model"
    TASK = "task"
    TENANT = "tenant"
    USER = "user"
    DATA = "data"
    PIPELINE = "pipeline"
    CUSTOM = "custom"


class AIUseCaseType(str, Enum):
    CHATBOT = "chatbot"
    DECISION_SUPPORT = "decision_support"
    AUTOMATED_DECISION = "automated_decision"
    CONTENT_GENERATION = "content_generation"
    CLASSIFICATION = "classification"
    RANKING = "ranking"
    RECOMMENDATION = "recommendation"
    BIOMETRICS = "biometrics"
    FRAUD_DETECTION = "fraud_detection"
    HEALTHCARE = "healthcare"
    LEGAL = "legal"
    FINANCIAL = "financial"
    HR = "hr"
    EDUCATION = "education"
    SECURITY = "security"
    CUSTOM = "custom"


class DataSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PERSONAL_DATA = "personal_data"
    SENSITIVE_PERSONAL_DATA = "sensitive_personal_data"


class ModelLifecycleStage(str, Enum):
    EXPERIMENTAL = "experimental"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


# =============================================================================
# Exceptions
# =============================================================================


class AIGovernanceError(Exception):
    """Erro base de governança de IA."""


class PolicyViolationError(AIGovernanceError):
    """Erro lançado quando uma política bloqueia a operação."""


class ApprovalRequiredError(AIGovernanceError):
    """Erro lançado quando uma operação exige aprovação humana."""


class GovernanceConfigurationError(AIGovernanceError):
    """Erro de configuração de governança."""


class GovernanceRegistryError(AIGovernanceError):
    """Erro no registry de governança."""


# =============================================================================
# Models
# =============================================================================


class GovernanceSubject(BaseModel):
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    team: Optional[str] = None
    role: Optional[str] = None
    region: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ModelCard(BaseModel):
    model_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    provider: str = "custom"
    version: Optional[str] = None
    stage: ModelLifecycleStage = ModelLifecycleStage.EXPERIMENTAL
    owner: Optional[str] = None
    description: Optional[str] = None
    intended_uses: List[str] = Field(default_factory=list)
    prohibited_uses: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    training_data_summary: Optional[str] = None
    evaluation_summary: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.LOW
    data_sensitivity_allowed: List[DataSensitivity] = Field(default_factory=lambda: [DataSensitivity.PUBLIC, DataSensitivity.INTERNAL])
    approved_use_cases: List[AIUseCaseType] = Field(default_factory=list)
    approval_status: GovernanceStatus = GovernanceStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    expires_at: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    updated_at: str = Field(default_factory=lambda: utc_now_iso())

    @property
    def qualified_name(self) -> str:
        version = f":{self.version}" if self.version else ""
        return f"{self.provider}/{self.name}{version}"

    def is_approved(self) -> bool:
        if self.approval_status != GovernanceStatus.APPROVED:
            return False
        if self.expires_at and parse_datetime(self.expires_at) < datetime.now(timezone.utc):
            return False
        return True


class AISystemCard(BaseModel):
    system_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    owner: Optional[str] = None
    description: Optional[str] = None
    use_case_type: AIUseCaseType = AIUseCaseType.CUSTOM
    risk_level: RiskLevel = RiskLevel.LOW
    models: List[str] = Field(default_factory=list)
    data_sources: List[str] = Field(default_factory=list)
    human_oversight: bool = False
    automated_decisioning: bool = False
    impacted_users: Optional[str] = None
    compliance_requirements: List[str] = Field(default_factory=list)
    approval_status: GovernanceStatus = GovernanceStatus.PENDING
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    updated_at: str = Field(default_factory=lambda: utc_now_iso())

    def is_approved(self) -> bool:
        if self.approval_status != GovernanceStatus.APPROVED:
            return False
        if self.expires_at and parse_datetime(self.expires_at) < datetime.now(timezone.utc):
            return False
        return True


class GovernanceRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: Optional[str] = None
    subject: GovernanceSubject = Field(default_factory=GovernanceSubject)
    model_name: Optional[str] = None
    provider: Optional[str] = None
    model_version: Optional[str] = None
    system_name: Optional[str] = None
    use_case_type: AIUseCaseType = AIUseCaseType.CUSTOM
    task_type: Optional[str] = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    input_payload: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    requested_at: str = Field(default_factory=lambda: utc_now_iso())


class PolicyCondition(BaseModel):
    field: str
    operator: str = "eq"
    value: Any = None
    case_sensitive: bool = False


class GovernancePolicy(BaseModel):
    policy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    scope: PolicyScope = PolicyScope.GLOBAL
    effect: PolicyEffect = PolicyEffect.WARN
    severity: PolicySeverity = PolicySeverity.MEDIUM
    enabled: bool = True
    priority: int = 100
    conditions: List[PolicyCondition] = Field(default_factory=list)
    reason: Optional[str] = None
    remediation: Optional[str] = None
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    updated_at: str = Field(default_factory=lambda: utc_now_iso())

    def is_active(self) -> bool:
        if not self.enabled:
            return False
        if self.expires_at and parse_datetime(self.expires_at) < datetime.now(timezone.utc):
            return False
        return True


class PolicyEvaluationResult(BaseModel):
    policy_id: str
    policy_name: str
    matched: bool
    effect: PolicyEffect
    severity: PolicySeverity
    reason: Optional[str] = None
    remediation: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class GovernanceDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    correlation_id: str
    action: GovernanceAction
    status: GovernanceStatus = GovernanceStatus.NOT_REQUIRED
    risk_level: RiskLevel = RiskLevel.LOW
    score: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    remediations: List[str] = Field(default_factory=list)
    matched_policies: List[PolicyEvaluationResult] = Field(default_factory=list)
    requires_human_review: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    decided_at: str = Field(default_factory=lambda: utc_now_iso())

    @property
    def allowed(self) -> bool:
        return self.action in {GovernanceAction.ALLOW, GovernanceAction.WARN}


class GovernanceApproval(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    decision_id: Optional[str] = None
    status: GovernanceStatus = GovernanceStatus.PENDING
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    reason: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: str = Field(default_factory=lambda: utc_now_iso())
    updated_at: str = Field(default_factory=lambda: utc_now_iso())

    def is_valid(self) -> bool:
        if self.status != GovernanceStatus.APPROVED:
            return False
        if self.expires_at and parse_datetime(self.expires_at) < datetime.now(timezone.utc):
            return False
        return True


class PolicyException(BaseModel):
    exception_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    policy_id: str
    subject_id: Optional[str] = None
    model_name: Optional[str] = None
    provider: Optional[str] = None
    reason: str
    approved_by: str
    expires_at: str
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: utc_now_iso())

    def is_active(self) -> bool:
        return self.enabled and parse_datetime(self.expires_at) >= datetime.now(timezone.utc)


@dataclass(frozen=True)
class GovernanceConfig:
    enabled: bool = True
    fail_closed: bool = True
    decision_log_path: Optional[Path] = Path("data/governance/ai_governance_decisions.jsonl")
    registry_path: Optional[Path] = Path("data/governance/ai_governance_registry.json")
    default_high_risk_requires_review: bool = True
    block_unapproved_production_models: bool = True
    block_sensitive_data_on_unapproved_models: bool = True
    max_payload_scan_chars: int = 100_000

    @staticmethod
    def from_env() -> "GovernanceConfig":
        decision_raw = os.getenv("AI_GOVERNANCE_DECISION_LOG_PATH", "data/governance/ai_governance_decisions.jsonl")
        registry_raw = os.getenv("AI_GOVERNANCE_REGISTRY_PATH", "data/governance/ai_governance_registry.json")
        return GovernanceConfig(
            enabled=env_bool("AI_GOVERNANCE_ENABLED", True),
            fail_closed=env_bool("AI_GOVERNANCE_FAIL_CLOSED", True),
            decision_log_path=Path(decision_raw) if decision_raw else None,
            registry_path=Path(registry_raw) if registry_raw else None,
            default_high_risk_requires_review=env_bool("AI_GOVERNANCE_HIGH_RISK_REVIEW", True),
            block_unapproved_production_models=env_bool("AI_GOVERNANCE_BLOCK_UNAPPROVED_PROD_MODELS", True),
            block_sensitive_data_on_unapproved_models=env_bool("AI_GOVERNANCE_BLOCK_SENSITIVE_UNAPPROVED", True),
            max_payload_scan_chars=int(os.getenv("AI_GOVERNANCE_MAX_PAYLOAD_SCAN_CHARS", "100000")),
        )


@dataclass
class GovernanceMetrics:
    requests_evaluated: int = 0
    allowed: int = 0
    warned: int = 0
    review_required: int = 0
    blocked: int = 0
    policies_matched: int = 0
    decisions_logged: int = 0
    errors: int = 0
    total_eval_seconds: float = 0.0
    last_decision_at: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        avg = self.total_eval_seconds / self.requests_evaluated if self.requests_evaluated else 0.0
        return {
            "requests_evaluated": self.requests_evaluated,
            "allowed": self.allowed,
            "warned": self.warned,
            "review_required": self.review_required,
            "blocked": self.blocked,
            "policies_matched": self.policies_matched,
            "decisions_logged": self.decisions_logged,
            "errors": self.errors,
            "average_eval_seconds": round(avg, 6),
            "total_eval_seconds": round(self.total_eval_seconds, 6),
            "last_decision_at": self.last_decision_at,
        }


# =============================================================================
# Protocols
# =============================================================================


class GovernanceRule(Protocol):
    def evaluate(self, request: GovernanceRequest, registry: "GovernanceRegistry") -> Optional[PolicyEvaluationResult]:
        """Avalia uma request e retorna resultado caso a regra seja aplicável."""


class GovernanceObserver(Protocol):
    def on_decision(self, request: GovernanceRequest, decision: GovernanceDecision) -> None:
        """Hook chamado após decisão de governança."""


# =============================================================================
# Registry
# =============================================================================


class GovernanceRegistry:
    def __init__(self) -> None:
        self.policies: Dict[str, GovernancePolicy] = {}
        self.model_cards: Dict[str, ModelCard] = {}
        self.system_cards: Dict[str, AISystemCard] = {}
        self.approvals: Dict[str, GovernanceApproval] = {}
        self.exceptions: Dict[str, PolicyException] = {}
        self._lock = threading.RLock()

    def add_policy(self, policy: GovernancePolicy) -> None:
        with self._lock:
            self.policies[policy.policy_id] = policy

    def add_model_card(self, card: ModelCard) -> None:
        with self._lock:
            self.model_cards[self._model_key(card.name, card.provider, card.version)] = card

    def add_system_card(self, card: AISystemCard) -> None:
        with self._lock:
            self.system_cards[card.name.lower()] = card

    def add_approval(self, approval: GovernanceApproval) -> None:
        with self._lock:
            self.approvals[approval.approval_id] = approval

    def add_exception(self, exception: PolicyException) -> None:
        with self._lock:
            self.exceptions[exception.exception_id] = exception

    def get_model_card(self, name: Optional[str], provider: Optional[str] = None, version: Optional[str] = None) -> Optional[ModelCard]:
        if not name:
            return None
        with self._lock:
            exact = self.model_cards.get(self._model_key(name, provider, version))
            if exact:
                return exact
            for card in self.model_cards.values():
                if card.name.lower() == name.lower() and (provider is None or card.provider.lower() == provider.lower()):
                    return card
        return None

    def get_system_card(self, name: Optional[str]) -> Optional[AISystemCard]:
        if not name:
            return None
        with self._lock:
            return self.system_cards.get(name.lower())

    def active_policies(self) -> List[GovernancePolicy]:
        with self._lock:
            return sorted([p for p in self.policies.values() if p.is_active()], key=lambda p: p.priority)

    def active_exceptions(self) -> List[PolicyException]:
        with self._lock:
            return [e for e in self.exceptions.values() if e.is_active()]

    def has_valid_approval(self, request_id: str) -> bool:
        with self._lock:
            return any(a.request_id == request_id and a.is_valid() for a in self.approvals.values())

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "policies": [model_to_dict(p) for p in self.policies.values()],
                "model_cards": [model_to_dict(c) for c in self.model_cards.values()],
                "system_cards": [model_to_dict(c) for c in self.system_cards.values()],
                "approvals": [model_to_dict(a) for a in self.approvals.values()],
                "exceptions": [model_to_dict(e) for e in self.exceptions.values()],
            }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernanceRegistry":
        registry = cls()
        for item in payload.get("policies", []):
            registry.add_policy(parse_model(GovernancePolicy, item))
        for item in payload.get("model_cards", []):
            registry.add_model_card(parse_model(ModelCard, item))
        for item in payload.get("system_cards", []):
            registry.add_system_card(parse_model(AISystemCard, item))
        for item in payload.get("approvals", []):
            registry.add_approval(parse_model(GovernanceApproval, item))
        for item in payload.get("exceptions", []):
            registry.add_exception(parse_model(PolicyException, item))
        return registry

    def save(self, path: Union[str, Path]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2, default=json_default)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "GovernanceRegistry":
        source = Path(path)
        if not source.exists():
            return cls()
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload)

    @staticmethod
    def _model_key(name: str, provider: Optional[str], version: Optional[str]) -> str:
        return f"{provider or 'custom'}::{name}::{version or ''}".lower()


# =============================================================================
# Rules
# =============================================================================


class PolicyRule:
    def __init__(self, policy: GovernancePolicy) -> None:
        self.policy = policy

    def evaluate(self, request: GovernanceRequest, registry: GovernanceRegistry) -> Optional[PolicyEvaluationResult]:
        if not self.policy.is_active():
            return None

        if self._is_excepted(request, registry):
            return None

        matched = all(evaluate_condition(request, condition) for condition in self.policy.conditions)
        if not matched:
            return None

        return PolicyEvaluationResult(
            policy_id=self.policy.policy_id,
            policy_name=self.policy.name,
            matched=True,
            effect=self.policy.effect,
            severity=self.policy.severity,
            reason=self.policy.reason,
            remediation=self.policy.remediation,
            details={"scope": self.policy.scope.value, "tags": self.policy.tags},
        )

    def _is_excepted(self, request: GovernanceRequest, registry: GovernanceRegistry) -> bool:
        for exception in registry.active_exceptions():
            if exception.policy_id != self.policy.policy_id:
                continue
            if exception.model_name and request.model_name and exception.model_name.lower() != request.model_name.lower():
                continue
            if exception.provider and request.provider and exception.provider.lower() != request.provider.lower():
                continue
            if exception.subject_id and request.subject.user_id and exception.subject_id != request.subject.user_id:
                continue
            return True
        return False


class ProductionModelApprovalRule:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def evaluate(self, request: GovernanceRequest, registry: GovernanceRegistry) -> Optional[PolicyEvaluationResult]:
        if not self.enabled:
            return None

        card = registry.get_model_card(request.model_name, request.provider, request.model_version)
        if not card:
            return PolicyEvaluationResult(
                policy_id="builtin.production_model_card_required",
                policy_name="Production model card required",
                matched=True,
                effect=PolicyEffect.REVIEW,
                severity=PolicySeverity.HIGH,
                reason="Modelo não possui model card registrado.",
                remediation="Registrar model card e submeter para aprovação.",
            )

        if card.stage == ModelLifecycleStage.PRODUCTION and not card.is_approved():
            return PolicyEvaluationResult(
                policy_id="builtin.production_model_approval_required",
                policy_name="Production model approval required",
                matched=True,
                effect=PolicyEffect.DENY,
                severity=PolicySeverity.CRITICAL,
                reason="Modelo em produção não está aprovado ou aprovação expirou.",
                remediation="Aprovar o model card antes de usar em produção.",
            )

        return None


class SensitiveDataOnUnapprovedModelRule:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def evaluate(self, request: GovernanceRequest, registry: GovernanceRegistry) -> Optional[PolicyEvaluationResult]:
        if not self.enabled:
            return None

        if request.data_sensitivity not in {DataSensitivity.PERSONAL_DATA, DataSensitivity.SENSITIVE_PERSONAL_DATA, DataSensitivity.RESTRICTED}:
            return None

        card = registry.get_model_card(request.model_name, request.provider, request.model_version)
        if not card or not card.is_approved():
            return PolicyEvaluationResult(
                policy_id="builtin.sensitive_data_unapproved_model",
                policy_name="Sensitive data requires approved model",
                matched=True,
                effect=PolicyEffect.DENY,
                severity=PolicySeverity.CRITICAL,
                reason="Dados sensíveis/restritos exigem modelo aprovado.",
                remediation="Use um modelo aprovado ou reduza a sensibilidade dos dados.",
            )

        if request.data_sensitivity not in set(card.data_sensitivity_allowed):
            return PolicyEvaluationResult(
                policy_id="builtin.sensitive_data_not_allowed_for_model",
                policy_name="Data sensitivity not allowed for model",
                matched=True,
                effect=PolicyEffect.DENY,
                severity=PolicySeverity.CRITICAL,
                reason="O model card não permite este nível de sensibilidade de dados.",
                remediation="Atualize aprovação do model card ou use outro modelo.",
            )

        return None


class HighRiskUseCaseReviewRule:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def evaluate(self, request: GovernanceRequest, registry: GovernanceRegistry) -> Optional[PolicyEvaluationResult]:
        if not self.enabled:
            return None

        high_risk_use_cases = {
            AIUseCaseType.AUTOMATED_DECISION,
            AIUseCaseType.BIOMETRICS,
            AIUseCaseType.HEALTHCARE,
            AIUseCaseType.LEGAL,
            AIUseCaseType.FINANCIAL,
            AIUseCaseType.HR,
            AIUseCaseType.SECURITY,
        }

        if request.use_case_type in high_risk_use_cases:
            if registry.has_valid_approval(request.request_id):
                return None
            return PolicyEvaluationResult(
                policy_id="builtin.high_risk_use_case_review",
                policy_name="High risk use case requires review",
                matched=True,
                effect=PolicyEffect.REVIEW,
                severity=PolicySeverity.HIGH,
                reason="Caso de uso de alto risco exige revisão/aprovação humana.",
                remediation="Submeter request para aprovação de governança.",
            )

        return None


class PayloadSensitivePatternRule:
    def __init__(self, max_chars: int = 100_000) -> None:
        self.max_chars = max_chars
        self.patterns = {
            "possible_credit_card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
            "possible_email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
            "possible_brazil_cpf": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
        }

    def evaluate(self, request: GovernanceRequest, registry: GovernanceRegistry) -> Optional[PolicyEvaluationResult]:
        if not request.input_payload:
            return None

        text = json.dumps(request.input_payload, ensure_ascii=False, default=json_default)[: self.max_chars]
        matches = [name for name, pattern in self.patterns.items() if pattern.search(text)]

        if not matches:
            return None

        return PolicyEvaluationResult(
            policy_id="builtin.payload_sensitive_pattern",
            policy_name="Payload may contain sensitive data",
            matched=True,
            effect=PolicyEffect.WARN,
            severity=PolicySeverity.MEDIUM,
            reason="Payload parece conter dados pessoais/sensíveis.",
            remediation="Classifique corretamente data_sensitivity e aplique minimização/redaction.",
            details={"matches": matches},
        )


# =============================================================================
# Decision sink
# =============================================================================


class GovernanceDecisionSink:
    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, request: GovernanceRequest, decision: GovernanceDecision) -> int:
        if not self.path:
            return 0

        payload = {
            "request": model_to_dict(request),
            "decision": model_to_dict(decision),
            "logged_at": utc_now_iso(),
        }
        line = json.dumps(payload, ensure_ascii=False, default=json_default) + "\n"

        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

        return len(line.encode("utf-8"))


# =============================================================================
# Governance engine
# =============================================================================


class AIGovernanceEngine:
    def __init__(
        self,
        config: Optional[GovernanceConfig] = None,
        registry: Optional[GovernanceRegistry] = None,
        rules: Optional[Sequence[GovernanceRule]] = None,
        observers: Optional[Sequence[GovernanceObserver]] = None,
    ) -> None:
        self.config = config or GovernanceConfig.from_env()
        self.registry = registry or self._load_or_create_registry()
        self.rules = list(rules or self._default_rules())
        self.observers = list(observers or [])
        self.metrics = GovernanceMetrics()
        self.decision_sink = GovernanceDecisionSink(self.config.decision_log_path)

    @classmethod
    def from_env(cls) -> "AIGovernanceEngine":
        return cls(config=GovernanceConfig.from_env())

    def evaluate(self, request: GovernanceRequest) -> GovernanceDecision:
        if not self.config.enabled:
            return GovernanceDecision(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                action=GovernanceAction.ALLOW,
                status=GovernanceStatus.NOT_REQUIRED,
                risk_level=RiskLevel.MINIMAL,
                reasons=["Governança desabilitada."],
            )

        started = time.perf_counter()
        self.metrics.requests_evaluated += 1

        try:
            matched = self._evaluate_rules(request)
            risk_level, score = self._calculate_risk(request, matched)
            decision = self._build_decision(request, matched, risk_level, score)
            self._update_metrics(decision, matched)
            self._log_decision(request, decision)
            self._notify_observers(request, decision)
            return decision

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.errors += 1
            logger.exception("Falha ao avaliar governança de IA. error=%s", exc)
            if self.config.fail_closed:
                decision = GovernanceDecision(
                    request_id=request.request_id,
                    correlation_id=request.correlation_id,
                    action=GovernanceAction.BLOCK,
                    status=GovernanceStatus.REJECTED,
                    risk_level=RiskLevel.CRITICAL,
                    score=100.0,
                    reasons=[f"Erro de governança em modo fail-closed: {exc}"],
                )
                self._log_decision(request, decision)
                return decision
            return GovernanceDecision(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                action=GovernanceAction.WARN,
                status=GovernanceStatus.NOT_REQUIRED,
                risk_level=RiskLevel.MEDIUM,
                score=50.0,
                reasons=[f"Erro de governança em modo fail-open: {exc}"],
            )
        finally:
            self.metrics.total_eval_seconds += time.perf_counter() - started

    def enforce(self, request: GovernanceRequest) -> GovernanceDecision:
        decision = self.evaluate(request)

        if decision.action == GovernanceAction.BLOCK:
            raise PolicyViolationError("; ".join(decision.reasons) or "Operação bloqueada por governança.")

        if decision.action == GovernanceAction.REQUIRE_REVIEW:
            raise ApprovalRequiredError("; ".join(decision.reasons) or "Operação exige revisão/aprovação humana.")

        return decision

    def approve_model(
        self,
        model_name: str,
        provider: str = "custom",
        version: Optional[str] = None,
        approved_by: str = "governance-admin",
        expires_in_days: Optional[int] = 365,
    ) -> ModelCard:
        card = self.registry.get_model_card(model_name, provider, version)
        if not card:
            card = ModelCard(name=model_name, provider=provider, version=version)

        card.approval_status = GovernanceStatus.APPROVED
        card.approved_by = approved_by
        card.approved_at = utc_now_iso()
        card.expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
        card.updated_at = utc_now_iso()
        self.registry.add_model_card(card)
        self._persist_registry()
        return card

    def request_approval(
        self,
        request: GovernanceRequest,
        requested_by: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> GovernanceApproval:
        approval = GovernanceApproval(
            request_id=request.request_id,
            status=GovernanceStatus.PENDING,
            requested_by=requested_by or request.subject.user_id,
            reason=reason,
        )
        self.registry.add_approval(approval)
        self._persist_registry()
        return approval

    def approve_request(
        self,
        approval_id: str,
        approved_by: str,
        expires_in_days: Optional[int] = 30,
    ) -> GovernanceApproval:
        approval = self.registry.approvals.get(approval_id)
        if not approval:
            raise GovernanceRegistryError(f"Approval não encontrado: {approval_id}")

        approval.status = GovernanceStatus.APPROVED
        approval.approved_by = approved_by
        approval.updated_at = utc_now_iso()
        approval.expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat() if expires_in_days else None
        self.registry.add_approval(approval)
        self._persist_registry()
        return approval

    def add_policy(self, policy: GovernancePolicy) -> None:
        self.registry.add_policy(policy)
        self.rules.append(PolicyRule(policy))
        self._persist_registry()

    def add_model_card(self, card: ModelCard) -> None:
        self.registry.add_model_card(card)
        self._persist_registry()

    def add_system_card(self, card: AISystemCard) -> None:
        self.registry.add_system_card(card)
        self._persist_registry()

    def add_exception(self, exception: PolicyException) -> None:
        self.registry.add_exception(exception)
        self._persist_registry()

    def _evaluate_rules(self, request: GovernanceRequest) -> List[PolicyEvaluationResult]:
        results: List[PolicyEvaluationResult] = []

        for rule in self.rules:
            result = rule.evaluate(request, self.registry)
            if result and result.matched:
                results.append(result)

        return results

    def _calculate_risk(
        self,
        request: GovernanceRequest,
        results: Sequence[PolicyEvaluationResult],
    ) -> Tuple[RiskLevel, float]:
        score = 0.0

        sensitivity_score = {
            DataSensitivity.PUBLIC: 0,
            DataSensitivity.INTERNAL: 10,
            DataSensitivity.CONFIDENTIAL: 25,
            DataSensitivity.RESTRICTED: 40,
            DataSensitivity.PERSONAL_DATA: 45,
            DataSensitivity.SENSITIVE_PERSONAL_DATA: 65,
        }[request.data_sensitivity]
        score += sensitivity_score

        use_case_score = {
            AIUseCaseType.AUTOMATED_DECISION: 35,
            AIUseCaseType.BIOMETRICS: 40,
            AIUseCaseType.HEALTHCARE: 35,
            AIUseCaseType.LEGAL: 30,
            AIUseCaseType.FINANCIAL: 30,
            AIUseCaseType.HR: 30,
            AIUseCaseType.SECURITY: 30,
        }.get(request.use_case_type, 5)
        score += use_case_score

        for result in results:
            score += {
                PolicySeverity.INFO: 1,
                PolicySeverity.LOW: 5,
                PolicySeverity.MEDIUM: 15,
                PolicySeverity.HIGH: 30,
                PolicySeverity.CRITICAL: 50,
            }[result.severity]

        score = min(100.0, score)

        if score >= 85:
            return RiskLevel.CRITICAL, score
        if score >= 65:
            return RiskLevel.HIGH, score
        if score >= 40:
            return RiskLevel.MEDIUM, score
        if score >= 15:
            return RiskLevel.LOW, score
        return RiskLevel.MINIMAL, score

    def _build_decision(
        self,
        request: GovernanceRequest,
        results: Sequence[PolicyEvaluationResult],
        risk_level: RiskLevel,
        score: float,
    ) -> GovernanceDecision:
        effects = {result.effect for result in results}
        reasons = [result.reason or result.policy_name for result in results]
        remediations = [result.remediation for result in results if result.remediation]

        if PolicyEffect.DENY in effects:
            action = GovernanceAction.BLOCK
            status = GovernanceStatus.REJECTED
            requires_review = False
        elif PolicyEffect.REVIEW in effects:
            action = GovernanceAction.REQUIRE_REVIEW
            status = GovernanceStatus.PENDING
            requires_review = True
        elif PolicyEffect.WARN in effects:
            action = GovernanceAction.WARN
            status = GovernanceStatus.NOT_REQUIRED
            requires_review = False
        else:
            action = GovernanceAction.ALLOW
            status = GovernanceStatus.NOT_REQUIRED
            requires_review = False

        if (
            self.config.default_high_risk_requires_review
            and risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            and action == GovernanceAction.ALLOW
        ):
            action = GovernanceAction.REQUIRE_REVIEW
            status = GovernanceStatus.PENDING
            requires_review = True
            reasons.append("Risco alto/crítico exige revisão humana por configuração.")

        if not reasons:
            reasons.append("Nenhuma política bloqueante aplicável.")

        return GovernanceDecision(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            action=action,
            status=status,
            risk_level=risk_level,
            score=score,
            reasons=reasons,
            remediations=remediations,
            matched_policies=list(results),
            requires_human_review=requires_review,
            metadata={
                "model_name": request.model_name,
                "provider": request.provider,
                "use_case_type": request.use_case_type.value,
                "data_sensitivity": request.data_sensitivity.value,
            },
        )

    def _update_metrics(self, decision: GovernanceDecision, results: Sequence[PolicyEvaluationResult]) -> None:
        self.metrics.policies_matched += len(results)
        self.metrics.last_decision_at = decision.decided_at

        if decision.action == GovernanceAction.ALLOW:
            self.metrics.allowed += 1
        elif decision.action == GovernanceAction.WARN:
            self.metrics.warned += 1
        elif decision.action == GovernanceAction.REQUIRE_REVIEW:
            self.metrics.review_required += 1
        elif decision.action == GovernanceAction.BLOCK:
            self.metrics.blocked += 1

    def _log_decision(self, request: GovernanceRequest, decision: GovernanceDecision) -> None:
        try:
            self.decision_sink.write(request, decision)
            self.metrics.decisions_logged += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.metrics.errors += 1
            logger.warning("Falha ao gravar decisão de governança. error=%s", exc)

    def _notify_observers(self, request: GovernanceRequest, decision: GovernanceDecision) -> None:
        for observer in self.observers:
            try:
                observer.on_decision(request, decision)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Observer de governança falhou. error=%s", exc)

    def _default_rules(self) -> List[GovernanceRule]:
        rules: List[GovernanceRule] = [
            ProductionModelApprovalRule(enabled=self.config.block_unapproved_production_models),
            SensitiveDataOnUnapprovedModelRule(enabled=self.config.block_sensitive_data_on_unapproved_models),
            HighRiskUseCaseReviewRule(enabled=self.config.default_high_risk_requires_review),
            PayloadSensitivePatternRule(max_chars=self.config.max_payload_scan_chars),
        ]
        rules.extend(PolicyRule(policy) for policy in self.registry.active_policies())
        return rules

    def _load_or_create_registry(self) -> GovernanceRegistry:
        if self.config.registry_path and self.config.registry_path.exists():
            return GovernanceRegistry.load(self.config.registry_path)
        return GovernanceRegistry()

    def _persist_registry(self) -> None:
        if self.config.registry_path:
            self.registry.save(self.config.registry_path)


# =============================================================================
# Condition evaluation
# =============================================================================


def evaluate_condition(request: GovernanceRequest, condition: PolicyCondition) -> bool:
    value = get_path_value(model_to_dict(request), condition.field)
    expected = condition.value
    op = condition.operator.lower().strip()

    if isinstance(value, str) and isinstance(expected, str) and not condition.case_sensitive:
        left = value.lower()
        right = expected.lower()
    else:
        left = value
        right = expected

    if op in {"eq", "=", "=="}:
        return left == right
    if op in {"ne", "!=", "<>"}:
        return left != right
    if op in {"in"}:
        return left in (right or [])
    if op in {"not_in"}:
        return left not in (right or [])
    if op in {"contains"}:
        return str(right) in str(left)
    if op in {"regex", "matches"}:
        flags = 0 if condition.case_sensitive else re.IGNORECASE
        return re.search(str(expected), str(value), flags=flags) is not None
    if op in {"exists"}:
        return value is not None
    if op in {"missing", "not_exists"}:
        return value is None
    if op in {"gt", ">"}:
        return float(value) > float(expected)
    if op in {"gte", ">="}:
        return float(value) >= float(expected)
    if op in {"lt", "<"}:
        return float(value) < float(expected)
    if op in {"lte", "<="}:
        return float(value) <= float(expected)

    raise ValueError(f"Operador de política não suportado: {condition.operator}")


# =============================================================================
# Utilitários
# =============================================================================


def build_policy(
    name: str,
    effect: PolicyEffect,
    conditions: Sequence[Union[PolicyCondition, Mapping[str, Any]]],
    scope: PolicyScope = PolicyScope.GLOBAL,
    severity: PolicySeverity = PolicySeverity.MEDIUM,
    reason: Optional[str] = None,
    remediation: Optional[str] = None,
    priority: int = 100,
    tags: Optional[Sequence[str]] = None,
) -> GovernancePolicy:
    parsed_conditions = [c if isinstance(c, PolicyCondition) else parse_model(PolicyCondition, c) for c in conditions]
    return GovernancePolicy(
        name=name,
        effect=effect,
        conditions=parsed_conditions,
        scope=scope,
        severity=severity,
        reason=reason,
        remediation=remediation,
        priority=priority,
        tags=list(tags or []),
    )


def get_path_value(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def parse_model(model_class: Any, payload: Mapping[str, Any]) -> Any:
    if hasattr(model_class, "model_validate"):
        return model_class.model_validate(payload)
    return model_class.parse_obj(payload)


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[no-any-return]
    return model.dict()  # type: ignore[no-any-return]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Path, Enum)):
        return str(value)
    return str(value)


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


# =============================================================================
# Exemplo CLI simples
# =============================================================================


def example_engine() -> AIGovernanceEngine:
    registry = GovernanceRegistry()

    registry.add_model_card(
        ModelCard(
            name="enterprise-llm",
            provider="custom",
            version="1.0",
            stage=ModelLifecycleStage.PRODUCTION,
            owner="ai-platform",
            risk_level=RiskLevel.MEDIUM,
            data_sensitivity_allowed=[DataSensitivity.PUBLIC, DataSensitivity.INTERNAL, DataSensitivity.CONFIDENTIAL],
            approved_use_cases=[AIUseCaseType.CHATBOT, AIUseCaseType.CONTENT_GENERATION],
            approval_status=GovernanceStatus.APPROVED,
            approved_by="governance-admin",
            approved_at=utc_now_iso(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        )
    )

    registry.add_policy(
        build_policy(
            name="Block HR automated decisions",
            effect=PolicyEffect.DENY,
            severity=PolicySeverity.CRITICAL,
            scope=PolicyScope.TASK,
            conditions=[{"field": "use_case_type", "operator": "eq", "value": AIUseCaseType.HR.value}],
            reason="Casos de uso de RH com IA exigem processo específico de compliance.",
            remediation="Submeter ao comitê de governança antes de usar.",
            priority=10,
            tags=["hr", "compliance", "high-risk"],
        )
    )

    return AIGovernanceEngine(config=GovernanceConfig.from_env(), registry=registry)


def main() -> None:
    engine = example_engine()
    request = GovernanceRequest(
        model_name="enterprise-llm",
        provider="custom",
        model_version="1.0",
        use_case_type=AIUseCaseType.CHATBOT,
        data_sensitivity=DataSensitivity.INTERNAL,
        input_payload={"prompt": "Explique governança de IA", "email": "user@example.com"},
        subject=GovernanceSubject(user_id="example-user", tenant_id="example-tenant"),
    )
    decision = engine.evaluate(request)
    logger.info("Decisão de governança: %s", json.dumps(model_to_dict(decision), ensure_ascii=False, default=json_default))
    logger.info("Métricas: %s", json.dumps(engine.metrics.snapshot(), ensure_ascii=False))


if __name__ == "__main__":
    main()
