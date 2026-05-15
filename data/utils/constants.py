"""
data/utils/constants.py

Enterprise-grade constants module.

Este módulo centraliza constantes padronizadas para a plataforma de dados,
incluindo ambientes, formatos, extensões, status, limites, paths, chaves de
configuração, nomes de métricas, headers, mime types, timezone e defaults.

Objetivos:
- Evitar strings mágicas espalhadas pelo código.
- Padronizar nomenclatura entre ingestion, validation, ai, utils e pipelines.
- Facilitar governança, observabilidade e configuração enterprise.
- Manter compatibilidade com ambientes locais, staging e produção.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, FrozenSet, Mapping, Tuple


# =============================================================================
# Package metadata
# =============================================================================

PACKAGE_NAME: Final[str] = "data"
UTILS_PACKAGE_NAME: Final[str] = "data.utils"
DEFAULT_SERVICE_NAME: Final[str] = "data-platform"
DEFAULT_SERVICE_VERSION: Final[str] = "1.0.0"
DEFAULT_ENCODING: Final[str] = "utf-8"
DEFAULT_TIMEZONE: Final[str] = "UTC"
ISO_DATETIME_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"
ISO_DATE_FORMAT: Final[str] = "%Y-%m-%d"


# =============================================================================
# Runtime environments
# =============================================================================

class Environment(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    TEST = "test"
    CI = "ci"
    STAGING = "staging"
    HOMOLOGATION = "homologation"
    PRODUCTION = "production"
    SANDBOX = "sandbox"
    UNKNOWN = "unknown"


ENV_ALIASES: Final[Mapping[str, Environment]] = {
    "local": Environment.LOCAL,
    "localhost": Environment.LOCAL,
    "dev": Environment.DEVELOPMENT,
    "development": Environment.DEVELOPMENT,
    "test": Environment.TEST,
    "testing": Environment.TEST,
    "ci": Environment.CI,
    "stage": Environment.STAGING,
    "staging": Environment.STAGING,
    "hml": Environment.HOMOLOGATION,
    "homolog": Environment.HOMOLOGATION,
    "homologation": Environment.HOMOLOGATION,
    "prod": Environment.PRODUCTION,
    "production": Environment.PRODUCTION,
    "sandbox": Environment.SANDBOX,
}

ENV_VAR_APP_ENV: Final[str] = "APP_ENV"
ENV_VAR_ENVIRONMENT: Final[str] = "ENVIRONMENT"
ENV_VAR_LOG_LEVEL: Final[str] = "LOG_LEVEL"
ENV_VAR_CONFIG_PATH: Final[str] = "CONFIG_PATH"
ENV_VAR_SECRETS_PATH: Final[str] = "SECRETS_PATH"
ENV_VAR_DATA_ROOT: Final[str] = "DATA_ROOT"
ENV_VAR_TEMP_DIR: Final[str] = "TEMP_DIR"
ENV_VAR_RUN_ID: Final[str] = "RUN_ID"
ENV_VAR_CORRELATION_ID: Final[str] = "CORRELATION_ID"


# =============================================================================
# Directory and path defaults
# =============================================================================

PROJECT_ROOT: Final[Path] = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
DATA_ROOT: Final[Path] = Path(os.getenv(ENV_VAR_DATA_ROOT, PROJECT_ROOT / "data")).resolve()
CONFIG_DIR: Final[Path] = Path(os.getenv("CONFIG_DIR", PROJECT_ROOT / "config")).resolve()
LOG_DIR: Final[Path] = Path(os.getenv("LOG_DIR", PROJECT_ROOT / "logs")).resolve()
TEMP_DIR: Final[Path] = Path(os.getenv(ENV_VAR_TEMP_DIR, PROJECT_ROOT / "tmp")).resolve()
CACHE_DIR: Final[Path] = Path(os.getenv("CACHE_DIR", PROJECT_ROOT / ".cache")).resolve()
ARTIFACTS_DIR: Final[Path] = Path(os.getenv("ARTIFACTS_DIR", PROJECT_ROOT / "artifacts")).resolve()

RAW_DATA_DIR: Final[Path] = DATA_ROOT / "raw"
BRONZE_DATA_DIR: Final[Path] = DATA_ROOT / "bronze"
SILVER_DATA_DIR: Final[Path] = DATA_ROOT / "silver"
GOLD_DATA_DIR: Final[Path] = DATA_ROOT / "gold"
QUARANTINE_DATA_DIR: Final[Path] = DATA_ROOT / "quarantine"
CHECKPOINT_DIR: Final[Path] = DATA_ROOT / "checkpoints"
METADATA_DIR: Final[Path] = DATA_ROOT / "metadata"

DEFAULT_CONFIG_FILE: Final[Path] = CONFIG_DIR / "settings.yaml"
DEFAULT_LOG_FILE: Final[Path] = LOG_DIR / "application.log"
DEFAULT_AUDIT_LOG_FILE: Final[Path] = LOG_DIR / "audit.jsonl"
DEFAULT_METRICS_FILE: Final[Path] = LOG_DIR / "metrics.jsonl"


# =============================================================================
# File formats and extensions
# =============================================================================

class FileFormat(str, Enum):
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    JSONL = "jsonl"
    PARQUET = "parquet"
    AVRO = "avro"
    ORC = "orc"
    XML = "xml"
    YAML = "yaml"
    EXCEL = "excel"
    PICKLE = "pickle"
    DELTA = "delta"
    ICEBERG = "iceberg"
    HUDI = "hudi"
    TEXT = "text"
    BINARY = "binary"
    UNKNOWN = "unknown"


class CompressionFormat(str, Enum):
    NONE = "none"
    GZIP = "gzip"
    BZ2 = "bz2"
    XZ = "xz"
    ZIP = "zip"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"


CSV_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".csv"})
TSV_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".tsv", ".tab"})
JSON_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".json"})
JSONL_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".jsonl", ".ndjson"})
PARQUET_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".parquet", ".pq"})
AVRO_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".avro"})
ORC_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".orc"})
XML_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".xml"})
YAML_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".yaml", ".yml"})
EXCEL_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".xlsx", ".xls", ".xlsm"})
TEXT_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".txt", ".log", ".md"})
COMPRESSED_EXTENSIONS: Final[FrozenSet[str]] = frozenset({".gz", ".bz2", ".xz", ".zip", ".tar", ".tgz", ".tbz2", ".txz"})

ALL_SUPPORTED_EXTENSIONS: Final[FrozenSet[str]] = frozenset().union(
    CSV_EXTENSIONS,
    TSV_EXTENSIONS,
    JSON_EXTENSIONS,
    JSONL_EXTENSIONS,
    PARQUET_EXTENSIONS,
    AVRO_EXTENSIONS,
    ORC_EXTENSIONS,
    XML_EXTENSIONS,
    YAML_EXTENSIONS,
    EXCEL_EXTENSIONS,
    TEXT_EXTENSIONS,
    COMPRESSED_EXTENSIONS,
)

EXTENSION_TO_FORMAT: Final[Mapping[str, FileFormat]] = {
    **{ext: FileFormat.CSV for ext in CSV_EXTENSIONS},
    **{ext: FileFormat.TSV for ext in TSV_EXTENSIONS},
    **{ext: FileFormat.JSON for ext in JSON_EXTENSIONS},
    **{ext: FileFormat.JSONL for ext in JSONL_EXTENSIONS},
    **{ext: FileFormat.PARQUET for ext in PARQUET_EXTENSIONS},
    **{ext: FileFormat.AVRO for ext in AVRO_EXTENSIONS},
    **{ext: FileFormat.ORC for ext in ORC_EXTENSIONS},
    **{ext: FileFormat.XML for ext in XML_EXTENSIONS},
    **{ext: FileFormat.YAML for ext in YAML_EXTENSIONS},
    **{ext: FileFormat.EXCEL for ext in EXCEL_EXTENSIONS},
    **{ext: FileFormat.TEXT for ext in TEXT_EXTENSIONS},
}


# =============================================================================
# MIME types
# =============================================================================

MIME_JSON: Final[str] = "application/json"
MIME_JSONL: Final[str] = "application/x-ndjson"
MIME_CSV: Final[str] = "text/csv"
MIME_TEXT: Final[str] = "text/plain"
MIME_PARQUET: Final[str] = "application/octet-stream"
MIME_ZIP: Final[str] = "application/zip"
MIME_GZIP: Final[str] = "application/gzip"
MIME_BINARY: Final[str] = "application/octet-stream"

FORMAT_TO_MIME: Final[Mapping[FileFormat, str]] = {
    FileFormat.CSV: MIME_CSV,
    FileFormat.TSV: MIME_TEXT,
    FileFormat.JSON: MIME_JSON,
    FileFormat.JSONL: MIME_JSONL,
    FileFormat.PARQUET: MIME_PARQUET,
    FileFormat.TEXT: MIME_TEXT,
    FileFormat.BINARY: MIME_BINARY,
}


# =============================================================================
# Data layers and pipeline concepts
# =============================================================================

class DataLayer(str, Enum):
    RAW = "raw"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    MART = "mart"
    FEATURE = "feature"
    QUARANTINE = "quarantine"
    ARCHIVE = "archive"


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    RETRYING = "RETRYING"
    TIMEOUT = "TIMEOUT"


class DataOperation(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    APPEND = "APPEND"
    UPSERT = "UPSERT"
    DELETE = "DELETE"
    MERGE = "MERGE"
    VALIDATE = "VALIDATE"
    TRANSFORM = "TRANSFORM"
    INGEST = "INGEST"
    EXPORT = "EXPORT"
    ARCHIVE = "ARCHIVE"


class LoadMode(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    CDC = "cdc"
    SNAPSHOT = "snapshot"
    STREAMING = "streaming"
    MICRO_BATCH = "micro_batch"


class WriteMode(str, Enum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    UPSERT = "upsert"
    MERGE = "merge"
    IGNORE = "ignore"
    ERROR_IF_EXISTS = "error_if_exists"


# =============================================================================
# Validation constants
# =============================================================================

class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ValidationDomain(str, Enum):
    SCHEMA = "SCHEMA"
    QUALITY = "QUALITY"
    INTEGRITY = "INTEGRITY"
    PII = "PII"
    COMPLIANCE = "COMPLIANCE"
    CONSISTENCY = "CONSISTENCY"
    CONTRACT = "CONTRACT"
    DRIFT = "DRIFT"


DEFAULT_MIN_QUALITY_SCORE: Final[float] = 0.95
DEFAULT_WARNING_QUALITY_SCORE: Final[float] = 0.98
DEFAULT_MAX_EVIDENCE: Final[int] = 500
DEFAULT_RULE_MAX_EVIDENCE: Final[int] = 50
DEFAULT_SAMPLE_SIZE: Final[int] = 10_000
DEFAULT_MAX_SCHEMA_DRIFT_SCORE: Final[float] = 0.05
DEFAULT_MAX_NULL_RATIO: Final[float] = 0.01
DEFAULT_MAX_DUPLICATE_RATIO: Final[float] = 0.0
DEFAULT_OUTLIER_ZSCORE_THRESHOLD: Final[float] = 3.0
DEFAULT_OUTLIER_IQR_MULTIPLIER: Final[float] = 1.5
DEFAULT_FRESHNESS_SLA_SECONDS: Final[int] = 24 * 60 * 60


# =============================================================================
# Security and privacy constants
# =============================================================================

class PrivacyClassification(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET = "SECRET"


class PIIType(str, Enum):
    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    ADDRESS = "ADDRESS"
    PERSON_NAME = "PERSON_NAME"
    BIRTH_DATE = "BIRTH_DATE"
    TOKEN = "TOKEN"
    PASSWORD = "PASSWORD"
    API_KEY = "API_KEY"
    UNKNOWN = "UNKNOWN"


SECRET_FIELD_NAMES: Final[FrozenSet[str]] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "senha",
        "secret",
        "secret_key",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
        "authorization",
        "credential",
        "credentials",
    }
)

PII_FIELD_NAME_HINTS: Final[FrozenSet[str]] = frozenset(
    {
        "cpf",
        "cnpj",
        "email",
        "telefone",
        "phone",
        "celular",
        "whatsapp",
        "address",
        "endereco",
        "nome",
        "name",
        "birth",
        "nascimento",
        "document",
        "documento",
        "credit_card",
        "card_number",
    }
)

MASK_REDACTED: Final[str] = "[REDACTED]"
MASK_NULL: Final[str] = "[NULL]"
MASK_UNKNOWN: Final[str] = "[UNKNOWN]"


# =============================================================================
# HTTP/API constants
# =============================================================================

HTTP_HEADER_AUTHORIZATION: Final[str] = "Authorization"
HTTP_HEADER_CONTENT_TYPE: Final[str] = "Content-Type"
HTTP_HEADER_ACCEPT: Final[str] = "Accept"
HTTP_HEADER_USER_AGENT: Final[str] = "User-Agent"
HTTP_HEADER_CORRELATION_ID: Final[str] = "X-Correlation-ID"
HTTP_HEADER_REQUEST_ID: Final[str] = "X-Request-ID"
HTTP_HEADER_IDEMPOTENCY_KEY: Final[str] = "Idempotency-Key"

HTTP_STATUS_OK: Final[int] = 200
HTTP_STATUS_CREATED: Final[int] = 201
HTTP_STATUS_ACCEPTED: Final[int] = 202
HTTP_STATUS_NO_CONTENT: Final[int] = 204
HTTP_STATUS_BAD_REQUEST: Final[int] = 400
HTTP_STATUS_UNAUTHORIZED: Final[int] = 401
HTTP_STATUS_FORBIDDEN: Final[int] = 403
HTTP_STATUS_NOT_FOUND: Final[int] = 404
HTTP_STATUS_CONFLICT: Final[int] = 409
HTTP_STATUS_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_STATUS_INTERNAL_ERROR: Final[int] = 500
HTTP_STATUS_BAD_GATEWAY: Final[int] = 502
HTTP_STATUS_SERVICE_UNAVAILABLE: Final[int] = 503
HTTP_STATUS_GATEWAY_TIMEOUT: Final[int] = 504

RETRYABLE_HTTP_STATUS_CODES: Final[FrozenSet[int]] = frozenset(
    {
        HTTP_STATUS_TOO_MANY_REQUESTS,
        HTTP_STATUS_INTERNAL_ERROR,
        HTTP_STATUS_BAD_GATEWAY,
        HTTP_STATUS_SERVICE_UNAVAILABLE,
        HTTP_STATUS_GATEWAY_TIMEOUT,
    }
)


# =============================================================================
# Database and SQL constants
# =============================================================================

class DatabaseEngine(str, Enum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"
    ORACLE = "oracle"
    SQLITE = "sqlite"
    BIGQUERY = "bigquery"
    SNOWFLAKE = "snowflake"
    REDSHIFT = "redshift"
    DATABRICKS = "databricks"
    DUCKDB = "duckdb"
    UNKNOWN = "unknown"


DEFAULT_DB_POOL_SIZE: Final[int] = 10
DEFAULT_DB_MAX_OVERFLOW: Final[int] = 20
DEFAULT_DB_POOL_TIMEOUT_SECONDS: Final[int] = 30
DEFAULT_DB_QUERY_TIMEOUT_SECONDS: Final[int] = 300
DEFAULT_DB_BATCH_SIZE: Final[int] = 10_000
DEFAULT_DB_FETCH_SIZE: Final[int] = 10_000

SQL_PARAM_STYLE_NAMED: Final[str] = "named"
SQL_PARAM_STYLE_QMARK: Final[str] = "qmark"
SQL_PARAM_STYLE_FORMAT: Final[str] = "format"


# =============================================================================
# Concurrency/retry constants
# =============================================================================

DEFAULT_MAX_WORKERS: Final[int] = max(4, (os.cpu_count() or 2) * 2)
DEFAULT_ASYNC_CONCURRENCY: Final[int] = 25
DEFAULT_RETRY_ATTEMPTS: Final[int] = 3
DEFAULT_RETRY_INITIAL_DELAY_SECONDS: Final[float] = 0.2
DEFAULT_RETRY_MAX_DELAY_SECONDS: Final[float] = 10.0
DEFAULT_RETRY_BACKOFF_MULTIPLIER: Final[float] = 2.0
DEFAULT_RETRY_JITTER_SECONDS: Final[float] = 0.1
DEFAULT_TIMEOUT_SECONDS: Final[int] = 300
DEFAULT_CONNECT_TIMEOUT_SECONDS: Final[int] = 10
DEFAULT_READ_TIMEOUT_SECONDS: Final[int] = 60


# =============================================================================
# Logging, audit and metrics constants
# =============================================================================

LOG_FORMAT_TEXT: Final[str] = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_FORMAT_JSON: Final[str] = "json"
DEFAULT_LOG_LEVEL: Final[str] = os.getenv(ENV_VAR_LOG_LEVEL, "INFO")

AUDIT_EVENT_SCHEMA_VERSION: Final[str] = "1.0"
METRICS_NAMESPACE: Final[str] = "data_platform"
METRIC_VALIDATION_EXECUTED: Final[str] = "validation.executed"
METRIC_VALIDATION_DURATION_MS: Final[str] = "validation.duration_ms"
METRIC_VALIDATION_SCORE: Final[str] = "validation.score"
METRIC_VALIDATION_ISSUES: Final[str] = "validation.issues"
METRIC_PIPELINE_EXECUTED: Final[str] = "pipeline.executed"
METRIC_PIPELINE_DURATION_MS: Final[str] = "pipeline.duration_ms"
METRIC_INGESTION_ROWS: Final[str] = "ingestion.rows"
METRIC_INGESTION_BYTES: Final[str] = "ingestion.bytes"
METRIC_AI_TOKENS: Final[str] = "ai.tokens"
METRIC_AI_LATENCY_MS: Final[str] = "ai.latency_ms"

STANDARD_TAG_DATASET: Final[str] = "dataset"
STANDARD_TAG_PIPELINE: Final[str] = "pipeline"
STANDARD_TAG_ENVIRONMENT: Final[str] = "environment"
STANDARD_TAG_TENANT: Final[str] = "tenant"
STANDARD_TAG_SOURCE: Final[str] = "source"
STANDARD_TAG_STATUS: Final[str] = "status"
STANDARD_TAG_RUN_ID: Final[str] = "run_id"
STANDARD_TAG_RULE_ID: Final[str] = "rule_id"
STANDARD_TAG_RULE_TYPE: Final[str] = "rule_type"


# =============================================================================
# Size limits
# =============================================================================

BYTES_PER_KB: Final[int] = 1024
BYTES_PER_MB: Final[int] = 1024 * 1024
BYTES_PER_GB: Final[int] = 1024 * 1024 * 1024
DEFAULT_CHUNK_SIZE_BYTES: Final[int] = 1024 * 1024
DEFAULT_STREAM_BUFFER_SIZE_BYTES: Final[int] = 8 * 1024 * 1024
DEFAULT_MAX_FILE_SIZE_BYTES: Final[int] = 5 * BYTES_PER_GB
DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES: Final[int] = 20 * BYTES_PER_GB
DEFAULT_MAX_ARCHIVE_MEMBERS: Final[int] = 100_000
DEFAULT_MAX_JSON_PAYLOAD_BYTES: Final[int] = 50 * BYTES_PER_MB
DEFAULT_MAX_TEXT_LENGTH: Final[int] = 1_000_000


# =============================================================================
# DataFrame/schema defaults
# =============================================================================

DEFAULT_ID_COLUMNS: Final[Tuple[str, ...]] = ("id",)
DEFAULT_TIMESTAMP_COLUMNS: Final[Tuple[str, ...]] = ("created_at", "updated_at", "ingested_at")
DEFAULT_AUDIT_COLUMNS: Final[Tuple[str, ...]] = ("created_at", "updated_at", "created_by", "updated_by")
DEFAULT_SOFT_DELETE_COLUMNS: Final[Tuple[str, ...]] = ("deleted_at", "is_deleted")
DEFAULT_PARTITION_COLUMNS: Final[Tuple[str, ...]] = ("dt",)
DEFAULT_REQUIRED_METADATA_COLUMNS: Final[Tuple[str, ...]] = ("ingestion_run_id", "ingested_at", "source_system")

COLUMN_NAME_PATTERN: Final[str] = r"^[a-zA-Z_][a-zA-Z0-9_]*$"
SNAKE_CASE_PATTERN: Final[str] = r"^[a-z][a-z0-9_]*$"
EMAIL_PATTERN: Final[str] = r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$"
UUID_PATTERN: Final[str] = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
CPF_PATTERN: Final[str] = r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$"
CNPJ_PATTERN: Final[str] = r"^\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}$"


# =============================================================================
# AI/RAG constants
# =============================================================================

DEFAULT_EMBEDDING_DIMENSION: Final[int] = 1536
DEFAULT_VECTOR_TOP_K: Final[int] = 10
DEFAULT_VECTOR_SCORE_THRESHOLD: Final[float] = 0.75
DEFAULT_CHUNK_TOKENS: Final[int] = 800
DEFAULT_CHUNK_OVERLAP_TOKENS: Final[int] = 120
DEFAULT_MAX_CONTEXT_TOKENS: Final[int] = 12_000
DEFAULT_TEMPERATURE: Final[float] = 0.2
DEFAULT_MAX_OUTPUT_TOKENS: Final[int] = 2_000


# =============================================================================
# Dataclasses for grouped settings
# =============================================================================

@dataclass(frozen=True)
class RuntimeDefaults:
    environment: Environment = Environment.UNKNOWN
    encoding: str = DEFAULT_ENCODING
    timezone: str = DEFAULT_TIMEZONE
    max_workers: int = DEFAULT_MAX_WORKERS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS


@dataclass(frozen=True)
class PathDefaults:
    project_root: Path = PROJECT_ROOT
    data_root: Path = DATA_ROOT
    config_dir: Path = CONFIG_DIR
    log_dir: Path = LOG_DIR
    temp_dir: Path = TEMP_DIR
    cache_dir: Path = CACHE_DIR
    artifacts_dir: Path = ARTIFACTS_DIR


@dataclass(frozen=True)
class ValidationDefaults:
    min_quality_score: float = DEFAULT_MIN_QUALITY_SCORE
    warning_quality_score: float = DEFAULT_WARNING_QUALITY_SCORE
    max_evidence: int = DEFAULT_MAX_EVIDENCE
    rule_max_evidence: int = DEFAULT_RULE_MAX_EVIDENCE
    sample_size: int = DEFAULT_SAMPLE_SIZE
    freshness_sla_seconds: int = DEFAULT_FRESHNESS_SLA_SECONDS


# =============================================================================
# Helper functions
# =============================================================================

def normalize_environment(value: str | None) -> Environment:
    """Normaliza string de ambiente para enum Environment."""
    if value is None:
        return Environment.UNKNOWN
    return ENV_ALIASES.get(str(value).strip().lower(), Environment.UNKNOWN)


def current_environment() -> Environment:
    """Detecta ambiente atual usando variáveis de ambiente comuns."""
    return normalize_environment(
        os.getenv(ENV_VAR_APP_ENV)
        or os.getenv(ENV_VAR_ENVIRONMENT)
        or os.getenv("ENV")
        or os.getenv("PYTHON_ENV")
    )


def is_production() -> bool:
    """Indica se ambiente atual é produção."""
    return current_environment() == Environment.PRODUCTION


def is_local() -> bool:
    """Indica se ambiente atual é local."""
    return current_environment() == Environment.LOCAL


def extension_to_format(extension: str) -> FileFormat:
    """Mapeia extensão para formato conhecido."""
    ext = extension.lower().strip()
    if not ext.startswith("."):
        ext = f".{ext}"
    return EXTENSION_TO_FORMAT.get(ext, FileFormat.UNKNOWN)


def path_to_format(path: str | Path) -> FileFormat:
    """Infere formato de arquivo a partir de um path."""
    return extension_to_format(Path(path).suffix)


def runtime_defaults() -> RuntimeDefaults:
    """Retorna defaults de runtime com ambiente detectado."""
    return RuntimeDefaults(environment=current_environment())


def path_defaults() -> PathDefaults:
    """Retorna defaults de paths."""
    return PathDefaults()


def validation_defaults() -> ValidationDefaults:
    """Retorna defaults de validação."""
    return ValidationDefaults()


__all__ = [
    "ALL_SUPPORTED_EXTENSIONS",
    "ARTIFACTS_DIR",
    "AUDIT_EVENT_SCHEMA_VERSION",
    "AVRO_EXTENSIONS",
    "BRONZE_DATA_DIR",
    "BYTES_PER_GB",
    "BYTES_PER_KB",
    "BYTES_PER_MB",
    "CACHE_DIR",
    "CHECKPOINT_DIR",
    "CNPJ_PATTERN",
    "COLUMN_NAME_PATTERN",
    "COMPRESSED_EXTENSIONS",
    "CONFIG_DIR",
    "CSV_EXTENSIONS",
    "CompressionFormat",
    "DATA_ROOT",
    "DEFAULT_AI_TOKENS" if False else "DEFAULT_MAX_CONTEXT_TOKENS",
    "DEFAULT_ASYNC_CONCURRENCY",
    "DEFAULT_AUDIT_LOG_FILE",
    "DEFAULT_CHUNK_OVERLAP_TOKENS",
    "DEFAULT_CHUNK_SIZE_BYTES",
    "DEFAULT_CHUNK_TOKENS",
    "DEFAULT_CONFIG_FILE",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_DB_BATCH_SIZE",
    "DEFAULT_DB_FETCH_SIZE",
    "DEFAULT_DB_MAX_OVERFLOW",
    "DEFAULT_DB_POOL_SIZE",
    "DEFAULT_DB_POOL_TIMEOUT_SECONDS",
    "DEFAULT_DB_QUERY_TIMEOUT_SECONDS",
    "DEFAULT_ENCODING",
    "DEFAULT_EMBEDDING_DIMENSION",
    "DEFAULT_FRESHNESS_SLA_SECONDS",
    "DEFAULT_LOG_FILE",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_MAX_ARCHIVE_MEMBERS",
    "DEFAULT_MAX_ARCHIVE_UNCOMPRESSED_BYTES",
    "DEFAULT_MAX_EVIDENCE",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "DEFAULT_MAX_JSON_PAYLOAD_BYTES",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MAX_SCHEMA_DRIFT_SCORE",
    "DEFAULT_MAX_TEXT_LENGTH",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_METRICS_FILE",
    "DEFAULT_MIN_QUALITY_SCORE",
    "DEFAULT_OUTLIER_IQR_MULTIPLIER",
    "DEFAULT_OUTLIER_ZSCORE_THRESHOLD",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_RETRY_ATTEMPTS",
    "DEFAULT_RETRY_BACKOFF_MULTIPLIER",
    "DEFAULT_RETRY_INITIAL_DELAY_SECONDS",
    "DEFAULT_RETRY_JITTER_SECONDS",
    "DEFAULT_RETRY_MAX_DELAY_SECONDS",
    "DEFAULT_RULE_MAX_EVIDENCE",
    "DEFAULT_SAMPLE_SIZE",
    "DEFAULT_SERVICE_NAME",
    "DEFAULT_SERVICE_VERSION",
    "DEFAULT_STREAM_BUFFER_SIZE_BYTES",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_TIMEZONE",
    "DEFAULT_VECTOR_SCORE_THRESHOLD",
    "DEFAULT_VECTOR_TOP_K",
    "DEFAULT_WARNING_QUALITY_SCORE",
    "EMAIL_PATTERN",
    "ENV_ALIASES",
    "ENV_VAR_APP_ENV",
    "ENV_VAR_CONFIG_PATH",
    "ENV_VAR_CORRELATION_ID",
    "ENV_VAR_DATA_ROOT",
    "ENV_VAR_ENVIRONMENT",
    "ENV_VAR_LOG_LEVEL",
    "ENV_VAR_RUN_ID",
    "ENV_VAR_SECRETS_PATH",
    "ENV_VAR_TEMP_DIR",
    "EXCEL_EXTENSIONS",
    "EXTENSION_TO_FORMAT",
    "Environment",
    "FileFormat",
    "FORMAT_TO_MIME",
    "GOLD_DATA_DIR",
    "HTTP_HEADER_ACCEPT",
    "HTTP_HEADER_AUTHORIZATION",
    "HTTP_HEADER_CONTENT_TYPE",
    "HTTP_HEADER_CORRELATION_ID",
    "HTTP_HEADER_IDEMPOTENCY_KEY",
    "HTTP_HEADER_REQUEST_ID",
    "HTTP_HEADER_USER_AGENT",
    "HTTP_STATUS_ACCEPTED",
    "HTTP_STATUS_BAD_GATEWAY",
    "HTTP_STATUS_BAD_REQUEST",
    "HTTP_STATUS_CONFLICT",
    "HTTP_STATUS_CREATED",
    "HTTP_STATUS_FORBIDDEN",
    "HTTP_STATUS_GATEWAY_TIMEOUT",
    "HTTP_STATUS_INTERNAL_ERROR",
    "HTTP_STATUS_NOT_FOUND",
    "HTTP_STATUS_NO_CONTENT",
    "HTTP_STATUS_OK",
    "HTTP_STATUS_SERVICE_UNAVAILABLE",
    "HTTP_STATUS_TOO_MANY_REQUESTS",
    "HTTP_STATUS_UNAUTHORIZED",
    "ISO_DATE_FORMAT",
    "ISO_DATETIME_FORMAT",
    "JSONL_EXTENSIONS",
    "JSON_EXTENSIONS",
    "LOG_DIR",
    "LOG_FORMAT_JSON",
    "LOG_FORMAT_TEXT",
    "LoadMode",
    "MASK_NULL",
    "MASK_REDACTED",
    "MASK_UNKNOWN",
    "METADATA_DIR",
    "METRICS_NAMESPACE",
    "MIME_BINARY",
    "MIME_CSV",
    "MIME_GZIP",
    "MIME_JSON",
    "MIME_JSONL",
    "MIME_PARQUET",
    "MIME_TEXT",
    "MIME_ZIP",
    "ORC_EXTENSIONS",
    "PACKAGE_NAME",
    "PARQUET_EXTENSIONS",
    "PIIType",
    "PII_FIELD_NAME_HINTS",
    "PROJECT_ROOT",
    "PathDefaults",
    "PipelineStatus",
    "PrivacyClassification",
    "QUARANTINE_DATA_DIR",
    "RAW_DATA_DIR",
    "RETRYABLE_HTTP_STATUS_CODES",
    "RuntimeDefaults",
    "SILVER_DATA_DIR",
    "SECRET_FIELD_NAMES",
    "SNAKE_CASE_PATTERN",
    "STANDARD_TAG_DATASET",
    "STANDARD_TAG_ENVIRONMENT",
    "STANDARD_TAG_PIPELINE",
    "STANDARD_TAG_RULE_ID",
    "STANDARD_TAG_RULE_TYPE",
    "STANDARD_TAG_RUN_ID",
    "STANDARD_TAG_SOURCE",
    "STANDARD_TAG_STATUS",
    "STANDARD_TAG_TENANT",
    "Severity",
    "TEMP_DIR",
    "TEXT_EXTENSIONS",
    "TSV_EXTENSIONS",
    "UUID_PATTERN",
    "UTILS_PACKAGE_NAME",
    "ValidationDefaults",
    "ValidationDomain",
    "ValidationStatus",
    "WriteMode",
    "XML_EXTENSIONS",
    "YAML_EXTENSIONS",
    "current_environment",
    "extension_to_format",
    "is_local",
    "is_production",
    "normalize_environment",
    "path_defaults",
    "path_to_format",
    "runtime_defaults",
    "validation_defaults",
]
