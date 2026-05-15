#!/usr/bin/env python3
"""
core/config/settings.py

Enterprise-grade application settings.

Objetivo:
- Centralizar todas as configurações da aplicação em um único módulo confiável.
- Carregar valores de variáveis de ambiente e, opcionalmente, arquivo .env simples.
- Validar configurações críticas por ambiente.
- Expor settings imutáveis para API, auth, banco/Supabase, logging, CORS, modelos,
  segurança, observabilidade, limites operacionais e feature flags.

Uso:
    from core.config.settings import get_settings

    settings = get_settings()
    print(settings.api.name)

Variáveis principais:
    APP_ENV=development|staging|production|test
    API_NAME=Enterprise AI API
    API_VERSION=1.0.0
    API_HOST=0.0.0.0
    API_PORT=8000
    API_CORS_ORIGINS=*
    API_KEY=...
    API_JWT_SECRET=...
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
    LOG_LEVEL=INFO
    LOG_FORMAT=json

Notas:
- Este módulo usa somente biblioteca padrão para ser leve e fácil de portar.
- Para validação ainda mais forte, pode ser migrado para pydantic-settings depois.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set


SETTINGS_VERSION = "1.0.0"


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LogFormat(str, Enum):
    JSON = "json"
    TEXT = "text"


class AuthMode(str, Enum):
    DISABLED = "disabled"
    API_KEY = "api_key"
    JWT = "jwt"
    API_KEY_OR_JWT = "api_key_or_jwt"


class StorageBackend(str, Enum):
    LOCAL = "local"
    SUPABASE = "supabase"
    S3 = "s3"


@dataclass(frozen=True)
class ApiSettings:
    name: str = "Enterprise AI API"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""
    docs_enabled: bool = True
    reload: bool = False
    workers: int = 1
    request_timeout_seconds: float = 60.0
    max_request_body_bytes: int = 10_000_000


@dataclass(frozen=True)
class CorsSettings:
    origins: List[str] = field(default_factory=lambda: ["*"])
    methods: List[str] = field(default_factory=lambda: ["*"])
    headers: List[str] = field(default_factory=lambda: ["*"])
    allow_credentials: bool = True


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = True
    mode: AuthMode = AuthMode.API_KEY_OR_JWT
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


@dataclass(frozen=True)
class DatabaseSettings:
    url: Optional[str] = None
    host: Optional[str] = None
    port: int = 5432
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    pool_min_size: int = 1
    pool_max_size: int = 10
    connect_timeout_seconds: int = 10
    statement_timeout_seconds: int = 60
    ssl_mode: Optional[str] = None

    @property
    def configured(self) -> bool:
        return bool(self.url or (self.host and self.database and self.username))


@dataclass(frozen=True)
class SupabaseSettings:
    url: Optional[str] = None
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    user_jwt: Optional[str] = None
    schema: str = "public"
    timeout_seconds: float = 20.0
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.4

    @property
    def configured(self) -> bool:
        return bool(self.url and (self.service_role_key or self.anon_key))


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    format: LogFormat = LogFormat.JSON
    service_name: str = "enterprise-ai"
    file: Optional[str] = None
    rotation_bytes: int = 10_485_760
    backup_count: int = 5
    redact: bool = True
    include_source: bool = False


@dataclass(frozen=True)
class SecuritySettings:
    hash_entity_ids: bool = True
    redact_pii: bool = True
    allowed_hosts: List[str] = field(default_factory=lambda: ["*"])
    trusted_proxies: List[str] = field(default_factory=list)
    rate_limit_enabled: bool = False
    rate_limit_per_minute: int = 120
    idempotency_enabled: bool = True
    audit_enabled: bool = True


@dataclass(frozen=True)
class ModelSettings:
    model_registry_path: str = "models/registry"
    artifact_path: str = "models/artifacts"
    default_model_version: str = "latest"
    inference_timeout_seconds: float = 30.0
    enable_model_cache: bool = True
    model_cache_size: int = 16
    fallback_enabled: bool = True


@dataclass(frozen=True)
class ObservabilitySettings:
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    health_deep_enabled: bool = True
    build_sha: str = "unknown"
    build_time: str = "unknown"
    region: str = "local"
    release: str = "local"


@dataclass(frozen=True)
class StorageSettings:
    backend: StorageBackend = StorageBackend.LOCAL
    local_path: str = "storage"
    bucket: Optional[str] = None
    max_upload_bytes: int = 10_000_000


@dataclass(frozen=True)
class FeatureFlags:
    enable_fraud: bool = True
    enable_ueba: bool = True
    enable_finance: bool = True
    enable_payroll: bool = True
    enable_revenue: bool = True
    enable_documents: bool = True
    enable_nlp: bool = True
    enable_reports: bool = True
    enable_analytics: bool = True
    enable_experimental: bool = False


@dataclass(frozen=True)
class LimitsSettings:
    max_batch_size: int = 50_000
    max_rows: int = 100_000
    max_text_length: int = 200_000
    max_report_rows: int = 100_000
    max_concurrent_jobs: int = 4
    default_page_limit: int = 100
    max_page_limit: int = 10_000


@dataclass(frozen=True)
class AppSettings:
    environment: Environment
    debug: bool
    version: str
    api: ApiSettings
    cors: CorsSettings
    auth: AuthSettings
    database: DatabaseSettings
    supabase: SupabaseSettings
    logging: LoggingSettings
    security: SecuritySettings
    models: ModelSettings
    observability: ObservabilitySettings
    storage: StorageSettings
    features: FeatureFlags
    limits: LimitsSettings

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT

    @property
    def is_test(self) -> bool:
        return self.environment == Environment.TEST

    def public_metadata(self) -> Dict[str, Any]:
        return {
            "settings_version": SETTINGS_VERSION,
            "environment": self.environment.value,
            "debug": self.debug,
            "api": {
                "name": self.api.name,
                "version": self.api.version,
                "docs_enabled": self.api.docs_enabled,
                "root_path": self.api.root_path,
            },
            "auth": {
                "enabled": self.auth.enabled,
                "mode": self.auth.mode.value,
                "api_key_configured": bool(self.auth.api_key or self.auth.api_keys_json),
                "jwt_configured": bool(self.auth.jwt_secret),
                "require_tenant": self.auth.require_tenant,
            },
            "database": {"configured": self.database.configured},
            "supabase": {"configured": self.supabase.configured, "schema": self.supabase.schema},
            "logging": {"level": self.logging.level, "format": self.logging.format.value},
            "observability": {
                "metrics_enabled": self.observability.metrics_enabled,
                "tracing_enabled": self.observability.tracing_enabled,
                "region": self.observability.region,
                "build_sha": self.observability.build_sha,
            },
            "features": self.features.__dict__,
            "limits": self.limits.__dict__,
        }

    def validate(self) -> List[str]:
        warnings: List[str] = []
        errors: List[str] = []

        if self.is_production:
            if self.debug:
                errors.append("DEBUG não pode estar ativo em produção")
            if self.api.docs_enabled:
                warnings.append("API docs estão habilitados em produção")
            if self.auth.enabled and self.auth.mode in {AuthMode.JWT, AuthMode.API_KEY_OR_JWT} and not self.auth.jwt_secret:
                warnings.append("JWT habilitado sem API_JWT_SECRET")
            if self.auth.enabled and self.auth.mode in {AuthMode.API_KEY, AuthMode.API_KEY_OR_JWT} and not (self.auth.api_key or self.auth.api_keys_json):
                warnings.append("API key habilitada sem API_KEY/API_KEYS_JSON")
            if self.auth.jwt_secret and len(self.auth.jwt_secret) < 32:
                errors.append("API_JWT_SECRET deve ter pelo menos 32 caracteres em produção")
            if "*" in self.cors.origins:
                warnings.append("CORS wildcard habilitado em produção")
            if "*" in self.security.allowed_hosts:
                warnings.append("allowed_hosts wildcard habilitado em produção")
            if self.supabase.configured and not self.supabase.service_role_key:
                warnings.append("Supabase configurado sem service role key para backend")

        if self.api.port <= 0 or self.api.port > 65535:
            errors.append("API_PORT inválida")
        if self.limits.default_page_limit > self.limits.max_page_limit:
            errors.append("default_page_limit não pode exceder max_page_limit")
        if self.database.pool_min_size > self.database.pool_max_size:
            errors.append("DB_POOL_MIN_SIZE não pode exceder DB_POOL_MAX_SIZE")

        if errors:
            raise SettingsValidationError(errors)
        return warnings


class SettingsError(Exception):
    """Base settings error."""


class SettingsValidationError(SettingsError):
    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = list(errors)


class Env:
    def __init__(self, values: Optional[Mapping[str, str]] = None) -> None:
        self.values = dict(values or os.environ)

    def str(self, name: str, default: Optional[str] = None) -> Optional[str]:
        value = self.values.get(name)
        if value is None or value == "":
            return default
        return value

    def required_str(self, name: str) -> str:
        value = self.str(name)
        if value is None:
            raise SettingsError(f"Variável obrigatória ausente: {name}")
        return value

    def bool(self, name: str, default: bool = False) -> bool:
        value = self.str(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "sim", "s", "on"}

    def int(self, name: str, default: int) -> int:
        value = self.str(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise SettingsError(f"Variável {name} precisa ser int: {value}") from exc

    def float(self, name: str, default: float) -> float:
        value = self.str(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError as exc:
            raise SettingsError(f"Variável {name} precisa ser float: {value}") from exc

    def list(self, name: str, default: Optional[List[str]] = None, separator: str = ",") -> List[str]:
        value = self.str(name)
        if value is None:
            return list(default or [])
        return [item.strip() for item in value.split(separator) if item.strip()]

    def set(self, name: str, default: Optional[Set[str]] = None, separator: str = ",") -> Set[str]:
        return set(self.list(name, sorted(default or set()), separator))

    def json_dict(self, name: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        value = self.str(name)
        if value is None:
            return dict(default or {})
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SettingsError(f"Variável {name} precisa ser JSON válido") from exc
        if not isinstance(parsed, dict):
            raise SettingsError(f"Variável {name} precisa ser JSON object")
        return parsed


def load_dotenv(path: str = ".env", override: bool = False) -> None:
    file_path = Path(path)
    if not file_path.exists():
        return
    for line in file_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def build_settings(env: Optional[Env] = None) -> AppSettings:
    env = env or Env()
    environment = Environment(env.str("APP_ENV", env.str("API_ENV", "development")) or "development")
    debug = env.bool("DEBUG", default=environment == Environment.DEVELOPMENT)

    settings = AppSettings(
        environment=environment,
        debug=debug,
        version=SETTINGS_VERSION,
        api=ApiSettings(
            name=env.str("API_NAME", "Enterprise AI API") or "Enterprise AI API",
            version=env.str("API_VERSION", "1.0.0") or "1.0.0",
            host=env.str("API_HOST", "0.0.0.0") or "0.0.0.0",
            port=env.int("API_PORT", 8000),
            root_path=env.str("API_ROOT_PATH", "") or "",
            docs_enabled=env.bool("API_ENABLE_DOCS", default=environment != Environment.PRODUCTION),
            reload=env.bool("API_RELOAD", default=environment == Environment.DEVELOPMENT),
            workers=env.int("API_WORKERS", 1),
            request_timeout_seconds=env.float("API_REQUEST_TIMEOUT_SECONDS", 60.0),
            max_request_body_bytes=env.int("API_MAX_REQUEST_BODY_BYTES", 10_000_000),
        ),
        cors=CorsSettings(
            origins=env.list("API_CORS_ORIGINS", ["*"]),
            methods=env.list("API_CORS_METHODS", ["*"]),
            headers=env.list("API_CORS_HEADERS", ["*"]),
            allow_credentials=env.bool("API_CORS_ALLOW_CREDENTIALS", True),
        ),
        auth=AuthSettings(
            enabled=env.bool("API_AUTH_ENABLED", True),
            mode=AuthMode(env.str("API_AUTH_MODE", "api_key_or_jwt") or "api_key_or_jwt"),
            api_key=env.str("API_KEY"),
            api_keys_json=env.str("API_KEYS_JSON", "") or "",
            jwt_secret=env.str("API_JWT_SECRET"),
            jwt_issuer=env.str("API_JWT_ISSUER", "enterprise-ai-api") or "enterprise-ai-api",
            jwt_audience=env.str("API_JWT_AUDIENCE", "enterprise-ai-clients") or "enterprise-ai-clients",
            jwt_access_ttl_seconds=env.int("API_JWT_ACCESS_TTL_SECONDS", 900),
            jwt_refresh_ttl_seconds=env.int("API_JWT_REFRESH_TTL_SECONDS", 604800),
            jwt_clock_skew_seconds=env.int("API_JWT_CLOCK_SKEW_SECONDS", 60),
            require_tenant=env.bool("API_REQUIRE_TENANT", False),
            admin_scopes=env.set("API_ADMIN_SCOPES", {"admin", "system:admin"}),
        ),
        database=DatabaseSettings(
            url=env.str("DATABASE_URL"),
            host=env.str("DB_HOST"),
            port=env.int("DB_PORT", 5432),
            database=env.str("DB_NAME"),
            username=env.str("DB_USER"),
            password=env.str("DB_PASSWORD"),
            pool_min_size=env.int("DB_POOL_MIN_SIZE", 1),
            pool_max_size=env.int("DB_POOL_MAX_SIZE", 10),
            connect_timeout_seconds=env.int("DB_CONNECT_TIMEOUT_SECONDS", 10),
            statement_timeout_seconds=env.int("DB_STATEMENT_TIMEOUT_SECONDS", 60),
            ssl_mode=env.str("DB_SSL_MODE"),
        ),
        supabase=SupabaseSettings(
            url=(env.str("SUPABASE_URL") or "").rstrip("/") or None,
            anon_key=env.str("SUPABASE_ANON_KEY"),
            service_role_key=env.str("SUPABASE_SERVICE_ROLE_KEY"),
            user_jwt=env.str("SUPABASE_JWT"),
            schema=env.str("SUPABASE_SCHEMA", "public") or "public",
            timeout_seconds=env.float("SUPABASE_TIMEOUT_SECONDS", 20.0),
            retry_attempts=env.int("SUPABASE_RETRY_ATTEMPTS", 3),
            retry_backoff_seconds=env.float("SUPABASE_RETRY_BACKOFF_SECONDS", 0.4),
        ),
        logging=LoggingSettings(
            level=env.str("LOG_LEVEL", "INFO") or "INFO",
            format=LogFormat(env.str("LOG_FORMAT", "json") or "json"),
            service_name=env.str("LOG_SERVICE_NAME", env.str("API_NAME", "enterprise-ai")) or "enterprise-ai",
            file=env.str("LOG_FILE"),
            rotation_bytes=env.int("LOG_ROTATION_BYTES", 10_485_760),
            backup_count=env.int("LOG_BACKUP_COUNT", 5),
            redact=env.bool("LOG_REDACT", True),
            include_source=env.bool("LOG_INCLUDE_SOURCE", False),
        ),
        security=SecuritySettings(
            hash_entity_ids=env.bool("API_HASH_ENTITY_IDS", True),
            redact_pii=env.bool("API_REDACT_PII", True),
            allowed_hosts=env.list("API_ALLOWED_HOSTS", ["*"]),
            trusted_proxies=env.list("API_TRUSTED_PROXIES", []),
            rate_limit_enabled=env.bool("API_RATE_LIMIT_ENABLED", False),
            rate_limit_per_minute=env.int("API_RATE_LIMIT_PER_MINUTE", 120),
            idempotency_enabled=env.bool("API_IDEMPOTENCY_ENABLED", True),
            audit_enabled=env.bool("API_AUDIT_ENABLED", True),
        ),
        models=ModelSettings(
            model_registry_path=env.str("MODEL_REGISTRY_PATH", "models/registry") or "models/registry",
            artifact_path=env.str("MODEL_ARTIFACT_PATH", "models/artifacts") or "models/artifacts",
            default_model_version=env.str("DEFAULT_MODEL_VERSION", "latest") or "latest",
            inference_timeout_seconds=env.float("MODEL_INFERENCE_TIMEOUT_SECONDS", 30.0),
            enable_model_cache=env.bool("MODEL_CACHE_ENABLED", True),
            model_cache_size=env.int("MODEL_CACHE_SIZE", 16),
            fallback_enabled=env.bool("MODEL_FALLBACK_ENABLED", True),
        ),
        observability=ObservabilitySettings(
            metrics_enabled=env.bool("METRICS_ENABLED", True),
            tracing_enabled=env.bool("TRACING_ENABLED", False),
            health_deep_enabled=env.bool("API_HEALTH_DEEP_ENABLED", True),
            build_sha=env.str("API_BUILD_SHA", "unknown") or "unknown",
            build_time=env.str("API_BUILD_TIME", "unknown") or "unknown",
            region=env.str("API_REGION", "local") or "local",
            release=env.str("API_RELEASE", "local") or "local",
        ),
        storage=StorageSettings(
            backend=StorageBackend(env.str("STORAGE_BACKEND", "local") or "local"),
            local_path=env.str("STORAGE_LOCAL_PATH", "storage") or "storage",
            bucket=env.str("STORAGE_BUCKET"),
            max_upload_bytes=env.int("STORAGE_MAX_UPLOAD_BYTES", 10_000_000),
        ),
        features=FeatureFlags(
            enable_fraud=env.bool("FEATURE_FRAUD", True),
            enable_ueba=env.bool("FEATURE_UEBA", True),
            enable_finance=env.bool("FEATURE_FINANCE", True),
            enable_payroll=env.bool("FEATURE_PAYROLL", True),
            enable_revenue=env.bool("FEATURE_REVENUE", True),
            enable_documents=env.bool("FEATURE_DOCUMENTS", True),
            enable_nlp=env.bool("FEATURE_NLP", True),
            enable_reports=env.bool("FEATURE_REPORTS", True),
            enable_analytics=env.bool("FEATURE_ANALYTICS", True),
            enable_experimental=env.bool("FEATURE_EXPERIMENTAL", False),
        ),
        limits=LimitsSettings(
            max_batch_size=env.int("LIMIT_MAX_BATCH_SIZE", 50_000),
            max_rows=env.int("LIMIT_MAX_ROWS", 100_000),
            max_text_length=env.int("LIMIT_MAX_TEXT_LENGTH", 200_000),
            max_report_rows=env.int("LIMIT_MAX_REPORT_ROWS", 100_000),
            max_concurrent_jobs=env.int("LIMIT_MAX_CONCURRENT_JOBS", 4),
            default_page_limit=env.int("LIMIT_DEFAULT_PAGE", 100),
            max_page_limit=env.int("LIMIT_MAX_PAGE", 10_000),
        ),
    )
    settings.validate()
    return settings


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    dotenv_path = os.getenv("ENV_FILE", ".env")
    load_dotenv(dotenv_path, override=False)
    return build_settings()


def reload_settings() -> AppSettings:
    get_settings.cache_clear()
    return get_settings()


def settings_health() -> Dict[str, Any]:
    settings = get_settings()
    warnings = settings.validate()
    return {
        "status": "ok",
        "version": SETTINGS_VERSION,
        "warnings": warnings,
        "metadata": settings.public_metadata(),
    }


def generate_secret(length: int = 48) -> str:
    if length < 16:
        raise SettingsError("length mínimo é 16")
    return secrets.token_urlsafe(length)


__all__ = [
    "SETTINGS_VERSION",
    "Environment",
    "LogFormat",
    "AuthMode",
    "StorageBackend",
    "ApiSettings",
    "CorsSettings",
    "AuthSettings",
    "DatabaseSettings",
    "SupabaseSettings",
    "LoggingSettings",
    "SecuritySettings",
    "ModelSettings",
    "ObservabilitySettings",
    "StorageSettings",
    "FeatureFlags",
    "LimitsSettings",
    "AppSettings",
    "SettingsError",
    "SettingsValidationError",
    "Env",
    "load_dotenv",
    "build_settings",
    "get_settings",
    "reload_settings",
    "settings_health",
    "generate_secret",
]


