#!/usr/bin/env python3
"""
api/core/security.py

Enterprise-grade API security utilities.

Objetivo:
- Centralizar utilitários de segurança para APIs FastAPI e serviços internos.
- Aplicar headers seguros, validação de origem, IP allow/deny, assinatura HMAC,
  proteção contra replay, sanitização, hashing, mascaramento e política de senhas.
- Fornecer middlewares/dependencies reutilizáveis sem dependências externas obrigatórias.

Uso FastAPI:
    from fastapi import FastAPI, Depends
    from api.core.security import SecuritySettings, SecurityHeadersMiddleware, verify_request_signature

    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, settings=SecuritySettings.from_env())

    @app.post('/webhook', dependencies=[Depends(verify_request_signature(SecuritySettings.from_env()))])
    async def webhook():
        return {'ok': True}

Variáveis de ambiente:
    SECURITY_HMAC_SECRET=troque-em-producao
    SECURITY_ALLOWED_IPS=127.0.0.1,10.0.0.0/8
    SECURITY_DENIED_IPS=
    SECURITY_ALLOWED_ORIGINS=https://app.example.com
    SECURITY_REQUIRE_SIGNATURE=false
    SECURITY_REPLAY_TTL_SECONDS=300
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from fastapi import Header, HTTPException, Request, Response, status
    from starlette.middleware.base import BaseHTTPMiddleware
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Dependências ausentes. Instale com: pip install fastapi starlette") from exc


SECURITY_VERSION = "1.0.0"
UTC = timezone.utc
logger = logging.getLogger(__name__)


class PasswordStrength(str, Enum):
    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"
    VERY_STRONG = "very_strong"


class SecurityDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CHALLENGE = "challenge"


@dataclass(frozen=True)
class SecuritySettings:
    hmac_secret: str = "change-me-in-production"
    require_signature: bool = False
    signature_header: str = "x-signature"
    timestamp_header: str = "x-timestamp"
    nonce_header: str = "x-nonce"
    replay_ttl_seconds: int = 300
    allowed_ips: List[str] = field(default_factory=list)
    denied_ips: List[str] = field(default_factory=list)
    allowed_origins: List[str] = field(default_factory=lambda: ["*"])
    max_body_bytes: int = 1_048_576
    enable_hsts: bool = True
    hsts_max_age: int = 31_536_000
    frame_options: str = "DENY"
    content_type_options: str = "nosniff"
    referrer_policy: str = "no-referrer"
    permissions_policy: str = "geolocation=(), microphone=(), camera=()"
    content_security_policy: str = "default-src 'self'; frame-ancestors 'none'; object-src 'none'"
    mask_secrets_in_logs: bool = True
    min_password_length: int = 12
    password_require_upper: bool = True
    password_require_lower: bool = True
    password_require_digit: bool = True
    password_require_symbol: bool = True

    @staticmethod
    def from_env() -> "SecuritySettings":
        return SecuritySettings(
            hmac_secret=os.getenv("SECURITY_HMAC_SECRET", "change-me-in-production"),
            require_signature=parse_bool(os.getenv("SECURITY_REQUIRE_SIGNATURE"), False),
            signature_header=os.getenv("SECURITY_SIGNATURE_HEADER", "x-signature"),
            timestamp_header=os.getenv("SECURITY_TIMESTAMP_HEADER", "x-timestamp"),
            nonce_header=os.getenv("SECURITY_NONCE_HEADER", "x-nonce"),
            replay_ttl_seconds=int(os.getenv("SECURITY_REPLAY_TTL_SECONDS", "300")),
            allowed_ips=parse_csv(os.getenv("SECURITY_ALLOWED_IPS", "")),
            denied_ips=parse_csv(os.getenv("SECURITY_DENIED_IPS", "")),
            allowed_origins=parse_csv(os.getenv("SECURITY_ALLOWED_ORIGINS", "*")),
            max_body_bytes=int(os.getenv("SECURITY_MAX_BODY_BYTES", "1048576")),
            enable_hsts=parse_bool(os.getenv("SECURITY_ENABLE_HSTS"), True),
            hsts_max_age=int(os.getenv("SECURITY_HSTS_MAX_AGE", "31536000")),
            frame_options=os.getenv("SECURITY_FRAME_OPTIONS", "DENY"),
            content_type_options=os.getenv("SECURITY_CONTENT_TYPE_OPTIONS", "nosniff"),
            referrer_policy=os.getenv("SECURITY_REFERRER_POLICY", "no-referrer"),
            permissions_policy=os.getenv("SECURITY_PERMISSIONS_POLICY", "geolocation=(), microphone=(), camera=()"),
            content_security_policy=os.getenv("SECURITY_CSP", "default-src 'self'; frame-ancestors 'none'; object-src 'none'"),
            mask_secrets_in_logs=parse_bool(os.getenv("SECURITY_MASK_SECRETS_IN_LOGS"), True),
            min_password_length=int(os.getenv("SECURITY_MIN_PASSWORD_LENGTH", "12")),
        )


@dataclass(frozen=True)
class PasswordValidationResult:
    valid: bool
    strength: PasswordStrength
    score: int
    errors: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "strength": self.strength.value,
            "score": self.score,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class SignatureValidationResult:
    valid: bool
    reason: Optional[str] = None
    timestamp: Optional[int] = None
    nonce: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }


@dataclass(frozen=True)
class IpDecision:
    decision: SecurityDecision
    reason: str
    ip_address: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "ip_address": self.ip_address,
        }


class ReplayNonceStore:
    """In-memory nonce store for replay protection."""

    def __init__(self) -> None:
        self._nonces: Dict[str, int] = {}

    def seen(self, nonce: str, ttl_seconds: int) -> bool:
        self.cleanup(ttl_seconds)
        return nonce in self._nonces

    def add(self, nonce: str) -> None:
        self._nonces[nonce] = now_epoch()

    def cleanup(self, ttl_seconds: int) -> None:
        cutoff = now_epoch() - ttl_seconds
        for nonce, created_at in list(self._nonces.items()):
            if created_at < cutoff:
                self._nonces.pop(nonce, None)


nonce_store = ReplayNonceStore()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, settings: Optional[SecuritySettings] = None) -> None:
        super().__init__(app)
        self.settings = settings or SecuritySettings.from_env()

    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.settings.max_body_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large")

        ip_decision = evaluate_ip_access(client_ip(request), self.settings)
        if ip_decision.decision == SecurityDecision.DENY:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ip_decision.reason)

        response = await call_next(request)
        apply_security_headers(response, self.settings)
        return response


def apply_security_headers(response: Response, settings: Optional[SecuritySettings] = None) -> None:
    cfg = settings or SecuritySettings.from_env()
    response.headers["X-Content-Type-Options"] = cfg.content_type_options
    response.headers["X-Frame-Options"] = cfg.frame_options
    response.headers["Referrer-Policy"] = cfg.referrer_policy
    response.headers["Permissions-Policy"] = cfg.permissions_policy
    response.headers["Content-Security-Policy"] = cfg.content_security_policy
    response.headers["X-Security-Version"] = SECURITY_VERSION
    if cfg.enable_hsts:
        response.headers["Strict-Transport-Security"] = f"max-age={cfg.hsts_max_age}; includeSubDomains"


def verify_request_signature(settings: Optional[SecuritySettings] = None) -> Callable[..., Any]:
    cfg = settings or SecuritySettings.from_env()

    async def dependency(request: Request) -> None:
        if not cfg.require_signature:
            return
        body = await request.body()
        signature = request.headers.get(cfg.signature_header)
        timestamp_raw = request.headers.get(cfg.timestamp_header)
        nonce = request.headers.get(cfg.nonce_header)
        result = validate_signature(
            method=request.method,
            path=request.url.path,
            body=body,
            signature=signature,
            timestamp_raw=timestamp_raw,
            nonce=nonce,
            settings=cfg,
        )
        if not result.valid:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.reason or "Invalid signature")

    return dependency


def validate_signature(
    method: str,
    path: str,
    body: bytes,
    signature: Optional[str],
    timestamp_raw: Optional[str],
    nonce: Optional[str],
    settings: SecuritySettings,
) -> SignatureValidationResult:
    if not signature:
        return SignatureValidationResult(False, "missing_signature")
    if not timestamp_raw:
        return SignatureValidationResult(False, "missing_timestamp")
    if not nonce:
        return SignatureValidationResult(False, "missing_nonce")
    try:
        timestamp_value = int(timestamp_raw)
    except ValueError:
        return SignatureValidationResult(False, "invalid_timestamp")

    current = now_epoch()
    if abs(current - timestamp_value) > settings.replay_ttl_seconds:
        return SignatureValidationResult(False, "timestamp_outside_replay_window", timestamp_value, nonce)
    if nonce_store.seen(nonce, settings.replay_ttl_seconds):
        return SignatureValidationResult(False, "nonce_replay_detected", timestamp_value, nonce)

    expected = sign_request(
        method=method,
        path=path,
        body=body,
        timestamp=timestamp_value,
        nonce=nonce,
        secret=settings.hmac_secret,
    )
    normalized_signature = normalize_signature(signature)
    if not hmac.compare_digest(expected, normalized_signature):
        return SignatureValidationResult(False, "signature_mismatch", timestamp_value, nonce)

    nonce_store.add(nonce)
    return SignatureValidationResult(True, None, timestamp_value, nonce)


def sign_request(method: str, path: str, body: bytes, timestamp: int, nonce: str, secret: str) -> str:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    canonical = "\n".join([method.upper(), path, str(timestamp), nonce, body_hash])
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def normalize_signature(signature: str) -> str:
    value = signature.strip()
    if value.startswith("sha256="):
        return value
    return f"sha256={value}"


def evaluate_ip_access(ip_value: Optional[str], settings: SecuritySettings) -> IpDecision:
    if not ip_value:
        return IpDecision(SecurityDecision.ALLOW, "missing_ip_allowed", None)

    if matches_ip_list(ip_value, settings.denied_ips):
        return IpDecision(SecurityDecision.DENY, "ip_denied", ip_value)

    if settings.allowed_ips and not matches_ip_list(ip_value, settings.allowed_ips):
        return IpDecision(SecurityDecision.DENY, "ip_not_allowed", ip_value)

    return IpDecision(SecurityDecision.ALLOW, "ip_allowed", ip_value)


def matches_ip_list(ip_value: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_value)
    except ValueError:
        return False
    for pattern in patterns:
        text = pattern.strip()
        if not text:
            continue
        try:
            if "/" in text:
                if ip_obj in ipaddress.ip_network(text, strict=False):
                    return True
            elif ip_obj == ipaddress.ip_address(text):
                return True
        except ValueError:
            continue
    return False


def origin_allowed(origin: Optional[str], settings: SecuritySettings) -> bool:
    if not origin:
        return True
    if "*" in settings.allowed_origins:
        return True
    normalized = origin.rstrip("/").lower()
    return normalized in {item.rstrip("/").lower() for item in settings.allowed_origins}


def validate_password(password: str, settings: Optional[SecuritySettings] = None) -> PasswordValidationResult:
    cfg = settings or SecuritySettings.from_env()
    errors: List[str] = []
    warnings: List[str] = []
    score = 0

    if len(password) < cfg.min_password_length:
        errors.append(f"password_min_length_{cfg.min_password_length}")
    else:
        score += 25

    if cfg.password_require_upper and not re.search(r"[A-Z]", password):
        errors.append("password_requires_uppercase")
    else:
        score += 15

    if cfg.password_require_lower and not re.search(r"[a-z]", password):
        errors.append("password_requires_lowercase")
    else:
        score += 15

    if cfg.password_require_digit and not re.search(r"\d", password):
        errors.append("password_requires_digit")
    else:
        score += 15

    if cfg.password_require_symbol and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("password_requires_symbol")
    else:
        score += 15

    if has_repeated_sequence(password):
        warnings.append("password_has_repeated_sequence")
        score -= 10

    if is_common_password(password):
        errors.append("password_is_common")
        score -= 30

    if len(password) >= 20:
        score += 15

    score = max(0, min(score, 100))
    if score >= 85:
        strength = PasswordStrength.VERY_STRONG
    elif score >= 70:
        strength = PasswordStrength.STRONG
    elif score >= 45:
        strength = PasswordStrength.MEDIUM
    else:
        strength = PasswordStrength.WEAK

    return PasswordValidationResult(valid=not errors, strength=strength, score=score, errors=errors, warnings=warnings)


def has_repeated_sequence(password: str) -> bool:
    return bool(re.search(r"(.)\1\1", password))


def is_common_password(password: str) -> bool:
    common = {
        "password", "password123", "123456", "123456789", "qwerty", "admin", "admin123",
        "senha", "senha123", "superassis", "redefort", "letmein", "welcome",
    }
    return password.lower() in common


def hash_secret(value: str, salt: Optional[bytes] = None, iterations: int = 210_000) -> str:
    if not value:
        raise ValueError("value is required")
    salt_value = salt or secrets.token_bytes(32)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt_value, iterations)
    return "pbkdf2_sha256${}${}${}".format(iterations, b64url_encode(salt_value), b64url_encode(digest))


def verify_secret(value: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = b64url_decode(salt_raw)
        expected = b64url_decode(digest_raw)
        actual = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def secure_token(prefix: str = "tok", nbytes: int = 32) -> str:
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def sanitize_text(value: Any, max_length: int = 10_000) -> str:
    text = "" if value is None else str(value)
    text = text[:max_length]
    text = html.escape(text, quote=True)
    text = CONTROL_CHARS_RE.sub("", text)
    return text


def sanitize_dict(payload: Mapping[str, Any], max_depth: int = 5) -> Dict[str, Any]:
    return _sanitize_value(dict(payload), max_depth=max_depth, current_depth=0)


def _sanitize_value(value: Any, max_depth: int, current_depth: int) -> Any:
    if current_depth > max_depth:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        return {sanitize_text(key, 256): _sanitize_value(val, max_depth, current_depth + 1) for key, val in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, max_depth, current_depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(value)


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, val in value.items():
            if is_sensitive_key(str(key)):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = mask_sensitive(val)
        return result
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return mask_sensitive_string(value)
    return value


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    sensitive_terms = ["password", "secret", "token", "api_key", "apikey", "authorization", "cookie", "credential", "jwt"]
    return any(term in lowered for term in sensitive_terms)


def mask_sensitive_string(value: str) -> str:
    text = EMAIL_RE.sub("[EMAIL]", value)
    text = TOKEN_RE.sub("[TOKEN]", text)
    text = CREDIT_CARD_RE.sub("[CARD]", text)
    return text


def hash_payload(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None


def is_https_request(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto.lower() == "https"


def require_https(request: Request) -> None:
    if not is_https_request(request):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="HTTPS required")


def require_allowed_origin(settings: Optional[SecuritySettings] = None) -> Callable[..., Any]:
    cfg = settings or SecuritySettings.from_env()

    async def dependency(request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin_allowed(origin, cfg):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed")

    return dependency


def require_ip_allowed(settings: Optional[SecuritySettings] = None) -> Callable[..., Any]:
    cfg = settings or SecuritySettings.from_env()

    async def dependency(request: Request) -> None:
        decision = evaluate_ip_access(client_ip(request), cfg)
        if decision.decision == SecurityDecision.DENY:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)

    return dependency


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))


def parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "sim", "s"}


def parse_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def now_epoch() -> int:
    return int(time.time())


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_RE = re.compile(r"\b(?:Bearer\s+)?[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{10,}\.?[A-Za-z0-9_\-]*\b")
CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


__all__ = [
    "SecuritySettings",
    "PasswordValidationResult",
    "SignatureValidationResult",
    "IpDecision",
    "SecurityHeadersMiddleware",
    "apply_security_headers",
    "verify_request_signature",
    "validate_signature",
    "sign_request",
    "evaluate_ip_access",
    "origin_allowed",
    "validate_password",
    "hash_secret",
    "verify_secret",
    "secure_token",
    "sanitize_text",
    "sanitize_dict",
    "mask_sensitive",
    "hash_payload",
    "client_ip",
    "require_https",
    "require_allowed_origin",
    "require_ip_allowed",
]
