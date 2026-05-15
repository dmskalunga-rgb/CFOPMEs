"""
data/security/encryption.py

Enterprise-grade encryption module for Python services, data platforms,
pipelines, workers and internal security tooling.

Core capabilities:
- Envelope encryption model
- AES-GCM authenticated encryption when `cryptography` is available
- Fernet-compatible optional support when `cryptography.fernet` is available
- KMS/key-provider abstraction
- In-memory key provider for tests/local development
- Key versioning and rotation-aware encryption
- Associated Authenticated Data (AAD) support
- Structured audit events
- Batch encryption helpers
- Secure envelope serialization
- Redaction of sensitive audit metadata
- Fail-closed behavior by default

Production recommendations:
- Use a real KMS/HSM/Secrets Manager for key material.
- Keep master/data keys out of application logs.
- Prefer AES-GCM or another authenticated encryption mode.
- Rotate keys regularly and keep old key versions available for decryption.
- Store envelopes with key_id/key_version/algorithm/nonce/tag/AAD metadata.
"""

from __future__ import annotations

import base64
import binascii
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
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
BytesLike = Union[bytes, bytearray, memoryview]

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore

try:
    from cryptography.fernet import Fernet  # type: ignore
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore


# =============================================================================
# Exceptions
# =============================================================================


class EncryptionError(Exception):
    """Base encryption error."""


class InvalidPlaintextError(EncryptionError):
    """Raised when plaintext is invalid or exceeds configured limits."""


class KeyNotFoundError(EncryptionError):
    """Raised when a required key cannot be found."""


class KeyAccessDeniedError(EncryptionError):
    """Raised when access to a key is denied."""


class UnsupportedAlgorithmError(EncryptionError):
    """Raised when an unsupported encryption algorithm is requested."""


class EncryptionConfigurationError(EncryptionError):
    """Raised when encryption service is misconfigured."""


class EnvelopeSerializationError(EncryptionError):
    """Raised when an encryption envelope cannot be serialized/deserialized."""


# =============================================================================
# Enums and configuration
# =============================================================================


class EncryptionAlgorithm(str, Enum):
    AES_256_GCM = "AES-256-GCM"
    AES_128_GCM = "AES-128-GCM"
    FERNET = "FERNET"


class KeyType(str, Enum):
    DATA_KEY = "data_key"
    MASTER_KEY = "master_key"
    KEK = "key_encryption_key"


class EncryptionEventType(str, Enum):
    ENCRYPT_SUCCESS = "security.encryption.success"
    ENCRYPT_FAILURE = "security.encryption.failure"
    KEY_RESOLVED = "security.encryption.key_resolved"
    KEY_NOT_FOUND = "security.encryption.key_not_found"
    BATCH_ENCRYPT_COMPLETED = "security.encryption.batch_completed"


@dataclass(frozen=True)
class EncryptionConfig:
    """Runtime configuration for encryption behavior."""

    fail_closed: bool = True
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    require_authenticated_encryption: bool = True
    max_plaintext_bytes: int = 128 * 1024 * 1024
    default_associated_data: Optional[bytes] = None
    allow_fernet: bool = True
    split_gcm_tag: bool = False
    nonce_bytes: int = 12
    key_cache_ttl_seconds: int = 300
    max_key_cache_entries: int = 10_000


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class KeyMaterial:
    """Resolved cryptographic key material."""

    key_id: str
    version: str
    key_type: KeyType
    algorithm: EncryptionAlgorithm
    material: bytes
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    disabled: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def validate_for_encryption(self) -> None:
        if self.disabled:
            raise KeyAccessDeniedError(f"Key is disabled: {self.key_id}:{self.version}")
        if self.expires_at and self.expires_at <= datetime.now(timezone.utc):
            raise KeyAccessDeniedError(f"Key is expired: {self.key_id}:{self.version}")
        if self.algorithm == EncryptionAlgorithm.AES_256_GCM and len(self.material) != 32:
            raise EncryptionConfigurationError("AES-256-GCM key material must be 32 bytes.")
        if self.algorithm == EncryptionAlgorithm.AES_128_GCM and len(self.material) != 16:
            raise EncryptionConfigurationError("AES-128-GCM key material must be 16 bytes.")
        if self.algorithm == EncryptionAlgorithm.FERNET and not self.material:
            raise EncryptionConfigurationError("Fernet key material cannot be empty.")


@dataclass(frozen=True)
class EncryptionEnvelope:
    """
    Standard encrypted payload envelope.

    AES-GCM notes:
    - nonce is generated per encryption operation.
    - ciphertext may contain tag appended, or tag may be split depending on config.
    - associated_data is authenticated but not encrypted.

    Fernet notes:
    - ciphertext is the full Fernet token.
    - nonce/tag/AAD are not used by Fernet.
    """

    algorithm: EncryptionAlgorithm
    ciphertext: bytes
    key_id: str
    key_version: str
    nonce: Optional[bytes] = None
    tag: Optional[bytes] = None
    associated_data: Optional[bytes] = None
    encoding: str = "raw"
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, encoding: str = "base64url", include_ciphertext: bool = True) -> JsonDict:
        data = {
            "algorithm": self.algorithm.value,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "nonce": encode_optional_bytes(self.nonce, encoding),
            "tag": encode_optional_bytes(self.tag, encoding),
            "associated_data": encode_optional_bytes(self.associated_data, encoding),
            "encoding": encoding,
            "envelope_id": self.envelope_id,
            "created_at": self.created_at.isoformat(),
            "metadata": redact_sensitive(self.metadata),
        }
        data["ciphertext"] = encode_bytes(self.ciphertext, encoding) if include_ciphertext else "***REDACTED***"
        return data

    def to_json(self, encoding: str = "base64url", include_ciphertext: bool = True, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(encoding=encoding, include_ciphertext=include_ciphertext), sort_keys=True, indent=indent, default=str)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EncryptionEnvelope":
        try:
            encoding = str(payload.get("encoding", "base64url"))
            return cls(
                algorithm=EncryptionAlgorithm(str(payload["algorithm"])),
                ciphertext=decode_bytes(payload["ciphertext"], encoding),
                key_id=str(payload["key_id"]),
                key_version=str(payload.get("key_version", "latest")),
                nonce=decode_optional_bytes(payload.get("nonce"), encoding),
                tag=decode_optional_bytes(payload.get("tag"), encoding),
                associated_data=decode_optional_bytes(payload.get("associated_data"), encoding),
                encoding=encoding,
                envelope_id=str(payload.get("envelope_id") or uuid.uuid4()),
                created_at=parse_datetime(payload.get("created_at")) if payload.get("created_at") else datetime.now(timezone.utc),
                metadata=dict(payload.get("metadata") or {}),
            )
        except Exception as exc:
            raise EnvelopeSerializationError(f"Invalid envelope payload: {exc}") from exc

    @classmethod
    def from_json(cls, raw_json: Union[str, bytes]) -> "EncryptionEnvelope":
        try:
            payload = json.loads(raw_json)
        except Exception as exc:
            raise EnvelopeSerializationError("Invalid envelope JSON.") from exc
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("Envelope JSON must be an object.")
        return cls.from_dict(payload)


@dataclass(frozen=True)
class EncryptionRequest:
    """Encryption operation request."""

    plaintext: bytes
    key_id: str
    key_version: str = "latest"
    algorithm: Optional[EncryptionAlgorithm] = None
    associated_data: Optional[bytes] = None
    requester_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    environment: JsonDict = field(default_factory=dict)
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class EncryptionResult:
    """Encryption operation result."""

    envelope: EncryptionEnvelope
    plaintext_sha256: str
    encrypted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self, include_ciphertext: bool = True) -> JsonDict:
        return {
            "envelope": self.envelope.to_dict(include_ciphertext=include_ciphertext),
            "plaintext_sha256": self.plaintext_sha256,
            "encrypted_at": self.encrypted_at.isoformat(),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": redact_sensitive(self.metadata),
        }


@dataclass(frozen=True)
class EncryptionAuditEvent:
    """Structured audit event for encryption operations."""

    event_type: EncryptionEventType
    success: bool
    reason: str
    key_id: Optional[str] = None
    key_version: Optional[str] = None
    algorithm: Optional[EncryptionAlgorithm] = None
    envelope_id: Optional[str] = None
    requester_id: Optional[str] = None
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
            "algorithm": self.algorithm.value if self.algorithm else None,
            "envelope_id": self.envelope_id,
            "requester_id": self.requester_id,
            "tenant_id": self.tenant_id,
            "purpose": self.purpose,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "metadata": redact_sensitive(self.metadata) if redact else dict(self.metadata),
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Key provider abstractions
# =============================================================================


class KeyProvider(ABC):
    """Key provider/KMS abstraction."""

    @abstractmethod
    def get_key(self, key_id: str, version: str = "latest", tenant_id: Optional[str] = None) -> KeyMaterial:
        """Resolve key material for encryption."""


class InMemoryKeyProvider(KeyProvider):
    """Thread-safe in-memory key provider for tests and local development."""

    def __init__(self, keys: Optional[Iterable[KeyMaterial]] = None) -> None:
        self._keys: Dict[Tuple[str, str], KeyMaterial] = {}
        self._latest: Dict[str, str] = {}
        self._lock = threading.RLock()
        for key in keys or []:
            self.upsert_key(key)

    def upsert_key(self, key: KeyMaterial) -> None:
        with self._lock:
            self._keys[(key.key_id, key.version)] = key
            self._latest[key.key_id] = key.version

    def get_key(self, key_id: str, version: str = "latest", tenant_id: Optional[str] = None) -> KeyMaterial:
        with self._lock:
            resolved_version = self._latest.get(key_id) if version == "latest" else version
            if not resolved_version:
                raise KeyNotFoundError(f"Key not found: {key_id}:{version}")
            key = self._keys.get((key_id, resolved_version))
            if not key:
                raise KeyNotFoundError(f"Key not found: {key_id}:{resolved_version}")
            key_tenant = key.metadata.get("tenant_id")
            if key_tenant and tenant_id and key_tenant != tenant_id:
                raise KeyAccessDeniedError("Key tenant does not match request tenant.")
            return key


@dataclass
class _KeyCacheEntry:
    key: KeyMaterial
    expires_at: float


class CachingKeyProvider(KeyProvider):
    """TTL cache wrapper around another key provider."""

    def __init__(self, provider: KeyProvider, ttl_seconds: int = 300, max_entries: int = 10_000) -> None:
        self.provider = provider
        self.ttl_seconds = max(0, ttl_seconds)
        self.max_entries = max(1, max_entries)
        self._cache: Dict[str, _KeyCacheEntry] = {}
        self._lock = threading.RLock()

    def get_key(self, key_id: str, version: str = "latest", tenant_id: Optional[str] = None) -> KeyMaterial:
        cache_key = f"{tenant_id or '*'}:{key_id}:{version}"
        now = time.time()
        with self._lock:
            entry = self._cache.get(cache_key)
            if entry and entry.expires_at > now:
                return entry.key
            if entry:
                self._cache.pop(cache_key, None)

        key = self.provider.get_key(key_id, version, tenant_id)
        if self.ttl_seconds > 0:
            with self._lock:
                if len(self._cache) >= self.max_entries:
                    self._evict()
                self._cache[cache_key] = _KeyCacheEntry(key=key, expires_at=now + self.ttl_seconds)
        return key

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _evict(self) -> None:
        now = time.time()
        expired = [key for key, entry in self._cache.items() if entry.expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries and self._cache:
            oldest_key = min(self._cache.items(), key=lambda item: item[1].expires_at)[0]
            self._cache.pop(oldest_key, None)


# =============================================================================
# Audit
# =============================================================================


class EncryptionAuditSink(ABC):
    """Audit sink abstraction for encryption events."""

    @abstractmethod
    def emit(self, event: EncryptionAuditEvent) -> None:
        """Emit an encryption audit event."""


class LoggingEncryptionAuditSink(EncryptionAuditSink):
    """Logging-based encryption audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.encryption.audit")
        self.redact = redact

    def emit(self, event: EncryptionAuditEvent) -> None:
        self.audit_logger.info(
            "encryption_event=%s",
            json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str),
        )


# =============================================================================
# Encryptors
# =============================================================================


class CipherEncryptor(ABC):
    """Algorithm-specific encryptor abstraction."""

    @abstractmethod
    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        """Return True if this encryptor supports the algorithm."""

    @abstractmethod
    def encrypt(self, plaintext: bytes, key: KeyMaterial, associated_data: Optional[bytes], config: EncryptionConfig) -> EncryptionEnvelope:
        """Encrypt plaintext and return an envelope."""


class AESGCMEncryptor(CipherEncryptor):
    """AES-GCM authenticated encryptor."""

    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        return algorithm in {EncryptionAlgorithm.AES_256_GCM, EncryptionAlgorithm.AES_128_GCM}

    def encrypt(self, plaintext: bytes, key: KeyMaterial, associated_data: Optional[bytes], config: EncryptionConfig) -> EncryptionEnvelope:
        if AESGCM is None:
            raise UnsupportedAlgorithmError(
                "AES-GCM requires the optional 'cryptography' package. Install cryptography to enable AES-GCM encryption."
            )
        key.validate_for_encryption()
        if config.nonce_bytes < 12:
            raise EncryptionConfigurationError("AES-GCM nonce should be at least 12 bytes.")

        nonce = os.urandom(config.nonce_bytes)
        ciphertext_with_tag = AESGCM(key.material).encrypt(nonce, plaintext, associated_data)

        if config.split_gcm_tag:
            ciphertext = ciphertext_with_tag[:-16]
            tag = ciphertext_with_tag[-16:]
        else:
            ciphertext = ciphertext_with_tag
            tag = None

        return EncryptionEnvelope(
            algorithm=key.algorithm,
            ciphertext=ciphertext,
            key_id=key.key_id,
            key_version=key.version,
            nonce=nonce,
            tag=tag,
            associated_data=associated_data,
            metadata={"authenticated": True, "mode": "GCM"},
        )


class FernetEncryptor(CipherEncryptor):
    """Fernet encryptor."""

    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        return algorithm == EncryptionAlgorithm.FERNET

    def encrypt(self, plaintext: bytes, key: KeyMaterial, associated_data: Optional[bytes], config: EncryptionConfig) -> EncryptionEnvelope:
        if not config.allow_fernet:
            raise UnsupportedAlgorithmError("Fernet encryption is disabled by configuration.")
        if Fernet is None:
            raise UnsupportedAlgorithmError(
                "Fernet requires the optional 'cryptography' package. Install cryptography to enable Fernet encryption."
            )
        key.validate_for_encryption()
        token = Fernet(key.material).encrypt(plaintext)
        return EncryptionEnvelope(
            algorithm=EncryptionAlgorithm.FERNET,
            ciphertext=token,
            key_id=key.key_id,
            key_version=key.version,
            associated_data=None,
            metadata={"authenticated": True, "mode": "FERNET"},
        )


# =============================================================================
# Main service
# =============================================================================


class EncryptionService:
    """Enterprise encryption orchestration service."""

    def __init__(
        self,
        key_provider: KeyProvider,
        config: Optional[EncryptionConfig] = None,
        encryptors: Optional[Sequence[CipherEncryptor]] = None,
        audit_sink: Optional[EncryptionAuditSink] = None,
    ) -> None:
        self.config = config or EncryptionConfig()
        self.key_provider = CachingKeyProvider(
            key_provider,
            ttl_seconds=self.config.key_cache_ttl_seconds,
            max_entries=self.config.max_key_cache_entries,
        )
        self.encryptors = tuple(encryptors or (AESGCMEncryptor(), FernetEncryptor()))
        self.audit_sink = audit_sink or LoggingEncryptionAuditSink(redact=self.config.redact_sensitive_audit_fields)

    def encrypt(self, request: EncryptionRequest) -> EncryptionResult:
        """Encrypt a single plaintext payload."""
        try:
            self._validate_request(request)
            key = self.key_provider.get_key(request.key_id, request.key_version, tenant_id=request.tenant_id)
            key.validate_for_encryption()

            algorithm = request.algorithm or key.algorithm
            if algorithm != key.algorithm:
                raise EncryptionConfigurationError(
                    f"Requested algorithm {algorithm.value} does not match key algorithm {key.algorithm.value}."
                )

            self._audit(
                EncryptionEventType.KEY_RESOLVED,
                True,
                "Key resolved for encryption.",
                request=request,
                key=key,
            )

            encryptor = self._select_encryptor(algorithm)
            associated_data = self._resolve_associated_data(request)
            envelope = encryptor.encrypt(bytes(request.plaintext), key, associated_data, self.config)
            envelope = self._merge_envelope_metadata(envelope, request)
            plaintext_sha256 = hashlib.sha256(request.plaintext).hexdigest()

            result = EncryptionResult(
                envelope=envelope,
                plaintext_sha256=plaintext_sha256,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                metadata={
                    "plaintext_bytes": len(request.plaintext),
                    "ciphertext_bytes": len(envelope.ciphertext),
                    "purpose": request.purpose,
                },
            )

            self._audit(
                EncryptionEventType.ENCRYPT_SUCCESS,
                True,
                "Encryption completed successfully.",
                request=request,
                key=key,
                envelope=envelope,
                metadata={"plaintext_sha256": plaintext_sha256, "ciphertext_bytes": len(envelope.ciphertext)},
            )
            return result
        except Exception as exc:
            event_type = EncryptionEventType.KEY_NOT_FOUND if isinstance(exc, KeyNotFoundError) else EncryptionEventType.ENCRYPT_FAILURE
            self._audit(
                event_type,
                False,
                str(exc),
                request=request,
                metadata={"error_type": type(exc).__name__},
            )
            logger.exception("Encryption failed. key_id=%s request_id=%s", request.key_id, request.request_id)
            if self.config.fail_closed:
                if isinstance(exc, EncryptionError):
                    raise
                raise EncryptionError("Encryption failed.") from exc
            raise

    def encrypt_bytes(
        self,
        plaintext: bytes,
        key_id: str,
        key_version: str = "latest",
        algorithm: Optional[EncryptionAlgorithm] = None,
        associated_data: Optional[bytes] = None,
        requester_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> EncryptionEnvelope:
        """Convenience helper for direct byte encryption."""
        request = EncryptionRequest(
            plaintext=plaintext,
            key_id=key_id,
            key_version=key_version,
            algorithm=algorithm,
            associated_data=associated_data,
            requester_id=requester_id,
            tenant_id=tenant_id,
            purpose=purpose,
        )
        return self.encrypt(request).envelope

    def encrypt_text(
        self,
        plaintext: str,
        key_id: str,
        key_version: str = "latest",
        encoding: str = "utf-8",
        algorithm: Optional[EncryptionAlgorithm] = None,
        associated_data: Optional[bytes] = None,
        requester_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> EncryptionEnvelope:
        """Encrypt a text payload."""
        return self.encrypt_bytes(
            plaintext=plaintext.encode(encoding),
            key_id=key_id,
            key_version=key_version,
            algorithm=algorithm,
            associated_data=associated_data,
            requester_id=requester_id,
            tenant_id=tenant_id,
            purpose=purpose,
        )

    def encrypt_many(self, requests: Sequence[EncryptionRequest], continue_on_error: bool = False) -> Tuple[Union[EncryptionResult, EncryptionError], ...]:
        """Encrypt multiple payloads with optional partial failure handling."""
        results = []
        failures = 0
        for request in requests:
            try:
                results.append(self.encrypt(request))
            except EncryptionError as exc:
                failures += 1
                if not continue_on_error:
                    raise
                results.append(exc)

        self._audit_raw(
            EncryptionAuditEvent(
                event_type=EncryptionEventType.BATCH_ENCRYPT_COMPLETED,
                success=failures == 0,
                reason="Batch encryption completed." if failures == 0 else "Batch encryption completed with failures.",
                metadata={"total": len(requests), "failures": failures},
            )
        )
        return tuple(results)

    def rotate_envelope(
        self,
        plaintext: bytes,
        old_envelope: EncryptionEnvelope,
        new_key_id: str,
        new_key_version: str = "latest",
        requester_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: str = "key-rotation",
    ) -> EncryptionEnvelope:
        """
        Re-encrypt already decrypted plaintext using a new key.

        This method intentionally expects plaintext. Decrypting old envelopes
        belongs in decryption.py to keep responsibilities separated.
        """
        result = self.encrypt(
            EncryptionRequest(
                plaintext=plaintext,
                key_id=new_key_id,
                key_version=new_key_version,
                associated_data=old_envelope.associated_data,
                requester_id=requester_id,
                tenant_id=tenant_id,
                purpose=purpose,
                metadata={
                    "old_envelope_id": old_envelope.envelope_id,
                    "old_key_id": old_envelope.key_id,
                    "old_key_version": old_envelope.key_version,
                },
            )
        )
        return result.envelope

    def _validate_request(self, request: EncryptionRequest) -> None:
        if not request.key_id:
            raise EncryptionConfigurationError("key_id is required.")
        if not request.key_version:
            raise EncryptionConfigurationError("key_version is required.")
        if request.plaintext is None:
            raise InvalidPlaintextError("plaintext is required.")
        if not isinstance(request.plaintext, (bytes, bytearray, memoryview)):
            raise InvalidPlaintextError("plaintext must be bytes-like.")
        if len(request.plaintext) > self.config.max_plaintext_bytes:
            raise InvalidPlaintextError("plaintext exceeds max_plaintext_bytes.")

    def _select_encryptor(self, algorithm: EncryptionAlgorithm) -> CipherEncryptor:
        for encryptor in self.encryptors:
            if encryptor.supports(algorithm):
                return encryptor
        raise UnsupportedAlgorithmError(f"Unsupported encryption algorithm: {algorithm.value}")

    def _resolve_associated_data(self, request: EncryptionRequest) -> Optional[bytes]:
        if request.associated_data is not None:
            return request.associated_data
        return self.config.default_associated_data

    def _merge_envelope_metadata(self, envelope: EncryptionEnvelope, request: EncryptionRequest) -> EncryptionEnvelope:
        metadata = {
            **dict(envelope.metadata),
            **dict(request.metadata),
            "purpose": request.purpose,
            "tenant_id": request.tenant_id,
        }
        return EncryptionEnvelope(
            algorithm=envelope.algorithm,
            ciphertext=envelope.ciphertext,
            key_id=envelope.key_id,
            key_version=envelope.key_version,
            nonce=envelope.nonce,
            tag=envelope.tag,
            associated_data=envelope.associated_data,
            encoding=envelope.encoding,
            envelope_id=envelope.envelope_id,
            created_at=envelope.created_at,
            metadata=metadata,
        )

    def _audit(
        self,
        event_type: EncryptionEventType,
        success: bool,
        reason: str,
        request: EncryptionRequest,
        key: Optional[KeyMaterial] = None,
        envelope: Optional[EncryptionEnvelope] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not self.config.enable_audit:
            return
        event = EncryptionAuditEvent(
            event_type=event_type,
            success=success,
            reason=reason,
            key_id=key.key_id if key else request.key_id,
            key_version=key.version if key else request.key_version,
            algorithm=(envelope.algorithm if envelope else (request.algorithm if request.algorithm else (key.algorithm if key else None))),
            envelope_id=envelope.envelope_id if envelope else None,
            requester_id=request.requester_id,
            tenant_id=request.tenant_id,
            purpose=request.purpose,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            metadata={
                "request_metadata": dict(request.metadata),
                "environment": dict(request.environment),
                **dict(metadata or {}),
            },
        )
        self._audit_raw(event)

    def _audit_raw(self, event: EncryptionAuditEvent) -> None:
        if not self.config.enable_audit:
            return
        try:
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit encryption audit event.")


# =============================================================================
# Key helpers
# =============================================================================


def generate_aes_key(algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM) -> bytes:
    if algorithm == EncryptionAlgorithm.AES_256_GCM:
        return os.urandom(32)
    if algorithm == EncryptionAlgorithm.AES_128_GCM:
        return os.urandom(16)
    raise UnsupportedAlgorithmError(f"Cannot generate AES key for algorithm: {algorithm.value}")


def generate_fernet_key() -> bytes:
    if Fernet is None:
        raise UnsupportedAlgorithmError("cryptography is required to generate Fernet keys.")
    return Fernet.generate_key()


def create_key_material(
    key_id: str,
    version: str = "1",
    algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM,
    key_type: KeyType = KeyType.DATA_KEY,
    material: Optional[bytes] = None,
    tenant_id: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> KeyMaterial:
    if material is None:
        material = generate_fernet_key() if algorithm == EncryptionAlgorithm.FERNET else generate_aes_key(algorithm)
    merged_metadata = dict(metadata or {})
    if tenant_id:
        merged_metadata["tenant_id"] = tenant_id
    key = KeyMaterial(
        key_id=key_id,
        version=version,
        key_type=key_type,
        algorithm=algorithm,
        material=material,
        metadata=merged_metadata,
    )
    key.validate_for_encryption()
    return key


# =============================================================================
# Encoding / parsing utilities
# =============================================================================


def encode_bytes(raw: bytes, encoding: str = "base64url") -> str:
    if encoding == "base64url":
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if encoding == "base64":
        return base64.b64encode(raw).decode("ascii")
    if encoding == "hex":
        return raw.hex()
    if encoding == "utf-8":
        return raw.decode("utf-8")
    if encoding == "raw":
        raise ValueError("Raw bytes cannot be represented as a JSON string.")
    raise ValueError(f"Unsupported encoding: {encoding}")


def encode_optional_bytes(raw: Optional[bytes], encoding: str = "base64url") -> Optional[str]:
    return encode_bytes(raw, encoding) if raw is not None else None


def decode_bytes(value: Union[str, bytes], encoding: str = "base64url") -> bytes:
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        raise EnvelopeSerializationError("Encoded bytes must be a string or bytes.")
    try:
        if encoding == "base64url":
            return base64.urlsafe_b64decode((value + "=" * ((4 - len(value) % 4) % 4)).encode("ascii"))
        if encoding == "base64":
            return base64.b64decode(value.encode("ascii"))
        if encoding == "hex":
            return bytes.fromhex(value)
        if encoding == "utf-8":
            return value.encode("utf-8")
        if encoding == "raw":
            return value.encode("latin1")
    except (binascii.Error, ValueError) as exc:
        raise EnvelopeSerializationError(f"Invalid {encoding} encoded bytes.") from exc
    raise EnvelopeSerializationError(f"Unsupported encoding: {encoding}")


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
        "plaintext",
        "ciphertext",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "key_material",
        "material",
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


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def constant_time_equals(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)


def create_default_encryption_service() -> EncryptionService:
    """Create a local development encryption service with one random AES-256 key."""
    key = create_key_material(
        key_id="local-dev-key",
        version="1",
        algorithm=EncryptionAlgorithm.AES_256_GCM,
        metadata={"environment": "local-dev"},
    )
    return EncryptionService(InMemoryKeyProvider([key]))


__all__ = [
    "AESGCMEncryptor",
    "CachingKeyProvider",
    "CipherEncryptor",
    "EncryptionAlgorithm",
    "EncryptionAuditEvent",
    "EncryptionAuditSink",
    "EncryptionConfig",
    "EncryptionConfigurationError",
    "EncryptionEnvelope",
    "EncryptionError",
    "EncryptionEventType",
    "EncryptionRequest",
    "EncryptionResult",
    "EncryptionService",
    "EnvelopeSerializationError",
    "FernetEncryptor",
    "InMemoryKeyProvider",
    "InvalidPlaintextError",
    "KeyAccessDeniedError",
    "KeyMaterial",
    "KeyNotFoundError",
    "KeyProvider",
    "KeyType",
    "LoggingEncryptionAuditSink",
    "UnsupportedAlgorithmError",
    "constant_time_equals",
    "create_default_encryption_service",
    "create_key_material",
    "decode_bytes",
    "decode_optional_bytes",
    "encode_bytes",
    "encode_optional_bytes",
    "generate_aes_key",
    "generate_fernet_key",
    "parse_datetime",
    "redact_sensitive",
    "sha256_hex",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if AESGCM is None:
        print("cryptography is not installed; AES-GCM demo skipped.")
    else:
        key = create_key_material(key_id="demo-key", version="1")
        service = EncryptionService(InMemoryKeyProvider([key]))
        result = service.encrypt(
            EncryptionRequest(
                plaintext=b"enterprise-secret-payload",
                key_id="demo-key",
                key_version="1",
                associated_data=b"tenant-a:data-export",
                requester_id="user-001",
                tenant_id="tenant-a",
                purpose="local-demo",
                request_id="req-demo",
                correlation_id="corr-demo",
            )
        )
        print(json.dumps(result.to_dict(include_ciphertext=False), indent=2, default=str))
        print(result.envelope.to_json(indent=2)[:500] + "...")
