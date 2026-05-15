#!/usr/bin/env python3
"""
api/routes/auth.py

Enterprise-grade Auth API Router.

Objetivo:
- Expor endpoints de autenticação/autorização para a API.
- Emitir e renovar JWTs quando API_JWT_SECRET estiver configurado.
- Validar API Key/Bearer token e retornar usuário atual, scopes, roles e tenant.
- Fornecer introspection segura sem vazar token/segredos.
- Centralizar metadados públicos da configuração de auth.

Endpoints:
    GET  /auth/health
    GET  /auth/metadata
    GET  /auth/me
    POST /auth/token
    POST /auth/refresh
    POST /auth/introspect
    POST /auth/verify-scopes
    POST /auth/service-token

Integração:
    from fastapi import FastAPI
    from api.routes.auth import router as auth_router

    app.include_router(auth_router, prefix="/v1")

Variáveis de ambiente principais:
    API_AUTH_ENABLED=true
    API_KEY=...
    API_KEYS_JSON={"key":{"subject":"svc","scopes":["admin"]}}
    API_JWT_SECRET=secret-com-16-ou-mais-caracteres
    API_JWT_ISSUER=enterprise-ai-api
    API_JWT_AUDIENCE=enterprise-ai-clients
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

try:
    from api.auth.dependencies import (
        CurrentUser,
        auth_metadata,
        get_current_user,
        get_optional_current_user,
        require_admin,
        require_scopes,
    )
except Exception:  # pragma: no cover
    CurrentUser = Any  # type: ignore

    async def get_current_user() -> Any:  # type: ignore
        return {"subject": "auth-unavailable", "scopes": ["admin"], "roles": ["admin"]}

    async def get_optional_current_user() -> Any:  # type: ignore
        return {"subject": "anonymous", "scopes": [], "roles": []}

    async def require_admin() -> Any:  # type: ignore
        return None

    def require_scopes(*_: str, **__: Any) -> Any:  # type: ignore
        async def dependency() -> Any:
            return None

        return dependency

    def auth_metadata() -> Dict[str, Any]:  # type: ignore
        return {"auth_available": False}

try:
    from api.auth.jwt import (
        JwtConfigError,
        JwtExpiredError,
        JwtPermissionError,
        JwtService,
        JwtValidationError,
        PrincipalType,
        TokenType,
        create_random_secret,
        decode_unverified,
        require_scopes as jwt_require_scopes,
        token_metadata,
    )
except Exception:  # pragma: no cover
    JwtService = None  # type: ignore
    JwtConfigError = Exception  # type: ignore
    JwtExpiredError = Exception  # type: ignore
    JwtPermissionError = Exception  # type: ignore
    JwtValidationError = Exception  # type: ignore

    class PrincipalType:  # type: ignore
        USER = "user"
        SERVICE = "service"

    class TokenType:  # type: ignore
        ACCESS = "access"
        REFRESH = "refresh"
        SERVICE = "service"

    def create_random_secret(length: int = 48) -> str:  # type: ignore
        return "not-available"

    def token_metadata(token: str) -> Dict[str, Any]:  # type: ignore
        return {"available": False}


LOGGER = logging.getLogger(__name__)
ROUTER_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    scopes: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    tenant_id: Optional[str] = None
    principal_type: str = "user"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: Optional[int] = Field(default=None, ge=60, le=86_400)


class ServiceTokenRequest(BaseModel):
    subject: str = Field(..., min_length=1)
    scopes: List[str] = Field(default_factory=lambda: ["service"])
    roles: List[str] = Field(default_factory=lambda: ["service"])
    tenant_id: Optional[str] = None
    ttl_seconds: Optional[int] = Field(default=None, ge=60, le=86_400)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20)
    scopes: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)


class IntrospectRequest(BaseModel):
    token: str = Field(..., min_length=20)
    verify_signature: bool = True
    required_scopes: List[str] = Field(default_factory=list)


class VerifyScopesRequest(BaseModel):
    required_scopes: List[str] = Field(default_factory=list)
    require_all: bool = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_in: int
    refresh_expires_in: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IntrospectResponse(BaseModel):
    active: bool
    valid: bool
    metadata: Dict[str, Any] = Field(default_factory=dict)
    claims: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class MeResponse(BaseModel):
    subject: str
    principal_type: str
    auth_method: str
    scopes: List[str]
    roles: List[str]
    tenant_id: Optional[str] = None
    is_authenticated: bool
    is_admin: bool
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AuthHealthResponse(BaseModel):
    status: str
    router: str
    version: str
    timestamp: str
    jwt_enabled: bool
    auth_enabled: bool


@router.get("/health", response_model=AuthHealthResponse)
async def auth_health() -> AuthHealthResponse:
    metadata = auth_metadata()
    return AuthHealthResponse(
        status="ok",
        router="auth",
        version=ROUTER_VERSION,
        timestamp=utc_now_iso(),
        jwt_enabled=bool(metadata.get("jwt_enabled")),
        auth_enabled=bool(metadata.get("auth_enabled", True)),
    )


@router.get("/metadata")
async def get_auth_metadata() -> Dict[str, Any]:
    return {
        "router_version": ROUTER_VERSION,
        "timestamp": utc_now_iso(),
        "auth": auth_metadata(),
        "endpoints": [
            "/auth/health",
            "/auth/metadata",
            "/auth/me",
            "/auth/token",
            "/auth/refresh",
            "/auth/introspect",
            "/auth/verify-scopes",
            "/auth/service-token",
        ],
    }


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return user_to_response(user)


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(require_admin)])
async def create_token(payload: TokenRequest) -> TokenResponse:
    service = get_jwt_service()
    principal_type = parse_principal_type(payload.principal_type)
    pair = service.create_token_pair(
        subject=payload.subject,
        scopes=payload.scopes,
        roles=payload.roles,
        tenant_id=payload.tenant_id,
        principal_type=principal_type,
        metadata=sanitize_metadata(payload.metadata),
    )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        refresh_expires_in=pair.refresh_expires_in,
        metadata={
            "subject_hash": hash_identifier(payload.subject),
            "tenant_id": payload.tenant_id,
            "scope_count": len(payload.scopes),
            "role_count": len(payload.roles),
        },
    )


@router.post("/service-token", response_model=TokenResponse, dependencies=[Depends(require_admin)])
async def create_service_token(payload: ServiceTokenRequest) -> TokenResponse:
    service = get_jwt_service()
    token = service.create_service_token(
        subject=payload.subject,
        scopes=payload.scopes,
        roles=payload.roles,
        tenant_id=payload.tenant_id,
        ttl_seconds=payload.ttl_seconds,
    )
    expires_in = payload.ttl_seconds or service.config.access_ttl_seconds
    return TokenResponse(
        access_token=token,
        refresh_token=None,
        expires_in=expires_in,
        metadata={
            "subject_hash": hash_identifier(payload.subject),
            "tenant_id": payload.tenant_id,
            "service_token": True,
            "scope_count": len(payload.scopes),
        },
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest) -> TokenResponse:
    service = get_jwt_service()
    try:
        access = service.refresh_access_token(
            payload.refresh_token,
            scopes=payload.scopes,
            roles=payload.roles,
        )
        return TokenResponse(
            access_token=access,
            refresh_token=None,
            expires_in=service.config.access_ttl_seconds,
            metadata={"refreshed": True, "refresh_fingerprint": service.fingerprint(payload.refresh_token)},
        )
    except (JwtExpiredError, JwtValidationError, JwtPermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_refresh_token", "message": str(exc)}) from exc


@router.post("/introspect", response_model=IntrospectResponse)
async def introspect(payload: IntrospectRequest, user: CurrentUser = Depends(get_optional_current_user)) -> IntrospectResponse:
    try:
        metadata = token_metadata(payload.token)
        if not payload.verify_signature:
            return IntrospectResponse(active=False, valid=False, metadata=metadata, claims={}, error="signature_not_verified")

        service = get_jwt_service()
        claims = service.verify(payload.token, required_scopes=payload.required_scopes)
        safe_claims = claims.to_payload()
        safe_claims["sub_hash"] = hash_identifier(str(safe_claims.get("sub", "")))
        safe_claims.pop("sub", None)
        return IntrospectResponse(
            active=True,
            valid=True,
            metadata={**metadata, "token_fingerprint": service.fingerprint(payload.token)},
            claims=safe_claims,
        )
    except (JwtExpiredError, JwtValidationError, JwtPermissionError, JwtConfigError) as exc:
        return IntrospectResponse(active=False, valid=False, metadata=safe_introspection_metadata(payload.token), error=str(exc))


@router.post("/verify-scopes")
async def verify_scopes(payload: VerifyScopesRequest, user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    user_scopes = set(getattr(user, "scopes", []) or [])
    is_admin = bool(getattr(user, "is_admin", False))
    required = set(payload.required_scopes)
    if "*" in user_scopes or is_admin:
        allowed = True
    elif payload.require_all:
        allowed = required.issubset(user_scopes)
    else:
        allowed = bool(required & user_scopes)

    return {
        "allowed": allowed,
        "subject": getattr(user, "subject", "unknown"),
        "required_scopes": sorted(required),
        "present_scopes": sorted(user_scopes),
        "require_all": payload.require_all,
        "timestamp": utc_now_iso(),
    }


def get_jwt_service() -> Any:
    if JwtService is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "jwt_unavailable", "message": "JWT service unavailable"})
    try:
        return JwtService.from_env()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": "jwt_not_configured", "message": str(exc)}) from exc


def user_to_response(user: Any) -> MeResponse:
    subject = getattr(user, "subject", None) or (user.get("subject") if isinstance(user, dict) else "unknown")
    principal_type = getattr(user, "principal_type", None) or (user.get("principal_type") if isinstance(user, dict) else "unknown")
    auth_method = getattr(user, "auth_method", None) or (user.get("auth_method") if isinstance(user, dict) else "unknown")
    scopes = list(getattr(user, "scopes", []) or (user.get("scopes", []) if isinstance(user, dict) else []))
    roles = list(getattr(user, "roles", []) or (user.get("roles", []) if isinstance(user, dict) else []))
    tenant_id = getattr(user, "tenant_id", None) or (user.get("tenant_id") if isinstance(user, dict) else None)
    metadata = getattr(user, "metadata", {}) or (user.get("metadata", {}) if isinstance(user, dict) else {})
    is_authenticated = bool(getattr(user, "is_authenticated", False)) if not isinstance(user, dict) else subject not in {"anonymous", "unknown"}
    is_admin_value = getattr(user, "is_admin", False)
    is_admin = bool(is_admin_value() if callable(is_admin_value) else is_admin_value)
    return MeResponse(
        subject=str(subject),
        principal_type=str(principal_type),
        auth_method=str(auth_method),
        scopes=scopes,
        roles=roles,
        tenant_id=tenant_id,
        is_authenticated=is_authenticated,
        is_admin=is_admin,
        metadata=sanitize_metadata(metadata),
    )


def parse_principal_type(value: str) -> Any:
    text = (value or "user").strip().lower()
    if hasattr(PrincipalType, "USER"):
        if text == "service":
            return PrincipalType.SERVICE
        return PrincipalType.USER
    return text


def sanitize_metadata(metadata: Dict[str, Any], max_items: int = 50) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for index, (key, value) in enumerate((metadata or {}).items()):
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


def safe_introspection_metadata(token: str) -> Dict[str, Any]:
    return {
        "token_fingerprint": hash_identifier(token),
        "token_length": len(token),
    }


def hash_identifier(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()
