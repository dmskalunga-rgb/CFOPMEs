"""
kwanza-ai-core/core/config.py

Enterprise-grade configuration core for Kwanza AI Core.

Purpose
-------
Provide a single, typed, validated and secure configuration layer for the entire
application: API, security, database, Supabase, Redis, queues, storage, ML,
observability, audit, governance, payroll, revenue and feature flags.

Design goals
------------
- Strongly typed settings with dataclasses.
- Environment-first configuration with optional .env loading.
- Zero hard dependency on pydantic to keep the core lightweight.
- Clear validation errors at startup.
- Safe secret handling in logs/JSON output.
- Centralized feature flags.
- Helpers for URLs, booleans, lists, numbers and paths.
- Production-ready defaults with explicit CHANGE_ME detection.

Usage
-----
from kwanza_ai_core.core.config import get_settings

settings = get_settings()
print(settings.app.name)

Security note
-------------
Never print raw settings in production unless using `settings.safe_dict()`.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, TypeVar


# =============================================================================
# Exceptions
# =============================================================================


class ConfigError(RuntimeError):
    """Base configuration error."""


class ConfigValidationError(ConfigError):
    """Raised when configuration validation fails."""


# =============================================================================
# Enums
# =============================================================================


class AppEnvironment(str, Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


class SupabaseAuthMode(str, Enum):
    ANON = "anon"
    SERVICE_ROLE = "service_role"
    JWT = "jwt"


class ArtifactStoreProvider(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    SUPABASE = "supabase"


class MetricsProvider(str, Enum):
    NONE = "none"
    PROMETHEUS = "prometheus"
    STATSD = "statsd"
    OTEL = "opentelemetry"


class AuditSinkType(str, Enum):
    NONE = "none"
    DATABASE = "database"
    FILE = "file"
    WEBHOOK = "webhook"


class QueueProvider(str, Enum):
    REDIS = "redis"
    RABBITMQ = "rabbitmq"
    SQS = "sqs"
    MEMORY = "memory"


# =============================================================================
# Helpers
# =============================================================================


_SECRET_MARKERS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "KEY",
    "PRIVATE",
    "CREDENTIAL",
    "DSN",
    "WEBHOOK",
)

_CHANGE_ME_PREFIXES = ("CHANGE_ME", "replace_me", "REPLACE_ME", "todo", "TODO")

T = TypeVar("T")


def _getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_str(name: str, default: str = "") -> str:
    return str(_getenv(name, default))


def env_optional_str(name: str, default: Optional[str] = None) -> Optional[str]:
    return _getenv(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = _getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int = 0) -> int:
    value = _getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigValidationError(f"Environment variable {name} must be an integer.") from exc


def env_float(name: str, default: float = 0.0) -> float:
    value = _getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigValidationError(f"Environment variable {name} must be a float.") from exc


def env_list(name: str, default: Optional[Sequence[str]] = None, separator: str = ",") -> Tuple[str, ...]:
    value = _getenv(name)
    if value is None:
        return tuple(default or ())
    return tuple(item.strip() for item in value.split(separator) if item.strip())


def env_path(name: str, default: str) -> Path:
    return Path(env_str(name, default)).expanduser()


def env_enum(name: str, enum_type: type[T], default: T) -> T:
    value = _getenv(name)
    if value is None:
        return default
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise ConfigValidationError(f"Environment variable {name} must be one of: {allowed}.") from exc


def parse_env_file(path: Path, override: bool = False) -> None:
    """Minimal .env loader to avoid hard dependency on python-dotenv."""
    if not path.exists():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ConfigValidationError(f"Invalid .env line {line_number}: expected KEY=VALUE.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            raise ConfigValidationError(f"Invalid .env line {line_number}: empty key.")
        if override or key not in os.environ:
            os.environ[key] = value


def is_secret_field(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def mask_secret(value: Any) -> Any:
    if value is None or value == "":
        return value
    text = str(value)
    if len(text) <= 8:
        return "********"
    return f"{text[:4]}...{text[-4:]}"


def has_change_me(value: Optional[str]) -> bool:
    if not value:
        return False
    return any(str(value).startswith(prefix) for prefix in _CHANGE_ME_PREFIXES)


def dataclass_safe_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        output: Dict[str, Any] = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            output[f.name] = mask_secret(value) if is_secret_field(f.name) else dataclass_safe_dict(value)
        return output
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Mapping):
        return {k: mask_secret(v) if is_secret_field(str(k)) else dataclass_safe_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [dataclass_safe_dict(item) for item in obj]
    return obj


# =============================================================================
# Settings sections
# =============================================================================


@dataclass(frozen=True)
class AppSettings:
    name: str = "kwanza-ai-core"
    environment: AppEnvironment = AppEnvironment.DEVELOPMENT
    version: str = "1.0.0"
    debug: bool = False
    timezone: str = "UTC"
    locale: str = "pt_AO"
    default_currency: str = "AOA"
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    request_timeout_seconds: int = 60
    max_request_body_mb: int = 25
    cors_origins: Tuple[str, ...] = field(default_factory=tuple)
    trusted_hosts: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> "AppSettings":
        return cls(
            name=env_str("APP_NAME", "kwanza-ai-core"),
            environment=env_enum("APP_ENV", AppEnvironment, AppEnvironment.DEVELOPMENT),
            version=env_str("APP_VERSION", "1.0.0"),
            debug=env_bool("APP_DEBUG", False),
            timezone=env_str("APP_TIMEZONE", "UTC"),
            locale=env_str("APP_LOCALE", "pt_AO"),
            default_currency=env_str("APP_DEFAULT_CURRENCY", "AOA"),
            host=env_str("APP_HOST", "0.0.0.0"),
            port=env_int("APP_PORT", 8000),
            workers=env_int("APP_WORKERS", 4),
            request_timeout_seconds=env_int("APP_REQUEST_TIMEOUT_SECONDS", 60),
            max_request_body_mb=env_int("APP_MAX_REQUEST_BODY_MB", 25),
            cors_origins=env_list("APP_CORS_ORIGINS", ("http://localhost:3000", "http://localhost:5173")),
            trusted_hosts=env_list("APP_TRUSTED_HOSTS", ("localhost", "127.0.0.1")),
        )

    def validate(self) -> None:
        if self.port <= 0 or self.port > 65535:
            raise ConfigValidationError("APP_PORT must be between 1 and 65535.")
        if self.workers <= 0:
            raise ConfigValidationError("APP_WORKERS must be positive.")
        if self.environment == AppEnvironment.PRODUCTION and self.debug:
            raise ConfigValidationError("APP_DEBUG must be false in production.")


@dataclass(frozen=True)
class SecuritySettings:
    secret_key: str
    encryption_key: Optional[str]
    hash_salt: str
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30
    api_key_header_name: str = "X-API-Key"
    admin_api_key: Optional[str] = None
    internal_service_token: Optional[str] = None
    enable_rate_limiting: bool = True
    rate_limit_requests_per_minute: int = 120
    enable_ip_allowlist: bool = False
    ip_allowlist: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls) -> "SecuritySettings":
        return cls(
            secret_key=env_str("SECRET_KEY", "CHANGE_ME_GENERATE_64_CHAR_RANDOM_SECRET"),
            encryption_key=env_optional_str("ENCRYPTION_KEY"),
            hash_salt=env_str("HASH_SALT", "CHANGE_ME_RANDOM_HASH_SALT"),
            jwt_secret=env_str("JWT_SECRET", "CHANGE_ME_JWT_SECRET"),
            jwt_algorithm=env_str("JWT_ALGORITHM", "HS256"),
            jwt_access_token_expire_minutes=env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 60),
            jwt_refresh_token_expire_days=env_int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", 30),
            api_key_header_name=env_str("API_KEY_HEADER_NAME", "X-API-Key"),
            admin_api_key=env_optional_str("ADMIN_API_KEY"),
            internal_service_token=env_optional_str("INTERNAL_SERVICE_TOKEN"),
            enable_rate_limiting=env_bool("ENABLE_RATE_LIMITING", True),
            rate_limit_requests_per_minute=env_int("RATE_LIMIT_REQUESTS_PER_MINUTE", 120),
            enable_ip_allowlist=env_bool("ENABLE_IP_ALLOWLIST", False),
            ip_allowlist=env_list("IP_ALLOWLIST", ("127.0.0.1",)),
        )

    def validate(self, app_env: AppEnvironment) -> None:
        if len(self.secret_key) < 32:
            raise ConfigValidationError("SECRET_KEY must have at least 32 characters.")
        if len(self.jwt_secret) < 32:
            raise ConfigValidationError("JWT_SECRET must have at least 32 characters.")
        if self.rate_limit_requests_per_minute <= 0:
            raise ConfigValidationError("RATE_LIMIT_REQUESTS_PER_MINUTE must be positive.")
        if app_env == AppEnvironment.PRODUCTION:
            insecure = [
                name
                for name, value in {
                    "SECRET_KEY": self.secret_key,
                    "JWT_SECRET": self.jwt_secret,
                    "HASH_SALT": self.hash_salt,
                    "ADMIN_API_KEY": self.admin_api_key,
                    "INTERNAL_SERVICE_TOKEN": self.internal_service_token,
                }.items()
                if has_change_me(value)
            ]
            if insecure:
                raise ConfigValidationError(f"Production secrets must be configured: {', '.join(insecure)}")


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = "localhost"
    port: int = 5432
    database: str = "kwanza_ai_core"
    user: str = "kwanza_user"
    password: str = ""
    ssl_mode: str = "prefer"
    pool_min_size: int = 2
    pool_max_size: int = 20
    pool_timeout_seconds: int = 30
    database_url: Optional[str] = None
    async_database_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            host=env_str("POSTGRES_HOST", "localhost"),
            port=env_int("POSTGRES_PORT", 5432),
            database=env_str("POSTGRES_DB", "kwanza_ai_core"),
            user=env_str("POSTGRES_USER", "kwanza_user"),
            password=env_str("POSTGRES_PASSWORD", ""),
            ssl_mode=env_str("POSTGRES_SSL_MODE", "prefer"),
            pool_min_size=env_int("POSTGRES_POOL_MIN_SIZE", 2),
            pool_max_size=env_int("POSTGRES_POOL_MAX_SIZE", 20),
            pool_timeout_seconds=env_int("POSTGRES_POOL_TIMEOUT_SECONDS", 30),
            database_url=env_optional_str("DATABASE_URL"),
            async_database_url=env_optional_str("ASYNC_DATABASE_URL"),
        )

    def validate(self) -> None:
        if self.port <= 0 or self.port > 65535:
            raise ConfigValidationError("POSTGRES_PORT must be between 1 and 65535.")
        if self.pool_min_size < 0 or self.pool_max_size <= 0:
            raise ConfigValidationError("Database pool sizes are invalid.")
        if self.pool_min_size > self.pool_max_size:
            raise ConfigValidationError("POSTGRES_POOL_MIN_SIZE cannot exceed POSTGRES_POOL_MAX_SIZE.")

    @property
    def sync_url(self) -> str:
        return self.database_url or f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def async_url(self) -> str:
        return self.async_database_url or f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class SupabaseSettings:
    url: str = ""
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None
    jwt: Optional[str] = None
    schema: str = "public"
    auth_mode: SupabaseAuthMode = SupabaseAuthMode.SERVICE_ROLE
    timeout_seconds: int = 20
    retries: int = 3
    storage_bucket: str = "kwanza-ai-core"
    edge_functions_base_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "SupabaseSettings":
        return cls(
            url=env_str("SUPABASE_URL", ""),
            anon_key=env_optional_str("SUPABASE_ANON_KEY"),
            service_role_key=env_optional_str("SUPABASE_SERVICE_ROLE_KEY"),
            jwt=env_optional_str("SUPABASE_JWT"),
            schema=env_str("SUPABASE_SCHEMA", "public"),
            auth_mode=env_enum("SUPABASE_AUTH_MODE", SupabaseAuthMode, SupabaseAuthMode.SERVICE_ROLE),
            timeout_seconds=env_int("SUPABASE_TIMEOUT_SECONDS", 20),
            retries=env_int("SUPABASE_RETRIES", 3),
            storage_bucket=env_str("SUPABASE_STORAGE_BUCKET", "kwanza-ai-core"),
            edge_functions_base_url=env_optional_str("SUPABASE_EDGE_FUNCTIONS_BASE_URL"),
        )

    def validate(self, features: "FeatureFlagSettings") -> None:
        if not features.supabase_integration:
            return
        if not self.url:
            raise ConfigValidationError("SUPABASE_URL is required when FEATURE_SUPABASE_INTEGRATION=true.")
        if self.auth_mode == SupabaseAuthMode.SERVICE_ROLE and not self.service_role_key:
            raise ConfigValidationError("SUPABASE_SERVICE_ROLE_KEY is required for service_role mode.")
        if self.auth_mode == SupabaseAuthMode.ANON and not self.anon_key:
            raise ConfigValidationError("SUPABASE_ANON_KEY is required for anon mode.")
        if self.retries < 0:
            raise ConfigValidationError("SUPABASE_RETRIES cannot be negative.")


@dataclass(frozen=True)
class RedisSettings:
    url: str = "redis://localhost:6379/0"
    password: Optional[str] = None
    ssl: bool = False
    cache_enabled: bool = True
    cache_default_ttl_seconds: int = 300
    cache_max_size: int = 100_000
    cache_namespace: str = "kwanza-ai-core"

    @classmethod
    def from_env(cls) -> "RedisSettings":
        return cls(
            url=env_str("REDIS_URL", "redis://localhost:6379/0"),
            password=env_optional_str("REDIS_PASSWORD"),
            ssl=env_bool("REDIS_SSL", False),
            cache_enabled=env_bool("CACHE_ENABLED", True),
            cache_default_ttl_seconds=env_int("CACHE_DEFAULT_TTL_SECONDS", 300),
            cache_max_size=env_int("CACHE_MAX_SIZE", 100_000),
            cache_namespace=env_str("CACHE_NAMESPACE", "kwanza-ai-core"),
        )

    def validate(self) -> None:
        if self.cache_default_ttl_seconds < 0:
            raise ConfigValidationError("CACHE_DEFAULT_TTL_SECONDS cannot be negative.")
        if self.cache_max_size <= 0:
            raise ConfigValidationError("CACHE_MAX_SIZE must be positive.")


@dataclass(frozen=True)
class QueueSettings:
    provider: QueueProvider = QueueProvider.REDIS
    broker_url: str = "redis://localhost:6379/1"
    result_backend_url: str = "redis://localhost:6379/2"
    queue_default: str = "default"
    queue_high_priority: str = "high-priority"
    queue_low_priority: str = "low-priority"
    task_soft_time_limit_seconds: int = 900
    task_hard_time_limit_seconds: int = 1200

    @classmethod
    def from_env(cls) -> "QueueSettings":
        return cls(
            provider=env_enum("QUEUE_PROVIDER", QueueProvider, QueueProvider.REDIS),
            broker_url=env_str("BROKER_URL", "redis://localhost:6379/1"),
            result_backend_url=env_str("RESULT_BACKEND_URL", "redis://localhost:6379/2"),
            queue_default=env_str("QUEUE_DEFAULT", "default"),
            queue_high_priority=env_str("QUEUE_HIGH_PRIORITY", "high-priority"),
            queue_low_priority=env_str("QUEUE_LOW_PRIORITY", "low-priority"),
            task_soft_time_limit_seconds=env_int("TASK_SOFT_TIME_LIMIT_SECONDS", 900),
            task_hard_time_limit_seconds=env_int("TASK_HARD_TIME_LIMIT_SECONDS", 1200),
        )

    def validate(self) -> None:
        if self.task_soft_time_limit_seconds <= 0 or self.task_hard_time_limit_seconds <= 0:
            raise ConfigValidationError("Task time limits must be positive.")
        if self.task_soft_time_limit_seconds > self.task_hard_time_limit_seconds:
            raise ConfigValidationError("TASK_SOFT_TIME_LIMIT_SECONDS cannot exceed hard limit.")


@dataclass(frozen=True)
class StorageSettings:
    artifact_store_provider: ArtifactStoreProvider = ArtifactStoreProvider.LOCAL
    artifact_local_path: Path = Path("/tmp/kwanza-ai-core/artifacts")
    model_artifact_path: Path = Path("/tmp/kwanza-ai-core/models")
    dataset_cache_path: Path = Path("/tmp/kwanza-ai-core/datasets")
    public_base_url: Optional[str] = None
    s3_endpoint_url: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_access_key_id: Optional[str] = None
    s3_secret_access_key: Optional[str] = None
    s3_region: str = "us-east-1"
    s3_force_path_style: bool = True

    @classmethod
    def from_env(cls) -> "StorageSettings":
        return cls(
            artifact_store_provider=env_enum("ARTIFACT_STORE_PROVIDER", ArtifactStoreProvider, ArtifactStoreProvider.LOCAL),
            artifact_local_path=env_path("ARTIFACT_LOCAL_PATH", "/tmp/kwanza-ai-core/artifacts"),
            model_artifact_path=env_path("MODEL_ARTIFACT_PATH", "/tmp/kwanza-ai-core/models"),
            dataset_cache_path=env_path("DATASET_CACHE_PATH", "/tmp/kwanza-ai-core/datasets"),
            public_base_url=env_optional_str("STORAGE_PUBLIC_BASE_URL"),
            s3_endpoint_url=env_optional_str("S3_ENDPOINT_URL"),
            s3_bucket=env_optional_str("S3_BUCKET"),
            s3_access_key_id=env_optional_str("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=env_optional_str("S3_SECRET_ACCESS_KEY"),
            s3_region=env_str("S3_REGION", "us-east-1"),
            s3_force_path_style=env_bool("S3_FORCE_PATH_STYLE", True),
        )

    def validate(self) -> None:
        if self.artifact_store_provider == ArtifactStoreProvider.S3:
            missing = [
                name
                for name, value in {
                    "S3_BUCKET": self.s3_bucket,
                    "S3_ACCESS_KEY_ID": self.s3_access_key_id,
                    "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
                }.items()
                if not value
            ]
            if missing:
                raise ConfigValidationError(f"S3 storage requires: {', '.join(missing)}")


@dataclass(frozen=True)
class MLSettings:
    environment: str = "development"
    model_registry_provider: str = "local"
    experiment_tracking_provider: str = "local"
    default_model_stage: str = "production"
    inference_timeout_ms: int = 2500
    training_timeout_seconds: int = 3600
    batch_size: int = 256
    max_batch_size: int = 2000
    enable_model_cache: bool = True
    model_cache_ttl_seconds: int = 600
    enable_explainability: bool = True
    enable_shadow_mode: bool = False
    enable_canary_routing: bool = False
    canary_percentage: float = 0.0
    random_seed: int = 42

    @classmethod
    def from_env(cls) -> "MLSettings":
        return cls(
            environment=env_str("ML_ENV", "development"),
            model_registry_provider=env_str("ML_MODEL_REGISTRY_PROVIDER", "local"),
            experiment_tracking_provider=env_str("ML_EXPERIMENT_TRACKING_PROVIDER", "local"),
            default_model_stage=env_str("ML_DEFAULT_MODEL_STAGE", "production"),
            inference_timeout_ms=env_int("ML_INFERENCE_TIMEOUT_MS", 2500),
            training_timeout_seconds=env_int("ML_TRAINING_TIMEOUT_SECONDS", 3600),
            batch_size=env_int("ML_BATCH_SIZE", 256),
            max_batch_size=env_int("ML_MAX_BATCH_SIZE", 2000),
            enable_model_cache=env_bool("ML_ENABLE_MODEL_CACHE", True),
            model_cache_ttl_seconds=env_int("ML_MODEL_CACHE_TTL_SECONDS", 600),
            enable_explainability=env_bool("ML_ENABLE_EXPLAINABILITY", True),
            enable_shadow_mode=env_bool("ML_ENABLE_SHADOW_MODE", False),
            enable_canary_routing=env_bool("ML_ENABLE_CANARY_ROUTING", False),
            canary_percentage=env_float("ML_CANARY_PERCENTAGE", 0.0),
            random_seed=env_int("ML_RANDOM_SEED", 42),
        )

    def validate(self) -> None:
        if self.inference_timeout_ms <= 0 or self.training_timeout_seconds <= 0:
            raise ConfigValidationError("ML timeouts must be positive.")
        if self.batch_size <= 0 or self.max_batch_size <= 0:
            raise ConfigValidationError("ML batch sizes must be positive.")
        if self.batch_size > self.max_batch_size:
            raise ConfigValidationError("ML_BATCH_SIZE cannot exceed ML_MAX_BATCH_SIZE.")
        if not 0 <= self.canary_percentage <= 100:
            raise ConfigValidationError("ML_CANARY_PERCENTAGE must be between 0 and 100.")


@dataclass(frozen=True)
class LLMSettings:
    openai_api_key: Optional[str] = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    timeout_seconds: int = 60
    max_retries: int = 3
    temperature: float = 0.2
    max_tokens: int = 4096

    @classmethod
    def from_env(cls) -> "LLMSettings":
        return cls(
            openai_api_key=env_optional_str("OPENAI_API_KEY"),
            openai_base_url=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_default_model=env_str("OPENAI_DEFAULT_MODEL", "gpt-4.1-mini"),
            openai_embedding_model=env_str("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
            timeout_seconds=env_int("LLM_TIMEOUT_SECONDS", 60),
            max_retries=env_int("LLM_MAX_RETRIES", 3),
            temperature=env_float("LLM_TEMPERATURE", 0.2),
            max_tokens=env_int("LLM_MAX_TOKENS", 4096),
        )

    def validate(self) -> None:
        if self.timeout_seconds <= 0:
            raise ConfigValidationError("LLM_TIMEOUT_SECONDS must be positive.")
        if self.max_retries < 0:
            raise ConfigValidationError("LLM_MAX_RETRIES cannot be negative.")
        if not 0 <= self.temperature <= 2:
            raise ConfigValidationError("LLM_TEMPERATURE must be between 0 and 2.")


@dataclass(frozen=True)
class ServiceSettings:
    fraud_enable_ml_model: bool = True
    fraud_fail_closed: bool = False
    fraud_approve_threshold: float = 34.99
    fraud_review_threshold: float = 55.0
    fraud_challenge_threshold: float = 74.0
    fraud_block_threshold: float = 88.0
    fraud_high_value_amount: float = 500_000
    fraud_velocity_window_minutes: int = 30
    fraud_velocity_txn_count_limit: int = 8
    fraud_velocity_amount_limit: float = 1_500_000
    fraud_cache_ttl_seconds: int = 180

    ueba_baseline_window_days: int = 30
    ueba_recent_window_minutes: int = 60
    ueba_min_events_for_baseline: int = 10
    ueba_monitor_threshold: float = 30.0
    ueba_challenge_threshold: float = 55.0
    ueba_review_threshold: float = 72.0
    ueba_block_threshold: float = 90.0
    ueba_impossible_travel_kmh: float = 900.0
    ueba_enable_model: bool = True
    ueba_fail_open: bool = True
    ueba_cache_ttl_seconds: int = 180

    nlp_provider: str = "local"
    nlp_default_language: str = "pt"
    nlp_max_text_chars: int = 100_000
    nlp_cache_ttl_seconds: int = 300
    nlp_enable_pii_masking: bool = True
    nlp_pii_strategy: str = "mask"
    nlp_embedding_dim: int = 256

    prediction_provider: str = "local"
    prediction_default_timeout_ms: int = 2500
    prediction_max_batch_size: int = 2000
    prediction_max_forecast_horizon: int = 730
    prediction_min_history_points: int = 3
    prediction_cache_ttl_seconds: int = 300
    prediction_fail_open: bool = True

    training_provider: str = "local"
    training_max_rows: int = 2_000_000
    training_min_rows: int = 10
    training_max_columns: int = 10_000
    training_default_timeout_seconds: int = 3600
    training_artifact_base_path: Path = Path("/tmp/kwanza-ai-core/training-artifacts")
    training_auto_register: bool = True
    training_auto_promote: bool = False

    payroll_default_currency: str = "AOA"
    payroll_max_employees_per_run: int = 10_000
    payroll_allow_negative_net_pay: bool = False
    payroll_idempotency_ttl_seconds: int = 86_400
    payroll_fail_fast: bool = False
    payroll_default_policy_id: str = "default"
    payroll_export_provider: str = "local"

    revenue_default_currency: str = "AOA"
    revenue_default_payment_terms_days: int = 15
    revenue_default_tax_rate: float = 0.0
    revenue_max_invoice_lines: int = 1_000
    revenue_idempotency_ttl_seconds: int = 86_400
    revenue_recognize_tax_as_revenue: bool = False
    revenue_allow_negative_invoice_total: bool = False

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        return cls(
            fraud_enable_ml_model=env_bool("FRAUD_ENABLE_ML_MODEL", True),
            fraud_fail_closed=env_bool("FRAUD_FAIL_CLOSED", False),
            fraud_approve_threshold=env_float("FRAUD_APPROVE_THRESHOLD", 34.99),
            fraud_review_threshold=env_float("FRAUD_REVIEW_THRESHOLD", 55.0),
            fraud_challenge_threshold=env_float("FRAUD_CHALLENGE_THRESHOLD", 74.0),
            fraud_block_threshold=env_float("FRAUD_BLOCK_THRESHOLD", 88.0),
            fraud_high_value_amount=env_float("FRAUD_HIGH_VALUE_AMOUNT", 500_000),
            fraud_velocity_window_minutes=env_int("FRAUD_VELOCITY_WINDOW_MINUTES", 30),
            fraud_velocity_txn_count_limit=env_int("FRAUD_VELOCITY_TXN_COUNT_LIMIT", 8),
            fraud_velocity_amount_limit=env_float("FRAUD_VELOCITY_AMOUNT_LIMIT", 1_500_000),
            fraud_cache_ttl_seconds=env_int("FRAUD_CACHE_TTL_SECONDS", 180),
            ueba_baseline_window_days=env_int("UEBA_BASELINE_WINDOW_DAYS", 30),
            ueba_recent_window_minutes=env_int("UEBA_RECENT_WINDOW_MINUTES", 60),
            ueba_min_events_for_baseline=env_int("UEBA_MIN_EVENTS_FOR_BASELINE", 10),
            ueba_monitor_threshold=env_float("UEBA_MONITOR_THRESHOLD", 30.0),
            ueba_challenge_threshold=env_float("UEBA_CHALLENGE_THRESHOLD", 55.0),
            ueba_review_threshold=env_float("UEBA_REVIEW_THRESHOLD", 72.0),
            ueba_block_threshold=env_float("UEBA_BLOCK_THRESHOLD", 90.0),
            ueba_impossible_travel_kmh=env_float("UEBA_IMPOSSIBLE_TRAVEL_KMH", 900.0),
            ueba_enable_model=env_bool("UEBA_ENABLE_MODEL", True),
            ueba_fail_open=env_bool("UEBA_FAIL_OPEN", True),
            ueba_cache_ttl_seconds=env_int("UEBA_CACHE_TTL_SECONDS", 180),
            nlp_provider=env_str("NLP_PROVIDER", "local"),
            nlp_default_language=env_str("NLP_DEFAULT_LANGUAGE", "pt"),
            nlp_max_text_chars=env_int("NLP_MAX_TEXT_CHARS", 100_000),
            nlp_cache_ttl_seconds=env_int("NLP_CACHE_TTL_SECONDS", 300),
            nlp_enable_pii_masking=env_bool("NLP_ENABLE_PII_MASKING", True),
            nlp_pii_strategy=env_str("NLP_PII_STRATEGY", "mask"),
            nlp_embedding_dim=env_int("NLP_EMBEDDING_DIM", 256),
            prediction_provider=env_str("PREDICTION_PROVIDER", "local"),
            prediction_default_timeout_ms=env_int("PREDICTION_DEFAULT_TIMEOUT_MS", 2500),
            prediction_max_batch_size=env_int("PREDICTION_MAX_BATCH_SIZE", 2000),
            prediction_max_forecast_horizon=env_int("PREDICTION_MAX_FORECAST_HORIZON", 730),
            prediction_min_history_points=env_int("PREDICTION_MIN_HISTORY_POINTS", 3),
            prediction_cache_ttl_seconds=env_int("PREDICTION_CACHE_TTL_SECONDS", 300),
            prediction_fail_open=env_bool("PREDICTION_FAIL_OPEN", True),
            training_provider=env_str("TRAINING_PROVIDER", "local"),
            training_max_rows=env_int("TRAINING_MAX_ROWS", 2_000_000),
            training_min_rows=env_int("TRAINING_MIN_ROWS", 10),
            training_max_columns=env_int("TRAINING_MAX_COLUMNS", 10_000),
            training_default_timeout_seconds=env_int("TRAINING_DEFAULT_TIMEOUT_SECONDS", 3600),
            training_artifact_base_path=env_path("TRAINING_ARTIFACT_BASE_PATH", "/tmp/kwanza-ai-core/training-artifacts"),
            training_auto_register=env_bool("TRAINING_AUTO_REGISTER", True),
            training_auto_promote=env_bool("TRAINING_AUTO_PROMOTE", False),
            payroll_default_currency=env_str("PAYROLL_DEFAULT_CURRENCY", "AOA"),
            payroll_max_employees_per_run=env_int("PAYROLL_MAX_EMPLOYEES_PER_RUN", 10_000),
            payroll_allow_negative_net_pay=env_bool("PAYROLL_ALLOW_NEGATIVE_NET_PAY", False),
            payroll_idempotency_ttl_seconds=env_int("PAYROLL_IDEMPOTENCY_TTL_SECONDS", 86_400),
            payroll_fail_fast=env_bool("PAYROLL_FAIL_FAST", False),
            payroll_default_policy_id=env_str("PAYROLL_DEFAULT_POLICY_ID", "default"),
            payroll_export_provider=env_str("PAYROLL_EXPORT_PROVIDER", "local"),
            revenue_default_currency=env_str("REVENUE_DEFAULT_CURRENCY", "AOA"),
            revenue_default_payment_terms_days=env_int("REVENUE_DEFAULT_PAYMENT_TERMS_DAYS", 15),
            revenue_default_tax_rate=env_float("REVENUE_DEFAULT_TAX_RATE", 0.0),
            revenue_max_invoice_lines=env_int("REVENUE_MAX_INVOICE_LINES", 1000),
            revenue_idempotency_ttl_seconds=env_int("REVENUE_IDEMPOTENCY_TTL_SECONDS", 86_400),
            revenue_recognize_tax_as_revenue=env_bool("REVENUE_RECOGNIZE_TAX_AS_REVENUE", False),
            revenue_allow_negative_invoice_total=env_bool("REVENUE_ALLOW_NEGATIVE_INVOICE_TOTAL", False),
        )

    def validate(self) -> None:
        thresholds = [self.fraud_approve_threshold, self.fraud_review_threshold, self.fraud_challenge_threshold, self.fraud_block_threshold]
        if thresholds != sorted(thresholds):
            raise ConfigValidationError("Fraud thresholds must be ordered increasingly.")
        ueba_thresholds = [self.ueba_monitor_threshold, self.ueba_challenge_threshold, self.ueba_review_threshold, self.ueba_block_threshold]
        if ueba_thresholds != sorted(ueba_thresholds):
            raise ConfigValidationError("UEBA thresholds must be ordered increasingly.")
        positive_ints = {
            "NLP_MAX_TEXT_CHARS": self.nlp_max_text_chars,
            "PREDICTION_MAX_BATCH_SIZE": self.prediction_max_batch_size,
            "TRAINING_MAX_ROWS": self.training_max_rows,
            "PAYROLL_MAX_EMPLOYEES_PER_RUN": self.payroll_max_employees_per_run,
            "REVENUE_MAX_INVOICE_LINES": self.revenue_max_invoice_lines,
        }
        invalid = [name for name, value in positive_ints.items() if value <= 0]
        if invalid:
            raise ConfigValidationError(f"Values must be positive: {', '.join(invalid)}")


@dataclass(frozen=True)
class ObservabilitySettings:
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON
    log_include_trace_id: bool = True
    metrics_enabled: bool = True
    metrics_provider: MetricsProvider = MetricsProvider.PROMETHEUS
    metrics_port: int = 9090
    tracing_enabled: bool = False
    tracing_provider: str = "opentelemetry"
    otel_service_name: str = "kwanza-ai-core"
    otel_exporter_otlp_endpoint: Optional[str] = None
    otel_exporter_otlp_headers: Optional[str] = None
    sentry_dsn: Optional[str] = None
    sentry_environment: str = "development"
    sentry_traces_sample_rate: float = 0.1

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls(
            log_level=env_str("LOG_LEVEL", "INFO").upper(),
            log_format=env_enum("LOG_FORMAT", LogFormat, LogFormat.JSON),
            log_include_trace_id=env_bool("LOG_INCLUDE_TRACE_ID", True),
            metrics_enabled=env_bool("METRICS_ENABLED", True),
            metrics_provider=env_enum("METRICS_PROVIDER", MetricsProvider, MetricsProvider.PROMETHEUS),
            metrics_port=env_int("METRICS_PORT", 9090),
            tracing_enabled=env_bool("TRACING_ENABLED", False),
            tracing_provider=env_str("TRACING_PROVIDER", "opentelemetry"),
            otel_service_name=env_str("OTEL_SERVICE_NAME", "kwanza-ai-core"),
            otel_exporter_otlp_endpoint=env_optional_str("OTEL_EXPORTER_OTLP_ENDPOINT"),
            otel_exporter_otlp_headers=env_optional_str("OTEL_EXPORTER_OTLP_HEADERS"),
            sentry_dsn=env_optional_str("SENTRY_DSN"),
            sentry_environment=env_str("SENTRY_ENVIRONMENT", "development"),
            sentry_traces_sample_rate=env_float("SENTRY_TRACES_SAMPLE_RATE", 0.1),
        )

    def validate(self) -> None:
        if self.metrics_port <= 0 or self.metrics_port > 65535:
            raise ConfigValidationError("METRICS_PORT must be between 1 and 65535.")
        if not 0 <= self.sentry_traces_sample_rate <= 1:
            raise ConfigValidationError("SENTRY_TRACES_SAMPLE_RATE must be between 0 and 1.")


@dataclass(frozen=True)
class GovernanceSettings:
    audit_enabled: bool = True
    audit_sink: AuditSinkType = AuditSinkType.DATABASE
    audit_retention_days: int = 365
    audit_hash_personal_data: bool = True
    governance_enabled: bool = True
    data_lineage_enabled: bool = True
    data_masking_enabled: bool = True
    compliance_mode: str = "standard"

    @classmethod
    def from_env(cls) -> "GovernanceSettings":
        return cls(
            audit_enabled=env_bool("AUDIT_ENABLED", True),
            audit_sink=env_enum("AUDIT_SINK", AuditSinkType, AuditSinkType.DATABASE),
            audit_retention_days=env_int("AUDIT_RETENTION_DAYS", 365),
            audit_hash_personal_data=env_bool("AUDIT_HASH_PERSONAL_DATA", True),
            governance_enabled=env_bool("GOVERNANCE_ENABLED", True),
            data_lineage_enabled=env_bool("DATA_LINEAGE_ENABLED", True),
            data_masking_enabled=env_bool("DATA_MASKING_ENABLED", True),
            compliance_mode=env_str("COMPLIANCE_MODE", "standard"),
        )

    def validate(self) -> None:
        if self.audit_retention_days < 0:
            raise ConfigValidationError("AUDIT_RETENTION_DAYS cannot be negative.")


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from_email: str = "no-reply@kwanza-ai.local"
    smtp_use_tls: bool = False
    webhook_signing_secret: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    teams_webhook_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NotificationSettings":
        return cls(
            enabled=env_bool("NOTIFICATIONS_ENABLED", False),
            smtp_host=env_str("SMTP_HOST", "localhost"),
            smtp_port=env_int("SMTP_PORT", 1025),
            smtp_username=env_optional_str("SMTP_USERNAME"),
            smtp_password=env_optional_str("SMTP_PASSWORD"),
            smtp_from_email=env_str("SMTP_FROM_EMAIL", "no-reply@kwanza-ai.local"),
            smtp_use_tls=env_bool("SMTP_USE_TLS", False),
            webhook_signing_secret=env_optional_str("WEBHOOK_SIGNING_SECRET"),
            slack_webhook_url=env_optional_str("SLACK_WEBHOOK_URL"),
            teams_webhook_url=env_optional_str("TEAMS_WEBHOOK_URL"),
        )

    def validate(self) -> None:
        if self.smtp_port <= 0 or self.smtp_port > 65535:
            raise ConfigValidationError("SMTP_PORT must be between 1 and 65535.")


@dataclass(frozen=True)
class FeatureFlagSettings:
    fraud_service: bool = True
    ueba_service: bool = True
    nlp_service: bool = True
    payroll_service: bool = True
    revenue_service: bool = True
    prediction_service: bool = True
    training_service: bool = True
    supabase_integration: bool = True
    async_workers: bool = True
    experimental_models: bool = False

    @classmethod
    def from_env(cls) -> "FeatureFlagSettings":
        return cls(
            fraud_service=env_bool("FEATURE_FRAUD_SERVICE", True),
            ueba_service=env_bool("FEATURE_UEBA_SERVICE", True),
            nlp_service=env_bool("FEATURE_NLP_SERVICE", True),
            payroll_service=env_bool("FEATURE_PAYROLL_SERVICE", True),
            revenue_service=env_bool("FEATURE_REVENUE_SERVICE", True),
            prediction_service=env_bool("FEATURE_PREDICTION_SERVICE", True),
            training_service=env_bool("FEATURE_TRAINING_SERVICE", True),
            supabase_integration=env_bool("FEATURE_SUPABASE_INTEGRATION", True),
            async_workers=env_bool("FEATURE_ASYNC_WORKERS", True),
            experimental_models=env_bool("FEATURE_EXPERIMENTAL_MODELS", False),
        )

    def enabled(self, name: str) -> bool:
        normalized = name.strip().lower().replace("-", "_")
        if not hasattr(self, normalized):
            raise ConfigValidationError(f"Unknown feature flag: {name}")
        return bool(getattr(self, normalized))


@dataclass(frozen=True)
class DevelopmentSettings:
    testing: bool = False
    pythonpath: str = "."
    env_file: str = ".env"
    seed_demo_data: bool = False
    enable_swagger: bool = True
    enable_profiling: bool = False
    enable_sql_echo: bool = False
    local_dev_user_id: str = "dev-user"
    local_dev_tenant_id: str = "dev-tenant"

    @classmethod
    def from_env(cls) -> "DevelopmentSettings":
        return cls(
            testing=env_bool("TESTING", False),
            pythonpath=env_str("PYTHONPATH", "."),
            env_file=env_str("ENV_FILE", ".env"),
            seed_demo_data=env_bool("SEED_DEMO_DATA", False),
            enable_swagger=env_bool("ENABLE_SWAGGER", True),
            enable_profiling=env_bool("ENABLE_PROFILING", False),
            enable_sql_echo=env_bool("ENABLE_SQL_ECHO", False),
            local_dev_user_id=env_str("LOCAL_DEV_USER_ID", "dev-user"),
            local_dev_tenant_id=env_str("LOCAL_DEV_TENANT_ID", "dev-tenant"),
        )


# =============================================================================
# Root settings
# =============================================================================


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    security: SecuritySettings
    database: DatabaseSettings
    supabase: SupabaseSettings
    redis: RedisSettings
    queue: QueueSettings
    storage: StorageSettings
    ml: MLSettings
    llm: LLMSettings
    services: ServiceSettings
    observability: ObservabilitySettings
    governance: GovernanceSettings
    notifications: NotificationSettings
    features: FeatureFlagSettings
    development: DevelopmentSettings

    @classmethod
    def from_env(cls, env_file: Optional[str | Path] = None, override_env_file: bool = False) -> "Settings":
        if env_file:
            parse_env_file(Path(env_file), override=override_env_file)
        elif os.environ.get("ENV_FILE"):
            parse_env_file(Path(os.environ["ENV_FILE"]), override=override_env_file)
        elif Path(".env").exists():
            parse_env_file(Path(".env"), override=override_env_file)

        features = FeatureFlagSettings.from_env()
        settings = cls(
            app=AppSettings.from_env(),
            security=SecuritySettings.from_env(),
            database=DatabaseSettings.from_env(),
            supabase=SupabaseSettings.from_env(),
            redis=RedisSettings.from_env(),
            queue=QueueSettings.from_env(),
            storage=StorageSettings.from_env(),
            ml=MLSettings.from_env(),
            llm=LLMSettings.from_env(),
            services=ServiceSettings.from_env(),
            observability=ObservabilitySettings.from_env(),
            governance=GovernanceSettings.from_env(),
            notifications=NotificationSettings.from_env(),
            features=features,
            development=DevelopmentSettings.from_env(),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        self.app.validate()
        self.security.validate(self.app.environment)
        self.database.validate()
        self.supabase.validate(self.features)
        self.redis.validate()
        self.queue.validate()
        self.storage.validate()
        self.ml.validate()
        self.llm.validate()
        self.services.validate()
        self.observability.validate()
        self.governance.validate()
        self.notifications.validate()

    @property
    def is_production(self) -> bool:
        return self.app.environment == AppEnvironment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app.environment == AppEnvironment.DEVELOPMENT

    @property
    def is_testing(self) -> bool:
        return self.app.environment == AppEnvironment.TESTING or self.development.testing

    def safe_dict(self) -> JsonDict:
        return dataclass_safe_dict(self)

    def json(self, *, safe: bool = True, indent: int = 2) -> str:
        payload = self.safe_dict() if safe else asdict(self)
        return json.dumps(payload, indent=indent, ensure_ascii=False, default=str)

    def require_feature(self, name: str) -> None:
        if not self.features.enabled(name):
            raise ConfigValidationError(f"Feature is disabled: {name}")


# =============================================================================
# Global accessors
# =============================================================================


_settings_lock = threading.Lock()
_settings_instance: Optional[Settings] = None


def get_settings(*, reload: bool = False, env_file: Optional[str | Path] = None, override_env_file: bool = False) -> Settings:
    global _settings_instance
    if reload:
        with _settings_lock:
            _settings_instance = Settings.from_env(env_file=env_file, override_env_file=override_env_file)
            return _settings_instance
    if _settings_instance is None:
        with _settings_lock:
            if _settings_instance is None:
                _settings_instance = Settings.from_env(env_file=env_file, override_env_file=override_env_file)
    return _settings_instance


def reset_settings_cache() -> None:
    global _settings_instance
    with _settings_lock:
        _settings_instance = None


# =============================================================================
# Compatibility helpers for common frameworks
# =============================================================================


def get_database_url(async_: bool = False) -> str:
    settings = get_settings()
    return settings.database.async_url if async_ else settings.database.sync_url


def get_supabase_config_dict() -> JsonDict:
    settings = get_settings()
    return {
        "url": settings.supabase.url,
        "anon_key": settings.supabase.anon_key,
        "service_role_key": settings.supabase.service_role_key,
        "jwt": settings.supabase.jwt,
        "auth_mode": settings.supabase.auth_mode.value,
        "timeout_seconds": settings.supabase.timeout_seconds,
        "retries": settings.supabase.retries,
        "default_schema": settings.supabase.schema,
        "privacy_hash_salt": settings.security.hash_salt,
    }


def get_cors_config() -> JsonDict:
    settings = get_settings()
    return {
        "allow_origins": list(settings.app.cors_origins),
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


def generate_secret(length: int = 64) -> str:
    if length < 32:
        raise ConfigValidationError("Secret length must be at least 32.")
    return secrets.token_urlsafe(length)[:length]


# =============================================================================
# CLI smoke check
# =============================================================================


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Validate and print Kwanza AI Core settings")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--unsafe", action="store_true", help="Print raw secrets. Do not use in production.")
    parser.add_argument("--generate-secret", type=int, default=None)
    args = parser.parse_args()

    if args.generate_secret:
        print(generate_secret(args.generate_secret))
        return

    settings = Settings.from_env(env_file=args.env_file)
    print(settings.json(safe=not args.unsafe))


if __name__ == "__main__":
    _main()
