"""
data/security/decryption.py

Enterprise-grade decryption module for Python services, data platforms,
pipelines, workers and internal security tooling.

Core capabilities:
- Envelope decryption model
- AES-GCM authenticated decryption when `cryptography` is available
- Fernet-compatible optional support when `cryptography.fernet` is available
- KMS/key-provider abstraction
- In-memory key provider for tests/local development
- Key versioning and rotation-aware decryption
- Structured audit events
- Strict integrity validation
- Redaction of sensitive audit metadata
- Batch decryption helpers
- Streaming-friendly chunk helpers
- Secure error taxonomy
- Fail-closed behavior by default

Production recommendations:
- Use a real KMS/HSM/Secrets Manager for key material.
- Keep master/data keys out of application logs.
- Prefer AES-GCM or another authenticated encryption mode.
- Never use unauthenticated AES-CBC without a separate MAC.
- Rotate keys regularly and keep old key versions available for decryption.
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

try:  # Optional enterprise crypto dependency
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover - depends on runtime environment
    AESGCM = None  # type: ignore

try:
    from cryptography.fernet import Fernet, InvalidToken as FernetInvalidToken  # type: ignore
except Exception:  # pragma: no cover - depends on runtime environment
    Fernet = None  # type: ignore
    FernetInvalidToken = Exception  # type: ignore


# =============================================================================
# Exceptions
# =============================================================================


class DecryptionError(Exception):
    """Base decryption error."""


class InvalidCiphertextError(DecryptionError):
    """Raised when ciphertext is malformed or cannot be decoded."""


class IntegrityValidationError(DecryptionError):
    """Raised when authentication tag/MAC validation fails."""


class KeyNotFoundError(DecryptionError):
    """Raised when a required key cannot be found."""


class KeyAccessDeniedError(DecryptionError):
    """Raised when access to a key is denied."""


class UnsupportedAlgorithmError(DecryptionError):
    """Raised when ciphertext uses an unsupported algorithm."""


class DecryptionConfigurationError(DecryptionError):
    """Raised when decryption service is misconfigured."""


class EnvelopeValidationError(DecryptionError):
    """Raised when an encryption envelope is invalid."""


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


class DecryptionEventType(str, Enum):
    DECRYPT_SUCCESS = "security.decryption.success"
    DECRYPT_FAILURE = "security.decryption.failure"
    KEY_RESOLVED = "security.decryption.key_resolved"
    KEY_NOT_FOUND = "security.decryption.key_not_found"
    ENVELOPE_VALIDATION_FAILED = "security.decryption.envelope_validation_failed"
    BATCH_DECRYPT_COMPLETED = "security.decryption.batch_completed"


@dataclass(frozen=True)
class DecryptionConfig:
    """Runtime configuration for decryption behavior."""

    fail_closed: bool = True
    enable_audit: bool = True
    redact_sensitive_audit_fields: bool = True
    require_authenticated_encryption: bool = True
    max_ciphertext_bytes: int = 128 * 1024 * 1024
    default_associated_data: Optional[bytes] = None
    allow_fernet: bool = True
    allow_raw_key_material_export: bool = False
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

    def validate_for_decryption(self) -> None:
        if self.disabled:
            raise KeyAccessDeniedError(f"Key is disabled: {self.key_id}:{self.version}")
        if self.expires_at and self.expires_at <= datetime.now(timezone.utc):
            raise KeyAccessDeniedError(f"Key is expired: {self.key_id}:{self.version}")
        if self.algorithm == EncryptionAlgorithm.AES_256_GCM and len(self.material) != 32:
            raise DecryptionConfigurationError("AES-256-GCM key material must be 32 bytes.")
        if self.algorithm == EncryptionAlgorithm.AES_128_GCM and len(self.material) != 16:
            raise DecryptionConfigurationError("AES-128-GCM key material must be 16 bytes.")
        if self.algorithm == EncryptionAlgorithm.FERNET and not self.material:
            raise DecryptionConfigurationError("Fernet key material cannot be empty.")


@dataclass(frozen=True)
class DecryptionEnvelope:
    """
    Standard encryption envelope used by the decryption service.

    For AES-GCM:
    - ciphertext should contain encrypted bytes without nonce
    - nonce must be provided
    - tag may be separate or appended depending on producer. This module accepts
      both modes via `tag` optional field.

    For Fernet:
    - ciphertext is the full Fernet token
    - nonce/tag are ignored
    """

    algorithm: EncryptionAlgorithm
    ciphertext: bytes
    key_id: str
    key_version: str = "latest"
    nonce: Optional[bytes] = None
    tag: Optional[bytes] = None
    associated_data: Optional[bytes] = None
    encoding: str = "raw"
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: Optional[datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    def validate(self, config: DecryptionConfig) -> None:
        if not self.key_id:
            raise EnvelopeValidationError("key_id is required.")
        if not self.key_version:
            raise EnvelopeValidationError("key_version is required.")
        if not self.ciphertext:
            raise EnvelopeValidationError("ciphertext is required.")
        if len(self.ciphertext) > config.max_ciphertext_bytes:
            raise EnvelopeValidationError("ciphertext exceeds max_ciphertext_bytes.")
        if self.algorithm in {EncryptionAlgorithm.AES_256_GCM, EncryptionAlgorithm.AES_128_GCM}:
            if not self.nonce:
                raise EnvelopeValidationError("nonce is required for AES-GCM.")
            if len(self.nonce) not in {12, 16}:
                raise EnvelopeValidationError("AES-GCM nonce must usually be 12 or 16 bytes.")
        if self.algorithm == EncryptionAlgorithm.FERNET and not config.allow_fernet:
            raise UnsupportedAlgorithmError("Fernet decryption is disabled by configuration.")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecryptionEnvelope":
        """Build an envelope from a JSON-compatible dictionary."""
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
            created_at=parse_datetime(payload.get("created_at")) if payload.get("created_at") else None,
            metadata=dict(payload.get("metadata") or {}),
        )

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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": redact_sensitive(self.metadata),
        }
        if include_ciphertext:
            data["ciphertext"] = encode_bytes(self.ciphertext, encoding)
        else:
            data["ciphertext"] = "***REDACTED***"
        return data


@dataclass(frozen=True)
class DecryptionRequest:
    """Decryption operation request."""

    envelope: DecryptionEnvelope
    requester_id: Optional[str] = None
    tenant_id: Optional[str] = None
    purpose: Optional[str] = None
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    environment: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class DecryptionResult:
    """Decryption operation result."""

    plaintext: bytes
    envelope_id: str
    key_id: str
    key_version: str
    algorithm: EncryptionAlgorithm
    decrypted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = None
    correlation_id: Optional[str] = None
    plaintext_sha256: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def text(self, encoding: str = "utf-8") -> str:
        return self.plaintext.decode(encoding)

    def to_dict(self, include_plaintext: bool = False, plaintext_encoding: str = "base64url") -> JsonDict:
        return {
            "plaintext": encode_bytes(self.plaintext, plaintext_encoding) if include_plaintext else "***REDACTED***",
            "envelope_id": self.envelope_id,
            "key_id": self.key_id,
            "key_version": self.key_version,
            "algorithm": self.algorithm.value,
            "decrypted_at": self.decrypted_at.isoformat(),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "plaintext_sha256": self.plaintext_sha256,
            "metadata": redact_sensitive(self.metadata),
        }


@dataclass(frozen=True)
class DecryptionAuditEvent:
    """Structured audit event for decryption operations."""

    event_type: DecryptionEventType
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
        """Resolve key material for decryption."""


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


class DecryptionAuditSink(ABC):
    """Audit sink abstraction for decryption events."""

    @abstractmethod
    def emit(self, event: DecryptionAuditEvent) -> None:
        """Emit a decryption audit event."""


class LoggingDecryptionAuditSink(DecryptionAuditSink):
    """Logging-based decryption audit sink."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.decryption.audit")
        self.redact = redact

    def emit(self, event: DecryptionAuditEvent) -> None:
        self.audit_logger.info(
            "decryption_event=%s",
            json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str),
        )


# =============================================================================
# Decryptors
# =============================================================================


class CipherDecryptor(ABC):
    """Algorithm-specific decryptor abstraction."""

    @abstractmethod
    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        """Return True if this decryptor supports the algorithm."""

    @abstractmethod
    def decrypt(self, envelope: DecryptionEnvelope, key: KeyMaterial, associated_data: Optional[bytes]) -> bytes:
        """Decrypt ciphertext and return plaintext."""


class AESGCMDecryptor(CipherDecryptor):
    """AES-GCM authenticated decryptor."""

    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        return algorithm in {EncryptionAlgorithm.AES_256_GCM, EncryptionAlgorithm.AES_128_GCM}

    def decrypt(self, envelope: DecryptionEnvelope, key: KeyMaterial, associated_data: Optional[bytes]) -> bytes:
        if AESGCM is None:
            raise UnsupportedAlgorithmError(
                "AES-GCM requires the optional 'cryptography' package. Install cryptography to enable AES-GCM decryption."
            )
        key.validate_for_decryption()
        if not envelope.nonce:
            raise EnvelopeValidationError("AES-GCM nonce is required.")

        ciphertext_with_tag = envelope.ciphertext + envelope.tag if envelope.tag else envelope.ciphertext
        try:
            return AESGCM(key.material).decrypt(envelope.nonce, ciphertext_with_tag, associated_data)
        except Exception as exc:
            raise IntegrityValidationError("AES-GCM authentication failed or ciphertext is invalid.") from exc


class FernetDecryptor(CipherDecryptor):
    """Fernet decryptor."""

    def supports(self, algorithm: EncryptionAlgorithm) -> bool:
        return algorithm == EncryptionAlgorithm.FERNET

    def decrypt(self, envelope: DecryptionEnvelope, key: KeyMaterial, associated_data: Optional[bytes]) -> bytes:
        if Fernet is None:
            raise UnsupportedAlgorithmError(
                "Fernet requires the optional 'cryptography' package. Install cryptography to enable Fernet decryption."
            )
        key.validate_for_decryption()
        try:
            return Fernet(key.material).decrypt(envelope.ciphertext)
        except FernetInvalidToken as exc:
            raise IntegrityValidationError("Fernet token validation failed.") from exc
        except Exception as exc:
            raise InvalidCiphertextError("Fernet ciphertext is invalid.") from exc


# =============================================================================
# Main service
# =============================================================================


class DecryptionService:
    """Enterprise decryption orchestration service."""

    def __init__(
        self,
        key_provider: KeyProvider,
        config: Optional[DecryptionConfig] = None,
        decryptors: Optional[Sequence[CipherDecryptor]] = None,
        audit_sink: Optional[DecryptionAuditSink] = None,
    ) -> None:
        self.config = config or DecryptionConfig()
        self.key_provider = CachingKeyProvider(
            key_provider,
            ttl_seconds=self.config.key_cache_ttl_seconds,
            max_entries=self.config.max_key_cache_entries,
        )
        self.decryptors = tuple(decryptors or (AESGCMDecryptor(), FernetDecryptor()))
        self.audit_sink = audit_sink or LoggingDecryptionAuditSink(redact=self.config.redact_sensitive_audit_fields)

    def decrypt(self, request: DecryptionRequest) -> DecryptionResult:
        """Decrypt a single envelope."""
        try:
            request.envelope.validate(self.config)
            key = self.key_provider.get_key(
                request.envelope.key_id,
                request.envelope.key_version,
                tenant_id=request.tenant_id,
            )
            key.validate_for_decryption()

            self._audit(
                DecryptionEventType.KEY_RESOLVED,
                True,
                "Key resolved for decryption.",
                request=request,
                key=key,
            )

            decryptor = self._select_decryptor(request.envelope.algorithm)
            associated_data = self._resolve_associated_data(request.envelope)
            plaintext = decryptor.decrypt(request.envelope, key, associated_data)
            sha256 = hashlib.sha256(plaintext).hexdigest()

            result = DecryptionResult(
                plaintext=plaintext,
                envelope_id=request.envelope.envelope_id,
                key_id=key.key_id,
                key_version=key.version,
                algorithm=request.envelope.algorithm,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                plaintext_sha256=sha256,
                metadata={
                    "ciphertext_bytes": len(request.envelope.ciphertext),
                    "plaintext_bytes": len(plaintext),
                    "purpose": request.purpose,
                },
            )

            self._audit(
                DecryptionEventType.DECRYPT_SUCCESS,
                True,
                "Decryption completed successfully.",
                request=request,
                key=key,
                metadata={"plaintext_sha256": sha256, "plaintext_bytes": len(plaintext)},
            )
            return result
        except Exception as exc:
            event_type = DecryptionEventType.DECRYPT_FAILURE
            if isinstance(exc, KeyNotFoundError):
                event_type = DecryptionEventType.KEY_NOT_FOUND
            elif isinstance(exc, EnvelopeValidationError):
                event_type = DecryptionEventType.ENVELOPE_VALIDATION_FAILED

            self._audit(
                event_type,
                False,
                str(exc),
                request=request,
                metadata={"error_type": type(exc).__name__},
            )
            logger.exception("Decryption failed. envelope_id=%s request_id=%s", request.envelope.envelope_id, request.request_id)
            if self.config.fail_closed:
                if isinstance(exc, DecryptionError):
                    raise
                raise DecryptionError("Decryption failed.") from exc
            raise

    def decrypt_bytes(
        self,
        ciphertext: bytes,
        key_id: str,
        key_version: str = "latest",
        algorithm: EncryptionAlgorithm = EncryptionAlgorithm.AES_256_GCM,
        nonce: Optional[bytes] = None,
        tag: Optional[bytes] = None,
        associated_data: Optional[bytes] = None,
        requester_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> bytes:
        """Convenience helper for direct byte decryption."""
        envelope = DecryptionEnvelope(
            algorithm=algorithm,
            ciphertext=ciphertext,
            key_id=key_id,
            key_version=key_version,
            nonce=nonce,
            tag=tag,
            associated_data=associated_data,
        )
        return self.decrypt(DecryptionRequest(envelope=envelope, requester_id=requester_id, tenant_id=tenant_id, purpose=purpose)).plaintext

    def decrypt_text(
        self,
        envelope: DecryptionEnvelope,
        encoding: str = "utf-8",
        requester_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> str:
        """Decrypt an envelope and decode plaintext as text."""
        result = self.decrypt(DecryptionRequest(envelope=envelope, requester_id=requester_id, tenant_id=tenant_id, purpose=purpose))
        return result.text(encoding)

    def decrypt_many(self, requests: Sequence[DecryptionRequest], continue_on_error: bool = False) -> Tuple[Union[DecryptionResult, DecryptionError], ...]:
        """Decrypt multiple envelopes with optional partial failure handling."""
        results = []
        failures = 0
        for request in requests:
            try:
                results.append(self.decrypt(request))
            except DecryptionError as exc:
                failures += 1
                if not continue_on_error:
                    raise
                results.append(exc)

        self._audit_raw(
            DecryptionAuditEvent(
                event_type=DecryptionEventType.BATCH_DECRYPT_COMPLETED,
                success=failures == 0,
                reason="Batch decryption completed." if failures == 0 else "Batch decryption completed with failures.",
                metadata={"total": len(requests), "failures": failures},
            )
        )
        return tuple(results)

    def _select_decryptor(self, algorithm: EncryptionAlgorithm) -> CipherDecryptor:
        for decryptor in self.decryptors:
            if decryptor.supports(algorithm):
                return decryptor
        raise UnsupportedAlgorithmError(f"Unsupported decryption algorithm: {algorithm.value}")

    def _resolve_associated_data(self, envelope: DecryptionEnvelope) -> Optional[bytes]:
        if envelope.associated_data is not None:
            return envelope.associated_data
        return self.config.default_associated_data

    def _audit(
        self,
        event_type: DecryptionEventType,
        success: bool,
        reason: str,
        request: DecryptionRequest,
        key: Optional[KeyMaterial] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if not self.config.enable_audit:
            return
        event = DecryptionAuditEvent(
            event_type=event_type,
            success=success,
            reason=reason,
            key_id=key.key_id if key else request.envelope.key_id,
            key_version=key.version if key else request.envelope.key_version,
            algorithm=request.envelope.algorithm,
            envelope_id=request.envelope.envelope_id,
            requester_id=request.requester_id,
            tenant_id=request.tenant_id,
            purpose=request.purpose,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            metadata={
                "envelope_metadata": dict(request.envelope.metadata),
                "environment": dict(request.environment),
                **dict(metadata or {}),
            },
        )
        self._audit_raw(event)

    def _audit_raw(self, event: DecryptionAuditEvent) -> None:
        if not self.config.enable_audit:
            return
        try:
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit decryption audit event.")


# =============================================================================
# Envelope encryption helpers for local tests/dev
# =============================================================================


def encrypt_for_test_only_aes_gcm(
    plaintext: bytes,
    key: bytes,
    associated_data: Optional[bytes] = None,
    key_id: str = "local-test-key",
    key_version: str = "1",
) -> DecryptionEnvelope:
    """
    Test-only AES-GCM encryption helper.

    This function is included so the decryption module can be tested end-to-end.
    Production encryption should live in a dedicated encryption.py module and use
    enterprise-approved key management.
    """
    if AESGCM is None:
        raise UnsupportedAlgorithmError("cryptography is required for AES-GCM test encryption.")
    if len(key) == 32:
        algorithm = EncryptionAlgorithm.AES_256_GCM
    elif len(key) == 16:
        algorithm = EncryptionAlgorithm.AES_128_GCM
    else:
        raise DecryptionConfigurationError("AES-GCM key must be 16 or 32 bytes.")
    nonce = os.urandom(12)
    ciphertext_with_tag = AESGCM(key).encrypt(nonce, plaintext, associated_data)
    return DecryptionEnvelope(
        algorithm=algorithm,
        ciphertext=ciphertext_with_tag,
        key_id=key_id,
        key_version=key_version,
        nonce=nonce,
        associated_data=associated_data,
        metadata={"test_only": True},
    )


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
        raise InvalidCiphertextError("Encoded bytes must be a string or bytes.")
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
        raise InvalidCiphertextError(f"Invalid {encoding} encoded bytes.") from exc
    raise InvalidCiphertextError(f"Unsupported encoding: {encoding}")


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


def constant_time_equals(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_default_decryption_service() -> DecryptionService:
    """Create a local development decryption service with one random AES-256 key."""
    key = KeyMaterial(
        key_id="local-dev-key",
        version="1",
        key_type=KeyType.DATA_KEY,
        algorithm=EncryptionAlgorithm.AES_256_GCM,
        material=os.urandom(32),
        metadata={"environment": "local-dev"},
    )
    return DecryptionService(InMemoryKeyProvider([key]))


__all__ = [
    "AESGCMDecryptor",
    "CachingKeyProvider",
    "CipherDecryptor",
    "DecryptionAuditEvent",
    "DecryptionAuditSink",
    "DecryptionConfig",
    "DecryptionEnvelope",
    "DecryptionError",
    "DecryptionEventType",
    "DecryptionRequest",
    "DecryptionResult",
    "DecryptionService",
    "EncryptionAlgorithm",
    "EnvelopeValidationError",
    "FernetDecryptor",
    "InMemoryKeyProvider",
    "IntegrityValidationError",
    "InvalidCiphertextError",
    "KeyAccessDeniedError",
    "KeyMaterial",
    "KeyNotFoundError",
    "KeyProvider",
    "KeyType",
    "LoggingDecryptionAuditSink",
    "UnsupportedAlgorithmError",
    "constant_time_equals",
    "create_default_decryption_service",
    "decode_bytes",
    "decode_optional_bytes",
    "encode_bytes",
    "encode_optional_bytes",
    "encrypt_for_test_only_aes_gcm",
    "parse_datetime",
    "redact_sensitive",
    "sha256_hex",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if AESGCM is None:
        print("cryptography is not installed; AES-GCM demo skipped.")
    else:
        key_bytes = os.urandom(32)
        key = KeyMaterial(
            key_id="demo-key",
            version="1",
            key_type=KeyType.DATA_KEY,
            algorithm=EncryptionAlgorithm.AES_256_GCM,
            material=key_bytes,
        )
        service = DecryptionService(InMemoryKeyProvider([key]))
        envelope = encrypt_for_test_only_aes_gcm(
            b"enterprise-secret-payload",
            key_bytes,
            associated_data=b"tenant-a:data-export",
            key_id="demo-key",
            key_version="1",
        )
        result = service.decrypt(
            DecryptionRequest(
                envelope=envelope,
                requester_id="user-001",
                tenant_id="tenant-a",
                purpose="local-demo",
                request_id="req-demo",
                correlation_id="corr-demo",
            )
        )
        print(json.dumps(result.to_dict(include_plaintext=False), indent=2, default=str))
        print(result.text())
