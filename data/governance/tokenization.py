"""
data/governance/tokenization.py

Enterprise Tokenization Engine.

Recursos:
- Tokenização reversível e irreversível
- Vault seguro para detokenização
- Multi-tenant
- Políticas por campo, domínio e classificação
- Rotação de chaves
- HMAC para tokens determinísticos
- Tokens randômicos para dados altamente sensíveis
- Auditoria estruturada
- Métricas
- Dry-run
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class TokenizationMode(str, Enum):
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class TokenFormat(str, Enum):
    UUID_LIKE = "uuid_like"
    PREFIXED = "prefixed"
    NUMERIC = "numeric"
    BASE64URL = "base64url"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    PII = "pii"
    FINANCIAL = "financial"
    HEALTH = "health"
    LEGAL = "legal"
    SECRET = "secret"


class TokenizationAction(str, Enum):
    TOKENIZED = "tokenized"
    DETOKENIZED = "detokenized"
    REJECTED = "rejected"
    ROTATED = "rotated"
    VALIDATED = "validated"


# =============================================================================
# Exceptions
# =============================================================================

class TokenizationError(Exception):
    pass


class TokenizationPolicyNotFound(TokenizationError):
    pass


class TokenNotFound(TokenizationError):
    pass


class DetokenizationNotAllowed(TokenizationError):
    pass


class InvalidToken(TokenizationError):
    pass


class KeyNotFound(TokenizationError):
    pass


# =============================================================================
# Protocols
# =============================================================================

class TokenVaultBackend(Protocol):
    def store(self, token: str, value: str, metadata: Dict[str, Any]) -> None:
        ...

    def retrieve(self, token: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        ...

    def delete(self, token: str) -> None:
        ...

    def exists(self, token: str) -> bool:
        ...


class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class TokenizationPolicy:
    policy_id: str
    field_name: str
    classification: DataClassification
    mode: TokenizationMode
    token_format: TokenFormat = TokenFormat.PREFIXED
    token_prefix: str = "tok"
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    deterministic: bool = False
    allow_detokenization: bool = False
    preserve_last_chars: int = 0
    min_value_length: int = 1
    enabled: bool = True
    priority: int = 100
    key_id: str = "default"
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class TokenizationRequest:
    value: str
    field_name: str
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    subject_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenizationResult:
    token: str
    field_name: str
    policy_id: str
    mode: TokenizationMode
    tenant_id: Optional[str]
    created_at: datetime
    reversible: bool
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DetokenizationRequest:
    token: str
    field_name: str
    tenant_id: Optional[str] = None
    requester: Optional[str] = None
    purpose: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class KeyMaterial:
    key_id: str
    secret: bytes
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# =============================================================================
# Backends
# =============================================================================

class InMemoryTokenVault:
    """
    Vault em memória.

    Em produção, substitua por:
    - HSM/KMS + banco criptografado
    - HashiCorp Vault
    - AWS KMS + DynamoDB
    - GCP KMS + Firestore/Spanner
    - Azure Key Vault + SQL
    """

    def __init__(self) -> None:
        self._data: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def store(self, token: str, value: str, metadata: Dict[str, Any]) -> None:
        with self._lock:
            self._data[token] = (value, metadata)

    def retrieve(self, token: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        with self._lock:
            return self._data.get(token)

    def delete(self, token: str) -> None:
        with self._lock:
            self._data.pop(token, None)

    def exists(self, token: str) -> bool:
        with self._lock:
            return token in self._data


class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info("tokenization_audit=%s", json.dumps(event, default=str))


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})


# =============================================================================
# Key Manager
# =============================================================================

class TokenizationKeyManager:
    def __init__(self, keys: Optional[List[KeyMaterial]] = None) -> None:
        self._keys: Dict[str, KeyMaterial] = {}
        self._lock = threading.RLock()

        if keys:
            for key in keys:
                self.add_key(key)

        if "default" not in self._keys:
            env_secret = os.getenv("TOKENIZATION_DEFAULT_SECRET")
            secret = (
                base64.urlsafe_b64decode(env_secret.encode())
                if env_secret
                else secrets.token_bytes(32)
            )
            self.add_key(KeyMaterial(key_id="default", secret=secret, active=True))

    def add_key(self, key: KeyMaterial) -> None:
        with self._lock:
            if not key.secret:
                raise ValueError("Secret da chave não pode ser vazio")
            self._keys[key.key_id] = key

    def get_key(self, key_id: str) -> KeyMaterial:
        with self._lock:
            key = self._keys.get(key_id)
            if not key:
                raise KeyNotFound(f"Chave não encontrada: {key_id}")
            if not key.active:
                raise KeyNotFound(f"Chave inativa: {key_id}")
            return key

    def rotate_key(self, key_id: str, new_secret: Optional[bytes] = None) -> KeyMaterial:
        with self._lock:
            if key_id in self._keys:
                old = self._keys[key_id]
                self._keys[key_id] = KeyMaterial(
                    key_id=old.key_id,
                    secret=old.secret,
                    active=False,
                    created_at=old.created_at,
                )

            rotated = KeyMaterial(
                key_id=key_id,
                secret=new_secret or secrets.token_bytes(32),
                active=True,
            )
            self._keys[key_id] = rotated
            return rotated


# =============================================================================
# Policy Repository
# =============================================================================

class TokenizationPolicyRepository:
    def __init__(self, policies: Optional[List[TokenizationPolicy]] = None) -> None:
        self._policies: Dict[str, TokenizationPolicy] = {}
        for policy in policies or []:
            self.add(policy)

    def add(self, policy: TokenizationPolicy) -> None:
        if not policy.policy_id:
            raise ValueError("policy_id é obrigatório")
        if not policy.field_name:
            raise ValueError("field_name é obrigatório")
        self._policies[policy.policy_id] = policy

    def list_enabled(self) -> List[TokenizationPolicy]:
        return [p for p in self._policies.values() if p.enabled]

    def resolve(self, request: TokenizationRequest) -> TokenizationPolicy:
        candidates: List[Tuple[int, int, TokenizationPolicy]] = []

        for policy in self.list_enabled():
            score = self._score(policy, request)
            if score > 0:
                candidates.append((policy.priority, -score, policy))

        if not candidates:
            raise TokenizationPolicyNotFound(
                f"Nenhuma política encontrada para field={request.field_name}, "
                f"tenant={request.tenant_id}, domain={request.domain}"
            )

        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    @staticmethod
    def _score(policy: TokenizationPolicy, request: TokenizationRequest) -> int:
        if policy.field_name != request.field_name:
            return 0

        score = 10

        if policy.tenant_id:
            if policy.tenant_id != request.tenant_id:
                return 0
            score += 20

        if policy.domain:
            if policy.domain != request.domain:
                return 0
            score += 15

        return score


# =============================================================================
# Token Generator
# =============================================================================

class TokenGenerator:
    @staticmethod
    def deterministic_token(
        value: str,
        key: bytes,
        policy: TokenizationPolicy,
        tenant_id: Optional[str],
    ) -> str:
        payload = f"{tenant_id or '-'}|{policy.policy_id}|{policy.field_name}|{value}"
        digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
        raw = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return TokenGenerator._format(raw, policy)

    @staticmethod
    def random_token(policy: TokenizationPolicy) -> str:
        raw = secrets.token_urlsafe(32)
        return TokenGenerator._format(raw, policy)

    @staticmethod
    def irreversible_token(
        value: str,
        key: bytes,
        policy: TokenizationPolicy,
        tenant_id: Optional[str],
    ) -> str:
        payload = f"{tenant_id or '-'}|{policy.policy_id}|{value}"
        digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha512).digest()
        raw = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return TokenGenerator._format(raw, policy)

    @staticmethod
    def _format(raw: str, policy: TokenizationPolicy) -> str:
        if policy.token_format == TokenFormat.PREFIXED:
            return f"{policy.token_prefix}_{raw[:40]}"

        if policy.token_format == TokenFormat.UUID_LIKE:
            compact = hashlib.sha256(raw.encode()).hexdigest()
            return (
                f"{compact[0:8]}-"
                f"{compact[8:12]}-"
                f"{compact[12:16]}-"
                f"{compact[16:20]}-"
                f"{compact[20:32]}"
            )

        if policy.token_format == TokenFormat.NUMERIC:
            numeric = int(hashlib.sha256(raw.encode()).hexdigest(), 16)
            return str(numeric)[0:16]

        if policy.token_format == TokenFormat.BASE64URL:
            return raw[:48]

        return raw


# =============================================================================
# Engine
# =============================================================================

class TokenizationEngine:
    def __init__(
        self,
        policy_repository: TokenizationPolicyRepository,
        vault: Optional[TokenVaultBackend] = None,
        key_manager: Optional[TokenizationKeyManager] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.policy_repository = policy_repository
        self.vault = vault or InMemoryTokenVault()
        self.key_manager = key_manager or TokenizationKeyManager()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    def tokenize(self, request: TokenizationRequest, dry_run: bool = False) -> TokenizationResult:
        policy = self.policy_repository.resolve(request)
        self._validate_request(request, policy)

        key = self.key_manager.get_key(policy.key_id)

        if policy.mode == TokenizationMode.IRREVERSIBLE:
            token = TokenGenerator.irreversible_token(
                request.value,
                key.secret,
                policy,
                request.tenant_id,
            )
            reversible = False

        elif policy.deterministic:
            token = TokenGenerator.deterministic_token(
                request.value,
                key.secret,
                policy,
                request.tenant_id,
            )
            reversible = True

        else:
            token = self._generate_unique_random_token(policy)
            reversible = True

        result = TokenizationResult(
            token=token,
            field_name=request.field_name,
            policy_id=policy.policy_id,
            mode=policy.mode,
            tenant_id=request.tenant_id,
            created_at=datetime.now(timezone.utc),
            reversible=reversible and policy.mode == TokenizationMode.REVERSIBLE,
            metadata={
                "domain": request.domain,
                "subject_id": request.subject_id,
                "correlation_id": request.correlation_id,
                "classification": policy.classification.value,
                "deterministic": policy.deterministic,
                "dry_run": dry_run,
            },
        )

        if not dry_run and result.reversible:
            self.vault.store(
                token=token,
                value=request.value,
                metadata={
                    "policy_id": policy.policy_id,
                    "field_name": request.field_name,
                    "tenant_id": request.tenant_id,
                    "domain": request.domain,
                    "subject_id": request.subject_id,
                    "created_at": result.created_at.isoformat(),
                    "key_id": policy.key_id,
                    "classification": policy.classification.value,
                    "request_metadata": request.metadata,
                },
            )

        self._audit(
            TokenizationAction.TOKENIZED,
            field_name=request.field_name,
            token=token,
            tenant_id=request.tenant_id,
            policy_id=policy.policy_id,
            correlation_id=request.correlation_id,
            extra={"dry_run": dry_run, "mode": policy.mode.value},
        )

        self._metric(
            "tokenization.tokenized.total",
            policy,
            request.tenant_id,
        )

        return result

    def tokenize_record(
        self,
        record: Dict[str, Any],
        fields: Iterable[str],
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        output = dict(record)

        for field_name in fields:
            value = output.get(field_name)
            if value is None:
                continue

            result = self.tokenize(
                TokenizationRequest(
                    value=str(value),
                    field_name=field_name,
                    tenant_id=tenant_id,
                    domain=domain,
                    subject_id=str(record.get("id") or record.get("record_id") or ""),
                ),
                dry_run=dry_run,
            )
            output[field_name] = result.token

        return output

    def detokenize(self, request: DetokenizationRequest) -> str:
        stored = self.vault.retrieve(request.token)

        if not stored:
            self._audit(
                TokenizationAction.REJECTED,
                field_name=request.field_name,
                token=request.token,
                tenant_id=request.tenant_id,
                policy_id=None,
                correlation_id=request.correlation_id,
                extra={"reason": "token_not_found"},
            )
            raise TokenNotFound(f"Token não encontrado: {request.token}")

        value, metadata = stored

        if metadata.get("tenant_id") != request.tenant_id:
            raise DetokenizationNotAllowed("Tenant inválido para este token")

        if metadata.get("field_name") != request.field_name:
            raise DetokenizationNotAllowed("Campo inválido para este token")

        policy_id = metadata.get("policy_id")
        policy = self.policy_repository._policies.get(policy_id)

        if not policy:
            raise TokenizationPolicyNotFound(f"Política não encontrada: {policy_id}")

        if not policy.allow_detokenization:
            raise DetokenizationNotAllowed(
                f"Detokenização não permitida pela política {policy.policy_id}"
            )

        self._audit(
            TokenizationAction.DETOKENIZED,
            field_name=request.field_name,
            token=request.token,
            tenant_id=request.tenant_id,
            policy_id=policy.policy_id,
            correlation_id=request.correlation_id,
            extra={
                "requester": request.requester,
                "purpose": request.purpose,
            },
        )

        self._metric("tokenization.detokenized.total", policy, request.tenant_id)

        return value

    def validate_token(self, token: str) -> bool:
        if not token:
            return False

        if self.vault.exists(token):
            return True

        if len(token) < 8:
            return False

        return "_" in token or "-" in token or token.isdigit()

    def rotate_policy_key(self, policy_id: str, new_secret: Optional[bytes] = None) -> KeyMaterial:
        policy = self.policy_repository._policies.get(policy_id)
        if not policy:
            raise TokenizationPolicyNotFound(policy_id)

        rotated = self.key_manager.rotate_key(policy.key_id, new_secret)

        self._audit(
            TokenizationAction.ROTATED,
            field_name=policy.field_name,
            token=None,
            tenant_id=policy.tenant_id,
            policy_id=policy.policy_id,
            correlation_id=None,
            extra={"key_id": policy.key_id},
        )

        return rotated

    def _generate_unique_random_token(self, policy: TokenizationPolicy) -> str:
        for _ in range(10):
            token = TokenGenerator.random_token(policy)
            if not self.vault.exists(token):
                return token

        raise TokenizationError("Não foi possível gerar token único após 10 tentativas")

    @staticmethod
    def _validate_request(request: TokenizationRequest, policy: TokenizationPolicy) -> None:
        if request.value is None:
            raise ValueError("value não pode ser None")

        if len(str(request.value)) < policy.min_value_length:
            raise ValueError(
                f"Valor menor que o mínimo permitido: {policy.min_value_length}"
            )

    def _audit(
        self,
        action: TokenizationAction,
        field_name: str,
        token: Optional[str],
        tenant_id: Optional[str],
        policy_id: Optional[str],
        correlation_id: Optional[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = {
            "event_id": self._event_id(action, token, correlation_id),
            "event_type": f"tokenization.{action.value}",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "field_name": field_name,
            "tenant_id": tenant_id,
            "policy_id": policy_id,
            "token_hash": self._safe_hash(token) if token else None,
            "correlation_id": correlation_id,
            "extra": extra or {},
        }
        self.audit_backend.write_event(event)

    def _metric(
        self,
        metric_name: str,
        policy: TokenizationPolicy,
        tenant_id: Optional[str],
    ) -> None:
        self.metrics_backend.increment(
            metric_name,
            tags={
                "policy_id": policy.policy_id,
                "field_name": policy.field_name,
                "tenant_id": tenant_id or "-",
                "classification": policy.classification.value,
                "mode": policy.mode.value,
            },
        )

    @staticmethod
    def _safe_hash(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_id(
        action: TokenizationAction,
        token: Optional[str],
        correlation_id: Optional[str],
    ) -> str:
        raw = f"{action.value}|{token or '-'}|{correlation_id or '-'}|{datetime.now(timezone.utc).isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# =============================================================================
# Policy Factory
# =============================================================================

class TokenizationPolicyFactory:
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> TokenizationPolicy:
        return TokenizationPolicy(
            policy_id=data["policy_id"],
            field_name=data["field_name"],
            classification=DataClassification(data["classification"]),
            mode=TokenizationMode(data["mode"]),
            token_format=TokenFormat(data.get("token_format", TokenFormat.PREFIXED)),
            token_prefix=data.get("token_prefix", "tok"),
            tenant_id=data.get("tenant_id"),
            domain=data.get("domain"),
            deterministic=bool(data.get("deterministic", False)),
            allow_detokenization=bool(data.get("allow_detokenization", False)),
            preserve_last_chars=int(data.get("preserve_last_chars", 0)),
            min_value_length=int(data.get("min_value_length", 1)),
            enabled=bool(data.get("enabled", True)),
            priority=int(data.get("priority", 100)),
            key_id=data.get("key_id", "default"),
            tags=data.get("tags", {}),
        )

    @staticmethod
    def from_json(json_text: str) -> List[TokenizationPolicy]:
        payload = json.loads(json_text)

        if isinstance(payload, dict):
            payload = payload.get("policies", [])

        return [TokenizationPolicyFactory.from_dict(item) for item in payload]


# =============================================================================
# Default Policies
# =============================================================================

def build_default_tokenization_policies() -> List[TokenizationPolicy]:
    return [
        TokenizationPolicy(
            policy_id="tok-customer-email",
            field_name="email",
            classification=DataClassification.PII,
            mode=TokenizationMode.REVERSIBLE,
            token_format=TokenFormat.PREFIXED,
            token_prefix="email_tok",
            deterministic=True,
            allow_detokenization=True,
            domain="customer",
            priority=10,
            tags={"lgpd": "true", "pii": "true"},
        ),
        TokenizationPolicy(
            policy_id="tok-customer-document",
            field_name="document_number",
            classification=DataClassification.PII,
            mode=TokenizationMode.REVERSIBLE,
            token_format=TokenFormat.PREFIXED,
            token_prefix="doc_tok",
            deterministic=False,
            allow_detokenization=True,
            domain="customer",
            priority=5,
            tags={"lgpd": "true", "high_sensitivity": "true"},
        ),
        TokenizationPolicy(
            policy_id="tok-card-number",
            field_name="card_number",
            classification=DataClassification.FINANCIAL,
            mode=TokenizationMode.REVERSIBLE,
            token_format=TokenFormat.NUMERIC,
            token_prefix="card",
            deterministic=False,
            allow_detokenization=False,
            domain="payments",
            priority=1,
            key_id="default",
            tags={"pci": "true"},
        ),
        TokenizationPolicy(
            policy_id="tok-health-id",
            field_name="patient_id",
            classification=DataClassification.HEALTH,
            mode=TokenizationMode.IRREVERSIBLE,
            token_format=TokenFormat.PREFIXED,
            token_prefix="patient_tok",
            deterministic=True,
            allow_detokenization=False,
            domain="health",
            priority=1,
            tags={"hipaa": "true"},
        ),
    ]


# =============================================================================
# Example Usage
# =============================================================================

def example_usage() -> None:
    repository = TokenizationPolicyRepository(
        build_default_tokenization_policies()
    )

    engine = TokenizationEngine(repository)

    record = {
        "id": "cust-001",
        "name": "Maria Silva",
        "email": "maria@email.com",
        "document_number": "12345678900",
    }

    tokenized = engine.tokenize_record(
        record=record,
        fields=["email", "document_number"],
        tenant_id="tenant-a",
        domain="customer",
        dry_run=False,
    )

    print(json.dumps(tokenized, indent=2, default=str))

    original_email = engine.detokenize(
        DetokenizationRequest(
            token=tokenized["email"],
            field_name="email",
            tenant_id="tenant-a",
            requester="data-governance-admin",
            purpose="customer-support",
        )
    )

    print("Original email:", original_email)


if __name__ == "__main__":
    example_usage()