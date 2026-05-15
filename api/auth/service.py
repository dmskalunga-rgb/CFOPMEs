#!/usr/bin/env python3
"""
api/auth/service.py

Enterprise-grade Authentication and Authorization Service.

Objetivo:
- Centralizar autenticação e autorização da API.
- Suportar API Key, JWT HMAC-SHA256, RBAC, escopos/permissões, sessões e auditoria.
- Fornecer dependências prontas para FastAPI.
- Evitar dependências externas obrigatórias além de FastAPI/Pydantic quando usado na API.
- Permitir persistência em memória para desenvolvimento e adaptação futura para banco/Redis/Vault.

Exemplos FastAPI:
    from fastapi import Depends, FastAPI
    from api.auth.service import AuthService, AuthSettings, require_auth, require_permissions

    auth_service = AuthService(AuthSettings.from_env())

    @app.get('/secure')
    async def secure(user=Depends(require_auth(auth_service))):
        return {'user': user.subject}

    @app.get('/admin')
    async def admin(user=Depends(require_permissions(auth_service, ['admin:read']))):
        return {'ok': True}

Variáveis de ambiente:
    AUTH_ENABLED=true|false
    AUTH_JWT_SECRET=troque-em-producao
    AUTH_JWT_ISSUER=enterprise-api
    AUTH_JWT_AUDIENCE=enterprise-clients
    AUTH_ACCESS_TOKEN_TTL_SECONDS=3600
    AUTH_API_KEYS_JSON={"key":"service-name"}
    AUTH_ADMIN_API_KEY=opcional

Notas:
- Em produção, use segredo forte em variável de ambiente, Vault/KMS ou Secret Manager.
- Para senhas, este módulo usa PBKDF2-HMAC-SHA256 da stdlib.
- Para JWT, implementa HS256 sem dependência externa. Se preferir, pode adaptar para python-jose/PyJWT.
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
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from fastapi import Depends, Header, HTTPException, Request, status
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover
    Depends = None  # type: ignore
    Header = None  # type: ignore
    HTTPException = Exception  # type: ignore
    Request = Any  # type: ignore
    status = None  # type: ignore
    BaseModel = object  # type: ignore
    Field = lambda default=None, **_: default  # type: ignore


AUTH_SERVICE_VERSION = "1.0.0"
DEFAULT_ISSUER = "enterprise-api"
DEFAULT_AUDIENCE = "enterprise-clients"
DEFAULT_TOKEN_TYPE = "Bearer"
UTC = timezone.utc

logger = logging.getLogger(__name__)


class AuthMethod(str, Enum):
    API_KEY = "api_key"
    JWT = "jwt"
    SESSION = "session"
    NONE = "none"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = True
    jwt_secret: str = "change-me-in-production"
    jwt_issuer: str = DEFAULT_ISSUER
    jwt_audience: str = DEFAULT_AUDIENCE
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 604800
    api_keys: Dict[str, str] = field(default_factory=dict)
    admin_api_key: Optional[str] = None
    password_iterations: int = 210_000
    session_ttl_seconds: int = 3600
    max_failed_attempts: int = 10
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 120
    allow_anonymous_health: bool = True

    @staticmethod
    def from_env() -> "AuthSettings":
        api_keys = parse_json_dict(os.getenv("AUTH_API_KEYS_JSON"), default={})
        admin_key = os.getenv("AUTH_ADMIN_API_KEY")
        if admin_key:
            api_keys[admin_key] = "admin"
        return AuthSettings(
            enabled=parse_bool(os.getenv("AUTH_ENABLED"), True),
            jwt_secret=os.getenv("AUTH_JWT_SECRET", "change-me-in-production"),
            jwt_issuer=os.getenv("AUTH_JWT_ISSUER", DEFAULT_ISSUER),
            jwt_audience=os.getenv("AUTH_JWT_AUDIENCE", DEFAULT_AUDIENCE),
            access_token_ttl_seconds=int(os.getenv("AUTH_ACCESS_TOKEN_TTL_SECONDS", "3600")),
            refresh_token_ttl_seconds=int(os.getenv("AUTH_REFRESH_TOKEN_TTL_SECONDS", "604800")),
            api_keys=api_keys,
            admin_api_key=admin_key,
            password_iterations=int(os.getenv("AUTH_PASSWORD_ITERATIONS", "210000")),
            session_ttl_seconds=int(os.getenv("AUTH_SESSION_TTL_SECONDS", "3600")),
            max_failed_attempts=int(os.getenv("AUTH_MAX_FAILED_ATTEMPTS", "10")),
            rate_limit_window_seconds=int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS", "60")),
            rate_limit_max_requests=int(os.getenv("AUTH_RATE_LIMIT_MAX_REQUESTS", "120")),
            allow_anonymous_health=parse_bool(os.getenv("AUTH_ALLOW_ANONYMOUS_HEALTH"), True),
        )


@dataclass(frozen=True)
class Principal:
    subject: str
    display_name: str
    roles: List[str]
    permissions: List[str]
    auth_method: AuthMethod
    tenant_id: Optional[str] = None
    service_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        return role in set(self.roles)

    def has_permission(self, permission: str) -> bool:
        permissions = set(self.permissions)
        return "*" in permissions or permission in permissions

    def has_any_permission(self, permissions: Sequence[str]) -> bool:
        return any(self.has_permission(permission) for permission in permissions)

    def has_all_permissions(self, permissions: Sequence[str]) -> bool:
        return all(self.has_permission(permission) for permission in permissions)

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["auth_method"] = self.auth_method.value
        return payload


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    issued_at: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    display_name: str
    roles: List[str]
    permissions: List[str]
    tenant_id: Optional[str] = None
    active: bool = True
    failed_attempts: int = 0
    locked_until_epoch: Optional[int] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_principal(self) -> Principal:
        return Principal(
            subject=self.user_id,
            display_name=self.display_name,
            roles=list(self.roles),
            permissions=list(self.permissions),
            auth_method=AuthMethod.JWT,
            tenant_id=self.tenant_id,
            metadata={"username": self.username},
        )


@dataclass
class SessionRecord:
    session_id: str
    principal: Principal
    created_at_epoch: int
    expires_at_epoch: int
    revoked: bool = False

    def is_valid(self) -> bool:
        return not self.revoked and now_epoch() < self.expires_at_epoch


@dataclass(frozen=True)
class AuditEvent:
    audit_id: str
    timestamp: str
    outcome: AuditOutcome
    method: AuthMethod
    subject: Optional[str]
    action: str
    request_id: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["outcome"] = self.outcome.value
        payload["method"] = self.method.value
        return payload


class LoginRequest(BaseModel):
    username: str
    password: str
    tenant_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = DEFAULT_TOKEN_TYPE
    expires_in: int
    issued_at: str


class PrincipalResponse(BaseModel):
    subject: str
    display_name: str
    roles: List[str]
    permissions: List[str]
    auth_method: str
    tenant_id: Optional[str] = None
    service_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AuthError(Exception):
    """Base auth exception."""


class AuthenticationError(AuthError):
    """Authentication failed."""


class AuthorizationError(AuthError):
    """Authorization failed."""


class TokenError(AuthError):
    """Invalid token."""


class RateLimitError(AuthError):
    """Too many requests."""


class PasswordHasher:
    @staticmethod
    def hash_password(password: str, iterations: int = 210_000) -> str:
        if not password:
            raise ValueError("password is required")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return "pbkdf2_sha256${}${}${}".format(
            iterations,
            b64url_encode(salt),
            b64url_encode(digest),
        )

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations_raw, salt_raw, digest_raw = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_raw)
            salt = b64url_decode(salt_raw)
            expected = b64url_decode(digest_raw)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False


class JwtService:
    def __init__(self, settings: AuthSettings) -> None:
        self.settings = settings

    def create_access_token(self, principal: Principal, extra_claims: Optional[Dict[str, Any]] = None) -> str:
        now = now_epoch()
        payload = {
            "iss": self.settings.jwt_issuer,
            "aud": self.settings.jwt_audience,
            "sub": principal.subject,
            "name": principal.display_name,
            "roles": principal.roles,
            "permissions": principal.permissions,
            "tenant_id": principal.tenant_id,
            "service_name": principal.service_name,
            "iat": now,
            "nbf": now,
            "exp": now + self.settings.access_token_ttl_seconds,
            "jti": str(uuid.uuid4()),
            "typ": "access",
        }
        if extra_claims:
            payload.update(extra_claims)
        return self._encode(payload)

    def create_refresh_token(self, principal: Principal) -> str:
        now = now_epoch()
        payload = {
            "iss": self.settings.jwt_issuer,
            "aud": self.settings.jwt_audience,
            "sub": principal.subject,
            "iat": now,
            "nbf": now,
            "exp": now + self.settings.refresh_token_ttl_seconds,
            "jti": str(uuid.uuid4()),
            "typ": "refresh",
        }
        return self._encode(payload)

    def decode(self, token: str, expected_type: Optional[str] = None) -> Dict[str, Any]:
        try:
            header_raw, payload_raw, signature_raw = token.split(".")
        except ValueError as exc:
            raise TokenError("Malformed JWT") from exc

        signing_input = f"{header_raw}.{payload_raw}".encode("utf-8")
        expected_signature = self._sign(signing_input)
        actual_signature = b64url_decode(signature_raw)
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise TokenError("Invalid JWT signature")

        header = json.loads(b64url_decode(header_raw).decode("utf-8"))
        payload = json.loads(b64url_decode(payload_raw).decode("utf-8"))
        if header.get("alg") != "HS256":
            raise TokenError("Unsupported JWT algorithm")
        self._validate_claims(payload, expected_type)
        return payload

    def principal_from_token(self, token: str) -> Principal:
        payload = self.decode(token, expected_type="access")
        return Principal(
            subject=str(payload["sub"]),
            display_name=str(payload.get("name") or payload["sub"]),
            roles=list(payload.get("roles") or []),
            permissions=list(payload.get("permissions") or []),
            auth_method=AuthMethod.JWT,
            tenant_id=payload.get("tenant_id"),
            service_name=payload.get("service_name"),
            metadata={"jti": payload.get("jti")},
        )

    def _encode(self, payload: Mapping[str, Any]) -> str:
        header = {"typ": "JWT", "alg": "HS256"}
        header_raw = b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        payload_raw = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8"))
        signing_input = f"{header_raw}.{payload_raw}".encode("utf-8")
        signature = b64url_encode(self._sign(signing_input))
        return f"{header_raw}.{payload_raw}.{signature}"

    def _sign(self, signing_input: bytes) -> bytes:
        return hmac.new(self.settings.jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()

    def _validate_claims(self, payload: Mapping[str, Any], expected_type: Optional[str]) -> None:
        now = now_epoch()
        if payload.get("iss") != self.settings.jwt_issuer:
            raise TokenError("Invalid issuer")
        if payload.get("aud") != self.settings.jwt_audience:
            raise TokenError("Invalid audience")
        if "sub" not in payload:
            raise TokenError("Missing subject")
        if int(payload.get("nbf", 0)) > now:
            raise TokenError("Token not yet valid")
        if int(payload.get("exp", 0)) <= now:
            raise TokenError("Token expired")
        if expected_type and payload.get("typ") != expected_type:
            raise TokenError("Invalid token type")


class InMemoryUserStore:
    def __init__(self) -> None:
        self.users_by_username: Dict[str, UserRecord] = {}
        self.users_by_id: Dict[str, UserRecord] = {}

    def add_user(self, user: UserRecord) -> None:
        self.users_by_username[user.username.lower()] = user
        self.users_by_id[user.user_id] = user

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        return self.users_by_username.get(username.lower())

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        return self.users_by_id.get(user_id)

    def update_user(self, user: UserRecord) -> None:
        user.updated_at = utc_now_iso()
        self.add_user(user)


class AuthService:
    def __init__(self, settings: Optional[AuthSettings] = None, user_store: Optional[InMemoryUserStore] = None) -> None:
        self.settings = settings or AuthSettings.from_env()
        self.jwt = JwtService(self.settings)
        self.user_store = user_store or InMemoryUserStore()
        self.sessions: Dict[str, SessionRecord] = {}
        self.audit_log: List[AuditEvent] = []
        self.rate_counters: Dict[str, List[int]] = {}
        self.logger = logging.getLogger(f"{__name__}.AuthService")

    def authenticate_api_key(self, api_key: str, request_context: Optional[Mapping[str, Any]] = None) -> Principal:
        if not self.settings.enabled:
            return anonymous_principal()
        if not api_key:
            self._audit(AuditOutcome.FAILURE, AuthMethod.API_KEY, None, "api_key_auth", request_context, {"reason": "missing_api_key"})
            raise AuthenticationError("Missing API key")
        service_name = self.settings.api_keys.get(api_key)
        if not service_name:
            self._audit(AuditOutcome.FAILURE, AuthMethod.API_KEY, None, "api_key_auth", request_context, {"reason": "invalid_api_key"})
            raise AuthenticationError("Invalid API key")
        roles = ["admin"] if service_name == "admin" else ["service"]
        permissions = ["*"] if service_name == "admin" else ["inference:read", "inference:write", "alerts:write"]
        principal = Principal(
            subject=f"svc:{service_name}",
            display_name=service_name,
            roles=roles,
            permissions=permissions,
            auth_method=AuthMethod.API_KEY,
            service_name=service_name,
        )
        self._audit(AuditOutcome.SUCCESS, AuthMethod.API_KEY, principal.subject, "api_key_auth", request_context, {})
        return principal

    def authenticate_bearer_token(self, authorization: str, request_context: Optional[Mapping[str, Any]] = None) -> Principal:
        if not self.settings.enabled:
            return anonymous_principal()
        token = extract_bearer_token(authorization)
        try:
            principal = self.jwt.principal_from_token(token)
            self._audit(AuditOutcome.SUCCESS, AuthMethod.JWT, principal.subject, "jwt_auth", request_context, {})
            return principal
        except AuthError as exc:
            self._audit(AuditOutcome.FAILURE, AuthMethod.JWT, None, "jwt_auth", request_context, {"reason": str(exc)})
            raise AuthenticationError(str(exc)) from exc

    def authenticate_request(
        self,
        authorization: Optional[str] = None,
        api_key: Optional[str] = None,
        session_id: Optional[str] = None,
        request_context: Optional[Mapping[str, Any]] = None,
    ) -> Principal:
        if not self.settings.enabled:
            return anonymous_principal()
        self.check_rate_limit(request_context)
        if authorization:
            return self.authenticate_bearer_token(authorization, request_context)
        if api_key:
            return self.authenticate_api_key(api_key, request_context)
        if session_id:
            return self.authenticate_session(session_id, request_context)
        self._audit(AuditOutcome.FAILURE, AuthMethod.NONE, None, "request_auth", request_context, {"reason": "missing_credentials"})
        raise AuthenticationError("Missing credentials")

    def authenticate_session(self, session_id: str, request_context: Optional[Mapping[str, Any]] = None) -> Principal:
        session = self.sessions.get(session_id)
        if not session or not session.is_valid():
            self._audit(AuditOutcome.FAILURE, AuthMethod.SESSION, None, "session_auth", request_context, {"reason": "invalid_session"})
            raise AuthenticationError("Invalid session")
        self._audit(AuditOutcome.SUCCESS, AuthMethod.SESSION, session.principal.subject, "session_auth", request_context, {})
        return session.principal

    def login(self, username: str, password: str, tenant_id: Optional[str] = None, request_context: Optional[Mapping[str, Any]] = None) -> TokenPair:
        user = self.user_store.get_by_username(username)
        if not user:
            self._audit(AuditOutcome.FAILURE, AuthMethod.JWT, None, "login", request_context, {"reason": "user_not_found"})
            raise AuthenticationError("Invalid username or password")
        if not user.active:
            self._audit(AuditOutcome.DENIED, AuthMethod.JWT, user.user_id, "login", request_context, {"reason": "inactive_user"})
            raise AuthenticationError("User inactive")
        if user.locked_until_epoch and user.locked_until_epoch > now_epoch():
            self._audit(AuditOutcome.DENIED, AuthMethod.JWT, user.user_id, "login", request_context, {"reason": "locked"})
            raise AuthenticationError("User temporarily locked")
        if tenant_id and user.tenant_id and tenant_id != user.tenant_id:
            self._audit(AuditOutcome.DENIED, AuthMethod.JWT, user.user_id, "login", request_context, {"reason": "tenant_mismatch"})
            raise AuthenticationError("Invalid tenant")
        if not PasswordHasher.verify_password(password, user.password_hash):
            user.failed_attempts += 1
            if user.failed_attempts >= self.settings.max_failed_attempts:
                user.locked_until_epoch = now_epoch() + 900
            self.user_store.update_user(user)
            self._audit(AuditOutcome.FAILURE, AuthMethod.JWT, user.user_id, "login", request_context, {"reason": "bad_password"})
            raise AuthenticationError("Invalid username or password")
        user.failed_attempts = 0
        user.locked_until_epoch = None
        self.user_store.update_user(user)
        principal = user.to_principal()
        issued_at = utc_now_iso()
        pair = TokenPair(
            access_token=self.jwt.create_access_token(principal),
            refresh_token=self.jwt.create_refresh_token(principal),
            token_type=DEFAULT_TOKEN_TYPE,
            expires_in=self.settings.access_token_ttl_seconds,
            issued_at=issued_at,
        )
        self._audit(AuditOutcome.SUCCESS, AuthMethod.JWT, user.user_id, "login", request_context, {})
        return pair

    def refresh(self, refresh_token: str, request_context: Optional[Mapping[str, Any]] = None) -> TokenPair:
        payload = self.jwt.decode(refresh_token, expected_type="refresh")
        user = self.user_store.get_by_id(str(payload["sub"]))
        if not user or not user.active:
            raise AuthenticationError("User invalid or inactive")
        principal = user.to_principal()
        pair = TokenPair(
            access_token=self.jwt.create_access_token(principal),
            refresh_token=self.jwt.create_refresh_token(principal),
            token_type=DEFAULT_TOKEN_TYPE,
            expires_in=self.settings.access_token_ttl_seconds,
            issued_at=utc_now_iso(),
        )
        self._audit(AuditOutcome.SUCCESS, AuthMethod.JWT, principal.subject, "refresh", request_context, {})
        return pair

    def create_session(self, principal: Principal) -> SessionRecord:
        now = now_epoch()
        session = SessionRecord(
            session_id="sess_" + secrets.token_urlsafe(32),
            principal=principal,
            created_at_epoch=now,
            expires_at_epoch=now + self.settings.session_ttl_seconds,
        )
        self.sessions[session.session_id] = session
        return session

    def revoke_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.revoked = True
        return True

    def authorize(self, principal: Principal, permissions: Sequence[str], require_all: bool = True) -> None:
        if not self.settings.enabled:
            return
        allowed = principal.has_all_permissions(permissions) if require_all else principal.has_any_permission(permissions)
        if not allowed:
            self._audit(
                AuditOutcome.DENIED,
                principal.auth_method,
                principal.subject,
                "authorize",
                None,
                {"required_permissions": list(permissions), "principal_permissions": principal.permissions},
            )
            raise AuthorizationError("Insufficient permissions")

    def authorize_roles(self, principal: Principal, roles: Sequence[str], require_all: bool = False) -> None:
        principal_roles = set(principal.roles)
        required = set(roles)
        allowed = required.issubset(principal_roles) if require_all else bool(principal_roles & required)
        if not allowed:
            raise AuthorizationError("Insufficient roles")

    def check_rate_limit(self, request_context: Optional[Mapping[str, Any]]) -> None:
        key = "global"
        if request_context:
            key = str(request_context.get("ip_address") or request_context.get("request_id") or "global")
        now = now_epoch()
        window_start = now - self.settings.rate_limit_window_seconds
        values = [ts for ts in self.rate_counters.get(key, []) if ts >= window_start]
        if len(values) >= self.settings.rate_limit_max_requests:
            raise RateLimitError("Rate limit exceeded")
        values.append(now)
        self.rate_counters[key] = values

    def create_user(
        self,
        username: str,
        password: str,
        display_name: Optional[str] = None,
        roles: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        tenant_id: Optional[str] = None,
    ) -> UserRecord:
        if self.user_store.get_by_username(username):
            raise ValueError("username already exists")
        user = UserRecord(
            user_id="usr_" + hashlib.sha256(f"{username}|{uuid.uuid4()}".encode("utf-8")).hexdigest()[:20],
            username=username,
            password_hash=PasswordHasher.hash_password(password, self.settings.password_iterations),
            display_name=display_name or username,
            roles=roles or ["user"],
            permissions=permissions or ["inference:read"],
            tenant_id=tenant_id,
        )
        self.user_store.add_user(user)
        return user

    def _audit(
        self,
        outcome: AuditOutcome,
        method: AuthMethod,
        subject: Optional[str],
        action: str,
        request_context: Optional[Mapping[str, Any]],
        details: Dict[str, Any],
    ) -> None:
        ctx = dict(request_context or {})
        event = AuditEvent(
            audit_id="auth_aud_" + uuid.uuid4().hex[:20],
            timestamp=utc_now_iso(),
            outcome=outcome,
            method=method,
            subject=subject,
            action=action,
            request_id=ctx.get("request_id"),
            ip_address=ctx.get("ip_address"),
            user_agent=ctx.get("user_agent"),
            details=details,
        )
        self.audit_log.append(event)
        logger.info("auth_audit", extra=event.to_dict())


# FastAPI dependency helpers

def require_auth(auth_service: AuthService) -> Callable[..., Any]:
    async def dependency(
        request: Request,
        authorization: Optional[str] = Header(default=None),
        x_api_key: Optional[str] = Header(default=None),
        x_session_id: Optional[str] = Header(default=None),
    ) -> Principal:
        try:
            return auth_service.authenticate_request(
                authorization=authorization,
                api_key=x_api_key,
                session_id=x_session_id,
                request_context=request_context(request),
            )
        except RateLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    return dependency


def require_permissions(auth_service: AuthService, permissions: Sequence[str], require_all: bool = True) -> Callable[..., Any]:
    async def dependency(principal: Principal = Depends(require_auth(auth_service))) -> Principal:
        try:
            auth_service.authorize(principal, permissions, require_all=require_all)
            return principal
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return dependency


def require_roles(auth_service: AuthService, roles: Sequence[str], require_all: bool = False) -> Callable[..., Any]:
    async def dependency(principal: Principal = Depends(require_auth(auth_service))) -> Principal:
        try:
            auth_service.authorize_roles(principal, roles, require_all=require_all)
            return principal
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return dependency


def request_context(request: Request) -> Dict[str, Any]:
    return {
        "request_id": getattr(request.state, "request_id", None) or request.headers.get("x-request-id"),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
        "path": request.url.path,
        "method": request.method,
    }


def principal_response(principal: Principal) -> PrincipalResponse:
    return PrincipalResponse(**principal.to_dict())


def token_response(pair: TokenPair) -> TokenResponse:
    return TokenResponse(**pair.to_dict())


def anonymous_principal() -> Principal:
    return Principal(
        subject="anonymous",
        display_name="Anonymous",
        roles=["anonymous"],
        permissions=["*"],
        auth_method=AuthMethod.NONE,
    )


def extract_bearer_token(authorization: str) -> str:
    if not authorization:
        raise AuthenticationError("Missing Authorization header")
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("Invalid Authorization header")
    return parts[1].strip()


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def now_epoch() -> int:
    return int(time.time())


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


def parse_json_dict(value: Optional[str], default: Dict[str, str]) -> Dict[str, str]:
    if not value:
        return dict(default)
    try:
        payload = json.loads(value)
        if not isinstance(payload, dict):
            return dict(default)
        return {str(key): str(val) for key, val in payload.items()}
    except json.JSONDecodeError:
        return dict(default)


__all__ = [
    "AuthSettings",
    "AuthService",
    "Principal",
    "TokenPair",
    "UserRecord",
    "PasswordHasher",
    "JwtService",
    "LoginRequest",
    "TokenResponse",
    "PrincipalResponse",
    "require_auth",
    "require_permissions",
    "require_roles",
    "principal_response",
    "token_response",
    "AuthenticationError",
    "AuthorizationError",
    "TokenError",
    "RateLimitError",
]
