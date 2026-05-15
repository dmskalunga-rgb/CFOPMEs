# kwanza-ai-core/infrastructure/config.py
from __future__ import annotations

import json
import os
import pathlib
import secrets
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional


class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LogFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


class CacheBackend(str, Enum):
    MEMORY = "memory"
    REDIS = "redis"


class DatabaseDriver(str, Enum):
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"


class ModelRuntime(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class AppConfig:
    name: str = "kwanza-ai-core"
    version: str = "1.0.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    timezone: str = "UTC"
    region: str = "local"
    instance_id: str = field(default_factory=lambda: os.getenv("INSTANCE_ID", secrets.token_hex(8)))

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.environment == Environment.TEST


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    reload: bool = False
    request_timeout_seconds: int = 60
    max_request_body_mb: int = 25
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


@dataclass(frozen=True)
class SecurityConfig:
    secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 60
    api_key_header: str = "X-API-Key"
    allowed_api_keys: List[str] = field(default_factory=list)
    enable_rate_limit: bool = True
    rate_limit_per_minute: int = 120
    enable_request_signing: bool = False

    def validate(self, env: Environment) -> None:
        if env == Environment.PRODUCTION and len(self.secret_key) < 32:
            raise ConfigValidationError(
                "SECURITY_SECRET_KEY must have at least 32 characters in production"
            )


@dataclass(frozen=True)
class DatabaseConfig:
    driver: DatabaseDriver = DatabaseDriver.POSTGRESQL
    host: str = "localhost"
    port: int = 5432
    database: str = "kwanza_ai"
    username: str = "postgres"
    password: str = ""
    pool_min_size: int = 1
    pool_max_size: int = 20
    connect_timeout_seconds: int = 10
    statement_timeout_seconds: int = 60
    ssl_mode: str = "prefer"

    @property
    def dsn(self) -> str:
        if self.driver == DatabaseDriver.SQLITE:
            return f"sqlite:///{self.database}"

        return (
            f"postgresql://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
            f"?sslmode={self.ssl_mode}"
        )

    def safe_dsn(self) -> str:
        if not self.password:
            return self.dsn
        return self.dsn.replace(self.password, "***")


@dataclass(frozen=True)
class RedisConfig:
    enabled: bool = False
    url: str = "redis://localhost:6379/0"
    socket_timeout_seconds: float = 5.0
    health_check_interval_seconds: int = 30


@dataclass(frozen=True)
class CacheConfig:
    backend: CacheBackend = CacheBackend.MEMORY
    namespace: str = "kwanza-ai-core"
    default_ttl_seconds: int = 300
    max_memory_items: int = 50_000
    stale_while_revalidate_seconds: int = 60


@dataclass(frozen=True)
class ObservabilityConfig:
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.TEXT
    enable_tracing: bool = True
    enable_metrics: bool = True
    enable_audit: bool = True
    service_name: str = "kwanza-ai-core"
    otel_endpoint: Optional[str] = None
    metrics_namespace: str = "kwanza_ai"


@dataclass(frozen=True)
class StorageConfig:
    base_path: pathlib.Path = pathlib.Path("./storage")
    datasets_path: pathlib.Path = pathlib.Path("./storage/datasets")
    models_path: pathlib.Path = pathlib.Path("./storage/models")
    artifacts_path: pathlib.Path = pathlib.Path("./storage/artifacts")
    reports_path: pathlib.Path = pathlib.Path("./storage/reports")

    def ensure_directories(self) -> None:
        for path in [
            self.base_path,
            self.datasets_path,
            self.models_path,
            self.artifacts_path,
            self.reports_path,
        ]:
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class MLConfig:
    runtime: ModelRuntime = ModelRuntime.LOCAL
    default_model_name: str = "kwanza-default-model"
    model_version: str = "latest"
    batch_size: int = 512
    prediction_timeout_seconds: int = 30
    training_timeout_seconds: int = 3600
    enable_model_registry: bool = True
    enable_feature_store: bool = True
    drift_threshold: float = 0.15
    anomaly_threshold: float = 0.85


@dataclass(frozen=True)
class FeatureStoreConfig:
    enabled: bool = True
    offline_store_table: str = "features_offline"
    online_store_prefix: str = "features:online"
    entity_id_column: str = "entity_id"
    event_timestamp_column: str = "event_timestamp"
    ttl_seconds: int = 86_400


@dataclass(frozen=True)
class ExternalServicesConfig:
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    webhook_url: Optional[str] = None


@dataclass(frozen=True)
class FeatureFlags:
    enable_realtime_prediction: bool = True
    enable_batch_prediction: bool = True
    enable_training_pipeline: bool = True
    enable_explainability: bool = True
    enable_governance: bool = True
    enable_data_quality_checks: bool = True
    enable_experimental_models: bool = False


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    server: ServerConfig
    security: SecurityConfig
    database: DatabaseConfig
    redis: RedisConfig
    cache: CacheConfig
    observability: ObservabilityConfig
    storage: StorageConfig
    ml: MLConfig
    feature_store: FeatureStoreConfig
    external: ExternalServicesConfig
    flags: FeatureFlags

    def validate(self) -> None:
        self.security.validate(self.app.environment)

        if self.server.port < 1 or self.server.port > 65535:
            raise ConfigValidationError("SERVER_PORT must be between 1 and 65535")

        if self.database.pool_min_size < 0:
            raise ConfigValidationError("DB_POOL_MIN_SIZE cannot be negative")

        if self.database.pool_max_size < self.database.pool_min_size:
            raise ConfigValidationError("DB_POOL_MAX_SIZE must be >= DB_POOL_MIN_SIZE")

        if self.cache.default_ttl_seconds < 0:
            raise ConfigValidationError("CACHE_DEFAULT_TTL_SECONDS cannot be negative")

        if self.ml.batch_size <= 0:
            raise ConfigValidationError("ML_BATCH_SIZE must be greater than zero")

    def ensure_runtime_directories(self) -> None:
        self.storage.ensure_directories()

    def to_safe_dict(self) -> Dict[str, Any]:
        return {
            "app": vars(self.app),
            "server": vars(self.server),
            "security": {
                **vars(self.security),
                "secret_key": "***" if self.security.secret_key else "",
                "allowed_api_keys": ["***" for _ in self.security.allowed_api_keys],
            },
            "database": {
                **vars(self.database),
                "password": "***" if self.database.password else "",
                "dsn": self.database.safe_dsn(),
            },
            "redis": {
                **vars(self.redis),
                "url": mask_url_secret(self.redis.url),
            },
            "cache": vars(self.cache),
            "observability": vars(self.observability),
            "storage": {
                key: str(value)
                for key, value in vars(self.storage).items()
            },
            "ml": vars(self.ml),
            "feature_store": vars(self.feature_store),
            "external": {
                key: "***" if value else None
                for key, value in vars(self.external).items()
            },
            "flags": vars(self.flags),
        }


class ConfigValidationError(ValueError):
    pass


class EnvReader:
    def __init__(self, env: Optional[Mapping[str, str]] = None) -> None:
        self.env = env or os.environ

    def str(self, key: str, default: str = "") -> str:
        return self.env.get(key, default)

    def optional_str(self, key: str) -> Optional[str]:
        value = self.env.get(key)
        return value if value not in {"", None} else None

    def int(self, key: str, default: int) -> int:
        raw = self.env.get(key)
        if raw is None or raw == "":
            return default
        return int(raw)

    def float(self, key: str, default: float) -> float:
        raw = self.env.get(key)
        if raw is None or raw == "":
            return default
        return float(raw)

    def bool(self, key: str, default: bool = False) -> bool:
        raw = self.env.get(key)
        if raw is None or raw == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    def list(self, key: str, default: Optional[List[str]] = None, sep: str = ",") -> List[str]:
        raw = self.env.get(key)
        if raw is None or raw.strip() == "":
            return default or []
        return [item.strip() for item in raw.split(sep) if item.strip()]

    def enum(self, key: str, enum_cls: type[Enum], default: Enum) -> Enum:
        raw = self.env.get(key)
        if raw is None or raw == "":
            return default
        return enum_cls(raw.strip().lower())


def load_dotenv(path: str | pathlib.Path = ".env", override: bool = False) -> None:
    dotenv_path = pathlib.Path(path)

    if not dotenv_path.exists():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if override or key not in os.environ:
            os.environ[key] = value


def build_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    reader = EnvReader(env)

    app = AppConfig(
        name=reader.str("APP_NAME", "kwanza-ai-core"),
        version=reader.str("APP_VERSION", "1.0.0"),
        environment=reader.enum("APP_ENV", Environment, Environment.DEVELOPMENT),  # type: ignore[arg-type]
        debug=reader.bool("APP_DEBUG", False),
        timezone=reader.str("APP_TIMEZONE", "UTC"),
        region=reader.str("APP_REGION", "local"),
        instance_id=reader.str("INSTANCE_ID", secrets.token_hex(8)),
    )

    server = ServerConfig(
        host=reader.str("SERVER_HOST", "0.0.0.0"),
        port=reader.int("SERVER_PORT", 8000),
        workers=reader.int("SERVER_WORKERS", 1),
        reload=reader.bool("SERVER_RELOAD", False),
        request_timeout_seconds=reader.int("SERVER_REQUEST_TIMEOUT_SECONDS", 60),
        max_request_body_mb=reader.int("SERVER_MAX_REQUEST_BODY_MB", 25),
        cors_origins=reader.list("SERVER_CORS_ORIGINS", ["*"]),
    )

    security = SecurityConfig(
        secret_key=reader.str("SECURITY_SECRET_KEY", "dev-secret-key-change-me"),
        jwt_algorithm=reader.str("SECURITY_JWT_ALGORITHM", "HS256"),
        jwt_expiration_minutes=reader.int("SECURITY_JWT_EXPIRATION_MINUTES", 60),
        api_key_header=reader.str("SECURITY_API_KEY_HEADER", "X-API-Key"),
        allowed_api_keys=reader.list("SECURITY_ALLOWED_API_KEYS", []),
        enable_rate_limit=reader.bool("SECURITY_ENABLE_RATE_LIMIT", True),
        rate_limit_per_minute=reader.int("SECURITY_RATE_LIMIT_PER_MINUTE", 120),
        enable_request_signing=reader.bool("SECURITY_ENABLE_REQUEST_SIGNING", False),
    )

    database = DatabaseConfig(
        driver=reader.enum("DB_DRIVER", DatabaseDriver, DatabaseDriver.POSTGRESQL),  # type: ignore[arg-type]
        host=reader.str("DB_HOST", "localhost"),
        port=reader.int("DB_PORT", 5432),
        database=reader.str("DB_NAME", "kwanza_ai"),
        username=reader.str("DB_USER", "postgres"),
        password=reader.str("DB_PASSWORD", ""),
        pool_min_size=reader.int("DB_POOL_MIN_SIZE", 1),
        pool_max_size=reader.int("DB_POOL_MAX_SIZE", 20),
        connect_timeout_seconds=reader.int("DB_CONNECT_TIMEOUT_SECONDS", 10),
        statement_timeout_seconds=reader.int("DB_STATEMENT_TIMEOUT_SECONDS", 60),
        ssl_mode=reader.str("DB_SSL_MODE", "prefer"),
    )

    redis = RedisConfig(
        enabled=reader.bool("REDIS_ENABLED", False),
        url=reader.str("REDIS_URL", "redis://localhost:6379/0"),
        socket_timeout_seconds=reader.float("REDIS_SOCKET_TIMEOUT_SECONDS", 5.0),
        health_check_interval_seconds=reader.int("REDIS_HEALTH_CHECK_INTERVAL_SECONDS", 30),
    )

    cache = CacheConfig(
        backend=reader.enum("CACHE_BACKEND", CacheBackend, CacheBackend.MEMORY),  # type: ignore[arg-type]
        namespace=reader.str("CACHE_NAMESPACE", "kwanza-ai-core"),
        default_ttl_seconds=reader.int("CACHE_DEFAULT_TTL_SECONDS", 300),
        max_memory_items=reader.int("CACHE_MAX_MEMORY_ITEMS", 50_000),
        stale_while_revalidate_seconds=reader.int("CACHE_STALE_WHILE_REVALIDATE_SECONDS", 60),
    )

    observability = ObservabilityConfig(
        log_level=reader.str("LOG_LEVEL", "INFO"),
        log_format=reader.enum("LOG_FORMAT", LogFormat, LogFormat.TEXT),  # type: ignore[arg-type]
        enable_tracing=reader.bool("OBS_ENABLE_TRACING", True),
        enable_metrics=reader.bool("OBS_ENABLE_METRICS", True),
        enable_audit=reader.bool("OBS_ENABLE_AUDIT", True),
        service_name=reader.str("OBS_SERVICE_NAME", app.name),
        otel_endpoint=reader.optional_str("OTEL_EXPORTER_OTLP_ENDPOINT"),
        metrics_namespace=reader.str("METRICS_NAMESPACE", "kwanza_ai"),
    )

    storage = StorageConfig(
        base_path=pathlib.Path(reader.str("STORAGE_BASE_PATH", "./storage")),
        datasets_path=pathlib.Path(reader.str("STORAGE_DATASETS_PATH", "./storage/datasets")),
        models_path=pathlib.Path(reader.str("STORAGE_MODELS_PATH", "./storage/models")),
        artifacts_path=pathlib.Path(reader.str("STORAGE_ARTIFACTS_PATH", "./storage/artifacts")),
        reports_path=pathlib.Path(reader.str("STORAGE_REPORTS_PATH", "./storage/reports")),
    )

    ml = MLConfig(
        runtime=reader.enum("ML_RUNTIME", ModelRuntime, ModelRuntime.LOCAL),  # type: ignore[arg-type]
        default_model_name=reader.str("ML_DEFAULT_MODEL_NAME", "kwanza-default-model"),
        model_version=reader.str("ML_MODEL_VERSION", "latest"),
        batch_size=reader.int("ML_BATCH_SIZE", 512),
        prediction_timeout_seconds=reader.int("ML_PREDICTION_TIMEOUT_SECONDS", 30),
        training_timeout_seconds=reader.int("ML_TRAINING_TIMEOUT_SECONDS", 3600),
        enable_model_registry=reader.bool("ML_ENABLE_MODEL_REGISTRY", True),
        enable_feature_store=reader.bool("ML_ENABLE_FEATURE_STORE", True),
        drift_threshold=reader.float("ML_DRIFT_THRESHOLD", 0.15),
        anomaly_threshold=reader.float("ML_ANOMALY_THRESHOLD", 0.85),
    )

    feature_store = FeatureStoreConfig(
        enabled=reader.bool("FEATURE_STORE_ENABLED", True),
        offline_store_table=reader.str("FEATURE_STORE_OFFLINE_TABLE", "features_offline"),
        online_store_prefix=reader.str("FEATURE_STORE_ONLINE_PREFIX", "features:online"),
        entity_id_column=reader.str("FEATURE_STORE_ENTITY_ID_COLUMN", "entity_id"),
        event_timestamp_column=reader.str("FEATURE_STORE_EVENT_TIMESTAMP_COLUMN", "event_timestamp"),
        ttl_seconds=reader.int("FEATURE_STORE_TTL_SECONDS", 86_400),
    )

    external = ExternalServicesConfig(
        supabase_url=reader.optional_str("SUPABASE_URL"),
        supabase_key=reader.optional_str("SUPABASE_KEY"),
        openai_api_key=reader.optional_str("OPENAI_API_KEY"),
        webhook_url=reader.optional_str("WEBHOOK_URL"),
    )

    flags = FeatureFlags(
        enable_realtime_prediction=reader.bool("FF_ENABLE_REALTIME_PREDICTION", True),
        enable_batch_prediction=reader.bool("FF_ENABLE_BATCH_PREDICTION", True),
        enable_training_pipeline=reader.bool("FF_ENABLE_TRAINING_PIPELINE", True),
        enable_explainability=reader.bool("FF_ENABLE_EXPLAINABILITY", True),
        enable_governance=reader.bool("FF_ENABLE_GOVERNANCE", True),
        enable_data_quality_checks=reader.bool("FF_ENABLE_DATA_QUALITY_CHECKS", True),
        enable_experimental_models=reader.bool("FF_ENABLE_EXPERIMENTAL_MODELS", False),
    )

    settings = Settings(
        app=app,
        server=server,
        security=security,
        database=database,
        redis=redis,
        cache=cache,
        observability=observability,
        storage=storage,
        ml=ml,
        feature_store=feature_store,
        external=external,
        flags=flags,
    )

    settings.validate()
    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(".env", override=False)
    settings = build_settings()
    settings.ensure_runtime_directories()
    return settings


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def mask_url_secret(url: str) -> str:
    if not url:
        return url

    if "@" not in url:
        return url

    prefix, suffix = url.split("@", 1)

    if ":" not in prefix:
        return "***@" + suffix

    scheme_user, _password = prefix.rsplit(":", 1)
    return f"{scheme_user}:***@{suffix}"


def settings_as_json(settings: Optional[Settings] = None) -> str:
    selected = settings or get_settings()
    return json.dumps(selected.to_safe_dict(), indent=2, ensure_ascii=False, default=str)