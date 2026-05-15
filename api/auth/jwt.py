#!/usr/bin/env python3
"""
api/auth/jwt.py

Enterprise-grade JWT utilities.

Objetivo:
- Emitir e validar JWT HS256 sem dependências externas obrigatórias.
- Padronizar claims, scopes, roles, tenant, subject, audience, issuer, expiração e JTI.
- Fornecer helpers seguros para APIs FastAPI, serviços internos e autenticação machine-to-machine.
- Evitar logging de tokens em texto puro.

Variáveis de ambiente:
    API_JWT_SECRET=secret-obrigatorio-para-assinar
    API_JWT_ISSUER=enterprise-ai-api
    API_JWT_AUDIENCE=enterprise-ai-clients
    API_JWT_ACCESS_TTL_SECONDS=900
    API_JWT_REFRESH_TTL_SECONDS=604800
    API_JWT_CLOCK_SKEW_SECONDS=60

Exemplos:
    token = JwtService.from_env().create_access_token(
        subject="user-123",
        scopes=["inference:read"],
        roles=["analyst"],
        tenant_id="tenant-a",
    )

    claims = JwtService.from_env().verify(token)
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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


LOGGER = logging.getLogger(__name__)
JWT_VERSION = "1.0.0"
DEFAULT_ALGORITHM = "HS256"
DEFAULT_ISSUER = os.getenv("API_JWT_ISSUER", "enterprise-ai-api")
DEFAULT_AUDIENCE = os.getenv("API_JWT_AUDIENCE", "enterprise-ai-clients")
DEFAULT_ACCESS_TTL_SECONDS = int(os.getenv("API_JWT_ACCESS_TTL_SECONDS", "900"))
DEFAULT_REFRESH_TTL_SECONDS = int(os.getenv("API_JWT_REFRESH_TTL_SECONDS", "604800"))
DEFAULT_CLOCK_SKEW_SECONDS = int(os.getenv("API_JWT_CLOCK_SKEW_SECONDS", "60"))


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"
    SERVICE = "service"


class PrincipalType(str, Enum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


@dataclass(frozen=True)
class JwtConfig:
    secret: str
    issuer: str = DEFAULT_ISSUER
    audience: str = DEFAULT_AUDIENCE
    algorithm: str = DEFAULT_ALGORITHM
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS
    refresh_ttl_seconds: int = DEFAULT_REFRESH_TTL_SECONDS
    clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS

    def __post_init__(self) -> None:
        if not self.secret or len(self.secret) < 16:
            raise JwtConfigError("JWT secret precisa ter pelo menos 16 caracteres")
        if self.algorithm != DEFAULT_ALGORITHM:
            raise JwtConfigError("Somente HS256 é suportado por este utilitário")
        if self.access_ttl_seconds <= 0:
            raise JwtConfigError("access_ttl_seconds deve ser positivo")
        if self.refresh_ttl_seconds <= 0:
            raise JwtConfigError("refresh_ttl_seconds deve ser positivo")


@dataclass(frozen=True)
class JwtClaims:
    sub: str
    iss: str
    aud: str
    exp: int
    iat: int
    nbf: int
    jti: str
    token_type: str
    scopes: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None
    principal_type: str = PrincipalType.USER.value
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["scope"] = " ".join(self.scopes)
        payload.pop("scopes", None)
        payload = {key: value for key, value in payload.items() if value is not None}
        return payload

    @staticmethod
    def from_payload(payload: Mapping[str, Any]) -> "JwtClaims":
        scopes = extract_scopes(payload)
        roles = extract_roles(payload)
        return JwtClaims(
            sub=str(payload.get("sub") or ""),
            iss=str(payload.get("iss") or ""),
            aud=str(payload.get("aud") or ""),
            exp=int(payload.get("exp") or 0),
            iat=int(payload.get("iat") or 0),
            nbf=int(payload.get("nbf") or 0),
            jti=str(payload.get("jti") or ""),
            token_type=str(payload.get("token_type") or TokenType.ACCESS.value),
            scopes=scopes,
            roles=roles,
            tenant_id=payload.get("tenant_id") or payload.get("tid"),
            principal_type=str(payload.get("principal_type") or PrincipalType.USER.value),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = DEFAULT_ACCESS_TTL_SECONDS
    refresh_expires_in: int = DEFAULT_REFRESH_TTL_SECONDS

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class JwtError(Exception):
    """Base JWT error."""


class JwtConfigError(JwtError):
    """JWT config error."""


class JwtValidationError(JwtError):
    """JWT validation error."""


class JwtExpiredError(JwtValidationError):
    """JWT expired."""


class JwtPermissionError(JwtValidationError):
    """JWT lacks required permissions."""


class JwtService:
    def __init__(self, config: JwtConfig) -> None:
        self.config = config

    @staticmethod
    def from_env() -> "JwtService":
        secret = os.getenv("API_JWT_SECRET")
        if not secret:
            raise JwtConfigError("API_JWT_SECRET não configurado")
        return JwtService(
            JwtConfig(
                secret=secret,
                issuer=os.getenv("API_JWT_ISSUER", DEFAULT_ISSUER),
                audience=os.getenv("API_JWT_AUDIENCE", DEFAULT_AUDIENCE),
                access_ttl_seconds=int(os.getenv("API_JWT_ACCESS_TTL_SECONDS", str(DEFAULT_ACCESS_TTL_SECONDS))),
                refresh_ttl_seconds=int(os.getenv("API_JWT_REFRESH_TTL_SECONDS", str(DEFAULT_REFRESH_TTL_SECONDS))),
                clock_skew_seconds=int(os.getenv("API_JWT_CLOCK_SKEW_SECONDS", str(DEFAULT_CLOCK_SKEW_SECONDS))),
            )
        )

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
            metadata=metadata or {},
            ttl_seconds=ttl_seconds or self.config.access_ttl_seconds,
        )

    def create_refresh_token(
        self,
        subject: str,
        tenant_id: Optional[str] = None,
        principal_type: PrincipalType = PrincipalType.USER,
        metadata: Optional[Mapping[str, Any]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        return self._create_token(
            subject=subject,
            token_type=TokenType.REFRESH,
            scopes=["token:refresh"],
            roles=[],
            tenant_id=tenant_id,
            principal_type=principal_type,
            metadata=metadata or {},
            ttl_seconds=ttl_seconds or self.config.refresh_ttl_seconds,
        )

    def create_service_token(
        self,
        subject: str,
        scopes: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
        tenant_id: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        return self._create_token(
            subject=subject,
            token_type=TokenType.SERVICE,
            scopes=scopes or ["service"],
            roles=roles or ["service"],
            tenant_id=tenant_id,
            principal_type=PrincipalType.SERVICE,
            metadata={"service_token": True},
            ttl_seconds=ttl_seconds or self.config.access_ttl_seconds,
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
            refresh_token=self.create_refresh_token(subject, tenant_id, principal_type, metadata),
            expires_in=self.config.access_ttl_seconds,
            refresh_expires_in=self.config.refresh_ttl_seconds,
        )

    def refresh_access_token(
        self,
        refresh_token: str,
        scopes: Optional[Sequence[str]] = None,
        roles: Optional[Sequence[str]] = None,
    ) -> str:
        claims = self.verify(refresh_token, expected_type=TokenType.REFRESH)
        return self.create_access_token(
            subject=claims.sub,
            scopes=scopes or [],
            roles=roles or claims.roles,
            tenant_id=claims.tenant_id,
            principal_type=PrincipalType(claims.principal_type),
            metadata={"refreshed_from_jti": claims.jti},
        )

    def verify(
        self,
        token: str,
        required_scopes: Optional[Sequence[str]] = None,
        expected_type: Optional[TokenType] = None,
    ) -> JwtClaims:
        header, payload, signature = decode_unverified(token)
        if header.get("alg") != self.config.algorithm:
            raise JwtValidationError("Algoritmo JWT inválido")
        signing_input = ".".join(token.split(".")[:2]).encode("utf-8")
        expected = hmac.new(self.config.secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64url_decode(signature)):
            raise JwtValidationError("Assinatura JWT inválida")
        claims = JwtClaims.from_payload(payload)
        self._validate_claims(claims, expected_type)
        if required_scopes:
            require_scopes(claims, required_scopes)
        return claims

    def fingerprint(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

    def _create_token(
        self,
        subject: str,
        token_type: TokenType,
        scopes: Sequence[str],
        roles: Sequence[str],
        tenant_id: Optional[str],
        principal_type: PrincipalType,
        metadata: Mapping[str, Any],
        ttl_seconds: int,
    ) -> str:
        if not subject or not subject.strip():
            raise JwtValidationError("subject é obrigatório")
        now = int(time.time())
        claims = JwtClaims(
            sub=subject.strip(),
            iss=self.config.issuer,
            aud=self.config.audience,
            exp=now + ttl_seconds,
            iat=now,
            nbf=now,
            jti=f"jti_{uuid.uuid4().hex}",
            token_type=token_type.value,
            scopes=normalize_scopes(scopes),
            roles=normalize_scopes(roles),
            tenant_id=tenant_id,
            principal_type=principal_type.value,
            metadata=safe_metadata(metadata),
        )
        header = {"typ": "JWT", "alg": self.config.algorithm, "ver": JWT_VERSION}
        payload = claims.to_payload()
        signing_input = f"{base64url_json(header)}.{base64url_json(payload)}"
        signature = hmac.new(self.config.secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
        return f"{signing_input}.{base64url_encode(signature)}"

    def _validate_claims(self, claims: JwtClaims, expected_type: Optional[TokenType]) -> None:
        now = int(time.time())
        skew = self.config.clock_skew_seconds
        if not claims.sub:
            raise JwtValidationError("Claim sub ausente")
        if claims.iss != self.config.issuer:
            raise JwtValidationError("Issuer inválido")
        if claims.aud != self.config.audience:
            raise JwtValidationError("Audience inválida")
        if claims.exp < now - skew:
            raise JwtExpiredError("Token expirado")
        if claims.nbf > now + skew:
            raise JwtValidationError("Token ainda não válido")
        if claims.iat > now + skew:
            raise JwtValidationError("Token emitido no futuro")
        if expected_type and claims.token_type != expected_type.value:
            raise JwtValidationError(f"Tipo de token inválido. Esperado={expected_type.value}, recebido={claims.token_type}")


def decode_unverified(token: str) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise JwtValidationError("JWT malformado")
    try:
        header = json.loads(base64url_decode(parts[0]).decode("utf-8"))
        payload = json.loads(base64url_decode(parts[1]).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise JwtValidationError("JWT inválido") from exc
    return header, payload, parts[2]


def require_scopes(claims: JwtClaims, required_scopes: Sequence[str], require_all: bool = True) -> None:
    if "*" in claims.scopes or "admin" in claims.scopes:
        return
    required = set(normalize_scopes(required_scopes))
    actual = set(claims.scopes)
    allowed = required.issubset(actual) if require_all else bool(required & actual)
    if not allowed:
        raise JwtPermissionError(f"Escopos insuficientes. Requerido: {', '.join(sorted(required))}")


def extract_scopes(payload: Mapping[str, Any]) -> List[str]:
    value = payload.get("scope", payload.get("scopes", []))
    if isinstance(value, str):
        return normalize_scopes(value.replace(",", " ").split())
    if isinstance(value, list):
        return normalize_scopes([str(item) for item in value])
    return []


def extract_roles(payload: Mapping[str, Any]) -> List[str]:
    value = payload.get("roles", payload.get("role", []))
    if isinstance(value, str):
        return normalize_scopes(value.replace(",", " ").split())
    if isinstance(value, list):
        return normalize_scopes([str(item) for item in value])
    return []


def normalize_scopes(values: Iterable[str]) -> List[str]:
    clean: List[str] = []
    seen = set()
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            clean.append(item)
    return clean


def safe_metadata(metadata: Mapping[str, Any], max_items: int = 50) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    sensitive_keys = {"password", "secret", "token", "api_key", "authorization"}
    for index, (key, value) in enumerate(metadata.items()):
        if index >= max_items:
            break
        key_text = str(key)
        if key_text.lower() in sensitive_keys:
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


def create_random_secret(length: int = 48) -> str:
    if length < 16:
        raise JwtConfigError("length mínimo é 16")
    return secrets.token_urlsafe(length)


def token_metadata(token: str) -> Dict[str, Any]:
    """Retorna metadados seguros do token sem validar assinatura e sem expor conteúdo sensível."""
    header, payload, _ = decode_unverified(token)
    return {
        "alg": header.get("alg"),
        "typ": header.get("typ"),
        "sub_hash": hashlib.sha256(str(payload.get("sub", "")).encode("utf-8")).hexdigest()[:12],
        "iss": payload.get("iss"),
        "aud": payload.get("aud"),
        "exp": payload.get("exp"),
        "iat": payload.get("iat"),
        "jti_hash": hashlib.sha256(str(payload.get("jti", "")).encode("utf-8")).hexdigest()[:12],
        "token_type": payload.get("token_type"),
        "scope_count": len(extract_scopes(payload)),
        "role_count": len(extract_roles(payload)),
    }


__all__ = [
    "TokenType",
    "PrincipalType",
    "JwtConfig",
    "JwtClaims",
    "TokenPair",
    "JwtService",
    "JwtError",
    "JwtConfigError",
    "JwtValidationError",
    "JwtExpiredError",
    "JwtPermissionError",
    "decode_unverified",
    "require_scopes",
    "create_random_secret",
    "token_metadata",
]
