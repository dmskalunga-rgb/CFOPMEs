#!/usr/bin/env python3
"""
core/security/auth.py

Enterprise-grade security authentication and authorization core.

Objetivo:
- Centralizar autenticação/autorização para API, services e jobs.
- Suportar API Key, JWT HS256, roles, scopes, tenant context e service accounts.
- Fornecer primitives reutilizáveis sem acoplar com FastAPI.
- Evitar vazamento de secrets em logs/respostas.

Variáveis de ambiente:
    API_AUTH_ENABLED=true
    API_KEY=...
    API_KEYS_JSON={"key":{"subject":"svc","scopes":["admin"],"roles":["service"]}}
    API_JWT_SECRET=...
    API_JWT_ISSUER=enterprise-ai-api
    API_JWT_AUDIENCE=enterprise-ai-clients
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from core.config.settings import get_settings
except Exception:  # pragma: no cover
    get_settings = None  # type: ignore


AUTH_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class AuthMethod(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    INTERNAL = "internal"


class PrincipalType(str, Enum):
    ANONYMOUS = "anonymous"
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    SERVICE = "service"


@dataclass(frozen=True)
class SecurityAuthConfig:
    auth_enabled: bool = True
    api_key: Optional[str] = None
    api_keys_json: str = ""
    jwt_secret: Optional[str] = None
    jwt_issuer: str = "enterprise-ai-api"
    jwt_audience: str = "enterprise-ai-clients"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 604800
    jwt_clock_skew_seconds: int = 60
    require_tenant: bool = False
    admin_scopes: Set[str] = field(default_factory=lambda: {"admin", "system:admin"})

    @staticmethod
    def from_env() -> "SecurityAuthConfig":
        if get_settings:
            try:
                settings = get_settings()
                return SecurityAuthConfig(
                    auth_enabled=settings.auth.enabled,
                    api_key=settings.auth.api_key,
                    api_keys_json=settings.auth.api_keys_json,
                    jwt_secret=settings.auth.jwt_secret,
                    jwt_issuer=settings.auth.jwt_issuer,
                    jwt_audience=settings.auth.jwt_audience,
                    jwt_access_ttl_seconds=settings.auth.jwt_access_ttl_seconds,
                    jwt_refresh_ttl_seconds=settings.auth.jwt_refresh_ttl_seconds,
                    jwt_clock_skew_seconds=settings.auth.jwt_clock_skew_seconds,
                    require_tenant=settings.auth.require_tenant,
                    admin_scopes=settings.auth.admin_scopes,
                )
            except Exception:
                pass
        return SecurityAuthConfig(
            auth_enabled=env_bool("API_AUTH_ENABLED", True),
            api_key=os.getenv("API_KEY"),
            api_keys_json=os.getenv("API_KEYS_JSON", ""),
            jwt_secret=os.getenv("API_JWT_SECRET"),
            jwt_issuer=os.getenv("API_JWT_ISSUER", "enterprise-ai-api"),
            jwt_audience=os.getenv("API_JWT_AUDIENCE", "enterprise-ai-clients"),
            jwt_access_ttl_seconds=env_int("API_JWT_ACCESS_TTL_SECONDS", 900),
            jwt_refresh_ttl_seconds=env_int("API_JWT_REFRESH_TTL_SECONDS", 604800),
            jwt_clock_skew_seconds=env_int("API_JWT_CLOCK_SKEW_SECONDS", 60),
            require_tenant=env_bool("API_REQUIRE_TENANT", False),
            admin_scopes={item.strip() for item in os.getenv("API_ADMIN_SCOPES", "admin,system:admin").split(",") if item.strip()},
        )

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "auth_version": AUTH_VERSION,
            "auth_enabled": self.auth_enabled,
            "api_key_enabled": bool(self.api_key or self.api_keys_json),
            "jwt_enabled": bool(self.jwt_secret),
            "jwt_issuer": self.jwt_issuer,
            "jwt_audience": self.jwt_audience,
            "require_tenant": self.require_tenant,
            "admin_scopes": sorted(self.admin_scopes),
        }


@dataclass(frozen=True)
class Principal:
    subject: str
    principal_type: PrincipalType
    auth_method: AuthMethod
    scopes: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None
    token_id: Optional[str] = None
    issued_at: Optional[int] = None
    expires_at: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.auth_method != AuthMethod.NONE and self.principal_type != PrincipalType.ANONYMOUS

    def is_admin(self, config: Optional[SecurityAuthConfig] = None) -> bool:
        admin_scopes = config.admin_scopes if config else {"admin", "system:admin"}
        return "admin" in self.roles or "*" in self.scopes or bool(set(self.scopes) & admin_scopes)

    def require_authenticated(self) -> None:
        if not self.is_authenticated:
            raise AuthenticationError("Principal não autenticado")

    def require_scopes(self, scopes: Sequence[str], config: Optional[SecurityAuthConfig] = None, require_all: bool = True) -> None:
        self.require_authenticated()
        if self.is_admin(config):
            return
        required = set(scopes)
        actual = set(self.scopes)
        allowed = required.issubset(actual) if require_all else bool(required & actual)
        if not allowed:
            raise AuthorizationError(f"Escopos insuficientes. Requerido: {', '.join(sorted(required))}")

    def require_roles(self, roles: Sequence[str], config: Optional[SecurityAuthConfig] = None, require_all: bool = False) -> None:
        self.require_authenticated()
        if self.is_admin(config):
            return
        required = set(roles)
        actual = set(self.roles)
        allowed = required.issubset(actual) if require_all else bool(required & actual)
        if not allowed:
            raise AuthorizationError(f"Roles insuficientes. Requerido: {', '.join(sorted(required))}")

    def require_tenant(self, tenant_id: Optional[str]) -> None:
        if not tenant_id:
            raise AuthorizationError("Tenant obrigatório")
        if self.tenant_id and self.tenant_id != tenant_id:
            raise AuthorizationError("Tenant divergente")

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "principal_type": self.principal_type.value,
            "auth_method": self.auth_method.value,
            "scopes": list(self.scopes),
            "roles": list(self.roles),
            "tenant_id": self.tenant_id,
            "token_id_hash": hash_text(self.token_id) if self.token_id else None,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "metadata": sanitize_metadata(self.metadata),
            "is_authenticated": self.is_authenticated,
        }


@dataclass(frozen=True)
class AuthResult:
    principal: Principal
    token_fingerprint: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int


class SecurityAuthError(Exception):
    """Base security auth error."""


class AuthenticationError(SecurityAuthError):
    """Authentication failed."""


class AuthorizationError(SecurityAuthError):
    """Authorization failed."""


class TokenValidationError(SecurityAuthError):
    """Token validation failed."""


class TokenExpiredError(TokenValidationError):
    """Token expired."""


class ApiKeyStore:
    def __init__(self, config: SecurityAuthConfig) -> None:
        self.config = config
        self.records = self._load_records(config)

    def authenticate(self, api_key: Optional[str]) -> Optional[Principal]:
        if not api_key:
            return None
        api_key_hash = hash_text(api_key)
        for stored_hash, record in self.records.items():
            if hmac.compare_digest(api_key_hash, stored_hash):
                return Principal(
                    subject=record["subject"],
                    principal_type=PrincipalType(record.get("principal_type", PrincipalType.SERVICE.value)),
                    auth_method=AuthMethod.API_KEY,
                    scopes=list(record.get("scopes", [])),
                    roles=list(record.get("roles", [])),
                    tenant_id=record.get("tenant_id"),
                    metadata={"api_key_fingerprint": fingerprint(api_key), "source": record.get("source", "api_key")},
                )
        return None

    @staticmethod
    def _load_records(config: SecurityAuthConfig) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        if config.api_key:
            records[hash_text(config.api_key)] = {
                "subject": "default-api-key",
                "principal_type": PrincipalType.SERVICE.value,
                "scopes": ["*", "admin"],
                "roles": ["admin", "service"],
                "source": "API_KEY",
            }
        if config.api_keys_json:
            try:
                payload = json.loads(config.api_keys_json)
                if isinstance(payload, Mapping):
                    for raw_key, item in payload.items():
                        item = dict(item) if isinstance(item, Mapping) else {"subject": str(item)}
                        records[hash_text(str(raw_key))] = {
                            "subject": str(item.get("subject") or "api-key-subject"),
                            "principal_type": str(item.get("principal_type") or PrincipalType.SERVICE.value),
                            "scopes": normalize_list(item.get("scopes", [])),
                            "roles": normalize_list(item.get("roles", [])),
                            "tenant_id": item.get("tenant_id"),
                            "source": "API_KEYS_JSON",
                        }
            except Exception as exc:
                raise AuthenticationError(f"API_KEYS_JSON inválido: {exc}") from exc
        return records


class JwtAuth:
    def __init__(self, config: SecurityAuthConfig) -> None:
        self.config = config

    def create_access_token(
        self,
        subject: str,
        scopes: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
        tenant_id: Optional[str] = None,
        principal_type: PrincipalType = PrincipalType.USER,
        metadata: Optional[Mapping[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        return self._create_token(
            subject=subject,
            token_type=TokenType.ACCESS,
            scopes=scopes or [],
            roles=roles or [],
            tenant_id=tenant_id,
            principal_type=principal_type,
            ttl_seconds=ttl_seconds or self.config.jwt_access_ttl_seconds,
            metadata=metadata or {},
        )

    def create_refresh_token(self, subject: str, tenant_id: Optional[str] = None, principal_type: PrincipalType = PrincipalType.USER) -> str:
        return self._create_token(
            subject=subject,
            token_type=TokenType.REFRESH,
            scopes=["token:refresh"],
            roles=[],
            tenant_id=tenant_id,
            principal_type=principal_type,
            ttl_seconds=self.config.jwt_refresh_ttl_seconds,
            metadata={},
        )

    def create_token_pair(
        self,
        subject: str,
        scopes: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
        tenant_id: Optional[str] = None,
        principal_type: PrincipalType = PrincipalType.USER,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> TokenPair:
        return TokenPair(
            access_token=self.create_access_token(subject, scopes, roles, tenant_id, principal_type, metadata),
            refresh_token=self.create_refresh_token(subject, tenant_id, principal_type),
            token_type="Bearer",
            expires_in=self.config.jwt_access_ttl_seconds,
            refresh_expires_in=self.config.jwt_refresh_ttl_seconds,
        )

    def verify(self, token: str, expected_type: Optional[TokenType] = None) -> Principal:
        if not self.config.jwt_secret:
            raise TokenValidationError("JWT secret não configurado")
        header, payload, signature = decode_jwt_unverified(token)
        if header.get("alg") != "HS256":
            raise TokenValidationError("Algoritmo JWT inválido")
        signing_input = ".".join(token.split(".")[:2]).encode("utf-8")
        expected = hmac.new(self.config.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64url_decode(signature)):
            raise TokenValidationError("Assinatura JWT inválida")
        self._validate_claims(payload, expected_type)
        return principal_from_claims(payload, token)

    def refresh_access_token(self, refresh_token: str, scopes: Optional[Sequence[str]] = None, roles: Optional[Sequence[str]] = None) -> str:
        principal = self.verify(refresh_token, expected_type=TokenType.REFRESH)
        return self.create_access_token(
            subject=principal.subject,
            scopes=scopes or [],
            roles=roles or principal.roles,
            tenant_id=principal.tenant_id,
            principal_type=principal.principal_type,
            metadata={"refreshed": True},
        )

    def _create_token(
        self,
        subject: str,
        token_type: TokenType,
        scopes: Sequence[str],
        roles: Sequence[str],
        tenant_id: Optional[str],
        principal_type: PrincipalType,
        ttl_seconds: int,
        metadata: Mapping[str, Any],
    ) -> str:
        if not self.config.jwt_secret:
            raise TokenValidationError("JWT secret não configurado")
        if not subject or not subject.strip():
            raise TokenValidationError("subject é obrigatório")
        now = int(time.time())
        header = {"typ": "JWT", "alg": "HS256", "ver": AUTH_VERSION}
        payload = {
            "sub": subject.strip(),
            "iss": self.config.jwt_issuer,
            "aud": self.config.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "jti": f"jti_{uuid.uuid4().hex}",
            "token_type": token_type.value,
            "principal_type": principal_type.value,
            "scope": " ".join(normalize_list(scopes)),
            "roles": normalize_list(roles),
            "tenant_id": tenant_id,
            "metadata": sanitize_metadata(metadata),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        signing_input = f"{base64url_json(header)}.{base64url_json(payload)}"
        signature = hmac.new(self.config.jwt_secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
        return f"{signing_input}.{base64url_encode(signature)}"

    def _validate_claims(self, payload: Mapping[str, Any], expected_type: Optional[TokenType]) -> None:
        now = int(time.time())
        skew = self.config.jwt_clock_skew_seconds
        if payload.get("iss") != self.config.jwt_issuer:
            raise TokenValidationError("Issuer inválido")
        aud = payload.get("aud")
        if isinstance(aud, list):
            aud_ok = self.config.jwt_audience in aud
        else:
            aud_ok = aud == self.config.jwt_audience
        if not aud_ok:
            raise TokenValidationError("Audience inválida")
        if int(payload.get("exp", 0)) < now - skew:
            raise TokenExpiredError("Token expirado")
        if int(payload.get("nbf", 0)) > now + skew:
            raise TokenValidationError("Token ainda não válido")
        if int(payload.get("iat", 0)) > now + skew:
            raise TokenValidationError("Token emitido no futuro")
        if not payload.get("sub"):
            raise TokenValidationError("Claim sub ausente")
        if expected_type and payload.get("token_type") != expected_type.value:
            raise TokenValidationError(f"Tipo de token inválido. Esperado={expected_type.value}")


class AuthManager:
    def __init__(self, config: Optional[SecurityAuthConfig] = None) -> None:
        self.config = config or SecurityAuthConfig.from_env()
        self.api_keys = ApiKeyStore(self.config)
        self.jwt = JwtAuth(self.config)

    def authenticate(self, authorization_header: Optional[str] = None, api_key: Optional[str] = None, tenant_id: Optional[str] = None) -> AuthResult:
        if not self.config.auth_enabled:
            return AuthResult(
                principal=Principal(
                    subject="auth-disabled",
                    principal_type=PrincipalType.SYSTEM,
                    auth_method=AuthMethod.INTERNAL,
                    scopes=["*", "admin"],
                    roles=["admin", "system"],
                    tenant_id=tenant_id,
                    metadata={"auth_enabled": False},
                ),
                warnings=["auth_disabled"],
            )

        bearer = parse_bearer_token(authorization_header)
        if bearer:
            principal = self.jwt.verify(bearer)
            principal = with_tenant(principal, tenant_id or principal.tenant_id)
            self._validate_tenant(principal, tenant_id)
            return AuthResult(principal=principal, token_fingerprint=fingerprint(bearer))

        principal = self.api_keys.authenticate(api_key)
        if principal:
            principal = with_tenant(principal, tenant_id or principal.tenant_id)
            self._validate_tenant(principal, tenant_id)
            return AuthResult(principal=principal, token_fingerprint=fingerprint(api_key))

        raise AuthenticationError("Credencial ausente ou inválida")

    def authorize(self, principal: Principal, scopes: Optional[Sequence[str]] = None, roles: Optional[Sequence[str]] = None, tenant_id: Optional[str] = None) -> None:
        principal.require_authenticated()
        if self.config.require_tenant:
            principal.require_tenant(tenant_id or principal.tenant_id)
        if tenant_id:
            principal.require_tenant(tenant_id)
        if scopes:
            principal.require_scopes(scopes, self.config)
        if roles:
            principal.require_roles(roles, self.config)

    def _validate_tenant(self, principal: Principal, requested_tenant: Optional[str]) -> None:
        if self.config.require_tenant and not (requested_tenant or principal.tenant_id):
            raise AuthorizationError("Tenant obrigatório")
        if requested_tenant and principal.tenant_id and requested_tenant != principal.tenant_id:
            raise AuthorizationError("Tenant divergente")


def principal_from_claims(claims: Mapping[str, Any], token: str) -> Principal:
    return Principal(
        subject=str(claims.get("sub") or ""),
        principal_type=PrincipalType(str(claims.get("principal_type") or PrincipalType.USER.value)),
        auth_method=AuthMethod.BEARER,
        scopes=extract_scopes(claims),
        roles=extract_roles(claims),
        tenant_id=claims.get("tenant_id") or claims.get("tid"),
        token_id=claims.get("jti"),
        issued_at=int(claims["iat"]) if claims.get("iat") is not None else None,
        expires_at=int(claims["exp"]) if claims.get("exp") is not None else None,
        metadata={"token_fingerprint": fingerprint(token), "issuer": claims.get("iss")},
    )


def with_tenant(principal: Principal, tenant_id: Optional[str]) -> Principal:
    return dataclasses.replace(principal, tenant_id=tenant_id)


def parse_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None
    parts = authorization_header.strip().split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def decode_jwt_unverified(token: str) -> Tuple[Mapping[str, Any], Mapping[str, Any], str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenValidationError("JWT malformado")
    try:
        header = json.loads(base64url_decode(parts[0]).decode("utf-8"))
        payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:
        raise TokenValidationError("JWT inválido") from exc
    return header, payload, parts[2]


def extract_scopes(claims: Mapping[str, Any]) -> List[str]:
    value = claims.get("scope", claims.get("scopes", []))
    if isinstance(value, str):
        return normalize_list(value.replace(",", " ").split())
    if isinstance(value, list):
        return normalize_list(value)
    return []


def extract_roles(claims: Mapping[str, Any]) -> List[str]:
    value = claims.get("roles", claims.get("role", []))
    if isinstance(value, str):
        return normalize_list(value.replace(",", " ").split())
    if isinstance(value, list):
        return normalize_list(value)
    return []


def normalize_list(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def sanitize_metadata(metadata: Mapping[str, Any], max_items: int = 50) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for index, (key, value) in enumerate(metadata.items()):
        if index >= max_items:
            break
        key_text = str(key)
        lower = key_text.lower()
        if any(item in lower for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def base64url_json(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64url_encode(raw)


def hash_text(value: Optional[str]) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def fingerprint(value: Optional[str], length: int = 16) -> Optional[str]:
    if value is None:
        return None
    return hash_text(value)[:length]


def generate_api_key(prefix: str = "sk") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "s", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


_default_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _default_auth_manager
    if _default_auth_manager is None:
        _default_auth_manager = AuthManager()
    return _default_auth_manager


def reset_auth_manager() -> None:
    global _default_auth_manager
    _default_auth_manager = None


def auth_health() -> Dict[str, Any]:
    manager = get_auth_manager()
    return {"status": "ok", "version": AUTH_VERSION, "config": manager.config.public_metadata(), "checked_at": datetime.now(tz=DEFAULT_TIMEZONE).isoformat()}


__all__ = [
    "AUTH_VERSION",
    "AuthMethod",
    "PrincipalType",
    "TokenType",
    "SecurityAuthConfig",
    "Principal",
    "AuthResult",
    "TokenPair",
    "SecurityAuthError",
    "AuthenticationError",
    "AuthorizationError",
    "TokenValidationError",
    "TokenExpiredError",
    "ApiKeyStore",
    "JwtAuth",
    "AuthManager",
    "parse_bearer_token",
    "principal_from_claims",
    "with_tenant",
    "generate_api_key",
    "get_auth_manager",
    "reset_auth_manager",
    "auth_health",
]
