#!/usr/bin/env python3
"""
api/auth/dependencies.py

Enterprise-grade authentication and authorization dependencies for FastAPI.

Objetivo:
- Centralizar dependências de autenticação/autorização para a API.
- Suportar API Key, Bearer token/JWT opcional, scopes, roles e permissões por rota.
- Fornecer CurrentUser padronizado, request context, tenant context e helpers reutilizáveis.
- Evitar dependências externas obrigatórias além de FastAPI/Pydantic.
- Permitir endurecimento progressivo via variáveis de ambiente.

Variáveis de ambiente:
    API_AUTH_ENABLED=true|false
    API_KEY=valor_unico_opcional
    API_KEYS_JSON={"key1":{"subject":"svc-a","scopes":["inference:read"]}}
    API_JWT_SECRET=secret_opcional_para_hs256
    API_JWT_ISSUER=issuer_opcional
    API_JWT_AUDIENCE=audience_opcional
    API_REQUIRE_TENANT=false|true
    API_ADMIN_SCOPES=admin,system:admin

Uso em rotas:
    from fastapi import APIRouter, Depends
    from api.auth.dependencies import require_scopes, get_current_user

    @router.get("/secure", dependencies=[Depends(require_scopes("inference:read"))])
    async def secure_route(user = Depends(get_current_user)):
        return {"subject": user.subject}

Notas:
- JWT HS256 é validado sem PyJWT para reduzir dependência. Para produção avançada com JWKS/OIDC,
  substitua JwtVerifier por integração oficial do seu IdP.
- Nunca faça log de tokens ou API keys em texto puro.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

try:
    from fastapi import Depends, Header, HTTPException, Request, Security, status
    from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependências ausentes. Instale com: pip install fastapi pydantic") from exc


LOGGER = logging.getLogger(__name__)

AUTH_ENABLED = os.getenv("API_AUTH_ENABLED", "true").lower() in {"1", "true", "yes", "sim"}
DEFAULT_API_KEY = os.getenv("API_KEY")
API_KEYS_JSON = os.getenv("API_KEYS_JSON", "")
JWT_SECRET = os.getenv("API_JWT_SECRET")
JWT_ISSUER = os.getenv("API_JWT_ISSUER")
JWT_AUDIENCE = os.getenv("API_JWT_AUDIENCE")
REQUIRE_TENANT = os.getenv("API_REQUIRE_TENANT", "false").lower() in {"1", "true", "yes", "sim"}
ADMIN_SCOPES = {item.strip() for item in os.getenv("API_ADMIN_SCOPES", "admin,system:admin").split(",") if item.strip()}

API_KEY_HEADER_NAME = "x-api-key"
REQUEST_ID_HEADER_NAME = "x-request-id"
TENANT_HEADER_NAME = "x-tenant-id"

api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


class AuthMethod(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    INTERNAL = "internal"


class PrincipalType(str, Enum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class ApiKeyPrincipal:
    subject: str
    scopes: List[str]
    roles: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None
    principal_type: PrincipalType = PrincipalType.SERVICE
    metadata: Dict[str, Any] = field(default_factory=dict)


class CurrentUser(BaseModel):
    subject: str
    principal_type: str = PrincipalType.USER.value
    auth_method: str = AuthMethod.NONE.value
    scopes: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    token_id: Optional[str] = None
    issued_at: Optional[int] = None
    expires_at: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        return self.auth_method != AuthMethod.NONE.value and self.subject != "anonymous"

    @property
    def is_admin(self) -> bool:
        return bool(set(self.scopes) & ADMIN_SCOPES or "admin" in self.roles)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or self.is_admin

    def has_any_scope(self, scopes: Iterable[str]) -> bool:
        return any(self.has_scope(scope) for scope in scopes)

    def has_all_scopes(self, scopes: Iterable[str]) -> bool:
        return all(self.has_scope(scope) for scope in scopes)


class RequestContext(BaseModel):
    request_id: str
    tenant_id: Optional[str] = None
    user: CurrentUser
    path: str
    method: str
    client_host: Optional[str] = None
    started_at: str = Field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


class AuthError(Exception):
    """Base auth error."""


class TokenValidationError(AuthError):
    """JWT/token validation failure."""


class ApiKeyValidationError(AuthError):
    """API key validation failure."""


class PermissionDeniedError(AuthError):
    """Permission/scopes validation failure."""


class ApiKeyStore:
    """In-memory API key store loaded from environment."""

    def __init__(self) -> None:
        self._keys = self._load_keys()

    def lookup(self, provided_key: str) -> Optional[ApiKeyPrincipal]:
        if not provided_key:
            return None
        provided_hash = sha256_hex(provided_key)
        for key_hash, principal in self._keys.items():
            if hmac.compare_digest(provided_hash, key_hash):
                return principal
        return None

    def _load_keys(self) -> Dict[str, ApiKeyPrincipal]:
        keys: Dict[str, ApiKeyPrincipal] = {}
        if DEFAULT_API_KEY:
            keys[sha256_hex(DEFAULT_API_KEY)] = ApiKeyPrincipal(
                subject="default-api-key",
                scopes=["*", "admin"],
                roles=["admin"],
                principal_type=PrincipalType.SERVICE,
                metadata={"source": "API_KEY"},
            )
        if API_KEYS_JSON:
            try:
                payload = json.loads(API_KEYS_JSON)
                if not isinstance(payload, dict):
                    raise ValueError("API_KEYS_JSON precisa ser objeto JSON")
                for raw_key, config in payload.items():
                    if not isinstance(config, dict):
                        config = {"subject": str(config)}
                    keys[sha256_hex(str(raw_key))] = ApiKeyPrincipal(
                        subject=str(config.get("subject") or "api-key-subject"),
                        scopes=[str(item) for item in config.get("scopes", [])],
                        roles=[str(item) for item in config.get("roles", [])],
                        tenant_id=config.get("tenant_id"),
                        principal_type=PrincipalType(config.get("principal_type", PrincipalType.SERVICE.value)),
                        metadata={"source": "API_KEYS_JSON"},
                    )
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("API_KEYS_JSON inválido: %s", exc)
        return keys


class JwtVerifier:
    """Minimal HS256 JWT verifier without external dependencies."""

    def __init__(self, secret: Optional[str], issuer: Optional[str] = None, audience: Optional[str] = None) -> None:
        self.secret = secret
        self.issuer = issuer
        self.audience = audience

    def verify(self, token: str) -> Mapping[str, Any]:
        if not self.secret:
            raise TokenValidationError("JWT não configurado")
        header, payload, signature = self._split(token)
        algorithm = header.get("alg")
        if algorithm != "HS256":
            raise TokenValidationError("Algoritmo JWT não suportado")
        signing_input = ".".join(token.split(".")[:2]).encode("utf-8")
        expected = hmac.new(self.secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = base64url_decode(signature)
        if not hmac.compare_digest(expected, actual):
            raise TokenValidationError("Assinatura JWT inválida")
        self._validate_claims(payload)
        return payload

    @staticmethod
    def _split(token: str) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenValidationError("JWT malformado")
        try:
            header = json.loads(base64url_decode(parts[0]).decode("utf-8"))
            payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise TokenValidationError("JWT inválido") from exc
        return header, payload, parts[2]

    def _validate_claims(self, payload: Mapping[str, Any]) -> None:
        now = int(time.time())
        exp = payload.get("exp")
        nbf = payload.get("nbf")
        iat = payload.get("iat")
        if exp is not None and int(exp) < now:
            raise TokenValidationError("JWT expirado")
        if nbf is not None and int(nbf) > now:
            raise TokenValidationError("JWT ainda não válido")
        if iat is not None and int(iat) > now + 60:
            raise TokenValidationError("JWT emitido no futuro")
        if self.issuer and payload.get("iss") != self.issuer:
            raise TokenValidationError("Issuer inválido")
        if self.audience:
            aud = payload.get("aud")
            valid = self.audience in aud if isinstance(aud, list) else aud == self.audience
            if not valid:
                raise TokenValidationError("Audience inválida")


api_key_store = ApiKeyStore()
jwt_verifier = JwtVerifier(JWT_SECRET, JWT_ISSUER, JWT_AUDIENCE)


async def get_optional_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    return api_key


async def get_optional_bearer(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> Optional[str]:
    if credentials is None:
        return None
    return credentials.credentials


async def get_current_user(
    request: Request,
    api_key: Optional[str] = Depends(get_optional_api_key),
    bearer_token: Optional[str] = Depends(get_optional_bearer),
    x_tenant_id: Optional[str] = Header(default=None, alias=TENANT_HEADER_NAME),
) -> CurrentUser:
    """Resolve o usuário atual usando Bearer JWT, API Key ou anonymous quando auth desabilitado."""

    if not AUTH_ENABLED:
        return CurrentUser(
            subject="auth-disabled",
            principal_type=PrincipalType.SYSTEM.value,
            auth_method=AuthMethod.INTERNAL.value,
            scopes=["*", "admin"],
            roles=["admin"],
            tenant_id=x_tenant_id,
            metadata={"auth_enabled": False},
        )

    if bearer_token:
        try:
            return user_from_jwt(bearer_token, x_tenant_id)
        except TokenValidationError as exc:
            raise unauthorized(str(exc)) from exc

    if api_key:
        principal = api_key_store.lookup(api_key)
        if principal is None:
            raise unauthorized("API key inválida")
        tenant_id = x_tenant_id or principal.tenant_id
        return CurrentUser(
            subject=principal.subject,
            principal_type=principal.principal_type.value,
            auth_method=AuthMethod.API_KEY.value,
            scopes=principal.scopes,
            roles=principal.roles,
            tenant_id=tenant_id,
            metadata={**principal.metadata, "api_key_hash": sha256_hex(api_key)[:12]},
        )

    raise unauthorized("Credencial ausente")


async def get_optional_current_user(
    request: Request,
    api_key: Optional[str] = Depends(get_optional_api_key),
    bearer_token: Optional[str] = Depends(get_optional_bearer),
    x_tenant_id: Optional[str] = Header(default=None, alias=TENANT_HEADER_NAME),
) -> CurrentUser:
    try:
        return await get_current_user(request, api_key, bearer_token, x_tenant_id)
    except HTTPException:
        return CurrentUser(
            subject="anonymous",
            principal_type=PrincipalType.ANONYMOUS.value,
            auth_method=AuthMethod.NONE.value,
            scopes=[],
            roles=[],
            tenant_id=x_tenant_id,
        )


async def get_request_context(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    x_request_id: Optional[str] = Header(default=None, alias=REQUEST_ID_HEADER_NAME),
    x_tenant_id: Optional[str] = Header(default=None, alias=TENANT_HEADER_NAME),
) -> RequestContext:
    tenant_id = x_tenant_id or user.tenant_id
    if REQUIRE_TENANT and not tenant_id:
        raise forbidden("Tenant obrigatório")
    client_host = request.client.host if request.client else None
    request_id = x_request_id or getattr(request.state, "request_id", None) or f"req_{uuid.uuid4().hex}"
    return RequestContext(
        request_id=request_id,
        tenant_id=tenant_id,
        user=user,
        path=request.url.path,
        method=request.method,
        client_host=client_host,
    )


def require_scopes(*required_scopes: str, require_all: bool = True) -> Callable[[CurrentUser], CurrentUser]:
    """Cria dependência que exige scopes."""

    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not required_scopes:
            return user
        if "*" in user.scopes or user.is_admin:
            return user
        required = [scope for scope in required_scopes if scope]
        allowed = user.has_all_scopes(required) if require_all else user.has_any_scope(required)
        if not allowed:
            raise forbidden(f"Escopo insuficiente. Requerido: {', '.join(required)}")
        return user

    return dependency


def require_any_scope(*required_scopes: str) -> Callable[[CurrentUser], CurrentUser]:
    return require_scopes(*required_scopes, require_all=False)


def require_roles(*required_roles: str, require_all: bool = False) -> Callable[[CurrentUser], CurrentUser]:
    """Cria dependência que exige roles."""

    async def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "admin" in user.roles or user.is_admin:
            return user
        roles = set(user.roles)
        required = {role for role in required_roles if role}
        allowed = required.issubset(roles) if require_all else bool(roles & required)
        if not allowed:
            raise forbidden(f"Role insuficiente. Requerido: {', '.join(sorted(required))}")
        return user

    return dependency


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise forbidden("Admin requerido")
    return user


async def require_authenticated(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_authenticated:
        raise unauthorized("Autenticação requerida")
    return user


async def require_tenant(context: RequestContext = Depends(get_request_context)) -> RequestContext:
    if not context.tenant_id:
        raise forbidden("Tenant obrigatório")
    return context


def user_from_jwt(token: str, override_tenant_id: Optional[str] = None) -> CurrentUser:
    payload = jwt_verifier.verify(token)
    scopes = extract_scopes(payload)
    roles = extract_roles(payload)
    tenant_id = override_tenant_id or payload.get("tenant_id") or payload.get("tid")
    return CurrentUser(
        subject=str(payload.get("sub") or payload.get("subject") or "jwt-subject"),
        principal_type=str(payload.get("principal_type") or PrincipalType.USER.value),
        auth_method=AuthMethod.BEARER.value,
        scopes=scopes,
        roles=roles,
        tenant_id=tenant_id,
        token_id=payload.get("jti"),
        issued_at=payload.get("iat"),
        expires_at=payload.get("exp"),
        metadata={
            "issuer": payload.get("iss"),
            "audience": payload.get("aud"),
            "token_hash": sha256_hex(token)[:12],
        },
    )


def extract_scopes(payload: Mapping[str, Any]) -> List[str]:
    value = payload.get("scope", payload.get("scopes", []))
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def extract_roles(payload: Mapping[str, Any]) -> List[str]:
    value = payload.get("roles", payload.get("role", []))
    if isinstance(value, str):
        return [item for item in value.replace(",", " ").split() if item]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "unauthorized", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def forbidden(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "forbidden", "message": message},
    )


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def auth_metadata() -> Dict[str, Any]:
    """Retorna metadados seguros da configuração de auth, sem segredos."""

    return {
        "auth_enabled": AUTH_ENABLED,
        "api_key_enabled": bool(DEFAULT_API_KEY or API_KEYS_JSON),
        "jwt_enabled": bool(JWT_SECRET),
        "jwt_issuer_configured": bool(JWT_ISSUER),
        "jwt_audience_configured": bool(JWT_AUDIENCE),
        "tenant_required": REQUIRE_TENANT,
        "admin_scopes": sorted(ADMIN_SCOPES),
    }


__all__ = [
    "AuthMethod",
    "PrincipalType",
    "CurrentUser",
    "RequestContext",
    "get_current_user",
    "get_optional_current_user",
    "get_request_context",
    "require_scopes",
    "require_any_scope",
    "require_roles",
    "require_admin",
    "require_authenticated",
    "require_tenant",
    "auth_metadata",
]
