# ml/configs/ml_config.py
"""
Enterprise ML Configuration.

Recursos:
- configuração central para módulos ML
- suporte a ambientes: local/dev/staging/prod
- carregamento JSON/YAML
- override por variáveis de ambiente
- validação forte sem dependência obrigatória de pydantic
- secrets via env var
- configs de modelos, pipelines, registry, monitoring e segurança
- feature flags
- export seguro com mascaramento
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Type, TypeVar


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


T = TypeVar("T")


class ConfigError(RuntimeError):
    pass


class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ModelBackend(str, Enum):
    SKLEARN = "sklearn"
    PYTORCH = "pytorch"
    TENSORFLOW = "tensorflow"
    PROPHET = "prophet"
    RULES = "rules"
    HYBRID = "hybrid"


class StorageBackend(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    GCS = "gcs"
    AZURE_BLOB = "azure_blob"
    POSTGRES = "postgres"


@dataclass(frozen=True)
class SecretRef:
    env: str
    required: bool = True
    default: Optional[str] = None

    def resolve(self) -> Optional[str]:
        value = os.getenv(self.env)

        if value is None:
            if self.required and self.default is None:
                raise ConfigError(f"Secret obrigatório ausente: env:{self.env}")
            return self.default

        return value


@dataclass
class AppConfig:
    app_name: str = "enterprise-ml-platform"
    environment: Environment = Environment.DEVELOPMENT
    version: str = "1.0.0"
    timezone: str = "UTC"
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class LoggingConfig:
    level: LogLevel = LogLevel.INFO
    json_logs: bool = True
    include_trace_id: bool = True
    log_dir: str = "logs/ml"
    audit_log_dir: str = "logs/ml/audit"


@dataclass
class SecurityConfig:
    enable_pii_masking: bool = True
    enable_payload_encryption: bool = False
    encryption_key: Optional[SecretRef] = None
    allowed_tenants: List[str] = field(default_factory=list)
    require_tenant_id: bool = False
    max_payload_mb: int = 25


@dataclass
class StorageConfig:
    backend: StorageBackend = StorageBackend.LOCAL
    artifact_root: str = "artifacts"
    bucket: Optional[str] = None
    region: Optional[str] = None
    endpoint_url: Optional[str] = None
    access_key: Optional[SecretRef] = None
    secret_key: Optional[SecretRef] = None


@dataclass
class DatabaseConfig:
    enabled: bool = False
    dsn: Optional[SecretRef] = None
    host: str = "localhost"
    port: int = 5432
    database: str = "ml_platform"
    username: Optional[SecretRef] = None
    password: Optional[SecretRef] = None
    pool_size: int = 10
    pool_timeout_seconds: int = 30


@dataclass
class MonitoringConfig:
    enabled: bool = True
    metrics_namespace: str = "ml"
    prometheus_enabled: bool = True
    prometheus_port: int = 9090
    drift_detection_enabled: bool = True
    drift_reference_window_days: int = 30
    alerting_enabled: bool = True
    alert_webhook_url: Optional[SecretRef] = None


@dataclass
class RegistryConfig:
    enabled: bool = True
    registry_uri: str = "artifacts/model_registry"
    require_approval_for_production: bool = True
    allow_overwrite_versions: bool = False
    default_stage: str = "development"


@dataclass
class FeatureFlagConfig:
    enable_explainability: bool = True
    enable_model_registry: bool = True
    enable_online_learning: bool = False
    enable_shadow_mode: bool = False
    enable_canary_release: bool = False
    enable_cost_tracking: bool = True


@dataclass
class ModelConfig:
    name: str
    version: str = "1.0.0"
    backend: ModelBackend = ModelBackend.SKLEARN
    artifact_uri: Optional[str] = None
    enabled: bool = True
    timeout_seconds: float = 5.0
    batch_size: int = 512
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    name: str
    enabled: bool = True
    schedule: Optional[str] = None
    retries: int = 3
    timeout_seconds: int = 3600
    max_concurrency: int = 4
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FraudDetectionConfig:
    enabled: bool = True
    review_threshold: float = 0.55
    block_threshold: float = 0.82
    critical_threshold: float = 0.92
    max_batch_size: int = 2_000
    rules_path: str = "configs/fraud_rules.json"
    model_path: str = "artifacts/fraud_detection/model.pkl"


@dataclass
class ForecastingConfig:
    enabled: bool = True
    default_horizon: int = 30
    default_frequency: str = "daily"
    confidence_interval: float = 0.95
    artifact_dir: str = "artifacts/forecasting"


@dataclass
class MLConfig:
    app: AppConfig = field(default_factory=AppConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    feature_flags: FeatureFlagConfig = field(default_factory=FeatureFlagConfig)
    fraud_detection: FraudDetectionConfig = field(default_factory=FraudDetectionConfig)
    forecasting: ForecastingConfig = field(default_factory=ForecastingConfig)
    models: List[ModelConfig] = field(default_factory=list)
    pipelines: List[PipelineConfig] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ConfigMasker:
    SENSITIVE_KEY_RE = re.compile(
        r"(password|secret|token|key|credential|authorization)",
        re.IGNORECASE,
    )

    @classmethod
    def mask(cls, value: Any) -> Any:
        if isinstance(value, SecretRef):
            return {"env": value.env, "required": value.required, "resolved": bool(os.getenv(value.env))}

        if is_dataclass(value):
            return cls.mask(asdict(value))

        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                if cls.SENSITIVE_KEY_RE.search(str(key)):
                    result[key] = "***MASKED***"
                else:
                    result[key] = cls.mask(item)
            return result

        if isinstance(value, list):
            return [cls.mask(v) for v in value]

        if isinstance(value, Enum):
            return value.value

        return value


class ConfigParser:
    SECRET_PREFIX = "env:"

    @classmethod
    def load_file(cls, path: str | Path) -> Dict[str, Any]:
        source = Path(path)

        if not source.exists():
            raise ConfigError(f"Arquivo de configuração não encontrado: {source}")

        text = source.read_text(encoding="utf-8")

        if source.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise ConfigError("PyYAML não está instalado para carregar YAML.")
            data = yaml.safe_load(text) or {}
            return dict(data)

        if source.suffix.lower() == ".json":
            return json.loads(text)

        raise ConfigError(f"Formato de config não suportado: {source.suffix}")

    @classmethod
    def parse_secret(cls, value: Any) -> Any:
        if isinstance(value, str) and value.startswith(cls.SECRET_PREFIX):
            env_name = value[len(cls.SECRET_PREFIX):]
            return SecretRef(env=env_name)

        if isinstance(value, Mapping) and "env" in value:
            return SecretRef(
                env=str(value["env"]),
                required=bool(value.get("required", True)),
                default=value.get("default"),
            )

        return value


class ConfigFactory:
    @classmethod
    def from_file(cls, path: str | Path) -> MLConfig:
        raw = ConfigParser.load_file(path)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MLConfig:
        return MLConfig(
            app=cls._build_dataclass(AppConfig, raw.get("app", {})),
            logging=cls._build_dataclass(LoggingConfig, raw.get("logging", {})),
            security=cls._build_dataclass(SecurityConfig, raw.get("security", {})),
            storage=cls._build_dataclass(StorageConfig, raw.get("storage", {})),
            database=cls._build_dataclass(DatabaseConfig, raw.get("database", {})),
            monitoring=cls._build_dataclass(MonitoringConfig, raw.get("monitoring", {})),
            registry=cls._build_dataclass(RegistryConfig, raw.get("registry", {})),
            feature_flags=cls._build_dataclass(FeatureFlagConfig, raw.get("feature_flags", {})),
            fraud_detection=cls._build_dataclass(FraudDetectionConfig, raw.get("fraud_detection", {})),
            forecasting=cls._build_dataclass(ForecastingConfig, raw.get("forecasting", {})),
            models=[cls._build_dataclass(ModelConfig, x) for x in raw.get("models", [])],
            pipelines=[cls._build_dataclass(PipelineConfig, x) for x in raw.get("pipelines", [])],
            metadata=dict(raw.get("metadata", {})),
        )

    @classmethod
    def from_env(cls, prefix: str = "ML_") -> MLConfig:
        config = MLConfig()

        env = os.getenv(f"{prefix}ENVIRONMENT")
        if env:
            config.app.environment = Environment(env)

        app_name = os.getenv(f"{prefix}APP_NAME")
        if app_name:
            config.app.app_name = app_name

        log_level = os.getenv(f"{prefix}LOG_LEVEL")
        if log_level:
            config.logging.level = LogLevel(log_level.upper())

        artifact_root = os.getenv(f"{prefix}ARTIFACT_ROOT")
        if artifact_root:
            config.storage.artifact_root = artifact_root

        registry_uri = os.getenv(f"{prefix}REGISTRY_URI")
        if registry_uri:
            config.registry.registry_uri = registry_uri

        prometheus_port = os.getenv(f"{prefix}PROMETHEUS_PORT")
        if prometheus_port:
            config.monitoring.prometheus_port = int(prometheus_port)

        return config

    @classmethod
    def merge(cls, base: MLConfig, override: MLConfig) -> MLConfig:
        base_dict = asdict(base)
        override_dict = asdict(override)
        merged = cls._deep_merge(base_dict, override_dict)
        return cls.from_dict(merged)

    @classmethod
    def _build_dataclass(cls, target: Type[T], raw: Mapping[str, Any]) -> T:
        kwargs = {}

        field_map = {f.name: f for f in fields(target)}

        for name, f in field_map.items():
            if name not in raw:
                continue

            value = ConfigParser.parse_secret(raw[name])
            kwargs[name] = cls._coerce_value(value, f.type)

        return target(**kwargs)

    @classmethod
    def _coerce_value(cls, value: Any, target_type: Any) -> Any:
        if target_type in {Environment, LogLevel, ModelBackend, StorageBackend}:
            return target_type(value)

        if target_type == Optional[SecretRef] or target_type == SecretRef:
            return ConfigParser.parse_secret(value)

        return value

    @classmethod
    def _deep_merge(cls, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)

        for key, value in override.items():
            if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
                result[key] = cls._deep_merge(dict(result[key]), dict(value))
            elif value not in (None, [], {}):
                result[key] = value

        return result


class ConfigValidator:
    @classmethod
    def validate(cls, config: MLConfig) -> None:
        cls._validate_app(config)
        cls._validate_security(config)
        cls._validate_storage(config)
        cls._validate_monitoring(config)
        cls._validate_models(config)
        cls._validate_fraud(config)
        cls._validate_forecasting(config)

    @classmethod
    def _validate_app(cls, config: MLConfig) -> None:
        if not config.app.app_name:
            raise ConfigError("app.app_name obrigatório.")

        if not config.app.version:
            raise ConfigError("app.version obrigatório.")

    @classmethod
    def _validate_security(cls, config: MLConfig) -> None:
        if config.security.max_payload_mb <= 0:
            raise ConfigError("security.max_payload_mb precisa ser maior que zero.")

    @classmethod
    def _validate_storage(cls, config: MLConfig) -> None:
        if config.storage.backend != StorageBackend.LOCAL and not config.storage.bucket:
            raise ConfigError("storage.bucket obrigatório para storage remoto.")

    @classmethod
    def _validate_monitoring(cls, config: MLConfig) -> None:
        if config.monitoring.prometheus_port <= 0:
            raise ConfigError("monitoring.prometheus_port inválido.")

        if config.monitoring.drift_reference_window_days <= 0:
            raise ConfigError("monitoring.drift_reference_window_days inválido.")

    @classmethod
    def _validate_models(cls, config: MLConfig) -> None:
        seen = set()

        for model in config.models:
            if not model.name:
                raise ConfigError("models[].name obrigatório.")

            key = f"{model.name}:{model.version}"
            if key in seen:
                raise ConfigError(f"Modelo duplicado: {key}")
            seen.add(key)

            if model.timeout_seconds <= 0:
                raise ConfigError(f"timeout inválido para modelo {key}")

            if model.batch_size <= 0:
                raise ConfigError(f"batch_size inválido para modelo {key}")

    @classmethod
    def _validate_fraud(cls, config: MLConfig) -> None:
        fraud = config.fraud_detection

        if not (0 <= fraud.review_threshold <= 1):
            raise ConfigError("fraud_detection.review_threshold inválido.")

        if not (0 <= fraud.block_threshold <= 1):
            raise ConfigError("fraud_detection.block_threshold inválido.")

        if fraud.review_threshold >= fraud.block_threshold:
            raise ConfigError("review_threshold deve ser menor que block_threshold.")

    @classmethod
    def _validate_forecasting(cls, config: MLConfig) -> None:
        if config.forecasting.default_horizon <= 0:
            raise ConfigError("forecasting.default_horizon precisa ser maior que zero.")

        if not (0 < config.forecasting.confidence_interval < 1):
            raise ConfigError("forecasting.confidence_interval precisa estar entre 0 e 1.")


class MLConfigManager:
    def __init__(self, config: Optional[MLConfig] = None) -> None:
        self.config = config or MLConfig()
        ConfigValidator.validate(self.config)

    @classmethod
    def load(
        cls,
        path: Optional[str | Path] = None,
        *,
        env_prefix: str = "ML_",
        validate: bool = True,
    ) -> "MLConfigManager":
        if path:
            config = ConfigFactory.from_file(path)
        else:
            config = ConfigFactory.from_env(env_prefix)

        if validate:
            ConfigValidator.validate(config)

        return cls(config)

    def get_model(self, name: str, version: Optional[str] = None) -> ModelConfig:
        candidates = [
            model for model in self.config.models
            if model.name == name and (version is None or model.version == version)
        ]

        if not candidates:
            raise ConfigError(f"Modelo não encontrado: {name}:{version or '*'}")

        return candidates[-1]

    def get_pipeline(self, name: str) -> PipelineConfig:
        for pipeline in self.config.pipelines:
            if pipeline.name == name:
                return pipeline

        raise ConfigError(f"Pipeline não encontrado: {name}")

    def resolve_secret(self, ref: Optional[SecretRef]) -> Optional[str]:
        if ref is None:
            return None
        return ref.resolve()

    def safe_dict(self) -> Dict[str, Any]:
        return ConfigMasker.mask(self.config)

    def to_json(self, indent: int = 2, safe: bool = True) -> str:
        data = self.safe_dict() if safe else asdict(self.config)
        return json.dumps(data, ensure_ascii=False, indent=indent, default=str)

    def save_template(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        data = ConfigMasker.mask(self.config)

        if target.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise ConfigError("PyYAML não está instalado.")
            target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        else:
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        return target


def default_enterprise_config() -> MLConfig:
    return MLConfig(
        app=AppConfig(
            app_name="digital-meta-ml-platform",
            environment=Environment.DEVELOPMENT,
            version="1.0.0",
        ),
        models=[
            ModelConfig(
                name="enterprise_fraud_detector",
                version="1.0.0",
                backend=ModelBackend.HYBRID,
                artifact_uri="artifacts/fraud_detection/model.pkl",
                metadata={"domain": "fraud_detection"},
            ),
            ModelConfig(
                name="enterprise_cashflow_predictor",
                version="1.0.0",
                backend=ModelBackend.SKLEARN,
                artifact_uri="artifacts/forecasting/cashflow.pkl",
                metadata={"domain": "forecasting"},
            ),
        ],
        pipelines=[
            PipelineConfig(
                name="fraud_training_pipeline",
                schedule="0 2 * * *",
                metadata={"owner": "ml-platform"},
            ),
            PipelineConfig(
                name="cashflow_forecast_pipeline",
                schedule="0 6 * * *",
                metadata={"owner": "finance"},
            ),
        ],
        metadata={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile": "enterprise_default",
        },
    )


if __name__ == "__main__":
    manager = MLConfigManager(default_enterprise_config())

    print(manager.to_json())

    path = manager.save_template("artifacts/configs/ml_config_template.json")
    print(f"Template salvo em: {path}")