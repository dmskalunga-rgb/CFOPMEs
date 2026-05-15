"""
data/security/key_management.py

Enterprise-grade key management module for Python services, data platforms,
pipelines, APIs, workers and internal security tooling.

Core capabilities:
- KMS-like key lifecycle management
- Symmetric key creation, storage abstraction and lookup
- Key versioning and rotation
- Key status lifecycle: active, disabled, scheduled deletion, destroyed
- Purpose and usage policy enforcement
- Tenant-aware key isolation
- Envelope encryption helpers for data-key wrapping/unwrapping
- Audit event emission
- In-memory repository for local development/tests
- Cache wrapper for key metadata/material lookups
- Import/export controls
- Safe redaction for logs and diagnostics

Production recommendations:
- Back this module with a real KMS/HSM/Secrets Manager when possible.
- Never log raw key material.
- Restrict key export in production.
- Use least-privilege policies for encrypt/decrypt/sign/verify/wrap/unwrap.
- Rotate keys regularly and retain old versions for decryption only.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore


# =============================================================================
# Exceptions
# =============================================================================


class KeyManagementError(Exception):
    """Base key management error."""


class KeyNotFoundError(KeyManagementError):
    """Raised when a key or key version cannot be found."""


class KeyAccessDeniedError(KeyManagementError):
    """Raised when a caller is not allowed to use a key."""


class KeyPolicyViolationError(KeyManagementError):
    """Raised when a key policy blocks an operation."""


class KeyStateError(KeyManagementError):
    """Raised when a key is in a state incompatible with the requested operation."""


class KeyValidationError(KeyManagementError):
    """Raised when key input is invalid."""


class KeyRepositoryError(KeyManagementError):
    """Raised when repository operations fail."""


class UnsupportedKeyAlgorithmError(KeyManagementError):
    """Raised when an unsupported key algorithm is requested."""


class KeyWrappingError(KeyManagementError):
    """Raised when key wrapping/unwrapping fails."""


# =============================================================================
# Enums and configuration
# =============================================================================


class KeyAlgorithm(str, Enum):
    AES_256_GCM = "AES-256-GCM"
    AES_128_GCM = "AES-128-GCM"
    HMAC_SHA256 = "HMAC-SHA256"
    FERNET = "FERNET"


class KeyType(str, Enum):
    DATA_KEY = "data_key"
    MASTER_KEY = "master_key"
    KEY_ENCRYPTION_KEY = "key_encryption_key"
    SIGNING_KEY = "signing_key"
    HMAC_KEY = "hmac_key"


class KeyStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    PENDING_ROTATION = "pending_rotation"
    SCHEDULED_DELETION = "scheduled_deletion"
    DESTROYED = "destroyed"


class KeyUsage(str, Enum):
    ENCRYPT = "encrypt"
    DECRYPT = "decrypt"
    WRAP_KEY = "wrap_key"
    UNWRAP_KEY = "unwrap_key"
    SIGN = "sign"
    VERIFY = "verify"
    DERIVE = "derive"
    EXPORT = "export"
    IMPORT = "import"
    ROTATE = "rotate"
    ADMIN = "admin"


class KeyOrigin(str, Enum):
    GENERATED = "generated"
    IMPORTED = "imported"
    EXTERNAL = "external"


class KeyEventType(str, Enum):
    KEY_CREATED = "security.key.created"
    KEY_VERSION_CREATED = "security.key.version_created"
    KEY_ROTATED = "security.key.rotated"
    KEY_DISABLED = "security.key.disabled"
    KEY_ENABLED = "security.key.enabled"
    KEY_DELETION_SCHEDULED = "security.key.deletion_scheduled"
    KEY_DESTROYED = "security.key.destroyed"
    KEY_RESOLVED = "security.key.resolved"
    KEY_EXPORTED = "security.key.exported"
    KEY_IMPORTED = "security.key.imported"
    KEY_WRAPPED = "security.key.wrapped"
    KEY_UNWRAPPED = "security.key.unwrapped"
    KEY_ACCESS_DENIED = "security.key.access_denied"


@dataclass(frozen=True)
class KeyManagementConfig:
    """Runtime configuration for key management."""

    fail_closed: bool = True
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    allow_key_export: bool = False
    allow_key_import: bool = True
    default_rotation_days: int = 90
    minimum_deletion_wait_days: int = 7
    key_cache_ttl_seconds: int = 300
    max_key_cache_entries: int = 10_000
    default_nonce_bytes: int = 12
    require_tenant_match: bool = True
    require_purpose_match: bool = True


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class KeyPolicy:
    """Usage policy attached to a managed key."""

    allowed_usages: Tuple[KeyUsage, ...]
    allowed_principals: Tuple[str, ...] = ("*",)
    allowed_tenants: Tuple[str, ...] = ("*",)
    allowed_purposes: Tuple[str, ...] = ("*",)
    allow_export: bool = False
    allow_rotation: bool = True
    require_mfa_for_admin: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def allows(
        self,
        usage: KeyUsage,
        principal_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
        config: Optional[KeyManagementConfig] = None,
    ) -> bool:
        if usage not in self.allowed_usages and KeyUsage.ADMIN not in self.allowed_usages:
            return False
        if not _matches_any(principal_id or "", self.allowed_principals):
            return False
        if tenant_id and not _matches_any(tenant_id, self.allowed_tenants):
            return False
        if config and config.require_purpose_match and purpose and not _matches_any(purpose, self.allowed_purposes):
            return False
        return True


@dataclass(frozen=True)
class KeyMetadata:
    """Non-secret metadata for a managed key."""

    key_id: str
    name: str
    key_type: KeyType
    algorithm: KeyAlgorithm
    status: KeyStatus
    current_version: str
    origin: KeyOrigin = KeyOrigin.GENERATED
    tenant_id: Optional[str] = None
    description: Optional[str] = None
    tags: Tuple[str, ...] = ()
    policy: KeyPolicy = field(default_factory=lambda: KeyPolicy(allowed_usages=(KeyUsage.ENCRYPT, KeyUsage.DECRYPT)))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    next_rotation_at: Optional[datetime] = None
    scheduled_deletion_at: Optional[datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def is_usable(self) -> bool:
        return self.status in {KeyStatus.ACTIVE, KeyStatus.PENDING_ROTATION}


@dataclass(frozen=True)
class KeyVersion:
    """Secret material for a specific key version."""

    key_id: str
    version: str
    material: bytes
    algorithm: KeyAlgorithm
    status: KeyStatus = KeyStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deactivated_at: Optional[datetime] = None
    destroyed_at: Optional[datetime] = None
    checksum_sha256: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.checksum_sha256:
            object.__setattr__(self, "checksum_sha256", hashlib.sha256(self.material).hexdigest())

    def validate_material(self) -> None:
        if self.status == KeyStatus.DESTROYED:
            raise KeyStateError(f"Key version destroyed: {self.key_id}:{self.version}")
        if self.status == KeyStatus.DISABLED:
            raise KeyStateError(f"Key version disabled: {self.key_id}:{self.version}")
        if self.algorithm == KeyAlgorithm.AES_256_GCM and len(self.material) != 32:
            raise KeyValidationError("AES-256-GCM key material must be 32 bytes.")
        if self.algorithm == KeyAlgorithm.AES_128_GCM and len(self.material) != 16:
            raise KeyValidationError("AES-128-GCM key material must be 16 bytes.")
        if self.algorithm == KeyAlgorithm.HMAC_SHA256 and len(self.material) < 32:
            raise KeyValidationError("HMAC-SHA256 key material should be at least 32 bytes.")
        if self.algorithm == KeyAlgorithm.FERNET and not self.material:
            raise KeyValidationError("Fernet key material cannot be empty.")
        if hashlib.sha256(self.material).hexdigest() != self.checksum_sha256:
            raise KeyValidationError("Key material checksum mismatch.")


@dataclass(frozen=True)
class ResolvedKey:
    """Key metadata and material returned for cryptographic use."""

    metadata: KeyMetadata
    version: KeyVersion

    @property
    def key_id(self) -> str:
        return self.metadata.key_id

    @property
    def key_version(self) -> str:
        return self.version.version

    @property
    def material(self) -> bytes:
        return self.version.material

    @property
    def algorithm(self) -> KeyAlgorithm:
        return self.version.algorithm

    def to_safe_dict(self) -> JsonDict:
        return {
            "key_id": self.key_id,
            "name": self.metadata.name,
            "key_type": self.metadata.key_type.value,
            "algorithm": self.algorithm.value,
            "status": self.metadata.status.value,
            "version": self.key_version,
            "tenant_id": self.metadata.tenant_id,
            "tags": list(self.metadata.tags),
            "created_at": self.metadata.created_at.isoformat(),
            "updated_at": self.metadata.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class KeyAccessContext:
    """Caller/request context for key operations."""

    principal_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    mfa_verified: bool = False
    environment: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class CreateKeyRequest:
    """Request to create a managed key."""

    name: str
    key_type: KeyType
    algorithm: KeyAlgorithm
    tenant_id: Optional[str] = None
    description: Optional[str] = None
    tags: Tuple[str, ...] = ()
    policy: Optional[KeyPolicy] = None
    origin: KeyOrigin = KeyOrigin.GENERATED
    imported_material: Optional[bytes] = None
    rotation_days: Optional[int] = None
    metadata: JsonDict = field(default_factory=dict)
    context: KeyAccessContext = field(default_factory=KeyAccessContext)


@dataclass(frozen=True)
class WrappedDataKey:
    """Envelope-wrapped data key result."""

    key_id: str
    key_version: str
    wrapping_algorithm: KeyAlgorithm
    encrypted_key: bytes
    nonce: bytes
    tag: Optional[bytes] = None
    associated_data: Optional[bytes] = None
    wrapped_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, encoding: str = "base64url") -> JsonDict:
        return {
            "key_id": self.key_id,
            "key_version": self.key_version,
            "wrapping_algorithm": self.wrapping_algorithm.value,
            "encrypted_key": encode_bytes(self.encrypted_key, encoding),
            "nonce": encode_bytes(self.nonce, encoding),
            "tag": encode_optional_bytes(self.tag, encoding),
            "associated_data": encode_optional_bytes(self.associated_data, encoding),
            "wrapped_at": self.wrapped_at.isoformat(),
            "metadata": redact_sensitive(self.metadata),
            "encoding": encoding,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WrappedDataKey":
        encoding = str(payload.get("encoding", "base64url"))
        return cls(
            key_id=str(payload["key_id"]),
            key_version=str(payload["key_version"]),
            wrapping_algorithm=KeyAlgorithm(str(payload["wrapping_algorithm"])),
            encrypted_key=decode_bytes(payload["encrypted_key"], encoding),
            nonce=decode_bytes(payload["nonce"], encoding),
            tag=decode_optional_bytes(payload.get("tag"), encoding),
            associated_data=decode_optional_bytes(payload.get("associated_data"), encoding),
            wrapped_at=parse_datetime(payload.get("wrapped_at")) if payload.get("wrapped_at") else datetime.now(timezone.utc),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class KeyAuditEvent:
    """Structured audit event for key management operations."""

    event_type: KeyEventType
    success: bool
    reason: str
    key_id: Optional[str] = None
    key_version: Optional[str] = None
    key_type: Optional[KeyType] = None
    algorithm: Optional[KeyAlgorithm] = None
    principal_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        return {
            "event_type": self.event_type.value,
            "success": self.success,
            "reason": self.reason,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "key_type": self.key_type.value if self.key_type else None,
            "algorithm": self.algorithm.value if self.algorithm else None,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "purpose": self.purpose,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Repository and audit abstractions
# =============================================================================


class KeyRepository(ABC):
    """Key metadata/material repository abstraction."""

    @abstractmethod
    def create_key(self, metadata: KeyMetadata, version: KeyVersion) -> None:
        """Create a key and its first version."""

    @abstractmethod
    def get_metadata(self, key_id: str) -> Optional[KeyMetadata]:
        """Return key metadata."""

    @abstractmethod
    def get_version(self, key_id: str, version: str) -> Optional[KeyVersion]:
        """Return a key version."""

    @abstractmethod
    def list_versions(self, key_id: str) -> Sequence[KeyVersion]:
        """List key versions."""

    @abstractmethod
    def update_metadata(self, metadata: KeyMetadata) -> None:
        """Update key metadata."""

    @abstractmethod
    def upsert_version(self, version: KeyVersion) -> None:
        """Create or update a key version."""

    @abstractmethod
    def list_keys(self, tenant_id: Optional[str] = None) -> Sequence[KeyMetadata]:
        """List keys, optionally filtered by tenant."""


class InMemoryKeyRepository(KeyRepository):
    """Thread-safe in-memory key repository."""

    def __init__(self) -> None:
        self._metadata: Dict[str, KeyMetadata] = {}
        self._versions: Dict[Tuple[str, str], KeyVersion] = {}
        self._lock = threading.RLock()

    def create_key(self, metadata: KeyMetadata, version: KeyVersion) -> None:
        with self._lock:
            if metadata.key_id in self._metadata:
                raise KeyValidationError(f"Key already exists: {metadata.key_id}")
            self._metadata[metadata.key_id] = metadata
            self._versions[(version.key_id, version.version)] = version

    def get_metadata(self, key_id: str) -> Optional[KeyMetadata]:
        with self._lock:
            return self._metadata.get(key_id)

    def get_version(self, key_id: str, version: str) -> Optional[KeyVersion]:
        with self._lock:
            return self._versions.get((key_id, version))

    def list_versions(self, key_id: str) -> Sequence[KeyVersion]:
        with self._lock:
            return tuple(sorted((v for (kid, _), v in self._versions.items() if kid == key_id), key=lambda item: item.created_at))

    def update_metadata(self, metadata: KeyMetadata) -> None:
        with self._lock:
            if metadata.key_id not in self._metadata:
                raise KeyNotFoundError(f"Key not found: {metadata.key_id}")
            self._metadata[metadata.key_id] = dataclasses.replace(metadata, updated_at=datetime.now(timezone.utc))

    def upsert_version(self, version: KeyVersion) -> None:
        with self._lock:
            self._versions[(version.key_id, version.version)] = version

    def list_keys(self, tenant_id: Optional[str] = None) -> Sequence[KeyMetadata]:
        with self._lock:
            keys = tuple(self._metadata.values())
            if tenant_id is not None:
                keys = tuple(key for key in keys if key.tenant_id == tenant_id)
            return tuple(sorted(keys, key=lambda key: key.created_at))


class KeyAuditSink(ABC):
    """Audit sink abstraction for key events."""

    @abstractmethod
    def emit(self, event: KeyAuditEvent) -> None:
        """Emit a key audit event."""


class LoggingKeyAuditSink(KeyAuditSink):
    """Logging-backed key audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.key_management.audit")
        self.redact = redact

    def emit(self, event: KeyAuditEvent) -> None:
        self.audit_logger.info("key_management_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


# =============================================================================
# Cache
# =============================================================================


@dataclass
class _ResolvedKeyCacheEntry:
    key: ResolvedKey
    expires_at: float


class ResolvedKeyCache:
    """Small TTL cache for resolved keys."""

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _ResolvedKeyCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, cache_key: str) -> Optional[ResolvedKey]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(cache_key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(cache_key, None)
                return None
            return entry.key

    def set(self, cache_key: str, key: ResolvedKey) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[cache_key] = _ResolvedKeyCacheEntry(key=key, expires_at=time.time() + self.ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest, None)


# =============================================================================
# Key management service
# =============================================================================


class KeyManagementService:
    """Enterprise KMS-like key management service."""

    def __init__(
        self,
        repository: Optional[KeyRepository] = None,
        config: Optional[KeyManagementConfig] = None,
        audit_sink: Optional[KeyAuditSink] = None,
        cache: Optional[ResolvedKeyCache] = None,
    ) -> None:
        self.repository = repository or InMemoryKeyRepository()
        self.config = config or KeyManagementConfig()
        self.audit_sink = audit_sink or LoggingKeyAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or ResolvedKeyCache(self.config.key_cache_ttl_seconds, self.config.max_key_cache_entries)

    def create_key(self, request: CreateKeyRequest) -> KeyMetadata:
        """Create a managed key with initial version."""
        try:
            self._validate_create_request(request)
            key_id = str(uuid.uuid4())
            version = "1"
            material = request.imported_material if request.imported_material is not None else generate_key_material(request.algorithm)
            key_version = KeyVersion(
                key_id=key_id,
                version=version,
                material=material,
                algorithm=request.algorithm,
                metadata={"origin": request.origin.value},
            )
            key_version.validate_material()

            rotation_days = request.rotation_days if request.rotation_days is not None else self.config.default_rotation_days
            policy = request.policy or default_policy_for_key_type(request.key_type)
            metadata = KeyMetadata(
                key_id=key_id,
                name=request.name,
                key_type=request.key_type,
                algorithm=request.algorithm,
                status=KeyStatus.ACTIVE,
                current_version=version,
                origin=request.origin,
                tenant_id=request.tenant_id,
                description=request.description,
                tags=tuple(request.tags),
                policy=policy,
                next_rotation_at=datetime.now(timezone.utc) + timedelta(days=rotation_days) if policy.allow_rotation else None,
                metadata=dict(request.metadata),
            )

            self.repository.create_key(metadata, key_version)
            self._audit(
                KeyEventType.KEY_CREATED,
                True,
                "Key created.",
                metadata=metadata,
                context=request.context,
                extra={"origin": request.origin.value},
            )
            return metadata
        except Exception as exc:
            self._audit_raw(KeyAuditEvent(
                event_type=KeyEventType.KEY_ACCESS_DENIED,
                success=False,
                reason=str(exc),
                principal_id=request.context.principal_id,
                tenant_id=request.context.tenant_id or request.tenant_id,
                purpose=request.context.purpose,
                request_id=request.context.request_id,
                correlation_id=request.context.correlation_id,
                metadata={"error_type": type(exc).__name__, "operation": "create_key"},
            ))
            if self.config.fail_closed:
                if isinstance(exc, KeyManagementError):
                    raise
                raise KeyManagementError("Key creation failed.") from exc
            raise

    def resolve_key(
        self,
        key_id: str,
        usage: KeyUsage,
        version: str = "current",
        context: Optional[KeyAccessContext] = None,
    ) -> ResolvedKey:
        """Resolve a key version for a specific usage."""
        context = context or KeyAccessContext()
        cache_key = self._cache_key(key_id, version, usage, context)
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            self._audit_raw(KeyAuditEvent(
                event_type=KeyEventType.KEY_ACCESS_DENIED,
                success=False,
                reason="Key not found.",
                key_id=key_id,
                principal_id=context.principal_id,
                tenant_id=context.tenant_id,
                purpose=context.purpose,
                request_id=context.request_id,
                correlation_id=context.correlation_id,
            ))
            raise KeyNotFoundError(f"Key not found: {key_id}")

        self._assert_metadata_usable(metadata, usage, context)
        resolved_version = metadata.current_version if version in {"current", "latest"} else version
        key_version = self.repository.get_version(key_id, resolved_version)
        if not key_version:
            raise KeyNotFoundError(f"Key version not found: {key_id}:{resolved_version}")
        key_version.validate_material()

        resolved = ResolvedKey(metadata=metadata, version=key_version)
        self.cache.set(cache_key, resolved)
        self._audit(KeyEventType.KEY_RESOLVED, True, "Key resolved.", metadata=metadata, version=key_version, context=context, extra={"usage": usage.value})
        return resolved

    def rotate_key(self, key_id: str, context: Optional[KeyAccessContext] = None) -> KeyMetadata:
        """Rotate key by creating a new active current version."""
        context = context or KeyAccessContext()
        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        self._assert_metadata_usable(metadata, KeyUsage.ROTATE, context)
        if not metadata.policy.allow_rotation:
            raise KeyPolicyViolationError("Key policy does not allow rotation.")

        versions = self.repository.list_versions(key_id)
        next_version = str(max([int(v.version) for v in versions if v.version.isdigit()] or [0]) + 1)
        new_material = generate_key_material(metadata.algorithm)
        new_version = KeyVersion(key_id=key_id, version=next_version, material=new_material, algorithm=metadata.algorithm)
        new_version.validate_material()
        self.repository.upsert_version(new_version)

        updated = dataclasses.replace(
            metadata,
            status=KeyStatus.ACTIVE,
            current_version=next_version,
            updated_at=datetime.now(timezone.utc),
            next_rotation_at=datetime.now(timezone.utc) + timedelta(days=self.config.default_rotation_days),
        )
        self.repository.update_metadata(updated)
        self.cache.clear()
        self._audit(KeyEventType.KEY_ROTATED, True, "Key rotated.", metadata=updated, version=new_version, context=context)
        return updated

    def disable_key(self, key_id: str, context: Optional[KeyAccessContext] = None) -> KeyMetadata:
        context = context or KeyAccessContext()
        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        self._assert_policy(metadata, KeyUsage.ADMIN, context)
        updated = dataclasses.replace(metadata, status=KeyStatus.DISABLED, updated_at=datetime.now(timezone.utc))
        self.repository.update_metadata(updated)
        self.cache.clear()
        self._audit(KeyEventType.KEY_DISABLED, True, "Key disabled.", metadata=updated, context=context)
        return updated

    def enable_key(self, key_id: str, context: Optional[KeyAccessContext] = None) -> KeyMetadata:
        context = context or KeyAccessContext()
        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        self._assert_policy(metadata, KeyUsage.ADMIN, context)
        if metadata.status == KeyStatus.DESTROYED:
            raise KeyStateError("Destroyed keys cannot be enabled.")
        updated = dataclasses.replace(metadata, status=KeyStatus.ACTIVE, updated_at=datetime.now(timezone.utc))
        self.repository.update_metadata(updated)
        self.cache.clear()
        self._audit(KeyEventType.KEY_ENABLED, True, "Key enabled.", metadata=updated, context=context)
        return updated

    def schedule_key_deletion(self, key_id: str, wait_days: Optional[int] = None, context: Optional[KeyAccessContext] = None) -> KeyMetadata:
        context = context or KeyAccessContext()
        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        self._assert_policy(metadata, KeyUsage.ADMIN, context)
        wait = max(wait_days or self.config.minimum_deletion_wait_days, self.config.minimum_deletion_wait_days)
        updated = dataclasses.replace(
            metadata,
            status=KeyStatus.SCHEDULED_DELETION,
            scheduled_deletion_at=datetime.now(timezone.utc) + timedelta(days=wait),
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.update_metadata(updated)
        self.cache.clear()
        self._audit(KeyEventType.KEY_DELETION_SCHEDULED, True, "Key deletion scheduled.", metadata=updated, context=context, extra={"wait_days": wait})
        return updated

    def destroy_key_version(self, key_id: str, version: str, context: Optional[KeyAccessContext] = None) -> KeyVersion:
        """Destroy a specific key version by replacing material with empty bytes marker."""
        context = context or KeyAccessContext()
        metadata = self.repository.get_metadata(key_id)
        if not metadata:
            raise KeyNotFoundError(f"Key not found: {key_id}")
        self._assert_policy(metadata, KeyUsage.ADMIN, context)
        key_version = self.repository.get_version(key_id, version)
        if not key_version:
            raise KeyNotFoundError(f"Key version not found: {key_id}:{version}")
        if version == metadata.current_version:
            raise KeyStateError("Cannot destroy current key version. Rotate or disable key first.")
        destroyed = dataclasses.replace(
            key_version,
            material=b"",
            status=KeyStatus.DESTROYED,
            destroyed_at=datetime.now(timezone.utc),
            checksum_sha256=hashlib.sha256(b"").hexdigest(),
        )
        self.repository.upsert_version(destroyed)
        self.cache.clear()
        self._audit(KeyEventType.KEY_DESTROYED, True, "Key version destroyed.", metadata=metadata, version=destroyed, context=context)
        return destroyed

    def export_key_material(self, key_id: str, version: str = "current", context: Optional[KeyAccessContext] = None) -> bytes:
        """Export raw key material only when globally and policy allowed."""
        context = context or KeyAccessContext()
        if not self.config.allow_key_export:
            raise KeyAccessDeniedError("Raw key export is disabled by configuration.")
        resolved = self.resolve_key(key_id, KeyUsage.EXPORT, version, context)
        if not resolved.metadata.policy.allow_export:
            raise KeyPolicyViolationError("Key policy does not allow export.")
        self._audit(KeyEventType.KEY_EXPORTED, True, "Key material exported.", metadata=resolved.metadata, version=resolved.version, context=context)
        return resolved.material

    def generate_data_key(self, algorithm: KeyAlgorithm = KeyAlgorithm.AES_256_GCM) -> bytes:
        """Generate an ephemeral data key."""
        return generate_key_material(algorithm)

    def wrap_data_key(
        self,
        data_key: bytes,
        wrapping_key_id: str,
        wrapping_key_version: str = "current",
        associated_data: Optional[bytes] = None,
        context: Optional[KeyAccessContext] = None,
    ) -> WrappedDataKey:
        """Encrypt/wrap a data key using a managed AES-GCM wrapping key."""
        context = context or KeyAccessContext()
        resolved = self.resolve_key(wrapping_key_id, KeyUsage.WRAP_KEY, wrapping_key_version, context)
        if resolved.algorithm not in {KeyAlgorithm.AES_256_GCM, KeyAlgorithm.AES_128_GCM}:
            raise UnsupportedKeyAlgorithmError("Key wrapping currently requires AES-GCM wrapping keys.")
        if AESGCM is None:
            raise UnsupportedKeyAlgorithmError("cryptography is required for AES-GCM key wrapping.")
        nonce = os.urandom(self.config.default_nonce_bytes)
        try:
            encrypted = AESGCM(resolved.material).encrypt(nonce, data_key, associated_data)
            wrapped = WrappedDataKey(
                key_id=resolved.key_id,
                key_version=resolved.key_version,
                wrapping_algorithm=resolved.algorithm,
                encrypted_key=encrypted,
                nonce=nonce,
                associated_data=associated_data,
                metadata={"data_key_sha256": hashlib.sha256(data_key).hexdigest()},
            )
            self._audit(KeyEventType.KEY_WRAPPED, True, "Data key wrapped.", metadata=resolved.metadata, version=resolved.version, context=context)
            return wrapped
        except Exception as exc:
            raise KeyWrappingError("Failed to wrap data key.") from exc

    def unwrap_data_key(self, wrapped: WrappedDataKey, context: Optional[KeyAccessContext] = None) -> bytes:
        """Decrypt/unwrap a data key using the managed wrapping key."""
        context = context or KeyAccessContext()
        resolved = self.resolve_key(wrapped.key_id, KeyUsage.UNWRAP_KEY, wrapped.key_version, context)
        if resolved.algorithm not in {KeyAlgorithm.AES_256_GCM, KeyAlgorithm.AES_128_GCM}:
            raise UnsupportedKeyAlgorithmError("Key unwrapping currently requires AES-GCM wrapping keys.")
        if AESGCM is None:
            raise UnsupportedKeyAlgorithmError("cryptography is required for AES-GCM key unwrapping.")
        try:
            data_key = AESGCM(resolved.material).decrypt(wrapped.nonce, wrapped.encrypted_key + (wrapped.tag or b""), wrapped.associated_data)
            self._audit(KeyEventType.KEY_UNWRAPPED, True, "Data key unwrapped.", metadata=resolved.metadata, version=resolved.version, context=context)
            return data_key
        except Exception as exc:
            raise KeyWrappingError("Failed to unwrap data key.") from exc

    def list_keys(self, tenant_id: Optional[str] = None) -> Sequence[KeyMetadata]:
        return self.repository.list_keys(tenant_id=tenant_id)

    def list_versions(self, key_id: str) -> Sequence[KeyVersion]:
        return self.repository.list_versions(key_id)

    def _validate_create_request(self, request: CreateKeyRequest) -> None:
        if not request.name.strip():
            raise KeyValidationError("Key name is required.")
        if request.imported_material is not None and not self.config.allow_key_import:
            raise KeyAccessDeniedError("Key import is disabled by configuration.")
        if request.origin == KeyOrigin.IMPORTED and request.imported_material is None:
            raise KeyValidationError("imported_material is required when origin is IMPORTED.")
        if request.context.tenant_id and request.tenant_id and self.config.require_tenant_match and request.context.tenant_id != request.tenant_id:
            raise KeyAccessDeniedError("Request tenant does not match key tenant.")
        if request.policy and request.policy.require_mfa_for_admin and not request.context.mfa_verified:
            raise KeyAccessDeniedError("MFA is required by key policy.")

    def _assert_metadata_usable(self, metadata: KeyMetadata, usage: KeyUsage, context: KeyAccessContext) -> None:
        if not metadata.is_usable():
            raise KeyStateError(f"Key is not usable in current status: {metadata.status.value}")
        if self.config.require_tenant_match and metadata.tenant_id and context.tenant_id and metadata.tenant_id != context.tenant_id:
            raise KeyAccessDeniedError("Key tenant does not match request tenant.")
        self._assert_policy(metadata, usage, context)

    def _assert_policy(self, metadata: KeyMetadata, usage: KeyUsage, context: KeyAccessContext) -> None:
        if metadata.policy.require_mfa_for_admin and usage in {KeyUsage.ADMIN, KeyUsage.ROTATE, KeyUsage.EXPORT} and not context.mfa_verified:
            raise KeyAccessDeniedError("MFA is required for this key operation.")
        if not metadata.policy.allows(usage, context.principal_id, context.tenant_id or metadata.tenant_id, context.purpose, self.config):
            self._audit(KeyEventType.KEY_ACCESS_DENIED, False, "Key policy denied operation.", metadata=metadata, context=context, extra={"usage": usage.value})
            raise KeyPolicyViolationError(f"Key policy denied usage: {usage.value}")

    def _cache_key(self, key_id: str, version: str, usage: KeyUsage, context: KeyAccessContext) -> str:
        payload = {
            "key_id": key_id,
            "version": version,
            "usage": usage.value,
            "principal_id": context.principal_id,
            "tenant_id": context.tenant_id,
            "purpose": context.purpose,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _audit(
        self,
        event_type: KeyEventType,
        success: bool,
        reason: str,
        metadata: Optional[KeyMetadata] = None,
        version: Optional[KeyVersion] = None,
        context: Optional[KeyAccessContext] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        context = context or KeyAccessContext()
        event = KeyAuditEvent(
            event_type=event_type,
            success=success,
            reason=reason,
            key_id=metadata.key_id if metadata else None,
            key_version=version.version if version else (metadata.current_version if metadata else None),
            key_type=metadata.key_type if metadata else None,
            algorithm=metadata.algorithm if metadata else (version.algorithm if version else None),
            principal_id=context.principal_id,
            tenant_id=context.tenant_id or (metadata.tenant_id if metadata else None),
            purpose=context.purpose,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            metadata={
                "key_name": metadata.name if metadata else None,
                "tags": list(metadata.tags) if metadata else [],
                "environment": dict(context.environment),
                **dict(extra or {}),
            },
        )
        self._audit_raw(event)

    def _audit_raw(self, event: KeyAuditEvent) -> None:
        if not self.config.enable_audit:
            return
        try:
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit key audit event.")


# =============================================================================
# Utility functions
# =============================================================================


def generate_key_material(algorithm: KeyAlgorithm) -> bytes:
    if algorithm == KeyAlgorithm.AES_256_GCM:
        return os.urandom(32)
    if algorithm == KeyAlgorithm.AES_128_GCM:
        return os.urandom(16)
    if algorithm == KeyAlgorithm.HMAC_SHA256:
        return os.urandom(32)
    if algorithm == KeyAlgorithm.FERNET:
        return base64.urlsafe_b64encode(os.urandom(32))
    raise UnsupportedKeyAlgorithmError(f"Unsupported key algorithm: {algorithm.value}")


def default_policy_for_key_type(key_type: KeyType) -> KeyPolicy:
    if key_type == KeyType.DATA_KEY:
        return KeyPolicy(allowed_usages=(KeyUsage.ENCRYPT, KeyUsage.DECRYPT))
    if key_type == KeyType.KEY_ENCRYPTION_KEY:
        return KeyPolicy(allowed_usages=(KeyUsage.WRAP_KEY, KeyUsage.UNWRAP_KEY, KeyUsage.ROTATE, KeyUsage.ADMIN))
    if key_type == KeyType.MASTER_KEY:
        return KeyPolicy(allowed_usages=(KeyUsage.ENCRYPT, KeyUsage.DECRYPT, KeyUsage.WRAP_KEY, KeyUsage.UNWRAP_KEY, KeyUsage.ROTATE, KeyUsage.ADMIN))
    if key_type == KeyType.HMAC_KEY:
        return KeyPolicy(allowed_usages=(KeyUsage.SIGN, KeyUsage.VERIFY, KeyUsage.ROTATE, KeyUsage.ADMIN))
    if key_type == KeyType.SIGNING_KEY:
        return KeyPolicy(allowed_usages=(KeyUsage.SIGN, KeyUsage.VERIFY, KeyUsage.ROTATE, KeyUsage.ADMIN))
    return KeyPolicy(allowed_usages=(KeyUsage.ADMIN,))


def encode_bytes(raw: bytes, encoding: str = "base64url") -> str:
    if encoding == "base64url":
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if encoding == "base64":
        return base64.b64encode(raw).decode("ascii")
    if encoding == "hex":
        return raw.hex()
    if encoding == "utf-8":
        return raw.decode("utf-8")
    raise ValueError(f"Unsupported encoding: {encoding}")


def encode_optional_bytes(raw: Optional[bytes], encoding: str = "base64url") -> Optional[str]:
    return encode_bytes(raw, encoding) if raw is not None else None


def decode_bytes(value: Union[str, bytes], encoding: str = "base64url") -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        raise KeyValidationError("Encoded bytes must be string or bytes.")
    try:
        if encoding == "base64url":
            return base64.urlsafe_b64decode((value + "=" * ((4 - len(value) % 4) % 4)).encode("ascii"))
        if encoding == "base64":
            return base64.b64decode(value.encode("ascii"))
        if encoding == "hex":
            return bytes.fromhex(value)
        if encoding == "utf-8":
            return value.encode("utf-8")
    except (binascii.Error, ValueError) as exc:
        raise KeyValidationError(f"Invalid {encoding} encoded value.") from exc
    raise KeyValidationError(f"Unsupported encoding: {encoding}")


def decode_optional_bytes(value: Any, encoding: str = "base64url") -> Optional[bytes]:
    if value is None:
        return None
    return decode_bytes(value, encoding)


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "material",
        "key_material",
        "secret",
        "private_key",
        "token",
        "password",
        "credential",
        "encrypted_key",
        "nonce",
        "tag",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_text = str(key)
                if any(term in key_text.lower() for term in sensitive_terms):
                    output[key_text] = "***REDACTED***"
                else:
                    output[key_text] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def _matches_any(value: str, patterns: Sequence[str]) -> bool:
    if "*" in patterns:
        return True
    return value in set(patterns)


def create_default_key_management_service() -> KeyManagementService:
    service = KeyManagementService()
    context = KeyAccessContext(principal_id="system", tenant_id="default", purpose="bootstrap", mfa_verified=True)
    service.create_key(
        CreateKeyRequest(
            name="default-master-key",
            key_type=KeyType.MASTER_KEY,
            algorithm=KeyAlgorithm.AES_256_GCM,
            tenant_id="default",
            tags=("default", "master"),
            policy=KeyPolicy(
                allowed_usages=(KeyUsage.ENCRYPT, KeyUsage.DECRYPT, KeyUsage.WRAP_KEY, KeyUsage.UNWRAP_KEY, KeyUsage.ROTATE, KeyUsage.ADMIN),
                allowed_principals=("system", "admin"),
                allowed_tenants=("default",),
                allowed_purposes=("*",),
                allow_export=False,
                allow_rotation=True,
                require_mfa_for_admin=False,
            ),
            context=context,
        )
    )
    return service


__all__ = [
    "CreateKeyRequest",
    "InMemoryKeyRepository",
    "KeyAccessContext",
    "KeyAccessDeniedError",
    "KeyAlgorithm",
    "KeyAuditEvent",
    "KeyAuditSink",
    "KeyEventType",
    "KeyManagementConfig",
    "KeyManagementError",
    "KeyManagementService",
    "KeyMetadata",
    "KeyNotFoundError",
    "KeyOrigin",
    "KeyPolicy",
    "KeyPolicyViolationError",
    "KeyRepository",
    "KeyRepositoryError",
    "KeyStateError",
    "KeyStatus",
    "KeyType",
    "KeyUsage",
    "KeyValidationError",
    "KeyVersion",
    "KeyWrappingError",
    "LoggingKeyAuditSink",
    "ResolvedKey",
    "ResolvedKeyCache",
    "UnsupportedKeyAlgorithmError",
    "WrappedDataKey",
    "create_default_key_management_service",
    "decode_bytes",
    "decode_optional_bytes",
    "default_policy_for_key_type",
    "encode_bytes",
    "encode_optional_bytes",
    "generate_key_material",
    "parse_datetime",
    "redact_sensitive",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    kms = create_default_key_management_service()
    context = KeyAccessContext(principal_id="system", tenant_id="default", purpose="demo", request_id="req-demo", mfa_verified=True)

    key = kms.resolve_key(
        key_id=kms.list_keys("default")[0].key_id,
        usage=KeyUsage.WRAP_KEY,
        context=context,
    )
    print(json.dumps(key.to_safe_dict(), indent=2, default=str))

    data_key = kms.generate_data_key(KeyAlgorithm.AES_256_GCM)
    wrapped = kms.wrap_data_key(data_key, key.key_id, context=context, associated_data=b"demo-aad")
    unwrapped = kms.unwrap_data_key(wrapped, context=context)
    print(json.dumps(wrapped.to_dict(), indent=2, default=str))
    print("unwrap_ok=", hmac.compare_digest(data_key, unwrapped))
