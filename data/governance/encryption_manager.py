"""
encryption_manager.py
=====================

Enterprise-grade encryption manager for data governance platforms.

Core capabilities
-----------------
- Envelope encryption with pluggable KMS/key providers.
- Symmetric authenticated encryption using AES-GCM when cryptography is available.
- Secure fallback interface that fails closed when required crypto is unavailable.
- Key metadata, lifecycle states, rotation, retirement and destruction workflow.
- Classification-aware encryption policy resolution.
- Field, record, JSON and bytes encryption/decryption helpers.
- HMAC signing/verification and deterministic SHA-256 hashing utilities.
- Audit events for encryption, decryption, key usage and rotation.
- Key versioning and ciphertext envelope format for long-term decryptability.

Security note
-------------
For production, prefer a managed KMS/HSM-backed provider. The in-memory key store
is intended for development, tests and local pipelines only.
"""

from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import enum
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
BytesLike = Union[bytes, bytearray, memoryview]


class EncryptionManagerError(Exception):
    """Base exception for encryption manager failures."""


class CryptoDependencyError(EncryptionManagerError):
    """Raised when required cryptography backend is unavailable."""


class KeyManagementError(EncryptionManagerError):
    """Raised when key lifecycle or key lookup fails."""


class EncryptionPolicyError(EncryptionManagerError):
    """Raised when encryption policy is invalid or cannot be resolved."""


class DecryptionError(EncryptionManagerError):
    """Raised when ciphertext cannot be decrypted or verified."""


class KeyState(str, enum.Enum):
    ACTIVE = "active"
    ROTATING = "rotating"
    RETIRED = "retired"
    DISABLED = "disabled"
    DESTROYED = "destroyed"


class KeyPurpose(str, enum.Enum):
    DATA_ENCRYPTION = "data_encryption"
    KEY_ENCRYPTION = "key_encryption"
    SIGNING = "signing"
    HMAC = "hmac"


class EncryptionAlgorithm(str, enum.Enum):
    AES_256_GCM = "AES-256-GCM"


class EncryptionMode(str, enum.Enum):
    RAW_BYTES = "raw_bytes"
    JSON = "json"
    FIELD = "field"
    RECORD = "record"


class SensitivityLevel(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    HIGHLY_RESTRICTED = "highly_restricted"


class MatchMode(str, enum.Enum):
    EXACT = "exact"
    REGEX = "regex"
    CLASSIFICATION = "classification"
    TAG = "tag"


@dataclass(frozen=True)
class EncryptionContext:
    purpose: str = "data_protection"
    dataset_name: Optional[str] = None
    field_name: Optional[str] = None
    record_id: Optional[str] = None
    tenant_id: Optional[str] = None
    actor_id: Optional[str] = None
    environment: str = "prod"
    classification: Optional[str] = None
    sensitivity: Optional[SensitivityLevel] = None
    tags: Set[str] = field(default_factory=set)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: JsonDict = field(default_factory=dict)

    def aad(self) -> bytes:
        payload = {
            "purpose": self.purpose,
            "dataset_name": self.dataset_name,
            "field_name": self.field_name,
            "record_id": self.record_id,
            "tenant_id": self.tenant_id,
            "environment": self.environment,
            "classification": self.classification,
            "sensitivity": self.sensitivity.value if self.sensitivity else None,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")

    def to_dict(self) -> JsonDict:
        return {
            "purpose": self.purpose,
            "dataset_name": self.dataset_name,
            "field_name": self.field_name,
            "record_id": self.record_id,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "environment": self.environment,
            "classification": self.classification,
            "sensitivity": self.sensitivity.value if self.sensitivity else None,
            "tags": sorted(self.tags),
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class KeyMetadata:
    key_id: str
    version: int
    alias: str
    purpose: KeyPurpose
    algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM
    state: KeyState = KeyState.ACTIVE
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    rotated_at: Optional[dt.datetime] = None
    expires_at: Optional[dt.datetime] = None
    owner_id: Optional[str] = None
    tenant_id: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    metadata: JsonDict = field(default_factory=dict)

    @property
    def key_ref(self) -> str:
        return f"{self.alias}:v{self.version}"

    def can_encrypt(self) -> bool:
        return self.state in {KeyState.ACTIVE, KeyState.ROTATING} and self.purpose in {KeyPurpose.DATA_ENCRYPTION, KeyPurpose.KEY_ENCRYPTION}

    def can_decrypt(self) -> bool:
        return self.state in {KeyState.ACTIVE, KeyState.ROTATING, KeyState.RETIRED} and self.purpose in {KeyPurpose.DATA_ENCRYPTION, KeyPurpose.KEY_ENCRYPTION}

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class KeyMaterial:
    metadata: KeyMetadata
    key_bytes: bytes

    def fingerprint(self) -> str:
        return hashlib.sha256(self.key_bytes).hexdigest()


@dataclass(frozen=True)
class CiphertextEnvelope:
    version: str
    algorithm: EncryptionAlgorithm
    key_id: str
    key_version: int
    key_alias: str
    nonce: str
    ciphertext: str
    aad_hash: str
    encrypted_at: dt.datetime
    mode: EncryptionMode
    compression: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "version": self.version,
            "algorithm": self.algorithm.value,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "key_alias": self.key_alias,
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
            "aad_hash": self.aad_hash,
            "encrypted_at": self.encrypted_at.isoformat(),
            "mode": self.mode.value,
            "compression": self.compression,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CiphertextEnvelope":
        return cls(
            version=str(data["version"]),
            algorithm=EncryptionAlgorithm(data["algorithm"]),
            key_id=str(data["key_id"]),
            key_version=int(data["key_version"]),
            key_alias=str(data["key_alias"]),
            nonce=str(data["nonce"]),
            ciphertext=str(data["ciphertext"]),
            aad_hash=str(data["aad_hash"]),
            encrypted_at=parse_datetime(data["encrypted_at"]),
            mode=EncryptionMode(data["mode"]),
            compression=data.get("compression"),
            metadata=dict(data.get("metadata") or {}),
        )

    def serialize(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return "enc:" + base64.urlsafe_b64encode(raw).decode("ascii")

    @classmethod
    def deserialize(cls, value: str) -> "CiphertextEnvelope":
        if not value.startswith("enc:"):
            raise DecryptionError("Invalid ciphertext envelope prefix")
        raw = base64.urlsafe_b64decode(value[4:].encode("ascii"))
        return cls.from_dict(json.loads(raw.decode("utf-8")))


@dataclass(frozen=True)
class EncryptionPolicy:
    policy_id: str
    name: str
    key_alias: str
    match_mode: MatchMode = MatchMode.EXACT
    fields: Tuple[str, ...] = field(default_factory=tuple)
    field_patterns: Tuple[str, ...] = field(default_factory=tuple)
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    sensitivity_levels: Tuple[SensitivityLevel, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    priority: int = 100
    encrypt_nulls: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def matches(self, context: EncryptionContext) -> bool:
        if not self.enabled:
            return False
        field = context.field_name or ""
        field_norm = normalize_name(field)
        if self.match_mode == MatchMode.EXACT:
            return field_norm in {normalize_name(item) for item in self.fields}
        if self.match_mode == MatchMode.REGEX:
            return any(re.search(pattern, field, re.IGNORECASE) for pattern in self.field_patterns)
        if self.match_mode == MatchMode.CLASSIFICATION:
            classification_match = bool(context.classification and context.classification in self.classifications)
            sensitivity_match = bool(context.sensitivity and context.sensitivity in self.sensitivity_levels)
            return classification_match or sensitivity_match
        if self.match_mode == MatchMode.TAG:
            return bool(set(self.tags).intersection(context.tags))
        return False

    def to_dict(self) -> JsonDict:
        return {
            "policy_id": self.policy_id,
            "name": self.name,
            "key_alias": self.key_alias,
            "match_mode": self.match_mode.value,
            "fields": list(self.fields),
            "field_patterns": list(self.field_patterns),
            "classifications": list(self.classifications),
            "sensitivity_levels": [level.value for level in self.sensitivity_levels],
            "tags": list(self.tags),
            "enabled": self.enabled,
            "priority": self.priority,
            "encrypt_nulls": self.encrypt_nulls,
            "metadata": dict(self.metadata),
        }


@dataclass
class EncryptionDecision:
    field_name: Optional[str]
    policy_id: Optional[str]
    key_alias: Optional[str]
    key_version: Optional[int]
    encrypted: bool
    reason: str
    plaintext_hash: Optional[str] = None
    ciphertext_hash: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return dataclasses.asdict(self)


@dataclass
class EncryptionOperationResult:
    output: Any
    decisions: List[EncryptionDecision]
    operation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "operation_id": self.operation_id,
            "duration_ms": self.duration_ms,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


@dataclass(frozen=True)
class EncryptionManagerConfig:
    default_key_alias: str = "data-default"
    envelope_version: str = "1.0"
    require_crypto_backend: bool = True
    enable_audit: bool = True
    fail_closed: bool = True
    metadata: JsonDict = field(default_factory=dict)


@runtime_checkable
class KeyProvider(Protocol):
    def create_key(self, alias: str, purpose: KeyPurpose, *, tenant_id: Optional[str] = None, owner_id: Optional[str] = None, tags: Sequence[str] = ()) -> KeyMetadata:
        ...

    def get_key(self, key_id: str, version: Optional[int] = None) -> KeyMaterial:
        ...

    def get_active_key_by_alias(self, alias: str, *, tenant_id: Optional[str] = None) -> KeyMaterial:
        ...

    def rotate_key(self, alias: str, *, tenant_id: Optional[str] = None) -> KeyMetadata:
        ...

    def update_key_state(self, key_id: str, version: int, state: KeyState) -> KeyMetadata:
        ...

    def list_keys(self, alias: Optional[str] = None, tenant_id: Optional[str] = None) -> List[KeyMetadata]:
        ...


class InMemoryKeyProvider(KeyProvider):
    """Development/test key provider.

    Production deployments should replace this with a managed KMS/HSM provider.
    """

    def __init__(self) -> None:
        self._keys: Dict[Tuple[str, int], KeyMaterial] = {}
        self._alias_versions: Dict[Tuple[str, Optional[str]], List[int]] = {}

    def create_key(
        self,
        alias: str,
        purpose: KeyPurpose,
        *,
        tenant_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        tags: Sequence[str] = (),
    ) -> KeyMetadata:
        version = self._next_version(alias, tenant_id)
        key_id = str(uuid.uuid4())
        metadata = KeyMetadata(
            key_id=key_id,
            version=version,
            alias=alias,
            purpose=purpose,
            owner_id=owner_id,
            tenant_id=tenant_id,
            tags=tuple(tags),
        )
        material = KeyMaterial(metadata=metadata, key_bytes=secrets.token_bytes(32))
        self._keys[(key_id, version)] = material
        self._alias_versions.setdefault((alias, tenant_id), []).append(version)
        return metadata

    def get_key(self, key_id: str, version: Optional[int] = None) -> KeyMaterial:
        candidates = [material for (kid, ver), material in self._keys.items() if kid == key_id and (version is None or ver == version)]
        if not candidates:
            raise KeyManagementError(f"Key not found: {key_id}:v{version}")
        return sorted(candidates, key=lambda item: item.metadata.version, reverse=True)[0]

    def get_active_key_by_alias(self, alias: str, *, tenant_id: Optional[str] = None) -> KeyMaterial:
        candidates = [
            material
            for material in self._keys.values()
            if material.metadata.alias == alias
            and material.metadata.tenant_id == tenant_id
            and material.metadata.state in {KeyState.ACTIVE, KeyState.ROTATING}
        ]
        if not candidates:
            metadata = self.create_key(alias, KeyPurpose.DATA_ENCRYPTION, tenant_id=tenant_id)
            return self.get_key(metadata.key_id, metadata.version)
        return sorted(candidates, key=lambda item: item.metadata.version, reverse=True)[0]

    def rotate_key(self, alias: str, *, tenant_id: Optional[str] = None) -> KeyMetadata:
        for material in list(self._keys.values()):
            meta = material.metadata
            if meta.alias == alias and meta.tenant_id == tenant_id and meta.state == KeyState.ACTIVE:
                updated = dataclasses.replace(meta, state=KeyState.RETIRED, rotated_at=dt.datetime.now(dt.timezone.utc))
                self._keys[(meta.key_id, meta.version)] = dataclasses.replace(material, metadata=updated)
        return self.create_key(alias, KeyPurpose.DATA_ENCRYPTION, tenant_id=tenant_id)

    def update_key_state(self, key_id: str, version: int, state: KeyState) -> KeyMetadata:
        material = self.get_key(key_id, version)
        updated = dataclasses.replace(material.metadata, state=state)
        self._keys[(key_id, version)] = dataclasses.replace(material, metadata=updated)
        return updated

    def list_keys(self, alias: Optional[str] = None, tenant_id: Optional[str] = None) -> List[KeyMetadata]:
        metas = [material.metadata for material in self._keys.values()]
        if alias is not None:
            metas = [meta for meta in metas if meta.alias == alias]
        if tenant_id is not None:
            metas = [meta for meta in metas if meta.tenant_id == tenant_id]
        return sorted(metas, key=lambda meta: (meta.alias, meta.version))

    def _next_version(self, alias: str, tenant_id: Optional[str]) -> int:
        versions = self._alias_versions.get((alias, tenant_id), [])
        return max(versions, default=0) + 1


@runtime_checkable
class EncryptionAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingEncryptionAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("encryption_audit", extra={"event_type": event_type, "payload": dict(payload)})


class EncryptionPolicyRegistry:
    def __init__(self, policies: Optional[Iterable[EncryptionPolicy]] = None) -> None:
        self._policies: Dict[str, EncryptionPolicy] = {}
        for policy in default_encryption_policies():
            self.register(policy, replace=True)
        if policies:
            for policy in policies:
                self.register(policy, replace=True)

    def register(self, policy: EncryptionPolicy, *, replace: bool = False) -> None:
        if policy.policy_id in self._policies and not replace:
            raise EncryptionPolicyError(f"Policy already registered: {policy.policy_id}")
        self._policies[policy.policy_id] = policy

    def resolve(self, context: EncryptionContext) -> Optional[EncryptionPolicy]:
        matches = [policy for policy in self._policies.values() if policy.matches(context)]
        if not matches:
            return None
        return sorted(matches, key=lambda policy: policy.priority)[0]

    def list_policies(self, *, enabled_only: bool = True) -> List[EncryptionPolicy]:
        policies = list(self._policies.values())
        if enabled_only:
            policies = [policy for policy in policies if policy.enabled]
        return sorted(policies, key=lambda policy: policy.priority)

    def to_dict(self) -> JsonDict:
        return {policy_id: policy.to_dict() for policy_id, policy in self._policies.items()}


class EncryptionManager:
    """Main enterprise encryption manager."""

    def __init__(
        self,
        *,
        config: Optional[EncryptionManagerConfig] = None,
        key_provider: Optional[KeyProvider] = None,
        policy_registry: Optional[EncryptionPolicyRegistry] = None,
        audit_sink: Optional[EncryptionAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or EncryptionManagerConfig()
        self.key_provider = key_provider or InMemoryKeyProvider()
        self.policies = policy_registry or EncryptionPolicyRegistry()
        self.audit = audit_sink or LoggingEncryptionAuditSink()
        self.log = log or logger
        self._ensure_crypto_available()

    def encrypt_bytes(
        self,
        plaintext: BytesLike,
        *,
        context: Optional[EncryptionContext] = None,
        key_alias: Optional[str] = None,
        mode: EncryptionMode = EncryptionMode.RAW_BYTES,
    ) -> CiphertextEnvelope:
        self._ensure_crypto_available()
        context = context or EncryptionContext()
        key_alias = key_alias or self.config.default_key_alias
        key = self.key_provider.get_active_key_by_alias(key_alias, tenant_id=context.tenant_id)
        if not key.metadata.can_encrypt():
            raise KeyManagementError(f"Key cannot encrypt in current state: {key.metadata.key_ref}")

        nonce = secrets.token_bytes(12)
        aad = context.aad()
        aes = AESGCM(key.key_bytes)  # type: ignore[misc]
        ciphertext = aes.encrypt(nonce, bytes(plaintext), aad)
        envelope = CiphertextEnvelope(
            version=self.config.envelope_version,
            algorithm=EncryptionAlgorithm.AES_256_GCM,
            key_id=key.metadata.key_id,
            key_version=key.metadata.version,
            key_alias=key.metadata.alias,
            nonce=b64encode(nonce),
            ciphertext=b64encode(ciphertext),
            aad_hash=sha256_hex(aad),
            encrypted_at=dt.datetime.now(dt.timezone.utc),
            mode=mode,
            metadata={"context_hash": stable_hash(context.to_dict())},
        )
        self._audit("bytes_encrypted", {"key": key.metadata.to_dict(), "context": context.to_dict(), "envelope": envelope.to_dict()})
        return envelope

    def decrypt_bytes(self, envelope: Union[CiphertextEnvelope, str, Mapping[str, Any]], *, context: Optional[EncryptionContext] = None) -> bytes:
        self._ensure_crypto_available()
        envelope_obj = coerce_envelope(envelope)
        context = context or EncryptionContext()
        aad = context.aad()
        if envelope_obj.aad_hash != sha256_hex(aad):
            raise DecryptionError("AAD/context hash mismatch. Decryption context is not valid for this ciphertext.")

        key = self.key_provider.get_key(envelope_obj.key_id, envelope_obj.key_version)
        if not key.metadata.can_decrypt():
            raise KeyManagementError(f"Key cannot decrypt in current state: {key.metadata.key_ref}")
        aes = AESGCM(key.key_bytes)  # type: ignore[misc]
        try:
            plaintext = aes.decrypt(b64decode(envelope_obj.nonce), b64decode(envelope_obj.ciphertext), aad)
        except Exception as exc:
            raise DecryptionError(f"Decryption failed: {exc}") from exc
        self._audit("bytes_decrypted", {"key_ref": key.metadata.key_ref, "context": context.to_dict(), "envelope_hash": stable_hash(envelope_obj.to_dict())})
        return plaintext

    def encrypt_json(self, value: Any, *, context: Optional[EncryptionContext] = None, key_alias: Optional[str] = None) -> str:
        raw = json.dumps(to_json_safe(value), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return self.encrypt_bytes(raw, context=context, key_alias=key_alias, mode=EncryptionMode.JSON).serialize()

    def decrypt_json(self, ciphertext: Union[str, CiphertextEnvelope, Mapping[str, Any]], *, context: Optional[EncryptionContext] = None) -> Any:
        raw = self.decrypt_bytes(ciphertext, context=context)
        return json.loads(raw.decode("utf-8"))

    def encrypt_field(self, value: Any, context: EncryptionContext) -> Tuple[Any, EncryptionDecision]:
        policy = self.policies.resolve(context)
        if policy is None:
            return value, EncryptionDecision(
                field_name=context.field_name,
                policy_id=None,
                key_alias=None,
                key_version=None,
                encrypted=False,
                reason="no_policy_matched",
                plaintext_hash=stable_hash(value),
                ciphertext_hash=stable_hash(value),
            )
        if value is None and not policy.encrypt_nulls:
            return value, EncryptionDecision(
                field_name=context.field_name,
                policy_id=policy.policy_id,
                key_alias=policy.key_alias,
                key_version=None,
                encrypted=False,
                reason="null_not_encrypted",
                plaintext_hash=stable_hash(value),
                ciphertext_hash=stable_hash(value),
            )
        try:
            ciphertext = self.encrypt_json(value, context=context, key_alias=policy.key_alias)
            envelope = CiphertextEnvelope.deserialize(ciphertext)
            return ciphertext, EncryptionDecision(
                field_name=context.field_name,
                policy_id=policy.policy_id,
                key_alias=policy.key_alias,
                key_version=envelope.key_version,
                encrypted=True,
                reason="policy_applied",
                plaintext_hash=stable_hash(value),
                ciphertext_hash=stable_hash(ciphertext),
            )
        except Exception as exc:
            if self.config.fail_closed:
                raise
            return value, EncryptionDecision(
                field_name=context.field_name,
                policy_id=policy.policy_id,
                key_alias=policy.key_alias,
                key_version=None,
                encrypted=False,
                reason="encryption_failed_open",
                plaintext_hash=stable_hash(value),
                ciphertext_hash=stable_hash(value),
                error=str(exc),
            )

    def decrypt_field(self, value: Any, context: EncryptionContext) -> Any:
        if not isinstance(value, str) or not value.startswith("enc:"):
            return value
        return self.decrypt_json(value, context=context)

    def encrypt_record(
        self,
        record: Mapping[str, Any],
        *,
        dataset_name: Optional[str] = None,
        record_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        classifications: Optional[Mapping[str, str]] = None,
        sensitivities: Optional[Mapping[str, SensitivityLevel]] = None,
        tags_by_field: Optional[Mapping[str, Set[str]]] = None,
    ) -> EncryptionOperationResult:
        output: JsonDict = {}
        decisions: List[EncryptionDecision] = []
        result = EncryptionOperationResult(output=output, decisions=decisions)
        for field_name, value in record.items():
            context = EncryptionContext(
                dataset_name=dataset_name,
                field_name=str(field_name),
                record_id=record_id or infer_record_id(record),
                tenant_id=tenant_id,
                actor_id=actor_id,
                classification=(classifications or {}).get(str(field_name)),
                sensitivity=(sensitivities or {}).get(str(field_name)),
                tags=(tags_by_field or {}).get(str(field_name), set()),
            )
            if isinstance(value, Mapping):
                nested = self.encrypt_record(
                    value,
                    dataset_name=dataset_name,
                    record_id=context.record_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    classifications=classifications,
                    sensitivities=sensitivities,
                    tags_by_field=tags_by_field,
                )
                output[field_name] = nested.output
                decisions.extend(nested.decisions)
            else:
                output[field_name], decision = self.encrypt_field(value, context)
                decisions.append(decision)
        result.finish()
        self._audit("record_encrypted", result.to_dict())
        return result

    def decrypt_record(
        self,
        record: Mapping[str, Any],
        *,
        dataset_name: Optional[str] = None,
        record_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        classifications: Optional[Mapping[str, str]] = None,
        sensitivities: Optional[Mapping[str, SensitivityLevel]] = None,
        tags_by_field: Optional[Mapping[str, Set[str]]] = None,
    ) -> JsonDict:
        output: JsonDict = {}
        for field_name, value in record.items():
            context = EncryptionContext(
                dataset_name=dataset_name,
                field_name=str(field_name),
                record_id=record_id or infer_record_id(record),
                tenant_id=tenant_id,
                actor_id=actor_id,
                classification=(classifications or {}).get(str(field_name)),
                sensitivity=(sensitivities or {}).get(str(field_name)),
                tags=(tags_by_field or {}).get(str(field_name), set()),
            )
            if isinstance(value, Mapping):
                output[field_name] = self.decrypt_record(
                    value,
                    dataset_name=dataset_name,
                    record_id=context.record_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    classifications=classifications,
                    sensitivities=sensitivities,
                    tags_by_field=tags_by_field,
                )
            else:
                output[field_name] = self.decrypt_field(value, context)
        self._audit("record_decrypted", {"record_id": record_id, "dataset_name": dataset_name, "tenant_id": tenant_id})
        return output

    def rotate_key(self, alias: str, *, tenant_id: Optional[str] = None) -> KeyMetadata:
        metadata = self.key_provider.rotate_key(alias, tenant_id=tenant_id)
        self._audit("key_rotated", metadata.to_dict())
        return metadata

    def hmac_sign(self, value: Any, *, key_alias: Optional[str] = None, context: Optional[EncryptionContext] = None) -> str:
        context = context or EncryptionContext(purpose="hmac")
        key = self.key_provider.get_active_key_by_alias(key_alias or self.config.default_key_alias, tenant_id=context.tenant_id)
        raw = json.dumps(to_json_safe(value), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        signature = hmac.new(key.key_bytes, raw, hashlib.sha256).hexdigest()
        self._audit("hmac_signed", {"key_ref": key.metadata.key_ref, "context": context.to_dict(), "value_hash": sha256_hex(raw)})
        return signature

    def hmac_verify(self, value: Any, signature: str, *, key_alias: Optional[str] = None, context: Optional[EncryptionContext] = None) -> bool:
        expected = self.hmac_sign(value, key_alias=key_alias, context=context)
        return hmac.compare_digest(expected, signature)

    def sha256_hash(self, value: Any, *, salt: str = "") -> str:
        raw = json.dumps(to_json_safe(value), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256((salt + raw).encode("utf-8")).hexdigest()

    def describe_keys(self, alias: Optional[str] = None, tenant_id: Optional[str] = None) -> List[JsonDict]:
        return [meta.to_dict() for meta in self.key_provider.list_keys(alias=alias, tenant_id=tenant_id)]

    def describe_policies(self) -> JsonDict:
        return self.policies.to_dict()

    def _ensure_crypto_available(self) -> None:
        if AESGCM is None and self.config.require_crypto_backend:
            raise CryptoDependencyError("cryptography package with AESGCM support is required")

    def _audit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.config.enable_audit:
            self.audit.emit(event_type, to_json_safe(payload))


# -----------------------------------------------------------------------------
# Default policies
# -----------------------------------------------------------------------------


def default_encryption_policies() -> List[EncryptionPolicy]:
    return [
        EncryptionPolicy(
            policy_id="encrypt_credentials",
            name="Encrypt credentials and secrets",
            key_alias="data-secrets",
            match_mode=MatchMode.REGEX,
            field_patterns=(r"password", r"passwd", r"secret", r"token", r"api[_-]?key", r"private[_-]?key", r"authorization"),
            priority=1,
            metadata={"category": "credential"},
        ),
        EncryptionPolicy(
            policy_id="encrypt_restricted_classified",
            name="Encrypt restricted classified fields",
            key_alias="data-restricted",
            match_mode=MatchMode.CLASSIFICATION,
            classifications=("restricted", "highly_restricted", "pci", "phi", "credential", "financial_sensitive"),
            sensitivity_levels=(SensitivityLevel.RESTRICTED, SensitivityLevel.HIGHLY_RESTRICTED),
            priority=10,
            metadata={"classification_based": True},
        ),
        EncryptionPolicy(
            policy_id="encrypt_pii_fields",
            name="Encrypt common PII fields",
            key_alias="data-pii",
            match_mode=MatchMode.REGEX,
            field_patterns=(r"email", r"phone", r"cpf", r"document", r"address", r"birth", r"nome", r"name"),
            priority=50,
            metadata={"category": "pii"},
        ),
    ]


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        return b64encode(value)
    return value


def parse_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def coerce_envelope(value: Union[CiphertextEnvelope, str, Mapping[str, Any]]) -> CiphertextEnvelope:
    if isinstance(value, CiphertextEnvelope):
        return value
    if isinstance(value, str):
        return CiphertextEnvelope.deserialize(value)
    if isinstance(value, Mapping):
        return CiphertextEnvelope.from_dict(value)
    raise DecryptionError(f"Unsupported envelope type: {type(value).__name__}")


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")


def infer_record_id(record: Mapping[str, Any]) -> Optional[str]:
    for key in ("id", "record_id", "uuid", "key"):
        if key in record and record[key] is not None:
            return str(record[key])
    return None


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_default_encryption_manager() -> EncryptionManager:
    provider = InMemoryKeyProvider()
    for alias in ("data-default", "data-secrets", "data-restricted", "data-pii"):
        provider.create_key(alias, KeyPurpose.DATA_ENCRYPTION)
    return EncryptionManager(key_provider=provider)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    manager = build_default_encryption_manager()
    record = {
        "id": "1",
        "customer_email": "ana@example.com",
        "password": "super-secret",
        "sales": 123.45,
    }
    encrypted = manager.encrypt_record(record, dataset_name="customers", actor_id="governance-api")
    print(json.dumps(encrypted.output, indent=2, ensure_ascii=False, default=str))
    decrypted = manager.decrypt_record(encrypted.output, dataset_name="customers", actor_id="governance-api")
    print(json.dumps(decrypted, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(manager.describe_keys(), indent=2, ensure_ascii=False, default=str))
