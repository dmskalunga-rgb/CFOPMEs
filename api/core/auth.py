#!/usr/bin/env python3
"""
api/core/auth.py

Enterprise-grade core authentication module.

Objetivo:
- Centralizar a configuração e utilitários de segurança/autenticação da API.
- Fornecer primitives reutilizáveis para API Key, Bearer token, JWT, scopes, roles e tenants.
- Evitar duplicação entre api/main.py, api/auth/dependencies.py e routers.
- Operar sem dependências externas obrigatórias além da biblioteca padrão.

Responsabilidades:
- Configuração de auth via ambiente.
- Hash e comparação segura de segredos.
- Parsing de Authorization header.
- Modelo de principal autenticado.
- Validação de scopes/roles.
- Geração de request-id/correlation-id.
- Sanitização de metadados sensíveis.

Variáveis de ambiente:
    API_AUTH_ENABLED=true|false
    API_KEY=chave_unica_opcional
    API_KEYS_JSON={"key":{"subject":"svc","scopes":["admin"],"roles":["service"]}}
    API_JWT_SECRET=secret_hs256_opcional
    API_JWT_ISSUER=enterprise-ai-api
    API_JWT_AUDIENCE=enterprise-ai-clients
    API_REQUIRE_TENANT=false|true
    API_ADMIN_SCOPES=admin,system:admin
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
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


LOGGER = logging.getLogger(__name__)
AUTH_CORE_VERSION = "1.0.0"
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
class AuthSettings:
    auth_enabled: bool = True
    api_key: Optional[str] = None
    api_keys_json: str = ""
    jwt_secret: Optional[str] = None
    jwt_issuer: str = "enterprise-ai-api"
    jwt_audience: str = "enterprise-ai-clients"
    jwt_clock_skew_seconds: int = 60
    require_tenant: bool = False
    admin_scopes: Set[str] = field(default_factory=lambda: {"admin", "system:admin"})
    hash_entity_ids: bool = True

    @staticmethod
    def from_env() -> "AuthSettings":
        return AuthSettings(
            auth_enabled=env_bool("API_AUTH_ENABLED", True),
            api_key=os.getenv("API_KEY"),
            api_keys_json=os.getenv("API_KEYS_JSON", ""),
            jwt_secret=os.getenv("API_JWT_SECRET"),
            jwt_issuer=os.getenv("API_JWT_ISSUER", "enterprise-ai-api"),
            jwt_audience=os.getenv("API_JWT_AUDIENCE", "enterprise-ai-clients"),
            jwt_clock_skew_seconds=env_int("API_JWT_CLOCK_SKEW_SECONDS", 60),
            require_tenant=env_bool("API_REQUIRE_TENANT", False),
            admin_scopes={item.strip() for item in os.getenv("API_ADMIN_SCOPES", "admin,system:admin").split(",") if item.strip()},
            hash_entity_ids=env_bool("API_HASH_ENTITY_IDS", True),
        )

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "auth_core_version": AUTH_CORE_VERSION,
            "auth_enabled": self.auth_enabled,
            "api_key_enabled": bool(self.api_key or self.api_keys_json),
            "jwt_enabled": bool(self.jwt_secret),
            "jwt_issuer_configured": bool(self.jwt_issuer),
            "jwt_audience_configured": bool(self.jwt_audience),
            "require_tenant": self.require_tenant,
            "admin_scopes": sorted(self.admin_scopes),
            "hash_entity_ids": self.hash_entity_ids,
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

    def is_admin(self, settings: Optional[AuthSettings] = None) -> bool:
        admin_scopes = settings.admin_scopes if settings else {"admin", "system:admin"}
        return "admin" in self.roles or bool(set(self.scopes) & admin_scopes) or "*" in self.scopes

    def has_scope(self, scope: str, settings: Optional[AuthSettings] = None) -> bool:
        return scope in self.scopes or self.is_admin(settings)

    def has_any_scope(self, scopes: Iterable[str], settings: Optional[AuthSettings] = None) -> bool:
        return any(self.has_scope(scope, settings) for scope in scopes)

    def has_all_scopes(self, scopes: Iterable[str], settings: Optional[AuthSettings] = None) -> bool:
        return all(self.has_scope(scope, settings) for scope in scopes)

    def has_role(self, role: str, settings: Optional[AuthSettings] = None) -> bool:
        return role in self.roles or self.is_admin(settings)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "principal_type": self.principal_type.value,
            "auth_method": self.auth_method.value,
            "scopes": list(self.scopes),
            "roles": list(self.roles),
            "tenant_id": self.tenant_id,
            "token_id": self.token_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "metadata": sanitize_metadata(self.metadata),
            "is_authenticated": self.is_authenticated,
        }


@dataclass(frozen=True)
class ApiKeyRecord:
    key_hash: str
    subject: str
    scopes: List[str]
    roles: List[str]
    tenant_id: Optional[str] = None
    principal_type: PrincipalType = PrincipalType.SERVICE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_principal(self, key_fingerprint: str) -> Principal:
        return Principal(
            subject=self.subject,
            principal_type=self.principal_type,
            auth_method=AuthMethod.API_KEY,
            scopes=self.scopes,
            roles=self.roles,
            tenant_id=self.tenant_id,
            metadata={**self.metadata, "api_key_fingerprint": key_fingerprint},
        )


@dataclass(frozen=True)
class AuthContext:
    principal: Principal
    request_id: str
    correlation_id: str
    tenant_id: Optional[str]
    client_host: Optional[str] = None
    user_agent_hash: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(tz=DEFAULT_TIMEZONE).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "principal": self.principal.to_dict(),
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "tenant_id": self.tenant_id,
            "client_host": self.client_host,
            "user_agent_hash": self.user_agent_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class JwtValidationResult:
    valid: bool
    principal: Optional[Principal]
    claims: Dict[str, Any]
    error: Optional[str] = None


class AuthError(Exception):
    """Base auth error."""


class AuthenticationError(AuthError):
    """Authentication failed."""


class AuthorizationError(AuthError):
    """Authorization failed."""


class JwtError(AuthError):
    """JWT error."""


class ApiKeyStore:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings
        self.records = self._load_records(settings)

    def authenticate(self, api_key: Optional[str]) -> Optional[Principal]:
        if not api_key:
            return None
        key_hash = sha256_hex(api_key)
        for record_hash, record in self.records.items():
            if constant_time_equal(key_hash, record_hash):
                return record.to_principal(fingerprint(api_key))
        return None

    @staticmethod
    def _load_records(settings: AuthSettings) -> Dict[str, ApiKeyRecord]:
        records: Dict[str, ApiKeyRecord] = {}
        if settings.api_key:
            records[sha256_hex(settings.api_key)] = ApiKeyRecord(
                key_hash=sha256_hex(settings.api_key),
                subject="default-api-key",
                scopes=["*", "admin"],
                roles=["admin"],
                principal_type=PrincipalType.SERVICE,
                metadata={"source": "API_KEY"},
            )
        if settings.api_keys_json:
            try:
                payload = json.loads(settings.api_keys_json)
                if not isinstance(payload, dict):
                    raise ValueError("API_KEYS_JSON precisa ser objeto")
                for raw_key, config in payload.items():
                    if not isinstance(config, dict):
                        config = {"subject": str(config)}
                    principal_type = PrincipalType(str(config.get("principal_type", PrincipalType.SERVICE.value)))
                    key_hash = sha256_hex(str(raw_key))
                    records[key_hash] = ApiKeyRecord(
                        key_hash=key_hash,
                        subject=str(config.get("subject") or "api-key-subject"),
                        scopes=normalize_list(config.get("scopes", [])),
                        roles=normalize_list(config.get("roles", [])),
                        tenant_id=config.get("tenant_id"),
                        principal_type=principal_type,
                        metadata={"source": "API_KEYS_JSON"},
                    )
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("API_KEYS_JSON inválido: %s", exc)
        return records


class JwtCore:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings

    def verify(self, token: str) -> JwtValidationResult:
        try:
            claims = self.verify_claims(token)
            principal = principal_from_claims(claims, token)
            return JwtValidationResult(valid=True, principal=principal, claims=claims)
        except Exception as exc:  # noqa: BLE001
            return JwtValidationResult(valid=False, principal=None, claims={}, error=str(exc))

    def verify_claims(self, token: str) -> Dict[str, Any]:
        if not self.settings.jwt_secret:
            raise JwtError("JWT não configurado")
        header, payload, signature = decode_jwt_unverified(token)
        if header.get("alg") != "HS256":
            raise JwtError("Algoritmo JWT inválido")
        signing_input = ".".join(token.split(".")[:2]).encode("utf-8")
        expected = hmac.new(self.settings.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = base64url_decode(signature)
        if not constant_time_equal_bytes(expected, actual):
            raise JwtError("Assinatura JWT inválida")
        self._validate_registered_claims(payload)
        return dict(payload)

    def create_token(
        self,
        subject: str,
        scopes: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
        tenant_id: Optional[str] = None,
        principal_type: PrincipalType = PrincipalType.USER,
        token_type: TokenType = TokenType.ACCESS,
        ttl_seconds: int = 900,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> str:
        if not self.settings.jwt_secret:
            raise JwtError("JWT secret não configurado")
        if not subject:
            raise JwtError("subject é obrigatório")
        now = int(time.time())
        header = {"typ": "JWT", "alg": "HS256", "ver": AUTH_CORE_VERSION}
        payload = {
            "sub": subject,
            "iss": self.settings.jwt_issuer,
            "aud": self.settings.jwt_audience,
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "jti": f"jti_{uuid.uuid4().hex}",
            "token_type": token_type.value,
            "principal_type": principal_type.value,
            "scope": " ".join(normalize_list(scopes or [])),
            "roles": normalize_list(roles or []),
            "tenant_id": tenant_id,
            "metadata": sanitize_metadata(metadata or {}),
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        signing_input = f"{base64url_json(header)}.{base64url_json(payload)}"
        signature = hmac.new(self.settings.jwt_secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
        return f"{signing_input}.{base64url_encode(signature)}"

    def _validate_registered_claims(self, payload: Mapping[str, Any]) -> None:
        now = int(time.time())
        skew = self.settings.jwt_clock_skew_seconds
        if payload.get("iss") != self.settings.jwt_issuer:
            raise JwtError("Issuer inválido")
        aud = payload.get("aud")
        if isinstance(aud, list):
            audience_ok = self.settings.jwt_audience in aud
        else:
            audience_ok = aud == self.settings.jwt_audience
        if not audience_ok:
            raise JwtError("Audience inválida")
        if int(payload.get("exp", 0)) < now - skew:
            raise JwtError("Token expirado")
        if int(payload.get("nbf", 0)) > now + skew:
            raise JwtError("Token ainda não válido")
        if int(payload.get("iat", 0)) > now + skew:
            raise JwtError("Token emitido no futuro")
        if not payload.get("sub"):
            raise JwtError("Claim sub ausente")


class AuthManager:
    def __init__(self, settings: Optional[AuthSettings] = None) -> None:
        self.settings = settings or AuthSettings.from_env()
        self.api_keys = ApiKeyStore(self.settings)
        self.jwt = JwtCore(self.settings)

    def authenticate(
        self,
        authorization_header: Optional[str] = None,
        api_key: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Principal:
        if not self.settings.auth_enabled:
            return Principal(
                subject="auth-disabled",
                principal_type=PrincipalType.SYSTEM,
                auth_method=AuthMethod.INTERNAL,
                scopes=["*", "admin"],
                roles=["admin"],
                tenant_id=tenant_id,
                metadata={"auth_enabled": False},
            )

        bearer = parse_bearer_token(authorization_header)
        if bearer:
            result = self.jwt.verify(bearer)
            if not result.valid or result.principal is None:
                raise AuthenticationError(result.error or "Bearer token inválido")
            if tenant_id and result.principal.tenant_id and tenant_id != result.principal.tenant_id:
                raise AuthorizationError("Tenant divergente")
            return replace_tenant(result.principal, tenant_id or result.principal.tenant_id)

        principal = self.api_keys.authenticate(api_key)
        if principal:
            if tenant_id and principal.tenant_id and tenant_id != principal.tenant_id:
                raise AuthorizationError("Tenant divergente")
            return replace_tenant(principal, tenant_id or principal.tenant_id)

        raise AuthenticationError("Credencial ausente ou inválida")

    def authorize_scopes(self, principal: Principal, scopes: Sequence[str], require_all: bool = True) -> None:
        if not scopes:
            return
        if principal.is_admin(self.settings):
            return
        allowed = principal.has_all_scopes(scopes, self.settings) if require_all else principal.has_any_scope(scopes, self.settings)
        if not allowed:
            raise AuthorizationError(f"Escopo insuficiente. Requerido: {', '.join(scopes)}")

    def authorize_roles(self, principal: Principal, roles: Sequence[str], require_all: bool = False) -> None:
        if not roles:
            return
        if principal.is_admin(self.settings):
            return
        actual = set(principal.roles)
        required = set(roles)
        allowed = required.issubset(actual) if require_all else bool(actual & required)
        if not allowed:
            raise AuthorizationError(f"Role insuficiente. Requerido: {', '.join(roles)}")

    def build_context(
        self,
        principal: Principal,
        request_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        client_host: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthContext:
        resolved_tenant = tenant_id or principal.tenant_id
        if self.settings.require_tenant and not resolved_tenant:
            raise AuthorizationError("Tenant obrigatório")
        return AuthContext(
            principal=replace_tenant(principal, resolved_tenant),
            request_id=request_id or new_request_id(),
            correlation_id=correlation_id or new_correlation_id(),
            tenant_id=resolved_tenant,
            client_host=client_host,
            user_agent_hash=fingerprint(user_agent) if user_agent else None,
        )


_default_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = AuthManager()
    return _default_manager


def principal_from_claims(claims: Mapping[str, Any], token: str) -> Principal:
    return Principal(
        subject=str(claims.get("sub") or "jwt-subject"),
        principal_type=PrincipalType(str(claims.get("principal_type") or PrincipalType.USER.value)),
        auth_method=AuthMethod.BEARER,
        scopes=extract_scopes(claims),
        roles=extract_roles(claims),
        tenant_id=claims.get("tenant_id") or claims.get("tid"),
        token_id=claims.get("jti"),
        issued_at=int(claims["iat"]) if claims.get("iat") is not None else None,
        expires_at=int(claims["exp"]) if claims.get("exp") is not None else None,
        metadata={
            "issuer": claims.get("iss"),
            "audience": claims.get("aud"),
            "token_fingerprint": fingerprint(token),
        },
    )


def replace_tenant(principal: Principal, tenant_id: Optional[str]) -> Principal:
    return Principal(
        subject=principal.subject,
        principal_type=principal.principal_type,
        auth_method=principal.auth_method,
        scopes=list(principal.scopes),
        roles=list(principal.roles),
        tenant_id=tenant_id,
        token_id=principal.token_id,
        issued_at=principal.issued_at,
        expires_at=principal.expires_at,
        metadata=dict(principal.metadata),
    )


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
        raise JwtError("JWT malformado")
    try:
        header = json.loads(base64url_decode(parts[0]).decode("utf-8"))
        payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise JwtError("JWT inválido") from exc
    return header, payload, parts[2]


def extract_scopes(claims: Mapping[str, Any]) -> List[str]:
    value = claims.get("scope", claims.get("scopes", []))
    if isinstance(value, str):
        return normalize_list(value.replace(",", " ").split())
    return normalize_list(value if isinstance(value, list) else [])


def extract_roles(claims: Mapping[str, Any]) -> List[str]:
    value = claims.get("roles", claims.get("role", []))
    if isinstance(value, str):
        return normalize_list(value.replace(",", " ").split())
    return normalize_list(value if isinstance(value, list) else [])


def normalize_list(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def sanitize_metadata(metadata: Mapping[str, Any], max_items: int = 50) -> Dict[str, Any]:
    sensitive_fragments = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for index, (key, value) in enumerate(metadata.items()):
        if index >= max_items:
            break
        key_text = str(key)
        lower = key_text.lower()
        if any(fragment in lower for fragment in sensitive_fragments):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "y", "s"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Invalid int env %s=%s; using default=%s", name, raw, default)
        return default


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(value: Optional[str], length: int = 16) -> Optional[str]:
    if value is None:
        return None
    return sha256_hex(value)[:length]


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def constant_time_equal_bytes(left: bytes, right: bytes) -> bool:
    return hmac.compare_digest(left, right)


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def base64url_json(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64url_encode(raw)


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_correlation_id() -> str:
    return f"corr_{uuid.uuid4().hex}"


def generate_api_key(prefix: str = "sk") -> str:
    safe_prefix = prefix.strip().replace("_", "-") or "sk"
    return f"{safe_prefix}_{secrets.token_urlsafe(32)}"


def build_error_payload(code: str, message: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or new_request_id(),
            "timestamp": datetime.now(tz=DEFAULT_TIMEZONE).isoformat(),
        }
    }


def auth_public_metadata() -> Dict[str, Any]:
    return get_auth_manager().settings.public_metadata()


__all__ = [
    "AuthMethod",
    "PrincipalType",
    "TokenType",
    "AuthSettings",
    "Principal",
    "ApiKeyRecord",
    "AuthContext",
    "JwtValidationResult",
    "AuthError",
    "AuthenticationError",
    "AuthorizationError",
    "JwtError",
    "ApiKeyStore",
    "JwtCore",
    "AuthManager",
    "get_auth_manager",
    "parse_bearer_token",
    "decode_jwt_unverified",
    "extract_scopes",
    "extract_roles",
    "sanitize_metadata",
    "sha256_hex",
    "fingerprint",
    "new_request_id",
    "new_correlation_id",
    "generate_api_key",
    "build_error_payload",
    "auth_public_metadata",
]
