"""
data/security/authentication.py

Enterprise-grade authentication module for Python services and data platforms.

This module is framework-agnostic and designed to be integrated with FastAPI,
Flask, Django, Celery workers, internal APIs, data pipelines, admin panels,
DataOps platforms, or microservices.

Core capabilities:
- Secure password hashing with PBKDF2-HMAC-SHA256
- Password verification with constant-time comparison
- JWT-like signed access tokens using HMAC-SHA256
- Refresh token issuing, hashing, rotation and revocation
- User status and account lockout controls
- Session creation, validation and revocation
- MFA challenge abstraction with TOTP-compatible verification hook
- Deny/fail-closed authentication behavior
- Structured authentication audit events
- In-memory repositories for local development and tests
- Repository abstractions for database-backed implementations
- Strong typing, validation and explicit error taxonomy

Security notes:
- For production, store secrets in a secret manager or environment variable.
- Replace in-memory repositories with durable database repositories.
- Prefer TLS everywhere.
- Consider replacing the minimal JWT implementation with PyJWT/python-jose if
  your enterprise standard requires JWK, KID rotation, asymmetric keys, OIDC, etc.
"""

from __future__ import annotations

import base64
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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


# =============================================================================
# Exceptions
# =============================================================================


class AuthenticationError(Exception):
    """Base authentication error."""


class InvalidCredentialsError(AuthenticationError):
    """Raised when credentials are invalid."""


class UserNotFoundError(AuthenticationError):
    """Raised when a user does not exist."""


class UserDisabledError(AuthenticationError):
    """Raised when a user account is disabled."""


class UserLockedError(AuthenticationError):
    """Raised when a user account is temporarily locked."""


class TokenError(AuthenticationError):
    """Raised for token generation, decoding, or validation failures."""


class TokenExpiredError(TokenError):
    """Raised when a token is expired."""


class TokenRevokedError(TokenError):
    """Raised when a token has been revoked."""


class MFARequiredError(AuthenticationError):
    """Raised when MFA is required before completing authentication."""


class MFAValidationError(AuthenticationError):
    """Raised when MFA validation fails."""


class SessionError(AuthenticationError):
    """Raised for session-related failures."""


class PolicyViolationError(AuthenticationError):
    """Raised when password/account policy is violated."""


# =============================================================================
# Enums and configuration
# =============================================================================


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"
    PENDING = "pending"
    PASSWORD_RESET_REQUIRED = "password_reset_required"


class AuthEventType(str, Enum):
    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILURE = "auth.login.failure"
    LOGOUT = "auth.logout"
    TOKEN_REFRESH = "auth.token.refresh"
    TOKEN_REVOKE = "auth.token.revoke"
    SESSION_VALIDATE = "auth.session.validate"
    PASSWORD_CHANGED = "auth.password.changed"
    MFA_CHALLENGE_CREATED = "auth.mfa.challenge.created"
    MFA_CHALLENGE_VERIFIED = "auth.mfa.challenge.verified"
    MFA_CHALLENGE_FAILED = "auth.mfa.challenge.failed"
    ACCOUNT_LOCKED = "auth.account.locked"


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True)
class AuthenticationConfig:
    """Runtime configuration for authentication behavior."""

    issuer: str = "enterprise-auth"
    audience: str = "enterprise-services"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14
    session_ttl_seconds: int = 60 * 60 * 8
    password_hash_iterations: int = 210_000
    password_salt_bytes: int = 32
    min_password_length: int = 12
    require_password_digit: bool = True
    require_password_uppercase: bool = True
    require_password_lowercase: bool = True
    require_password_symbol: bool = True
    max_failed_login_attempts: int = 5
    lockout_seconds: int = 900
    refresh_token_bytes: int = 48
    session_id_bytes: int = 32
    access_token_secret: str = field(default_factory=lambda: os.getenv("AUTH_ACCESS_TOKEN_SECRET", "change-me-access-secret"))
    refresh_token_pepper: str = field(default_factory=lambda: os.getenv("AUTH_REFRESH_TOKEN_PEPPER", "change-me-refresh-pepper"))
    fail_closed: bool = True
    redact_sensitive_audit_fields: bool = True

    def validate(self) -> None:
        if self.access_token_secret in {"", "change-me-access-secret"}:
            logger.warning("AUTH_ACCESS_TOKEN_SECRET is using a default value. Replace it in production.")
        if self.refresh_token_pepper in {"", "change-me-refresh-pepper"}:
            logger.warning("AUTH_REFRESH_TOKEN_PEPPER is using a default value. Replace it in production.")
        if self.access_token_ttl_seconds <= 0:
            raise ValueError("access_token_ttl_seconds must be positive.")
        if self.refresh_token_ttl_seconds <= 0:
            raise ValueError("refresh_token_ttl_seconds must be positive.")
        if self.session_ttl_seconds <= 0:
            raise ValueError("session_ttl_seconds must be positive.")


# =============================================================================
# Domain models
# =============================================================================


@dataclass(frozen=True)
class UserIdentity:
    """Authenticated user identity."""

    user_id: str
    username: str
    email: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)


@dataclass
class UserRecord:
    """User record stored by a user repository."""

    user_id: str
    username: str
    password_hash: str
    email: Optional[str] = None
    tenant_id: Optional[str] = None
    status: UserStatus = UserStatus.ACTIVE
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    attributes: JsonDict = field(default_factory=dict)
    mfa_enabled: bool = False
    mfa_secret: Optional[str] = None
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    password_changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = None

    def to_identity(self) -> UserIdentity:
        return UserIdentity(
            user_id=self.user_id,
            username=self.username,
            email=self.email,
            tenant_id=self.tenant_id,
            roles=tuple(self.roles),
            groups=tuple(self.groups),
            attributes=dict(self.attributes),
        )


@dataclass(frozen=True)
class AuthenticatedSession:
    """Server-side session record."""

    session_id: str
    user_id: str
    tenant_id: Optional[str]
    created_at: datetime
    expires_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    revoked_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.revoked_at is None and self.expires_at > now


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Persisted refresh token metadata."""

    token_id: str
    token_hash: str
    user_id: str
    session_id: str
    issued_at: datetime
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    replaced_by_token_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.revoked_at is None and self.expires_at > now


@dataclass(frozen=True)
class MFAChallenge:
    """MFA challenge object."""

    challenge_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    verified_at: Optional[datetime] = None
    metadata: JsonDict = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        now = datetime.now(timezone.utc)
        return self.verified_at is None and self.expires_at > now


@dataclass(frozen=True)
class AuthenticationRequest:
    """Login request payload."""

    username: str
    password: str
    tenant_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)


@dataclass(frozen=True)
class AuthenticationResult:
    """Authentication response payload."""

    authenticated: bool
    identity: Optional[UserIdentity]
    access_token: Optional[str]
    refresh_token: Optional[str]
    session: Optional[AuthenticatedSession]
    mfa_required: bool = False
    mfa_challenge_id: Optional[str] = None
    reason: str = ""


@dataclass(frozen=True)
class TokenClaims:
    """Access token claims."""

    subject: str
    username: str
    issuer: str
    audience: str
    token_type: TokenType
    issued_at: int
    expires_at: int
    token_id: str
    tenant_id: Optional[str] = None
    roles: Tuple[str, ...] = ()
    groups: Tuple[str, ...] = ()
    session_id: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_payload(self) -> JsonDict:
        return {
            "sub": self.subject,
            "username": self.username,
            "iss": self.issuer,
            "aud": self.audience,
            "typ": self.token_type.value,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "jti": self.token_id,
            "tenant_id": self.tenant_id,
            "roles": list(self.roles),
            "groups": list(self.groups),
            "sid": self.session_id,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TokenClaims":
        return cls(
            subject=str(payload["sub"]),
            username=str(payload.get("username", "")),
            issuer=str(payload["iss"]),
            audience=str(payload["aud"]),
            token_type=TokenType(payload.get("typ", TokenType.ACCESS.value)),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
            token_id=str(payload["jti"]),
            tenant_id=payload.get("tenant_id"),
            roles=tuple(payload.get("roles") or ()),
            groups=tuple(payload.get("groups") or ()),
            session_id=payload.get("sid"),
            attributes=dict(payload.get("attributes") or {}),
        )


@dataclass(frozen=True)
class AuthAuditEvent:
    """Structured audit event for authentication flows."""

    event_type: AuthEventType
    success: bool
    reason: str
    user_id: Optional[str] = None
    username: Optional[str] = None
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    token_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self, redact: bool = True) -> JsonDict:
        metadata = dict(self.metadata)
        if redact:
            metadata = redact_sensitive(metadata)
        return {
            "event_type": self.event_type.value,
            "success": self.success,
            "reason": self.reason,
            "user_id": self.user_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "token_id": self.token_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "correlation_id": self.correlation_id,
            "metadata": metadata,
            "timestamp": self.timestamp.isoformat(),
        }


# =============================================================================
# Repositories
# =============================================================================


class UserRepository(ABC):
    """User repository abstraction."""

    @abstractmethod
    def get_by_username(self, username: str, tenant_id: Optional[str] = None) -> Optional[UserRecord]:
        """Return a user by username."""

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        """Return a user by ID."""

    @abstractmethod
    def upsert(self, user: UserRecord) -> None:
        """Create or update a user."""

    @abstractmethod
    def update_login_state(
        self,
        user_id: str,
        failed_attempts: int,
        locked_until: Optional[datetime],
        last_login_at: Optional[datetime],
    ) -> None:
        """Update login state fields after success/failure."""


class SessionRepository(ABC):
    """Session repository abstraction."""

    @abstractmethod
    def create(self, session: AuthenticatedSession) -> None:
        """Persist a session."""

    @abstractmethod
    def get(self, session_id: str) -> Optional[AuthenticatedSession]:
        """Return a session by ID."""

    @abstractmethod
    def revoke(self, session_id: str, revoked_at: Optional[datetime] = None) -> bool:
        """Revoke a session."""


class RefreshTokenRepository(ABC):
    """Refresh token repository abstraction."""

    @abstractmethod
    def create(self, token: RefreshTokenRecord) -> None:
        """Persist a refresh token."""

    @abstractmethod
    def get_by_hash(self, token_hash: str) -> Optional[RefreshTokenRecord]:
        """Return a refresh token by hashed value."""

    @abstractmethod
    def revoke(self, token_id: str, revoked_at: Optional[datetime] = None, replaced_by_token_id: Optional[str] = None) -> bool:
        """Revoke a refresh token."""


class MFAChallengeRepository(ABC):
    """MFA challenge repository abstraction."""

    @abstractmethod
    def create(self, challenge: MFAChallenge) -> None:
        """Persist an MFA challenge."""

    @abstractmethod
    def get(self, challenge_id: str) -> Optional[MFAChallenge]:
        """Return an MFA challenge."""

    @abstractmethod
    def mark_verified(self, challenge_id: str, verified_at: Optional[datetime] = None) -> bool:
        """Mark an MFA challenge as verified."""


class InMemoryUserRepository(UserRepository):
    """Thread-safe in-memory user repository."""

    def __init__(self, users: Optional[Iterable[UserRecord]] = None) -> None:
        self._by_id: Dict[str, UserRecord] = {}
        self._lock = threading.RLock()
        for user in users or []:
            self.upsert(user)

    def get_by_username(self, username: str, tenant_id: Optional[str] = None) -> Optional[UserRecord]:
        normalized = normalize_username(username)
        with self._lock:
            for user in self._by_id.values():
                if normalize_username(user.username) == normalized and (tenant_id is None or user.tenant_id == tenant_id):
                    return dataclasses.replace(user)
        return None

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        with self._lock:
            user = self._by_id.get(user_id)
            return dataclasses.replace(user) if user else None

    def upsert(self, user: UserRecord) -> None:
        with self._lock:
            user.updated_at = datetime.now(timezone.utc)
            self._by_id[user.user_id] = dataclasses.replace(user)

    def update_login_state(
        self,
        user_id: str,
        failed_attempts: int,
        locked_until: Optional[datetime],
        last_login_at: Optional[datetime],
    ) -> None:
        with self._lock:
            user = self._by_id.get(user_id)
            if not user:
                return
            user.failed_login_attempts = failed_attempts
            user.locked_until = locked_until
            user.last_login_at = last_login_at
            user.updated_at = datetime.now(timezone.utc)
            self._by_id[user_id] = dataclasses.replace(user)


class InMemorySessionRepository(SessionRepository):
    """Thread-safe in-memory session repository."""

    def __init__(self) -> None:
        self._sessions: Dict[str, AuthenticatedSession] = {}
        self._lock = threading.RLock()

    def create(self, session: AuthenticatedSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session

    def get(self, session_id: str) -> Optional[AuthenticatedSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def revoke(self, session_id: str, revoked_at: Optional[datetime] = None) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            self._sessions[session_id] = dataclasses.replace(
                session,
                revoked_at=revoked_at or datetime.now(timezone.utc),
            )
            return True


class InMemoryRefreshTokenRepository(RefreshTokenRepository):
    """Thread-safe in-memory refresh token repository."""

    def __init__(self) -> None:
        self._tokens_by_hash: Dict[str, RefreshTokenRecord] = {}
        self._tokens_by_id: Dict[str, RefreshTokenRecord] = {}
        self._lock = threading.RLock()

    def create(self, token: RefreshTokenRecord) -> None:
        with self._lock:
            self._tokens_by_hash[token.token_hash] = token
            self._tokens_by_id[token.token_id] = token

    def get_by_hash(self, token_hash: str) -> Optional[RefreshTokenRecord]:
        with self._lock:
            return self._tokens_by_hash.get(token_hash)

    def revoke(self, token_id: str, revoked_at: Optional[datetime] = None, replaced_by_token_id: Optional[str] = None) -> bool:
        with self._lock:
            token = self._tokens_by_id.get(token_id)
            if not token:
                return False
            updated = dataclasses.replace(
                token,
                revoked_at=revoked_at or datetime.now(timezone.utc),
                replaced_by_token_id=replaced_by_token_id,
            )
            self._tokens_by_id[token_id] = updated
            self._tokens_by_hash[updated.token_hash] = updated
            return True


class InMemoryMFAChallengeRepository(MFAChallengeRepository):
    """Thread-safe in-memory MFA challenge repository."""

    def __init__(self) -> None:
        self._challenges: Dict[str, MFAChallenge] = {}
        self._lock = threading.RLock()

    def create(self, challenge: MFAChallenge) -> None:
        with self._lock:
            self._challenges[challenge.challenge_id] = challenge

    def get(self, challenge_id: str) -> Optional[MFAChallenge]:
        with self._lock:
            return self._challenges.get(challenge_id)

    def mark_verified(self, challenge_id: str, verified_at: Optional[datetime] = None) -> bool:
        with self._lock:
            challenge = self._challenges.get(challenge_id)
            if not challenge:
                return False
            self._challenges[challenge_id] = dataclasses.replace(
                challenge,
                verified_at=verified_at or datetime.now(timezone.utc),
            )
            return True


# =============================================================================
# Audit
# =============================================================================


class AuthAuditSink(ABC):
    """Authentication audit sink abstraction."""

    @abstractmethod
    def emit(self, event: AuthAuditEvent) -> None:
        """Emit an authentication audit event."""


class LoggingAuthAuditSink(AuthAuditSink):
    """Audit sink backed by Python logging."""

    def __init__(self, audit_logger: Optional[logging.Logger] = None, redact: bool = True) -> None:
        self.audit_logger = audit_logger or logging.getLogger("security.authentication.audit")
        self.redact = redact

    def emit(self, event: AuthAuditEvent) -> None:
        self.audit_logger.info(
            "authentication_event=%s",
            json.dumps(event.to_dict(redact=self.redact), sort_keys=True, default=str),
        )


# =============================================================================
# Password service
# =============================================================================


class PasswordHasher:
    """PBKDF2-HMAC-SHA256 password hashing service."""

    ALGORITHM = "pbkdf2_sha256"

    def __init__(self, iterations: int = 210_000, salt_bytes: int = 32) -> None:
        if iterations < 100_000:
            raise ValueError("Password hash iterations are too low for enterprise usage.")
        if salt_bytes < 16:
            raise ValueError("Password salt must be at least 16 bytes.")
        self.iterations = iterations
        self.salt_bytes = salt_bytes

    def hash_password(self, password: str) -> str:
        if not isinstance(password, str) or not password:
            raise PolicyViolationError("Password cannot be empty.")
        salt = secrets.token_bytes(self.salt_bytes)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.iterations)
        return "$".join(
            [
                self.ALGORITHM,
                str(self.iterations),
                b64url_encode(salt),
                b64url_encode(digest),
            ]
        )

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)
            if algorithm != self.ALGORITHM:
                return False
            iterations = int(iterations_raw)
            salt = b64url_decode(salt_raw)
            expected = b64url_decode(digest_raw)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    def needs_rehash(self, encoded_hash: str) -> bool:
        try:
            algorithm, iterations_raw, *_ = encoded_hash.split("$", 3)
            return algorithm != self.ALGORITHM or int(iterations_raw) < self.iterations
        except Exception:
            return True


class PasswordPolicy:
    """Enterprise password complexity policy."""

    def __init__(self, config: AuthenticationConfig) -> None:
        self.config = config

    def validate(self, password: str) -> None:
        errors: List[str] = []
        if len(password or "") < self.config.min_password_length:
            errors.append(f"Password must be at least {self.config.min_password_length} characters long.")
        if self.config.require_password_digit and not any(ch.isdigit() for ch in password):
            errors.append("Password must contain at least one digit.")
        if self.config.require_password_uppercase and not any(ch.isupper() for ch in password):
            errors.append("Password must contain at least one uppercase letter.")
        if self.config.require_password_lowercase and not any(ch.islower() for ch in password):
            errors.append("Password must contain at least one lowercase letter.")
        if self.config.require_password_symbol and not any(not ch.isalnum() for ch in password):
            errors.append("Password must contain at least one symbol.")
        if errors:
            raise PolicyViolationError(" ".join(errors))


# =============================================================================
# Token service
# =============================================================================


class AccessTokenService:
    """Minimal HMAC-SHA256 signed token service with JWT-compatible shape."""

    def __init__(self, config: AuthenticationConfig) -> None:
        self.config = config

    def issue(self, identity: UserIdentity, session_id: Optional[str] = None) -> str:
        now = int(time.time())
        claims = TokenClaims(
            subject=identity.user_id,
            username=identity.username,
            issuer=self.config.issuer,
            audience=self.config.audience,
            token_type=TokenType.ACCESS,
            issued_at=now,
            expires_at=now + self.config.access_token_ttl_seconds,
            token_id=str(uuid.uuid4()),
            tenant_id=identity.tenant_id,
            roles=tuple(identity.roles),
            groups=tuple(identity.groups),
            session_id=session_id,
            attributes=dict(identity.attributes),
        )
        return self.encode(claims.to_payload())

    def encode(self, payload: Mapping[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        header_part = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_part = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8"))
        signing_input = f"{header_part}.{payload_part}".encode("utf-8")
        signature = hmac.new(self.config.access_token_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        return f"{header_part}.{payload_part}.{b64url_encode(signature)}"

    def decode(self, token: str, verify_exp: bool = True) -> TokenClaims:
        try:
            header_part, payload_part, signature_part = token.split(".", 2)
            signing_input = f"{header_part}.{payload_part}".encode("utf-8")
            expected_signature = hmac.new(
                self.config.access_token_secret.encode("utf-8"),
                signing_input,
                hashlib.sha256,
            ).digest()
            actual_signature = b64url_decode(signature_part)
            if not hmac.compare_digest(actual_signature, expected_signature):
                raise TokenError("Invalid token signature.")

            header = json.loads(b64url_decode(header_part))
            if header.get("alg") != "HS256":
                raise TokenError("Unsupported token algorithm.")

            payload = json.loads(b64url_decode(payload_part))
            claims = TokenClaims.from_payload(payload)
            self._validate_claims(claims, verify_exp=verify_exp)
            return claims
        except TokenError:
            raise
        except Exception as exc:
            raise TokenError("Invalid access token.") from exc

    def _validate_claims(self, claims: TokenClaims, verify_exp: bool = True) -> None:
        now = int(time.time())
        if claims.issuer != self.config.issuer:
            raise TokenError("Invalid token issuer.")
        if claims.audience != self.config.audience:
            raise TokenError("Invalid token audience.")
        if claims.token_type != TokenType.ACCESS:
            raise TokenError("Invalid token type.")
        if verify_exp and claims.expires_at <= now:
            raise TokenExpiredError("Access token expired.")


class RefreshTokenService:
    """Opaque refresh token service with hashing and rotation support."""

    def __init__(self, config: AuthenticationConfig, repository: RefreshTokenRepository) -> None:
        self.config = config
        self.repository = repository

    def issue(
        self,
        user_id: str,
        session_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, RefreshTokenRecord]:
        raw_token = secrets.token_urlsafe(self.config.refresh_token_bytes)
        token_hash = self.hash_token(raw_token)
        now = datetime.now(timezone.utc)
        record = RefreshTokenRecord(
            token_id=str(uuid.uuid4()),
            token_hash=token_hash,
            user_id=user_id,
            session_id=session_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.config.refresh_token_ttl_seconds),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.repository.create(record)
        return raw_token, record

    def validate(self, raw_token: str) -> RefreshTokenRecord:
        token_hash = self.hash_token(raw_token)
        record = self.repository.get_by_hash(token_hash)
        if record is None:
            raise TokenError("Invalid refresh token.")
        if record.revoked_at is not None:
            raise TokenRevokedError("Refresh token revoked.")
        if record.expires_at <= datetime.now(timezone.utc):
            raise TokenExpiredError("Refresh token expired.")
        return record

    def rotate(
        self,
        raw_token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[str, RefreshTokenRecord, RefreshTokenRecord]:
        old_record = self.validate(raw_token)
        new_raw, new_record = self.issue(
            user_id=old_record.user_id,
            session_id=old_record.session_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.repository.revoke(old_record.token_id, replaced_by_token_id=new_record.token_id)
        return new_raw, new_record, old_record

    def revoke(self, raw_token: str) -> bool:
        record = self.validate(raw_token)
        return self.repository.revoke(record.token_id)

    def hash_token(self, raw_token: str) -> str:
        return hmac.new(
            self.config.refresh_token_pepper.encode("utf-8"),
            raw_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


# =============================================================================
# MFA
# =============================================================================


class MFAProvider(ABC):
    """MFA provider abstraction."""

    @abstractmethod
    def verify_code(self, secret: str, code: str, at_time: Optional[int] = None) -> bool:
        """Verify an MFA code."""


class TOTPProvider(MFAProvider):
    """
    Minimal TOTP-compatible verifier.

    This implementation expects Base32 secrets and follows RFC 6238 basics.
    It avoids third-party dependencies while remaining compatible with common
    authenticator apps. For stricter enterprise compliance, replace this with
    your approved pyotp/OIDC/WebAuthn provider.
    """

    def __init__(self, interval_seconds: int = 30, digits: int = 6, window: int = 1) -> None:
        self.interval_seconds = interval_seconds
        self.digits = digits
        self.window = window

    def verify_code(self, secret: str, code: str, at_time: Optional[int] = None) -> bool:
        if not code or not code.isdigit():
            return False
        timestamp = int(at_time if at_time is not None else time.time())
        counter = timestamp // self.interval_seconds
        for offset in range(-self.window, self.window + 1):
            expected = self._totp(secret, counter + offset)
            if hmac.compare_digest(expected, code.zfill(self.digits)):
                return True
        return False

    def _totp(self, secret: str, counter: int) -> str:
        key = base64.b32decode(secret.upper() + "=" * ((8 - len(secret) % 8) % 8))
        msg = counter.to_bytes(8, "big")
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = ((digest[offset] & 0x7F) << 24) | ((digest[offset + 1] & 0xFF) << 16) | ((digest[offset + 2] & 0xFF) << 8) | (digest[offset + 3] & 0xFF)
        otp = binary % (10 ** self.digits)
        return str(otp).zfill(self.digits)


# =============================================================================
# Authentication service
# =============================================================================


class AuthenticationService:
    """High-level enterprise authentication orchestration service."""

    def __init__(
        self,
        user_repository: UserRepository,
        session_repository: Optional[SessionRepository] = None,
        refresh_token_repository: Optional[RefreshTokenRepository] = None,
        mfa_challenge_repository: Optional[MFAChallengeRepository] = None,
        config: Optional[AuthenticationConfig] = None,
        password_hasher: Optional[PasswordHasher] = None,
        mfa_provider: Optional[MFAProvider] = None,
        audit_sink: Optional[AuthAuditSink] = None,
    ) -> None:
        self.config = config or AuthenticationConfig()
        self.config.validate()
        self.user_repository = user_repository
        self.session_repository = session_repository or InMemorySessionRepository()
        self.refresh_token_repository = refresh_token_repository or InMemoryRefreshTokenRepository()
        self.mfa_challenge_repository = mfa_challenge_repository or InMemoryMFAChallengeRepository()
        self.password_hasher = password_hasher or PasswordHasher(
            iterations=self.config.password_hash_iterations,
            salt_bytes=self.config.password_salt_bytes,
        )
        self.password_policy = PasswordPolicy(self.config)
        self.access_tokens = AccessTokenService(self.config)
        self.refresh_tokens = RefreshTokenService(self.config, self.refresh_token_repository)
        self.mfa_provider = mfa_provider or TOTPProvider()
        self.audit_sink = audit_sink or LoggingAuthAuditSink(redact=self.config.redact_sensitive_audit_fields)

    def authenticate(self, request: AuthenticationRequest) -> AuthenticationResult:
        """Authenticate a username/password request."""
        username = normalize_username(request.username)
        user: Optional[UserRecord] = None

        try:
            user = self.user_repository.get_by_username(username, tenant_id=request.tenant_id)
            if user is None:
                self._audit(
                    AuthEventType.LOGIN_FAILURE,
                    False,
                    "User not found.",
                    username=username,
                    tenant_id=request.tenant_id,
                    request=request,
                )
                raise InvalidCredentialsError("Invalid username or password.")

            self._assert_user_can_authenticate(user)

            if not self.password_hasher.verify_password(request.password, user.password_hash):
                self._record_failed_login(user)
                self._audit(
                    AuthEventType.LOGIN_FAILURE,
                    False,
                    "Invalid password.",
                    user_id=user.user_id,
                    username=user.username,
                    tenant_id=user.tenant_id,
                    request=request,
                )
                raise InvalidCredentialsError("Invalid username or password.")

            if user.mfa_enabled:
                challenge = self._create_mfa_challenge(user, request)
                self._audit(
                    AuthEventType.MFA_CHALLENGE_CREATED,
                    True,
                    "MFA challenge created.",
                    user_id=user.user_id,
                    username=user.username,
                    tenant_id=user.tenant_id,
                    request=request,
                    metadata={"challenge_id": challenge.challenge_id},
                )
                return AuthenticationResult(
                    authenticated=False,
                    identity=None,
                    access_token=None,
                    refresh_token=None,
                    session=None,
                    mfa_required=True,
                    mfa_challenge_id=challenge.challenge_id,
                    reason="MFA required.",
                )

            result = self._complete_login(user, request)
            self._record_successful_login(user)
            self._audit(
                AuthEventType.LOGIN_SUCCESS,
                True,
                "Login successful.",
                user_id=user.user_id,
                username=user.username,
                tenant_id=user.tenant_id,
                session_id=result.session.session_id if result.session else None,
                request=request,
            )
            return result
        except AuthenticationError:
            raise
        except Exception as exc:
            logger.exception("Unexpected authentication failure for username=%s", username)
            if self.config.fail_closed:
                raise AuthenticationError("Authentication failed.") from exc
            raise

    def verify_mfa_and_complete_login(
        self,
        challenge_id: str,
        code: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AuthenticationResult:
        """Verify MFA challenge and issue tokens/session."""
        challenge = self.mfa_challenge_repository.get(challenge_id)
        if challenge is None or not challenge.is_active:
            self._audit(AuthEventType.MFA_CHALLENGE_FAILED, False, "Invalid or expired MFA challenge.")
            raise MFAValidationError("Invalid or expired MFA challenge.")

        user = self.user_repository.get_by_id(challenge.user_id)
        if user is None:
            self._audit(AuthEventType.MFA_CHALLENGE_FAILED, False, "MFA user not found.")
            raise UserNotFoundError("User not found.")

        self._assert_user_can_authenticate(user)
        if not user.mfa_secret or not self.mfa_provider.verify_code(user.mfa_secret, code):
            self._record_failed_login(user)
            self._audit(
                AuthEventType.MFA_CHALLENGE_FAILED,
                False,
                "Invalid MFA code.",
                user_id=user.user_id,
                username=user.username,
                tenant_id=user.tenant_id,
                ip_address=ip_address,
                user_agent=user_agent,
                correlation_id=correlation_id,
                metadata={"challenge_id": challenge_id},
            )
            raise MFAValidationError("Invalid MFA code.")

        self.mfa_challenge_repository.mark_verified(challenge_id)
        request = AuthenticationRequest(
            username=user.username,
            password="***MFA_COMPLETED***",
            tenant_id=user.tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id,
        )
        result = self._complete_login(user, request)
        self._record_successful_login(user)
        self._audit(
            AuthEventType.MFA_CHALLENGE_VERIFIED,
            True,
            "MFA challenge verified and login completed.",
            user_id=user.user_id,
            username=user.username,
            tenant_id=user.tenant_id,
            session_id=result.session.session_id if result.session else None,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata={"challenge_id": challenge_id},
        )
        return result

    def validate_access_token(self, token: str) -> TokenClaims:
        """Validate an access token and its backing session when present."""
        claims = self.access_tokens.decode(token)
        if claims.session_id:
            session = self.session_repository.get(claims.session_id)
            if session is None or not session.is_active:
                raise SessionError("Session is invalid or expired.")
        return claims

    def refresh(
        self,
        refresh_token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> AuthenticationResult:
        """Rotate a refresh token and issue a new access token."""
        new_refresh_raw, new_refresh_record, old_record = self.refresh_tokens.rotate(
            refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        user = self.user_repository.get_by_id(new_refresh_record.user_id)
        if user is None:
            raise UserNotFoundError("User not found.")
        self._assert_user_can_authenticate(user)

        session = self.session_repository.get(new_refresh_record.session_id)
        if session is None or not session.is_active:
            raise SessionError("Session is invalid or expired.")

        access_token = self.access_tokens.issue(user.to_identity(), session_id=session.session_id)
        self._audit(
            AuthEventType.TOKEN_REFRESH,
            True,
            "Refresh token rotated.",
            user_id=user.user_id,
            username=user.username,
            tenant_id=user.tenant_id,
            session_id=session.session_id,
            token_id=new_refresh_record.token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            correlation_id=correlation_id,
            metadata={"old_token_id": old_record.token_id},
        )
        return AuthenticationResult(
            authenticated=True,
            identity=user.to_identity(),
            access_token=access_token,
            refresh_token=new_refresh_raw,
            session=session,
            reason="Token refreshed.",
        )

    def logout(self, session_id: str, correlation_id: Optional[str] = None) -> bool:
        """Revoke a server-side session."""
        session = self.session_repository.get(session_id)
        success = self.session_repository.revoke(session_id)
        self._audit(
            AuthEventType.LOGOUT,
            success,
            "Logout processed." if success else "Session not found.",
            user_id=session.user_id if session else None,
            tenant_id=session.tenant_id if session else None,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return success

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        """Change a user's password after validating the old password."""
        user = self.user_repository.get_by_id(user_id)
        if not user:
            raise UserNotFoundError("User not found.")
        self._assert_user_can_authenticate(user)
        if not self.password_hasher.verify_password(old_password, user.password_hash):
            raise InvalidCredentialsError("Invalid current password.")
        self.password_policy.validate(new_password)
        user.password_hash = self.password_hasher.hash_password(new_password)
        user.password_changed_at = datetime.now(timezone.utc)
        user.updated_at = datetime.now(timezone.utc)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.user_repository.upsert(user)
        self._audit(
            AuthEventType.PASSWORD_CHANGED,
            True,
            "Password changed.",
            user_id=user.user_id,
            username=user.username,
            tenant_id=user.tenant_id,
        )

    def create_user(
        self,
        username: str,
        password: str,
        email: Optional[str] = None,
        tenant_id: Optional[str] = None,
        roles: Sequence[str] = (),
        groups: Sequence[str] = (),
        attributes: Optional[Mapping[str, Any]] = None,
        status: UserStatus = UserStatus.ACTIVE,
        mfa_enabled: bool = False,
        mfa_secret: Optional[str] = None,
    ) -> UserRecord:
        """Create a new user record using configured password policy and hasher."""
        self.password_policy.validate(password)
        user = UserRecord(
            user_id=str(uuid.uuid4()),
            username=normalize_username(username),
            email=email,
            tenant_id=tenant_id,
            password_hash=self.password_hasher.hash_password(password),
            status=status,
            roles=tuple(roles),
            groups=tuple(groups),
            attributes=dict(attributes or {}),
            mfa_enabled=mfa_enabled,
            mfa_secret=mfa_secret,
        )
        self.user_repository.upsert(user)
        return user

    def _complete_login(self, user: UserRecord, request: AuthenticationRequest) -> AuthenticationResult:
        identity = user.to_identity()
        now = datetime.now(timezone.utc)
        session = AuthenticatedSession(
            session_id=secrets.token_urlsafe(self.config.session_id_bytes),
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self.config.session_ttl_seconds),
            ip_address=request.ip_address,
            user_agent=request.user_agent,
            metadata=dict(request.metadata),
        )
        self.session_repository.create(session)
        access_token = self.access_tokens.issue(identity, session_id=session.session_id)
        refresh_token, _ = self.refresh_tokens.issue(
            user_id=user.user_id,
            session_id=session.session_id,
            ip_address=request.ip_address,
            user_agent=request.user_agent,
        )
        return AuthenticationResult(
            authenticated=True,
            identity=identity,
            access_token=access_token,
            refresh_token=refresh_token,
            session=session,
            reason="Authentication successful.",
        )

    def _create_mfa_challenge(self, user: UserRecord, request: AuthenticationRequest) -> MFAChallenge:
        now = datetime.now(timezone.utc)
        challenge = MFAChallenge(
            challenge_id=str(uuid.uuid4()),
            user_id=user.user_id,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            metadata={
                "ip_address": request.ip_address,
                "user_agent": request.user_agent,
                "correlation_id": request.correlation_id,
            },
        )
        self.mfa_challenge_repository.create(challenge)
        return challenge

    def _assert_user_can_authenticate(self, user: UserRecord) -> None:
        now = datetime.now(timezone.utc)
        if user.status == UserStatus.DISABLED:
            raise UserDisabledError("User account is disabled.")
        if user.status == UserStatus.PENDING:
            raise UserDisabledError("User account is pending activation.")
        if user.status == UserStatus.PASSWORD_RESET_REQUIRED:
            raise PolicyViolationError("Password reset is required.")
        if user.status == UserStatus.LOCKED:
            raise UserLockedError("User account is locked.")
        if user.locked_until and user.locked_until > now:
            raise UserLockedError("User account is temporarily locked.")

    def _record_failed_login(self, user: UserRecord) -> None:
        failed_attempts = user.failed_login_attempts + 1
        locked_until = user.locked_until
        if failed_attempts >= self.config.max_failed_login_attempts:
            locked_until = datetime.now(timezone.utc) + timedelta(seconds=self.config.lockout_seconds)
            self._audit(
                AuthEventType.ACCOUNT_LOCKED,
                True,
                "Account temporarily locked after failed attempts.",
                user_id=user.user_id,
                username=user.username,
                tenant_id=user.tenant_id,
                metadata={"failed_login_attempts": failed_attempts, "locked_until": locked_until.isoformat()},
            )
        self.user_repository.update_login_state(
            user.user_id,
            failed_attempts=failed_attempts,
            locked_until=locked_until,
            last_login_at=user.last_login_at,
        )

    def _record_successful_login(self, user: UserRecord) -> None:
        self.user_repository.update_login_state(
            user.user_id,
            failed_attempts=0,
            locked_until=None,
            last_login_at=datetime.now(timezone.utc),
        )

    def _audit(
        self,
        event_type: AuthEventType,
        success: bool,
        reason: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
        token_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        correlation_id: Optional[str] = None,
        request: Optional[AuthenticationRequest] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        try:
            event = AuthAuditEvent(
                event_type=event_type,
                success=success,
                reason=reason,
                user_id=user_id,
                username=username,
                tenant_id=tenant_id,
                session_id=session_id,
                token_id=token_id,
                ip_address=ip_address or (request.ip_address if request else None),
                user_agent=user_agent or (request.user_agent if request else None),
                correlation_id=correlation_id or (request.correlation_id if request else None),
                metadata=dict(metadata or {}),
            )
            self.audit_sink.emit(event)
        except Exception:
            logger.exception("Failed to emit authentication audit event.")


# =============================================================================
# Utility functions
# =============================================================================


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def generate_mfa_secret(length: int = 20) -> str:
    """Generate a Base32 MFA secret compatible with TOTP apps."""
    return base64.b32encode(secrets.token_bytes(length)).decode("ascii").rstrip("=")


def redact_sensitive(data: Mapping[str, Any]) -> JsonDict:
    sensitive_terms = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "private_key",
        "mfa",
    )

    def walk(value: Any) -> Any:
        if isinstance(value, Mapping):
            output: JsonDict = {}
            for key, item in value.items():
                key_str = str(key)
                if any(term in key_str.lower() for term in sensitive_terms):
                    output[key_str] = "***REDACTED***"
                else:
                    output[key_str] = walk(item)
            return output
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(walk(item) for item in value)
        return value

    return walk(dict(data))


def user_to_public_dict(user: UserRecord) -> JsonDict:
    """Return a non-sensitive user representation."""
    return {
        "user_id": user.user_id,
        "username": user.username,
        "email": user.email,
        "tenant_id": user.tenant_id,
        "status": user.status.value,
        "roles": list(user.roles),
        "groups": list(user.groups),
        "attributes": redact_sensitive(user.attributes),
        "mfa_enabled": user.mfa_enabled,
        "failed_login_attempts": user.failed_login_attempts,
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "password_changed_at": user.password_changed_at.isoformat(),
        "created_at": user.created_at.isoformat(),
        "updated_at": user.updated_at.isoformat(),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def create_default_authentication_service() -> AuthenticationService:
    """Create a local in-memory authentication service for development/tests."""
    config = AuthenticationConfig()
    user_repo = InMemoryUserRepository()
    service = AuthenticationService(user_repository=user_repo, config=config)
    service.create_user(
        username="admin",
        password="Admin@123456789",
        email="admin@example.com",
        tenant_id="default",
        roles=("admin",),
        groups=("security",),
        attributes={"clearance_level": 10},
    )
    return service


__all__ = [
    "AccessTokenService",
    "AuthAuditEvent",
    "AuthAuditSink",
    "AuthEventType",
    "AuthenticatedSession",
    "AuthenticationConfig",
    "AuthenticationError",
    "AuthenticationRequest",
    "AuthenticationResult",
    "AuthenticationService",
    "InMemoryMFAChallengeRepository",
    "InMemoryRefreshTokenRepository",
    "InMemorySessionRepository",
    "InMemoryUserRepository",
    "InvalidCredentialsError",
    "LoggingAuthAuditSink",
    "MFAChallenge",
    "MFAChallengeRepository",
    "MFAProvider",
    "MFARequiredError",
    "MFAValidationError",
    "PasswordHasher",
    "PasswordPolicy",
    "PolicyViolationError",
    "RefreshTokenRecord",
    "RefreshTokenRepository",
    "RefreshTokenService",
    "SessionError",
    "SessionRepository",
    "TOTPProvider",
    "TokenClaims",
    "TokenError",
    "TokenExpiredError",
    "TokenRevokedError",
    "TokenType",
    "UserDisabledError",
    "UserIdentity",
    "UserLockedError",
    "UserNotFoundError",
    "UserRecord",
    "UserRepository",
    "UserStatus",
    "b64url_decode",
    "b64url_encode",
    "create_default_authentication_service",
    "generate_mfa_secret",
    "normalize_username",
    "redact_sensitive",
    "user_to_public_dict",
]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    auth = create_default_authentication_service()
    result = auth.authenticate(
        AuthenticationRequest(
            username="admin",
            password="Admin@123456789",
            tenant_id="default",
            ip_address="127.0.0.1",
            user_agent="local-dev",
            correlation_id="demo-correlation-id",
        )
    )

    print(json.dumps({
        "authenticated": result.authenticated,
        "user": dataclasses.asdict(result.identity) if result.identity else None,
        "access_token_preview": result.access_token[:32] + "..." if result.access_token else None,
        "refresh_token_preview": result.refresh_token[:16] + "..." if result.refresh_token else None,
        "session_id": result.session.session_id if result.session else None,
        "reason": result.reason,
    }, indent=2, default=str))
