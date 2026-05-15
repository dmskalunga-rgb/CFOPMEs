# kwanza-ai-core/infrastructure/security.py
from __future__ import annotations

import base64
import contextlib
import dataclasses
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
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence


class AuthScheme(str, Enum):
    BEARER = "bearer"
    API_KEY = "api_key"
    HMAC = "hmac"


class PrincipalType(str, Enum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class SecurityDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class TokenAlgorithm(str, Enum):
    HS256 = "HS256"
    HS384 = "HS384"
    HS512 = "HS512"


@dataclass(frozen=True)
class SecurityConfig:
    secret_key: str = field(default_factory=lambda: os.getenv("SECURITY_SECRET_KEY", "dev-secret-change-me"))
    jwt_algorithm: TokenAlgorithm = TokenAlgorithm.HS256
    jwt_issuer: str = "kwanza-ai-core"
    jwt_audience: str = "kwanza-ai-clients"
    jwt_expiration_minutes: int = 60
    jwt_clock_skew_seconds: int = 30

    api_key_header: str = "X-API-Key"
    allowed_api_keys: tuple[str, ...] = field(default_factory=tuple)

    hmac_header_signature: str = "X-Signature"
    hmac_header_timestamp: str = "X-Timestamp"
    hmac_timestamp_tolerance_seconds: int = 300

    password_min_length: int = 12
    password_pbkdf2_iterations: int = 260_000

    rate_limit_per_minute: int = 120
    enable_rate_limit: bool = True

    max_payload_bytes: int = 5 * 1024 * 1024
    redact_fields: tuple[str, ...] = (
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "private_key",
    )

    def validate(self) -> None:
        if len(self.secret_key) < 32 and os.getenv("APP_ENV") == "production":
            raise SecurityConfigurationError(
                "SECURITY_SECRET_KEY deve ter pelo menos 32 caracteres em produção."
            )


@dataclass(frozen=True)
class Principal:
    id: str
    type: PrincipalType = PrincipalType.USER
    name: Optional[str] = None
    tenant_id: Optional[str] = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    permissions: tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.type != PrincipalType.ANONYMOUS and self.id != "anonymous"

    @staticmethod
    def anonymous() -> "Principal":
        return Principal(id="anonymous", type=PrincipalType.ANONYMOUS)


@dataclass(frozen=True)
class SecurityContext:
    principal: Principal
    auth_scheme: Optional[AuthScheme] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class AuthorizationResult:
    decision: SecurityDecision
    reason: str = ""
    required_permissions: tuple[str, ...] = field(default_factory=tuple)
    missing_permissions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.decision == SecurityDecision.ALLOW


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    issuer: str
    audience: str
    issued_at: int
    expires_at: int
    token_id: str
    tenant_id: Optional[str] = None
    roles: tuple[str, ...] = field(default_factory=tuple)
    permissions: tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SecurityError(RuntimeError):
    pass


class SecurityConfigurationError(SecurityError):
    pass


class AuthenticationError(SecurityError):
    pass


class AuthorizationError(SecurityError):
    pass


class TokenError(AuthenticationError):
    pass


class SignatureError(AuthenticationError):
    pass


class RateLimitError(SecurityError):
    pass


class MetricsSink(Protocol):
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None: ...


class NoopMetricsSink:
    def increment(
        self,
        name: str,
        value: float = 1.0,
        tags: Optional[Mapping[str, str]] = None,
    ) -> None:
        return None


class PasswordHasher:
    def __init__(self, iterations: int = 260_000) -> None:
        self.iterations = iterations

    def hash_password(self, password: str) -> str:
        self._validate_password(password)

        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self.iterations,
        )

        return "pbkdf2_sha256${}${}${}".format(
            self.iterations,
            base64.urlsafe_b64encode(salt).decode("utf-8"),
            base64.urlsafe_b64encode(digest).decode("utf-8"),
        )

    def verify_password(self, password: str, encoded_hash: str) -> bool:
        try:
            algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)

            if algorithm != "pbkdf2_sha256":
                return False

            iterations = int(iterations_raw)
            salt = base64.urlsafe_b64decode(salt_raw.encode("utf-8"))
            expected = base64.urlsafe_b64decode(digest_raw.encode("utf-8"))

            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                iterations,
            )

            return hmac.compare_digest(actual, expected)

        except Exception:
            return False

    def _validate_password(self, password: str) -> None:
        if not password:
            raise AuthenticationError("Password cannot be empty")


class PasswordPolicy:
    def __init__(
        self,
        min_length: int = 12,
        require_uppercase: bool = True,
        require_lowercase: bool = True,
        require_digit: bool = True,
        require_symbol: bool = True,
    ) -> None:
        self.min_length = min_length
        self.require_uppercase = require_uppercase
        self.require_lowercase = require_lowercase
        self.require_digit = require_digit
        self.require_symbol = require_symbol

    def validate(self, password: str) -> list[str]:
        errors: list[str] = []

        if len(password) < self.min_length:
            errors.append(f"Password must have at least {self.min_length} characters")

        if self.require_uppercase and not re.search(r"[A-Z]", password):
            errors.append("Password must contain at least one uppercase letter")

        if self.require_lowercase and not re.search(r"[a-z]", password):
            errors.append("Password must contain at least one lowercase letter")

        if self.require_digit and not re.search(r"\d", password):
            errors.append("Password must contain at least one digit")

        if self.require_symbol and not re.search(r"[^A-Za-z0-9]", password):
            errors.append("Password must contain at least one symbol")

        return errors


class JWTManager:
    def __init__(self, config: SecurityConfig) -> None:
        self.config = config

    def create_token(
        self,
        subject: str,
        *,
        tenant_id: Optional[str] = None,
        roles: Optional[Iterable[str]] = None,
        permissions: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        expires_at = now + (expires_delta or timedelta(minutes=self.config.jwt_expiration_minutes))

        claims = {
            "sub": subject,
            "iss": self.config.jwt_issuer,
            "aud": self.config.jwt_audience,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "jti": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "roles": list(roles or []),
            "permissions": list(permissions or []),
            "metadata": dict(metadata or {}),
        }

        return self._encode(claims)

    def verify_token(self, token: str) -> TokenClaims:
        claims = self._decode(token)

        now = int(datetime.now(timezone.utc).timestamp())

        if claims.get("iss") != self.config.jwt_issuer:
            raise TokenError("Invalid token issuer")

        if claims.get("aud") != self.config.jwt_audience:
            raise TokenError("Invalid token audience")

        exp = int(claims.get("exp", 0))
        if now > exp + self.config.jwt_clock_skew_seconds:
            raise TokenError("Token expired")

        iat = int(claims.get("iat", 0))
        if iat > now + self.config.jwt_clock_skew_seconds:
            raise TokenError("Token issued in the future")

        return TokenClaims(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            audience=str(claims["aud"]),
            issued_at=iat,
            expires_at=exp,
            token_id=str(claims["jti"]),
            tenant_id=claims.get("tenant_id"),
            roles=tuple(claims.get("roles", [])),
            permissions=tuple(claims.get("permissions", [])),
            metadata=dict(claims.get("metadata", {})),
        )

    def principal_from_token(self, token: str) -> Principal:
        claims = self.verify_token(token)

        return Principal(
            id=claims.subject,
            type=PrincipalType.USER,
            tenant_id=claims.tenant_id,
            roles=claims.roles,
            permissions=claims.permissions,
            metadata={
                **claims.metadata,
                "token_id": claims.token_id,
            },
        )

    def _encode(self, payload: Mapping[str, Any]) -> str:
        header = {
            "alg": self.config.jwt_algorithm.value,
            "typ": "JWT",
        }

        header_b64 = b64url_json(header)
        payload_b64 = b64url_json(payload)
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = self._sign(signing_input)

        return f"{header_b64}.{payload_b64}.{signature}"

    def _decode(self, token: str) -> Dict[str, Any]:
        try:
            header_b64, payload_b64, signature = token.split(".", 2)
        except ValueError as exc:
            raise TokenError("Invalid token format") from exc

        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected = self._sign(signing_input)

        if not hmac.compare_digest(signature, expected):
            raise TokenError("Invalid token signature")

        header = json.loads(b64url_decode(header_b64))
        if header.get("alg") != self.config.jwt_algorithm.value:
            raise TokenError("Invalid token algorithm")

        return json.loads(b64url_decode(payload_b64))

    def _sign(self, signing_input: bytes) -> str:
        digest_name = {
            TokenAlgorithm.HS256: "sha256",
            TokenAlgorithm.HS384: "sha384",
            TokenAlgorithm.HS512: "sha512",
        }[self.config.jwt_algorithm]

        digest = hmac.new(
            self.config.secret_key.encode("utf-8"),
            signing_input,
            digest_name,
        ).digest()

        return b64url_bytes(digest)


class ApiKeyAuthenticator:
    def __init__(self, allowed_api_keys: Sequence[str]) -> None:
        self.allowed_hashes = {
            stable_secret_hash(api_key)
            for api_key in allowed_api_keys
            if api_key
        }

    def authenticate(self, api_key: str) -> Principal:
        if not api_key:
            raise AuthenticationError("Missing API key")

        candidate = stable_secret_hash(api_key)

        for allowed in self.allowed_hashes:
            if hmac.compare_digest(candidate, allowed):
                return Principal(
                    id=f"service:{candidate[:12]}",
                    type=PrincipalType.SERVICE,
                    roles=("service",),
                    permissions=("*",),
                )

        raise AuthenticationError("Invalid API key")


class HMACRequestSigner:
    def __init__(self, secret: str, timestamp_tolerance_seconds: int = 300) -> None:
        self.secret = secret
        self.timestamp_tolerance_seconds = timestamp_tolerance_seconds

    def sign(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        timestamp: Optional[int] = None,
    ) -> str:
        ts = timestamp or int(time.time())
        payload = self._canonical_payload(method, path, body, ts)

        digest = hmac.new(
            self.secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return f"t={ts},v1={digest}"

    def verify(
        self,
        signature_header: str,
        method: str,
        path: str,
        body: bytes = b"",
    ) -> bool:
        timestamp, signature = self._parse_signature(signature_header)

        now = int(time.time())
        if abs(now - timestamp) > self.timestamp_tolerance_seconds:
            raise SignatureError("Request signature timestamp outside tolerance")

        expected = self.sign(method, path, body, timestamp=timestamp)
        _, expected_signature = self._parse_signature(expected)

        if not hmac.compare_digest(signature, expected_signature):
            raise SignatureError("Invalid request signature")

        return True

    def _canonical_payload(self, method: str, path: str, body: bytes, timestamp: int) -> bytes:
        body_hash = hashlib.sha256(body or b"").hexdigest()
        canonical = f"{timestamp}.{method.upper()}.{path}.{body_hash}"
        return canonical.encode("utf-8")

    def _parse_signature(self, header: str) -> tuple[int, str]:
        parts = {}

        for item in header.split(","):
            if "=" in item:
                key, value = item.split("=", 1)
                parts[key.strip()] = value.strip()

        if "t" not in parts or "v1" not in parts:
            raise SignatureError("Invalid signature header")

        return int(parts["t"]), parts["v1"]


class Authorizer:
    def authorize(
        self,
        principal: Principal,
        required_permissions: Iterable[str],
        *,
        require_all: bool = True,
    ) -> AuthorizationResult:
        required = tuple(required_permissions)

        if "*" in principal.permissions:
            return AuthorizationResult(SecurityDecision.ALLOW, "Wildcard permission granted", required)

        principal_permissions = set(principal.permissions)

        if require_all:
            missing = tuple(permission for permission in required if permission not in principal_permissions)
            if missing:
                return AuthorizationResult(
                    SecurityDecision.DENY,
                    "Missing required permissions",
                    required_permissions=required,
                    missing_permissions=missing,
                )
        else:
            if not any(permission in principal_permissions for permission in required):
                return AuthorizationResult(
                    SecurityDecision.DENY,
                    "None of the required permissions were granted",
                    required_permissions=required,
                    missing_permissions=required,
                )

        return AuthorizationResult(SecurityDecision.ALLOW, "Permission granted", required)

    def require(
        self,
        principal: Principal,
        permissions: Iterable[str],
        *,
        require_all: bool = True,
    ) -> None:
        result = self.authorize(principal, permissions, require_all=require_all)
        if not result.allowed:
            raise AuthorizationError(result.reason)


class RolePermissionMapper:
    def __init__(self, role_permissions: Optional[Mapping[str, Iterable[str]]] = None) -> None:
        self.role_permissions = {
            role: set(permissions)
            for role, permissions in (role_permissions or {}).items()
        }

    def permissions_for_roles(self, roles: Iterable[str]) -> tuple[str, ...]:
        permissions: set[str] = set()

        for role in roles:
            permissions.update(self.role_permissions.get(role, set()))

        return tuple(sorted(permissions))


class InMemoryRateLimiter:
    def __init__(self, limit_per_minute: int = 120) -> None:
        self.limit = limit_per_minute
        self._buckets: Dict[str, list[float]] = {}

    def check(self, key: str) -> None:
        now = time.time()
        window_start = now - 60

        bucket = [item for item in self._buckets.get(key, []) if item >= window_start]

        if len(bucket) >= self.limit:
            raise RateLimitError("Rate limit exceeded")

        bucket.append(now)
        self._buckets[key] = bucket


class SecurityManager:
    def __init__(
        self,
        config: Optional[SecurityConfig] = None,
        metrics: Optional[MetricsSink] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or SecurityConfig()
        self.config.validate()

        self.metrics = metrics or NoopMetricsSink()
        self.logger = logger or logging.getLogger("kwanza.infrastructure.security")

        self.password_hasher = PasswordHasher(self.config.password_pbkdf2_iterations)
        self.password_policy = PasswordPolicy(self.config.password_min_length)
        self.jwt = JWTManager(self.config)
        self.api_keys = ApiKeyAuthenticator(self.config.allowed_api_keys)
        self.signer = HMACRequestSigner(
            self.config.secret_key,
            self.config.hmac_timestamp_tolerance_seconds,
        )
        self.authorizer = Authorizer()
        self.rate_limiter = InMemoryRateLimiter(self.config.rate_limit_per_minute)

    def authenticate_bearer(self, authorization_header: str) -> SecurityContext:
        token = extract_bearer_token(authorization_header)
        principal = self.jwt.principal_from_token(token)
        self.metrics.increment("security.auth.success", tags={"scheme": "bearer"})
        return SecurityContext(principal=principal, auth_scheme=AuthScheme.BEARER)

    def authenticate_api_key(self, api_key: str) -> SecurityContext:
        principal = self.api_keys.authenticate(api_key)
        self.metrics.increment("security.auth.success", tags={"scheme": "api_key"})
        return SecurityContext(principal=principal, auth_scheme=AuthScheme.API_KEY)

    def authorize(
        self,
        context: SecurityContext,
        permissions: Iterable[str],
        *,
        require_all: bool = True,
    ) -> AuthorizationResult:
        result = self.authorizer.authorize(
            context.principal,
            permissions,
            require_all=require_all,
        )

        self.metrics.increment(
            "security.authorization",
            tags={
                "decision": result.decision.value,
                "principal_type": context.principal.type.value,
            },
        )

        return result

    def require_permissions(
        self,
        context: SecurityContext,
        permissions: Iterable[str],
        *,
        require_all: bool = True,
    ) -> None:
        result = self.authorize(context, permissions, require_all=require_all)
        if not result.allowed:
            raise AuthorizationError(result.reason)

    def enforce_rate_limit(self, key: str) -> None:
        if not self.config.enable_rate_limit:
            return

        self.rate_limiter.check(key)

    def create_access_token(
        self,
        subject: str,
        *,
        tenant_id: Optional[str] = None,
        roles: Optional[Iterable[str]] = None,
        permissions: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        return self.jwt.create_token(
            subject,
            tenant_id=tenant_id,
            roles=roles,
            permissions=permissions,
            metadata=metadata,
        )


def extract_bearer_token(header: str) -> str:
    if not header:
        raise AuthenticationError("Missing Authorization header")

    scheme, _, token = header.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Invalid Authorization header")

    return token.strip()


def b64url_bytes(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def b64url_json(data: Mapping[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return b64url_bytes(raw)


def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def stable_secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_secret_key(length: int = 64) -> str:
    return secrets.token_urlsafe(length)


def generate_api_key(prefix: str = "kwa") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def redact_sensitive_data(
    value: Any,
    *,
    fields: Iterable[str] = SecurityConfig().redact_fields,
    replacement: str = "***",
) -> Any:
    sensitive = {field.lower() for field in fields}

    if dataclasses.is_dataclass(value):
        value = dataclasses.asdict(value)

    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}

        for key, item in value.items():
            key_str = str(key)
            if key_str.lower() in sensitive or any(field in key_str.lower() for field in sensitive):
                result[key_str] = replacement
            else:
                result[key_str] = redact_sensitive_data(item, fields=sensitive, replacement=replacement)

        return result

    if isinstance(value, list):
        return [redact_sensitive_data(item, fields=sensitive, replacement=replacement) for item in value]

    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item, fields=sensitive, replacement=replacement) for item in value)

    return value


def sanitize_string(value: str, max_length: int = 10_000) -> str:
    cleaned = value.replace("\x00", "")
    cleaned = re.sub(r"[\u0000-\u0008\u000B\u000C\u000E-\u001F]", "", cleaned)
    return cleaned[:max_length]


def validate_payload_size(payload: bytes, max_bytes: int) -> None:
    if len(payload or b"") > max_bytes:
        raise SecurityError(f"Payload too large. Maximum allowed: {max_bytes} bytes")


def safe_json_loads(payload: str | bytes, max_bytes: int = 5 * 1024 * 1024) -> Any:
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    validate_payload_size(raw, max_bytes)
    return json.loads(raw.decode("utf-8"))


def build_security_manager_from_env() -> SecurityManager:
    allowed_api_keys = tuple(
        item.strip()
        for item in os.getenv("SECURITY_ALLOWED_API_KEYS", "").split(",")
        if item.strip()
    )

    algorithm = TokenAlgorithm(os.getenv("SECURITY_JWT_ALGORITHM", "HS256"))

    config = SecurityConfig(
        secret_key=os.getenv("SECURITY_SECRET_KEY", "dev-secret-change-me-change-me-32"),
        jwt_algorithm=algorithm,
        jwt_issuer=os.getenv("SECURITY_JWT_ISSUER", "kwanza-ai-core"),
        jwt_audience=os.getenv("SECURITY_JWT_AUDIENCE", "kwanza-ai-clients"),
        jwt_expiration_minutes=int(os.getenv("SECURITY_JWT_EXPIRATION_MINUTES", "60")),
        jwt_clock_skew_seconds=int(os.getenv("SECURITY_JWT_CLOCK_SKEW_SECONDS", "30")),
        api_key_header=os.getenv("SECURITY_API_KEY_HEADER", "X-API-Key"),
        allowed_api_keys=allowed_api_keys,
        hmac_timestamp_tolerance_seconds=int(
            os.getenv("SECURITY_HMAC_TIMESTAMP_TOLERANCE_SECONDS", "300")
        ),
        password_min_length=int(os.getenv("SECURITY_PASSWORD_MIN_LENGTH", "12")),
        password_pbkdf2_iterations=int(os.getenv("SECURITY_PASSWORD_PBKDF2_ITERATIONS", "260000")),
        rate_limit_per_minute=int(os.getenv("SECURITY_RATE_LIMIT_PER_MINUTE", "120")),
        enable_rate_limit=os.getenv("SECURITY_ENABLE_RATE_LIMIT", "true").lower() == "true",
        max_payload_bytes=int(os.getenv("SECURITY_MAX_PAYLOAD_BYTES", str(5 * 1024 * 1024))),
    )

    return SecurityManager(config=config)


def require_permissions(
    security: SecurityManager,
    context: SecurityContext,
    permissions: Iterable[str],
) -> None:
    security.require_permissions(context, permissions)


@contextlib.contextmanager
def security_audit_context(
    logger: logging.Logger,
    action: str,
    context: Optional[SecurityContext] = None,
    metadata: Optional[Mapping[str, Any]] = None,
):
    started = time.monotonic()

    logger.info(
        "Security action started",
        extra={
            "action": action,
            "principal": context.principal.id if context else None,
            "request_id": context.request_id if context else None,
            "metadata": redact_sensitive_data(dict(metadata or {})),
        },
    )

    try:
        yield

        logger.info(
            "Security action completed",
            extra={
                "action": action,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "principal": context.principal.id if context else None,
            },
        )

    except Exception as exc:
        logger.warning(
            "Security action failed",
            extra={
                "action": action,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                "principal": context.principal.id if context else None,
                "error": repr(exc),
            },
        )
        raise