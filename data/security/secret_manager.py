"""
data/security/secret_manager.py

Enterprise-grade secret management module for Python services, data platforms,
APIs, workers, pipelines and internal tooling.

Core capabilities:
- Secret lifecycle management: create, read, update/version, rotate, disable, delete
- Secret versioning with current/previous lookup
- Tenant-aware secret isolation
- Access policies by principal, tenant, purpose and operation
- Optional encryption-at-rest through pluggable crypto provider
- TTL cache with safe invalidation
- Structured audit events
- In-memory repository for local development/tests
- Secret references for config injection
- Redaction helpers for logs and diagnostics
- Batch secret resolution
- Framework-agnostic integration

Production recommendations:
- Back this module with a real KMS/Secrets Manager/Vault when possible.
- Do not log raw secret values.
- Restrict export/read operations using least privilege.
- Rotate secrets regularly and keep previous versions only as long as needed.
- Prefer short-lived credentials and dynamic secrets where supported.
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
import re
import secrets as py_secrets
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
SecretValue = Union[str, bytes]
SecretGenerator = Callable[[int], str]

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore


# =============================================================================
# Exceptions
# =============================================================================


class SecretManagerError(Exception):
    """Base secret manager error."""


class SecretNotFoundError(SecretManagerError):
    """Raised when a secret or version cannot be found."""


class SecretAccessDeniedError(SecretManagerError):
    """Raised when access to a secret is denied."""


class SecretPolicyViolationError(SecretManagerError):
    """Raised when a secret policy blocks an operation."""


class SecretValidationError(SecretManagerError):
    """Raised when a secret definition or value is invalid."""


class SecretStateError(SecretManagerError):
    """Raised when a secret status is incompatible with an operation."""


class SecretRepositoryError(SecretManagerError):
    """Raised when repository operations fail."""


class SecretCryptoError(SecretManagerError):
    """Raised when encryption/decryption fails."""


class SecretRotationError(SecretManagerError):
    """Raised when rotation fails."""


# =============================================================================
# Enums and configuration
# =============================================================================


class SecretStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    SCHEDULED_DELETION = "scheduled_deletion"
    DELETED = "deleted"


class SecretVersionStatus(str, Enum):
    CURRENT = "current"
    PREVIOUS = "previous"
    DISABLED = "disabled"
    DESTROYED = "destroyed"


class SecretType(str, Enum):
    GENERIC = "generic"
    API_KEY = "api_key"
    PASSWORD = "password"
    TOKEN = "token"
    DATABASE_URL = "database_url"
    CONNECTION_STRING = "connection_string"
    CERTIFICATE = "certificate"
    PRIVATE_KEY = "private_key"
    WEBHOOK_SECRET = "webhook_secret"


class SecretOperation(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    ROTATE = "rotate"
    DISABLE = "disable"
    ENABLE = "enable"
    DELETE = "delete"
    LIST = "list"
    ADMIN = "admin"


class SecretOrigin(str, Enum):
    GENERATED = "generated"
    IMPORTED = "imported"
    EXTERNAL = "external"


class SecretEventType(str, Enum):
    SECRET_CREATED = "security.secret.created"
    SECRET_READ = "security.secret.read"
    SECRET_VERSION_CREATED = "security.secret.version_created"
    SECRET_ROTATED = "security.secret.rotated"
    SECRET_DISABLED = "security.secret.disabled"
    SECRET_ENABLED = "security.secret.enabled"
    SECRET_DELETION_SCHEDULED = "security.secret.deletion_scheduled"
    SECRET_DELETED = "security.secret.deleted"
    SECRET_ACCESS_DENIED = "security.secret.access_denied"
    SECRET_CACHE_HIT = "security.secret.cache_hit"
    SECRET_BATCH_RESOLVED = "security.secret.batch_resolved"


@dataclass(frozen=True)
class SecretManagerConfig:
    """Runtime configuration for secret manager behavior."""

    fail_closed: bool = True
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    encrypt_at_rest: bool = True
    allow_plaintext_storage: bool = False
    cache_enabled: bool = True
    cache_ttl_seconds: int = 120
    max_cache_entries: int = 10_000
    default_rotation_days: int = 90
    minimum_deletion_wait_days: int = 7
    max_secret_bytes: int = 1024 * 1024
    require_tenant_match: bool = True
    require_purpose_match: bool = True
    secret_reference_prefix: str = "secret://"


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class SecretAccessPolicy:
    """Access policy attached to a secret."""

    allowed_operations: Tuple[SecretOperation, ...]
    allowed_principals: Tuple[str, ...] = ("*",)
    allowed_tenants: Tuple[str, ...] = ("*",)
    allowed_purposes: Tuple[str, ...] = ("*",)
    require_mfa_for_read: bool = False
    require_mfa_for_admin: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def allows(
        self,
        operation: SecretOperation,
        principal_id: Optional[str],
        tenant_id: Optional[str],
        purpose: Optional[str],
        mfa_verified: bool,
        config: SecretManagerConfig,
    ) -> bool:
        if operation not in self.allowed_operations and SecretOperation.ADMIN not in self.allowed_operations:
            return False
        if not _matches_any(principal_id or "", self.allowed_principals):
            return False
        if tenant_id and not _matches_any(tenant_id, self.allowed_tenants):
            return False
        if config.require_purpose_match and purpose and not _matches_any(purpose, self.allowed_purposes):
            return False
        if operation == SecretOperation.READ and self.require_mfa_for_read and not mfa_verified:
            return False
        if operation in {SecretOperation.UPDATE, SecretOperation.ROTATE, SecretOperation.DELETE, SecretOperation.ADMIN} and self.require_mfa_for_admin and not mfa_verified:
            return False
        return True


@dataclass(frozen=True)
class SecretAccessContext:
    """Caller/request context for secret operations."""

    principal_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    mfa_verified: bool = False
    environment: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class SecretMetadata:
    """Non-secret metadata for a managed secret."""

    secret_id: str
    name: str
    secret_type: SecretType
    status: SecretStatus
    current_version: str
    tenant_id: Optional[str] = None
    description: Optional[str] = None
    tags: Tuple[str, ...] = ()
    policy: SecretAccessPolicy = field(default_factory=lambda: SecretAccessPolicy(allowed_operations=(SecretOperation.READ,)))
    origin: SecretOrigin = SecretOrigin.IMPORTED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    next_rotation_at: Optional[datetime] = None
    scheduled_deletion_at: Optional[datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def is_usable(self) -> bool:
        return self.status == SecretStatus.ACTIVE

    def reference(self, prefix: str = "secret://") -> str:
        return f"{prefix}{self.name}"


@dataclass(frozen=True)
class SecretVersion:
    """Secret version record. Value may be encrypted or plaintext depending on provider/config."""

    secret_id: str
    version: str
    value: bytes
    value_sha256: str
    status: SecretVersionStatus = SecretVersionStatus.CURRENT
    encrypted: bool = True
    encryption_metadata: JsonDict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    destroyed_at: Optional[datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def is_readable(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.status in {SecretVersionStatus.CURRENT, SecretVersionStatus.PREVIOUS} and self.destroyed_at is None and (self.expires_at is None or self.expires_at > now)


@dataclass(frozen=True)
class CreateSecretRequest:
    """Request to create a secret."""

    name: str
    value: Optional[SecretValue] = None
    secret_type: SecretType = SecretType.GENERIC
    tenant_id: Optional[str] = None
    description: Optional[str] = None
    tags: Tuple[str, ...] = ()
    policy: Optional[SecretAccessPolicy] = None
    origin: SecretOrigin = SecretOrigin.IMPORTED
    generate: bool = False
    generated_length: int = 48
    rotation_days: Optional[int] = None
    metadata: JsonDict = field(default_factory=dict)
    context: SecretAccessContext = field(default_factory=SecretAccessContext)


@dataclass(frozen=True)
class UpdateSecretRequest:
    """Request to create a new secret version."""

    secret_id_or_name: str
    value: SecretValue
    context: SecretAccessContext = field(default_factory=SecretAccessContext)
    metadata: JsonDict = field(default_factory=dict)
    expires_at: Optional[datetime] = None


@dataclass(frozen=True)
class ResolvedSecret:
    """Resolved secret returned to callers."""

    metadata: SecretMetadata
    version: SecretVersion
    plaintext: bytes
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hit: bool = False

    def text(self, encoding: str = "utf-8") -> str:
        return self.plaintext.decode(encoding)

    def to_safe_dict(self) -> JsonDict:
        return {
            "secret_id": self.metadata.secret_id,
            "name": self.metadata.name,
            "secret_type": self.metadata.secret_type.value,
            "status": self.metadata.status.value,
            "version": self.version.version,
            "tenant_id": self.metadata.tenant_id,
            "tags": list(self.metadata.tags),
            "value_sha256": self.version.value_sha256,
            "resolved_at": self.resolved_at.isoformat(),
            "cache_hit": self.cache_hit,
        }


@dataclass(frozen=True)
class SecretAuditEvent:
    """Structured audit event for secret manager operations."""

    event_type: SecretEventType
    success: bool
    reason: str
    secret_id: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    secret_type: Optional[SecretType] = None
    principal_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        data = {
            "event_type": self.event_type.value,
            "success": self.success,
            "reason": self.reason,
            "secret_id": self.secret_id,
            "name": self.name,
            "version": self.version,
            "secret_type": self.secret_type.value if self.secret_type else None,
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "purpose": self.purpose,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }
        return redact_sensitive(data) if redact else data


# =============================================================================
# Crypto providers
# =============================================================================


class SecretCryptoProvider(ABC):
    """Secret encryption/decryption provider abstraction."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> Tuple[bytes, JsonDict]:
        """Encrypt plaintext and return ciphertext plus metadata."""

    @abstractmethod
    def decrypt(self, ciphertext: bytes, metadata: Mapping[str, Any], aad: Optional[bytes] = None) -> bytes:
        """Decrypt ciphertext using metadata."""


class NoopSecretCryptoProvider(SecretCryptoProvider):
    """No-op provider for local-only plaintext storage when explicitly allowed."""

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> Tuple[bytes, JsonDict]:
        return plaintext, {"algorithm": "NOOP", "encrypted": False}

    def decrypt(self, ciphertext: bytes, metadata: Mapping[str, Any], aad: Optional[bytes] = None) -> bytes:
        return ciphertext


class AESGCMSecretCryptoProvider(SecretCryptoProvider):
    """AES-GCM secret crypto provider for local development/tests."""

    def __init__(self, master_key: bytes, key_id: str = "local-secret-master-key") -> None:
        if AESGCM is None:
            raise SecretCryptoError("cryptography is required for AES-GCM secret crypto provider.")
        if len(master_key) not in {16, 32}:
            raise SecretCryptoError("AES-GCM master key must be 16 or 32 bytes.")
        self.master_key = master_key
        self.key_id = key_id

    def encrypt(self, plaintext: bytes, aad: Optional[bytes] = None) -> Tuple[bytes, JsonDict]:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self.master_key).encrypt(nonce, plaintext, aad)
        return ciphertext, {
            "algorithm": "AES-GCM",
            "key_id": self.key_id,
            "nonce": encode_bytes(nonce),
            "aad_sha256": hashlib.sha256(aad or b"").hexdigest(),
            "encrypted": True,
        }

    def decrypt(self, ciphertext: bytes, metadata: Mapping[str, Any], aad: Optional[bytes] = None) -> bytes:
        try:
            nonce = decode_bytes(str(metadata["nonce"]))
            return AESGCM(self.master_key).decrypt(nonce, ciphertext, aad)
        except Exception as exc:
            raise SecretCryptoError("Secret decryption failed.") from exc


# =============================================================================
# Repository / audit abstractions
# =============================================================================


class SecretRepository(ABC):
    """Secret repository abstraction."""

    @abstractmethod
    def create_secret(self, metadata: SecretMetadata, version: SecretVersion) -> None:
        """Create secret metadata and first version."""

    @abstractmethod
    def get_metadata(self, secret_id_or_name: str) -> Optional[SecretMetadata]:
        """Return metadata by ID or name."""

    @abstractmethod
    def get_version(self, secret_id: str, version: str) -> Optional[SecretVersion]:
        """Return a specific secret version."""

    @abstractmethod
    def list_versions(self, secret_id: str) -> Sequence[SecretVersion]:
        """List versions for a secret."""

    @abstractmethod
    def update_metadata(self, metadata: SecretMetadata) -> None:
        """Update secret metadata."""

    @abstractmethod
    def upsert_version(self, version: SecretVersion) -> None:
        """Create or update secret version."""

    @abstractmethod
    def list_secrets(self, tenant_id: Optional[str] = None) -> Sequence[SecretMetadata]:
        """List secrets, optionally by tenant."""


class InMemorySecretRepository(SecretRepository):
    """Thread-safe in-memory secret repository."""

    def __init__(self) -> None:
        self._metadata_by_id: Dict[str, SecretMetadata] = {}
        self._id_by_name: Dict[str, str] = {}
        self._versions: Dict[Tuple[str, str], SecretVersion] = {}
        self._lock = threading.RLock()

    def create_secret(self, metadata: SecretMetadata, version: SecretVersion) -> None:
        with self._lock:
            if metadata.secret_id in self._metadata_by_id:
                raise SecretValidationError(f"Secret already exists: {metadata.secret_id}")
            if metadata.name in self._id_by_name:
                raise SecretValidationError(f"Secret name already exists: {metadata.name}")
            self._metadata_by_id[metadata.secret_id] = metadata
            self._id_by_name[metadata.name] = metadata.secret_id
            self._versions[(version.secret_id, version.version)] = version

    def get_metadata(self, secret_id_or_name: str) -> Optional[SecretMetadata]:
        with self._lock:
            secret_id = self._id_by_name.get(secret_id_or_name, secret_id_or_name)
            return self._metadata_by_id.get(secret_id)

    def get_version(self, secret_id: str, version: str) -> Optional[SecretVersion]:
        with self._lock:
            return self._versions.get((secret_id, version))

    def list_versions(self, secret_id: str) -> Sequence[SecretVersion]:
        with self._lock:
            return tuple(sorted((v for (sid, _), v in self._versions.items() if sid == secret_id), key=lambda item: item.created_at))

    def update_metadata(self, metadata: SecretMetadata) -> None:
        with self._lock:
            if metadata.secret_id not in self._metadata_by_id:
                raise SecretNotFoundError(f"Secret not found: {metadata.secret_id}")
            old = self._metadata_by_id[metadata.secret_id]
            if old.name != metadata.name:
                self._id_by_name.pop(old.name, None)
                self._id_by_name[metadata.name] = metadata.secret_id
            self._metadata_by_id[metadata.secret_id] = dataclasses.replace(metadata, updated_at=datetime.now(timezone.utc))

    def upsert_version(self, version: SecretVersion) -> None:
        with self._lock:
            self._versions[(version.secret_id, version.version)] = version

    def list_secrets(self, tenant_id: Optional[str] = None) -> Sequence[SecretMetadata]:
        with self._lock:
            values = tuple(self._metadata_by_id.values())
            if tenant_id is not None:
                values = tuple(item for item in values if item.tenant_id == tenant_id)
            return tuple(sorted(values, key=lambda item: item.name))


class SecretAuditSink(ABC):
    """Secret audit sink abstraction."""

    @abstractmethod
    def emit(self, event: SecretAuditEvent) -> None:
        """Emit a secret audit event."""


class LoggingSecretAuditSink(SecretAuditSink):
    """Logging-backed secret audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.secret_manager.audit")
        self.redact = redact

    def emit(self, event: SecretAuditEvent) -> None:
        self.audit_logger.info("secret_manager_event=%s", json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str))


# =============================================================================
# Cache
# =============================================================================


@dataclass
class _SecretCacheEntry:
    secret: ResolvedSecret
    expires_at: float


class SecretCache:
    """TTL cache for resolved secrets."""

    def __init__(self, ttl_seconds: int = 120, max_entries: int = 10_000) -> None:
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _SecretCacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[ResolvedSecret]:
        now = time.time()
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            return dataclasses.replace(entry.secret, cache_hit=True)

    def set(self, key: str, secret: ResolvedSecret) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            if len(self._cache) >= self.max_entries:
                self._evict()
            self._cache[key] = _SecretCacheEntry(secret=secret, expires_at=time.time() + self.ttl_seconds)

    def invalidate_secret(self, secret_id: str) -> None:
        with self._lock:
            keys = [key for key in self._cache if f":{secret_id}:" in key]
            for key in keys:
                self._cache.pop(key, None)

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
# Secret manager service
# =============================================================================


class SecretManager:
    """Enterprise secret manager service."""

    def __init__(
        self,
        repository: Optional[SecretRepository] = None,
        crypto_provider: Optional[SecretCryptoProvider] = None,
        config: Optional[SecretManagerConfig] = None,
        audit_sink: Optional[SecretAuditSink] = None,
        cache: Optional[SecretCache] = None,
        generator: Optional[SecretGenerator] = None,
    ) -> None:
        self.config = config or SecretManagerConfig()
        self.repository = repository or InMemorySecretRepository()
        self.crypto_provider = crypto_provider or self._default_crypto_provider()
        self.audit_sink = audit_sink or LoggingSecretAuditSink(redact=self.config.redact_sensitive_audit_fields)
        self.cache = cache or SecretCache(self.config.cache_ttl_seconds, self.config.max_cache_entries)
        self.generator = generator or generate_secret_string

    def create_secret(self, request: CreateSecretRequest) -> SecretMetadata:
        """Create a new secret with first version."""
        try:
            self._validate_create_request(request)
            plaintext = self._resolve_initial_value(request)
            secret_id = str(uuid.uuid4())
            version = "1"
            policy = request.policy or default_policy_for_secret_type(request.secret_type)
            rotation_days = request.rotation_days if request.rotation_days is not None else self.config.default_rotation_days
            encrypted_value, encryption_metadata, encrypted = self._encrypt_value(plaintext, secret_id, version, request.context)

            metadata = SecretMetadata(
                secret_id=secret_id,
                name=normalize_secret_name(request.name),
                secret_type=request.secret_type,
                status=SecretStatus.ACTIVE,
                current_version=version,
                tenant_id=request.tenant_id,
                description=request.description,
                tags=tuple(request.tags),
                policy=policy,
                origin=request.origin,
                next_rotation_at=datetime.now(timezone.utc) + timedelta(days=rotation_days),
                metadata=dict(request.metadata),
            )
            secret_version = SecretVersion(
                secret_id=secret_id,
                version=version,
                value=encrypted_value,
                value_sha256=hashlib.sha256(plaintext).hexdigest(),
                status=SecretVersionStatus.CURRENT,
                encrypted=encrypted,
                encryption_metadata=encryption_metadata,
                created_by=request.context.principal_id,
            )
            self.repository.create_secret(metadata, secret_version)
            self._audit(SecretEventType.SECRET_CREATED, True, "Secret created.", metadata, secret_version, request.context)
            return metadata
        except Exception as exc:
            self._audit_raw(SecretAuditEvent(
                event_type=SecretEventType.SECRET_ACCESS_DENIED,
                success=False,
                reason=str(exc),
                name=request.name,
                secret_type=request.secret_type,
                principal_id=request.context.principal_id,
                tenant_id=request.context.tenant_id or request.tenant_id,
                purpose=request.context.purpose,
                request_id=request.context.request_id,
                correlation_id=request.context.correlation_id,
                metadata={"operation": "create", "error_type": type(exc).__name__},
            ))
            if self.config.fail_closed:
                if isinstance(exc, SecretManagerError):
                    raise
                raise SecretManagerError("Secret creation failed.") from exc
            raise

    def get_secret(
        self,
        secret_id_or_name: str,
        version: str = "current",
        context: Optional[SecretAccessContext] = None,
    ) -> ResolvedSecret:
        """Resolve and decrypt a secret."""
        context = context or SecretAccessContext()
        metadata = self.repository.get_metadata(self._strip_reference(secret_id_or_name))
        if not metadata:
            raise SecretNotFoundError(f"Secret not found: {secret_id_or_name}")
        self._assert_access(metadata, SecretOperation.READ, context)

        resolved_version = metadata.current_version if version in {"current", "latest"} else version
        cache_key = self._cache_key(metadata.secret_id, resolved_version, context)
        if self.config.cache_enabled:
            cached = self.cache.get(cache_key)
            if cached:
                self._audit(SecretEventType.SECRET_CACHE_HIT, True, "Secret cache hit.", metadata, cached.version, context)
                return cached

        secret_version = self.repository.get_version(metadata.secret_id, resolved_version)
        if not secret_version:
            raise SecretNotFoundError(f"Secret version not found: {metadata.name}:{resolved_version}")
        if not secret_version.is_readable():
            raise SecretStateError(f"Secret version is not readable: {metadata.name}:{resolved_version}")

        plaintext = self._decrypt_value(secret_version, metadata, context)
        resolved = ResolvedSecret(metadata=metadata, version=secret_version, plaintext=plaintext)
        if self.config.cache_enabled:
            self.cache.set(cache_key, resolved)
        self._audit(SecretEventType.SECRET_READ, True, "Secret read.", metadata, secret_version, context)
        return resolved

    def get_secret_text(self, secret_id_or_name: str, version: str = "current", context: Optional[SecretAccessContext] = None, encoding: str = "utf-8") -> str:
        return self.get_secret(secret_id_or_name, version, context).text(encoding)

    def update_secret(self, request: UpdateSecretRequest) -> SecretMetadata:
        """Create a new current version for an existing secret."""
        metadata = self.repository.get_metadata(self._strip_reference(request.secret_id_or_name))
        if not metadata:
            raise SecretNotFoundError(f"Secret not found: {request.secret_id_or_name}")
        self._assert_access(metadata, SecretOperation.UPDATE, request.context)
        if metadata.status != SecretStatus.ACTIVE:
            raise SecretStateError("Only active secrets can be updated.")

        previous = self.repository.get_version(metadata.secret_id, metadata.current_version)
        if previous:
            self.repository.upsert_version(dataclasses.replace(previous, status=SecretVersionStatus.PREVIOUS))

        next_version = self._next_version(metadata.secret_id)
        plaintext = to_bytes(request.value)
        self._validate_secret_value(plaintext)
        encrypted_value, encryption_metadata, encrypted = self._encrypt_value(plaintext, metadata.secret_id, next_version, request.context)
        version_record = SecretVersion(
            secret_id=metadata.secret_id,
            version=next_version,
            value=encrypted_value,
            value_sha256=hashlib.sha256(plaintext).hexdigest(),
            status=SecretVersionStatus.CURRENT,
            encrypted=encrypted,
            encryption_metadata=encryption_metadata,
            created_by=request.context.principal_id,
            expires_at=request.expires_at,
            metadata=dict(request.metadata),
        )
        self.repository.upsert_version(version_record)
        updated = dataclasses.replace(metadata, current_version=next_version, updated_at=datetime.now(timezone.utc))
        self.repository.update_metadata(updated)
        self.cache.invalidate_secret(metadata.secret_id)
        self._audit(SecretEventType.SECRET_VERSION_CREATED, True, "Secret version created.", updated, version_record, request.context)
        return updated

    def rotate_secret(
        self,
        secret_id_or_name: str,
        context: Optional[SecretAccessContext] = None,
        generated_length: int = 48,
    ) -> SecretMetadata:
        """Rotate a secret by generating a new value."""
        context = context or SecretAccessContext()
        metadata = self.repository.get_metadata(self._strip_reference(secret_id_or_name))
        if not metadata:
            raise SecretNotFoundError(f"Secret not found: {secret_id_or_name}")
        self._assert_access(metadata, SecretOperation.ROTATE, context)
        try:
            new_value = self.generator(generated_length)
            updated = self.update_secret(UpdateSecretRequest(secret_id_or_name=metadata.secret_id, value=new_value, context=context, metadata={"rotated": True}))
            updated = dataclasses.replace(updated, next_rotation_at=datetime.now(timezone.utc) + timedelta(days=self.config.default_rotation_days))
            self.repository.update_metadata(updated)
            self._audit(SecretEventType.SECRET_ROTATED, True, "Secret rotated.", updated, self.repository.get_version(updated.secret_id, updated.current_version), context)
            return updated
        except Exception as exc:
            raise SecretRotationError(f"Failed to rotate secret: {metadata.name}") from exc

    def disable_secret(self, secret_id_or_name: str, context: Optional[SecretAccessContext] = None) -> SecretMetadata:
        context = context or SecretAccessContext()
        metadata = self._get_metadata_or_raise(secret_id_or_name)
        self._assert_access(metadata, SecretOperation.DISABLE, context)
        updated = dataclasses.replace(metadata, status=SecretStatus.DISABLED, updated_at=datetime.now(timezone.utc))
        self.repository.update_metadata(updated)
        self.cache.invalidate_secret(metadata.secret_id)
        self._audit(SecretEventType.SECRET_DISABLED, True, "Secret disabled.", updated, None, context)
        return updated

    def enable_secret(self, secret_id_or_name: str, context: Optional[SecretAccessContext] = None) -> SecretMetadata:
        context = context or SecretAccessContext()
        metadata = self._get_metadata_or_raise(secret_id_or_name)
        self._assert_access(metadata, SecretOperation.ENABLE, context)
        if metadata.status == SecretStatus.DELETED:
            raise SecretStateError("Deleted secrets cannot be enabled.")
        updated = dataclasses.replace(metadata, status=SecretStatus.ACTIVE, updated_at=datetime.now(timezone.utc))
        self.repository.update_metadata(updated)
        self._audit(SecretEventType.SECRET_ENABLED, True, "Secret enabled.", updated, None, context)
        return updated

    def schedule_secret_deletion(self, secret_id_or_name: str, wait_days: Optional[int] = None, context: Optional[SecretAccessContext] = None) -> SecretMetadata:
        context = context or SecretAccessContext()
        metadata = self._get_metadata_or_raise(secret_id_or_name)
        self._assert_access(metadata, SecretOperation.DELETE, context)
        wait = max(wait_days or self.config.minimum_deletion_wait_days, self.config.minimum_deletion_wait_days)
        updated = dataclasses.replace(
            metadata,
            status=SecretStatus.SCHEDULED_DELETION,
            scheduled_deletion_at=datetime.now(timezone.utc) + timedelta(days=wait),
            updated_at=datetime.now(timezone.utc),
        )
        self.repository.update_metadata(updated)
        self.cache.invalidate_secret(metadata.secret_id)
        self._audit(SecretEventType.SECRET_DELETION_SCHEDULED, True, "Secret deletion scheduled.", updated, None, context, {"wait_days": wait})
        return updated

    def destroy_secret_version(self, secret_id_or_name: str, version: str, context: Optional[SecretAccessContext] = None) -> SecretVersion:
        context = context or SecretAccessContext()
        metadata = self._get_metadata_or_raise(secret_id_or_name)
        self._assert_access(metadata, SecretOperation.DELETE, context)
        if version == metadata.current_version:
            raise SecretStateError("Cannot destroy current secret version. Rotate or disable first.")
        secret_version = self.repository.get_version(metadata.secret_id, version)
        if not secret_version:
            raise SecretNotFoundError(f"Secret version not found: {metadata.name}:{version}")
        destroyed = dataclasses.replace(
            secret_version,
            value=b"",
            status=SecretVersionStatus.DESTROYED,
            destroyed_at=datetime.now(timezone.utc),
            encrypted=False,
            encryption_metadata={},
        )
        self.repository.upsert_version(destroyed)
        self.cache.invalidate_secret(metadata.secret_id)
        self._audit(SecretEventType.SECRET_DELETED, True, "Secret version destroyed.", metadata, destroyed, context)
        return destroyed

    def list_secrets(self, tenant_id: Optional[str] = None, context: Optional[SecretAccessContext] = None) -> Sequence[SecretMetadata]:
        context = context or SecretAccessContext()
        result = []
        for metadata in self.repository.list_secrets(tenant_id=tenant_id):
            try:
                self._assert_access(metadata, SecretOperation.LIST, context)
                result.append(metadata)
            except SecretAccessDeniedError:
                continue
            except SecretPolicyViolationError:
                continue
        return tuple(result)

    def resolve_references(self, payload: Mapping[str, Any], context: Optional[SecretAccessContext] = None) -> JsonDict:
        """Resolve secret:// references recursively in a config-like mapping."""
        context = context or SecretAccessContext()

        def walk(value: Any) -> Any:
            if isinstance(value, Mapping):
                return {key: walk(item) for key, item in value.items()}
            if isinstance(value, list):
                return [walk(item) for item in value]
            if isinstance(value, tuple):
                return tuple(walk(item) for item in value)
            if isinstance(value, str) and value.startswith(self.config.secret_reference_prefix):
                return self.get_secret_text(value, context=context)
            return value

        resolved = walk(dict(payload))
        self._audit_raw(SecretAuditEvent(
            event_type=SecretEventType.SECRET_BATCH_RESOLVED,
            success=True,
            reason="Secret references resolved.",
            principal_id=context.principal_id,
            tenant_id=context.tenant_id,
            purpose=context.purpose,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
        ))
        return resolved

    def _default_crypto_provider(self) -> SecretCryptoProvider:
        if not self.config.encrypt_at_rest:
            if not self.config.allow_plaintext_storage:
                raise SecretCryptoError("Plaintext storage is disabled. Provide a crypto provider or enable encryption_at_rest.")
            return NoopSecretCryptoProvider()
        if AESGCM is None:
            if self.config.allow_plaintext_storage:
                logger.warning("cryptography is unavailable; falling back to plaintext storage because allow_plaintext_storage=True.")
                return NoopSecretCryptoProvider()
            raise SecretCryptoError("cryptography is required for default encrypted secret storage.")
        master = os.getenv("SECRET_MANAGER_MASTER_KEY_B64URL")
        if master:
            key = decode_bytes(master)
        else:
            key = os.urandom(32)
            logger.warning("Using ephemeral in-memory secret manager master key. Set SECRET_MANAGER_MASTER_KEY_B64URL in production.")
        return AESGCMSecretCryptoProvider(key)

    def _validate_create_request(self, request: CreateSecretRequest) -> None:
        name = normalize_secret_name(request.name)
        if not name:
            raise SecretValidationError("Secret name is required.")
        if not re.fullmatch(r"[a-zA-Z0-9_.\-/]{1,255}", name):
            raise SecretValidationError("Secret name contains invalid characters.")
        if request.generate and request.value is not None:
            raise SecretValidationError("Use either generate=True or value, not both.")
        if not request.generate and request.value is None:
            raise SecretValidationError("Secret value is required unless generate=True.")
        if self.config.require_tenant_match and request.context.tenant_id and request.tenant_id and request.context.tenant_id != request.tenant_id:
            raise SecretAccessDeniedError("Request tenant does not match secret tenant.")

    def _resolve_initial_value(self, request: CreateSecretRequest) -> bytes:
        if request.generate:
            value = self.generator(request.generated_length)
            return value.encode("utf-8")
        plaintext = to_bytes(request.value)
        self._validate_secret_value(plaintext)
        return plaintext

    def _validate_secret_value(self, value: bytes) -> None:
        if value is None or len(value) == 0:
            raise SecretValidationError("Secret value cannot be empty.")
        if len(value) > self.config.max_secret_bytes:
            raise SecretValidationError("Secret value exceeds max_secret_bytes.")

    def _encrypt_value(self, plaintext: bytes, secret_id: str, version: str, context: SecretAccessContext) -> Tuple[bytes, JsonDict, bool]:
        aad = self._aad(secret_id, version, context)
        ciphertext, metadata = self.crypto_provider.encrypt(plaintext, aad=aad)
        encrypted = bool(metadata.get("encrypted", self.config.encrypt_at_rest))
        return ciphertext, metadata, encrypted

    def _decrypt_value(self, version: SecretVersion, metadata: SecretMetadata, context: SecretAccessContext) -> bytes:
        aad = self._aad(metadata.secret_id, version.version, context)
        if version.encrypted:
            return self.crypto_provider.decrypt(version.value, version.encryption_metadata, aad=aad)
        if not self.config.allow_plaintext_storage:
            raise SecretCryptoError("Plaintext secret version exists but plaintext storage is disabled.")
        return version.value

    def _aad(self, secret_id: str, version: str, context: SecretAccessContext) -> bytes:
        payload = {
            "secret_id": secret_id,
            "version": version,
            "tenant_id": context.tenant_id,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _get_metadata_or_raise(self, secret_id_or_name: str) -> SecretMetadata:
        metadata = self.repository.get_metadata(self._strip_reference(secret_id_or_name))
        if not metadata:
            raise SecretNotFoundError(f"Secret not found: {secret_id_or_name}")
        return metadata

    def _assert_access(self, metadata: SecretMetadata, operation: SecretOperation, context: SecretAccessContext) -> None:
        if metadata.status != SecretStatus.ACTIVE and operation not in {SecretOperation.ENABLE, SecretOperation.DELETE, SecretOperation.LIST}:
            raise SecretStateError(f"Secret is not active: {metadata.name}")
        if self.config.require_tenant_match and metadata.tenant_id and context.tenant_id and metadata.tenant_id != context.tenant_id:
            self._audit(SecretEventType.SECRET_ACCESS_DENIED, False, "Tenant boundary denied secret access.", metadata, None, context, {"operation": operation.value})
            raise SecretAccessDeniedError("Secret tenant does not match request tenant.")
        if not metadata.policy.allows(operation, context.principal_id, context.tenant_id or metadata.tenant_id, context.purpose, context.mfa_verified, self.config):
            self._audit(SecretEventType.SECRET_ACCESS_DENIED, False, "Secret policy denied operation.", metadata, None, context, {"operation": operation.value})
            raise SecretPolicyViolationError(f"Secret policy denied operation: {operation.value}")

    def _next_version(self, secret_id: str) -> str:
        versions = self.repository.list_versions(secret_id)
        numeric = [int(item.version) for item in versions if item.version.isdigit()]
        return str((max(numeric) if numeric else 0) + 1)

    def _strip_reference(self, value: str) -> str:
        return value[len(self.config.secret_reference_prefix):] if value.startswith(self.config.secret_reference_prefix) else value

    def _cache_key(self, secret_id: str, version: str, context: SecretAccessContext) -> str:
        payload = {
            "secret_id": secret_id,
            "version": version,
            "principal_id": context.principal_id,
            "tenant_id": context.tenant_id,
            "purpose": context.purpose,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return f"secret:{secret_id}:{version}:{digest}"

    def _audit(
        self,
        event_type: SecretEventType,
        success: bool,
        reason: str,
        metadata: Optional[SecretMetadata],
        version: Optional[SecretVersion],
        context: SecretAccessContext,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._audit_raw(SecretAuditEvent(
            event_type=event_type,
            success=success,
            reason=reason,
            secret_id=metadata.secret_id if metadata else None,
            name=metadata.name if metadata else None,
            version=version.version if version else (metadata.current_version if metadata else None),
            secret_type=metadata.secret_type if metadata else None,
            principal_id=context.principal_id,
            tenant_id=context.tenant_id or (metadata.tenant_id if metadata else None),
            purpose=context.purpose,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            metadata={
                "tags": list(metadata.tags) if metadata else [],
                "environment": dict(context.environment),
                **dict(extra or {}),
            },
        ))

    def _audit_raw(self, event: SecretAuditEvent) -> None:
        if not self.config.enable_audit:
            return
        try:
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit secret audit event.")


# =============================================================================
# Utility functions
# =============================================================================


def normalize_secret_name(name: str) -> str:
    return (name or "").strip().strip("/")


def to_bytes(value: Optional[SecretValue], encoding: str = "utf-8") -> bytes:
    if value is None:
        raise SecretValidationError("Secret value is required.")
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode(encoding)
    raise SecretValidationError("Secret value must be str or bytes.")


def generate_secret_string(length: int = 48) -> str:
    if length < 16:
        raise SecretValidationError("Generated secrets should be at least 16 characters.")
    token = py_secrets.token_urlsafe(max(16, length))
    return token[:length]


def generate_password(length: int = 32) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()-_=+[]{}"
    if length < 16:
        raise SecretValidationError("Generated passwords should be at least 16 characters.")
    return "".join(py_secrets.choice(alphabet) for _ in range(length))


def default_policy_for_secret_type(secret_type: SecretType) -> SecretAccessPolicy:
    common = (SecretOperation.READ, SecretOperation.UPDATE, SecretOperation.ROTATE, SecretOperation.LIST)
    if secret_type in {SecretType.PRIVATE_KEY, SecretType.CERTIFICATE, SecretType.DATABASE_URL, SecretType.CONNECTION_STRING}:
        return SecretAccessPolicy(
            allowed_operations=common + (SecretOperation.DISABLE, SecretOperation.ENABLE, SecretOperation.DELETE),
            allowed_principals=("system", "admin"),
            allowed_purposes=("*",),
            require_mfa_for_admin=True,
        )
    return SecretAccessPolicy(
        allowed_operations=common + (SecretOperation.DISABLE, SecretOperation.ENABLE, SecretOperation.DELETE),
        allowed_principals=("*",),
        allowed_purposes=("*",),
    )


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


def decode_bytes(value: Union[str, bytes], encoding: str = "base64url") -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        raise SecretValidationError("Encoded value must be string or bytes.")
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
        raise SecretValidationError(f"Invalid {encoding} encoded value.") from exc
    raise SecretValidationError(f"Unsupported encoding: {encoding}")


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "secret",
        "value",
        "plaintext",
        "ciphertext",
        "password",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "connection_string",
        "database_url",
        "nonce",
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
    return "*" in patterns or value in set(patterns)


def create_default_secret_manager() -> SecretManager:
    config = SecretManagerConfig(encrypt_at_rest=AESGCM is not None, allow_plaintext_storage=AESGCM is None)
    return SecretManager(config=config)


__all__ = [
    "AESGCMSecretCryptoProvider",
    "CreateSecretRequest",
    "InMemorySecretRepository",
    "LoggingSecretAuditSink",
    "NoopSecretCryptoProvider",
    "ResolvedSecret",
    "SecretAccessContext",
    "SecretAccessDeniedError",
    "SecretAccessPolicy",
    "SecretAuditEvent",
    "SecretAuditSink",
    "SecretCache",
    "SecretCryptoError",
    "SecretCryptoProvider",
    "SecretEventType",
    "SecretManager",
    "SecretManagerConfig",
    "SecretManagerError",
    "SecretMetadata",
    "SecretNotFoundError",
    "SecretOperation",
    "SecretOrigin",
    "SecretPolicyViolationError",
    "SecretRepository",
    "SecretRepositoryError",
    "SecretRotationError",
    "SecretStateError",
    "SecretStatus",
    "SecretType",
    "SecretValidationError",
    "SecretVersion",
    "SecretVersionStatus",
    "UpdateSecretRequest",
    "create_default_secret_manager",
    "decode_bytes",
    "default_policy_for_secret_type",
    "encode_bytes",
    "generate_password",
    "generate_secret_string",
    "normalize_secret_name",
    "parse_datetime",
    "redact_sensitive",
    "to_bytes",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    manager = create_default_secret_manager()
    context = SecretAccessContext(
        principal_id="system",
        tenant_id="default",
        purpose="demo",
        request_id="req-demo",
        correlation_id="corr-demo",
        mfa_verified=True,
    )

    metadata = manager.create_secret(
        CreateSecretRequest(
            name="apps/demo/database-password",
            secret_type=SecretType.PASSWORD,
            tenant_id="default",
            generate=True,
            generated_length=32,
            context=context,
        )
    )

    resolved = manager.get_secret(metadata.name, context=context)
    print(json.dumps(resolved.to_safe_dict(), indent=2, default=str))
    print("secret_reference=", metadata.reference())
