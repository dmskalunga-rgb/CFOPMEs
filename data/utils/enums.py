"""
data/utils/enums.py

Enterprise-grade shared enums catalog.

Este módulo centraliza enums compartilhados por toda a plataforma de dados:
ingestão, validação, IA, governança, observabilidade, segurança, storage,
mensageria, orquestração e utilitários.

Objetivos:
- Evitar strings mágicas espalhadas pelo código.
- Padronizar status, severidades, formatos, ambientes e domínios.
- Facilitar serialização/deserialização segura de enums.
- Fornecer helpers para parse tolerante, listagem e validação.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar


E = TypeVar("E", bound=Enum)
JsonDict = Dict[str, Any]


class StrEnum(str, Enum):
    """Enum string-friendly com helpers comuns."""

    def __str__(self) -> str:
        return self.value

    @classmethod
    def values(cls) -> Tuple[str, ...]:
        return tuple(item.value for item in cls)  # type: ignore[misc]

    @classmethod
    def names(cls) -> Tuple[str, ...]:
        return tuple(item.name for item in cls)  # type: ignore[misc]

    @classmethod
    def has_value(cls, value: Any) -> bool:
        try:
            cls(value)  # type: ignore[call-arg]
            return True
        except Exception:
            return False

    @classmethod
    def parse(cls: Type[E], value: Any, *, default: Optional[E] = None, case_sensitive: bool = False) -> E:
        return parse_enum(cls, value, default=default, case_sensitive=case_sensitive)


# =============================================================================
# Runtime / platform
# =============================================================================

class Environment(StrEnum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    TEST = "test"
    CI = "ci"
    STAGING = "staging"
    HOMOLOGATION = "homologation"
    SANDBOX = "sandbox"
    PRODUCTION = "production"
    UNKNOWN = "unknown"


class RuntimeMode(StrEnum):
    BATCH = "batch"
    STREAMING = "streaming"
    MICRO_BATCH = "micro_batch"
    API = "api"
    CLI = "cli"
    WORKER = "worker"
    NOTEBOOK = "notebook"
    TEST = "test"


class Region(StrEnum):
    GLOBAL = "global"
    US = "us"
    EU = "eu"
    BR = "br"
    LATAM = "latam"
    APAC = "apac"
    AFRICA = "africa"
    UNKNOWN = "unknown"


# =============================================================================
# Generic statuses and severity
# =============================================================================

class Status(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    RETRYING = "RETRYING"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


class Severity(StrEnum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    WARN = "WARN"
    REVIEW = "REVIEW"
    QUARANTINE = "QUARANTINE"
    SKIP = "SKIP"


# =============================================================================
# Data concepts
# =============================================================================

class DataLayer(StrEnum):
    RAW = "raw"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    MART = "mart"
    FEATURE = "feature"
    SANDBOX = "sandbox"
    QUARANTINE = "quarantine"
    ARCHIVE = "archive"


class DataDomain(StrEnum):
    CUSTOMER = "customer"
    PRODUCT = "product"
    SALES = "sales"
    FINANCE = "finance"
    OPERATIONS = "operations"
    MARKETING = "marketing"
    SUPPLY_CHAIN = "supply_chain"
    HUMAN_RESOURCES = "human_resources"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    AI = "ai"
    UNKNOWN = "unknown"


class DataOperation(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    APPEND = "APPEND"
    OVERWRITE = "OVERWRITE"
    UPSERT = "UPSERT"
    MERGE = "MERGE"
    DELETE = "DELETE"
    VALIDATE = "VALIDATE"
    TRANSFORM = "TRANSFORM"
    INGEST = "INGEST"
    EXPORT = "EXPORT"
    ARCHIVE = "ARCHIVE"
    RESTORE = "RESTORE"


class DataSensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    SECRET = "SECRET"
    HIGHLY_SECRET = "HIGHLY_SECRET"


class DataFreshness(StrEnum):
    REAL_TIME = "real_time"
    NEAR_REAL_TIME = "near_real_time"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    AD_HOC = "ad_hoc"


# =============================================================================
# File / serialization / compression
# =============================================================================

class FileFormat(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    JSONL = "jsonl"
    NDJSON = "ndjson"
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


class CompressionFormat(StrEnum):
    NONE = "none"
    GZIP = "gzip"
    BZ2 = "bz2"
    XZ = "xz"
    ZIP = "zip"
    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"
    ZSTD = "zstd"
    SNAPPY = "snappy"


class Encoding(StrEnum):
    UTF_8 = "utf-8"
    UTF_8_SIG = "utf-8-sig"
    LATIN_1 = "latin-1"
    ASCII = "ascii"
    UTF_16 = "utf-16"


class SerializationFormat(StrEnum):
    JSON = "json"
    JSONL = "jsonl"
    NDJSON = "ndjson"
    CSV = "csv"
    TSV = "tsv"
    YAML = "yaml"
    XML = "xml"
    BYTES = "bytes"
    TEXT = "text"
    BASE64 = "base64"
    PICKLE = "pickle"
    AUTO = "auto"
    UNKNOWN = "unknown"


# =============================================================================
# Ingestion / loading
# =============================================================================

class LoadMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    CDC = "cdc"
    SNAPSHOT = "snapshot"
    STREAMING = "streaming"
    MICRO_BATCH = "micro_batch"


class WriteMode(StrEnum):
    APPEND = "append"
    OVERWRITE = "overwrite"
    UPSERT = "upsert"
    MERGE = "merge"
    IGNORE = "ignore"
    ERROR_IF_EXISTS = "error_if_exists"


class SourceType(StrEnum):
    FILE = "file"
    DATABASE = "database"
    API = "api"
    STREAM = "stream"
    QUEUE = "queue"
    OBJECT_STORAGE = "object_storage"
    FTP = "ftp"
    SFTP = "sftp"
    WEBHOOK = "webhook"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class IngestionStrategy(StrEnum):
    PULL = "pull"
    PUSH = "push"
    POLLING = "polling"
    EVENT_DRIVEN = "event_driven"
    SCHEDULED = "scheduled"
    BACKFILL = "backfill"


# =============================================================================
# Validation / governance
# =============================================================================

class ValidationDomain(StrEnum):
    SCHEMA = "SCHEMA"
    QUALITY = "QUALITY"
    INTEGRITY = "INTEGRITY"
    PII = "PII"
    COMPLIANCE = "COMPLIANCE"
    CONSISTENCY = "CONSISTENCY"
    CONTRACT = "CONTRACT"
    DRIFT = "DRIFT"
    SECURITY = "SECURITY"
    OBSERVABILITY = "OBSERVABILITY"
    CUSTOM = "CUSTOM"


class ValidationDimension(StrEnum):
    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    VALIDITY = "VALIDITY"
    CONSISTENCY = "CONSISTENCY"
    ACCURACY = "ACCURACY"
    TIMELINESS = "TIMELINESS"
    FRESHNESS = "FRESHNESS"
    STABILITY = "STABILITY"
    CONFORMITY = "CONFORMITY"
    INTEGRITY = "INTEGRITY"
    PRIVACY = "PRIVACY"
    SECURITY = "SECURITY"
    COMPLIANCE = "COMPLIANCE"
    CUSTOM = "CUSTOM"


class RuleAction(StrEnum):
    LOG = "LOG"
    WARN = "WARN"
    FAIL = "FAIL"
    BLOCK = "BLOCK"
    QUARANTINE = "QUARANTINE"
    MASK = "MASK"
    HASH = "HASH"
    TOKENIZE = "TOKENIZE"
    SKIP_RECORD = "SKIP_RECORD"
    CUSTOM = "CUSTOM"


class SchemaCompatibilityMode(StrEnum):
    BACKWARD = "BACKWARD"
    FORWARD = "FORWARD"
    FULL = "FULL"
    NONE = "NONE"


class DriftType(StrEnum):
    SCHEMA = "SCHEMA"
    DATA = "DATA"
    DISTRIBUTION = "DISTRIBUTION"
    CONCEPT = "CONCEPT"
    QUALITY = "QUALITY"
    VOLUME = "VOLUME"
    LATENCY = "LATENCY"


# =============================================================================
# Privacy / security
# =============================================================================

class PIIType(StrEnum):
    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    ADDRESS = "ADDRESS"
    PERSON_NAME = "PERSON_NAME"
    BIRTH_DATE = "BIRTH_DATE"
    DOCUMENT = "DOCUMENT"
    TOKEN = "TOKEN"
    PASSWORD = "PASSWORD"
    API_KEY = "API_KEY"
    BIOMETRIC = "BIOMETRIC"
    HEALTH = "HEALTH"
    UNKNOWN = "UNKNOWN"


class MaskingStrategy(StrEnum):
    NONE = "NONE"
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    LAST4 = "LAST4"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    HASH_SHA256 = "HASH_SHA256"
    HMAC_SHA256 = "HMAC_SHA256"
    TOKENIZE = "TOKENIZE"
    REDACT = "REDACT"


class AuthType(StrEnum):
    NONE = "none"
    BASIC = "basic"
    BEARER = "bearer"
    API_KEY = "api_key"
    OAUTH2 = "oauth2"
    JWT = "jwt"
    MTLS = "mtls"
    CUSTOM = "custom"


class Permission(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    ADMIN = "admin"
    OWNER = "owner"


# =============================================================================
# Storage / databases / messaging
# =============================================================================

class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    GCS = "gcs"
    AZURE_BLOB = "azure_blob"
    HDFS = "hdfs"
    SFTP = "sftp"
    DATABASE = "database"
    MEMORY = "memory"
    UNKNOWN = "unknown"


class DatabaseEngine(StrEnum):
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


class MessageBroker(StrEnum):
    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"
    SQS = "sqs"
    PUBSUB = "pubsub"
    EVENTHUB = "eventhub"
    REDIS = "redis"
    NATS = "nats"
    NONE = "none"


class DeliverySemantics(StrEnum):
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    EXACTLY_ONCE = "exactly_once"


# =============================================================================
# Observability / audit / metrics
# =============================================================================

class LogFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    STRUCTURED = "structured"


class MetricType(StrEnum):
    COUNTER = "COUNTER"
    GAUGE = "GAUGE"
    HISTOGRAM = "HISTOGRAM"
    SUMMARY = "SUMMARY"
    TIMING = "TIMING"
    EVENT = "EVENT"


class AuditAction(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    DELETED = "DELETED"
    READ = "READ"
    EXECUTED = "EXECUTED"
    STARTED = "STARTED"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DETECTED = "DETECTED"
    EXPORTED = "EXPORTED"
    IMPORTED = "IMPORTED"


class TracePropagation(StrEnum):
    W3C = "w3c"
    B3 = "b3"
    JAEGER = "jaeger"
    NONE = "none"


# =============================================================================
# AI / RAG / ML
# =============================================================================

class AIProvider(StrEnum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    AWS_BEDROCK = "aws_bedrock"
    HUGGINGFACE = "huggingface"
    LOCAL = "local"
    CUSTOM = "custom"


class ModelTask(StrEnum):
    CHAT = "chat"
    COMPLETION = "completion"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    SUMMARIZATION = "summarization"
    RERANKING = "reranking"
    MODERATION = "moderation"
    TRANSLATION = "translation"


class VectorMetric(StrEnum):
    COSINE = "cosine"
    DOT_PRODUCT = "dot_product"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"


class RetrievalStrategy(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    SEMANTIC = "semantic"
    KEYWORD = "keyword"
    GRAPH = "graph"


class HallucinationRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


# =============================================================================
# Helper dataclasses and functions
# =============================================================================

@dataclass(frozen=True)
class EnumInfo:
    """Informação serializável sobre um enum."""

    name: str
    module: str
    values: Tuple[str, ...]
    names: Tuple[str, ...]

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "module": self.module,
            "values": list(self.values),
            "names": list(self.names),
        }


def parse_enum(enum_cls: Type[E], value: Any, *, default: Optional[E] = None, case_sensitive: bool = False) -> E:
    """Parse tolerante de valor/nome para enum.

    Aceita:
    - instância do próprio enum;
    - value exato;
    - name exato;
    - comparação case-insensitive quando habilitada por padrão.
    """
    if isinstance(value, enum_cls):
        return value
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Cannot parse None as {enum_cls.__name__}")

    text = str(value)
    for item in enum_cls:
        if text == str(item.value) or text == item.name:
            return item
        if not case_sensitive:
            if text.lower() == str(item.value).lower() or text.lower() == item.name.lower():
                return item
    if default is not None:
        return default
    allowed = ", ".join(str(item.value) for item in enum_cls)
    raise ValueError(f"Invalid {enum_cls.__name__}: {value!r}. Allowed: {allowed}")


def enum_values(enum_cls: Type[Enum]) -> Tuple[str, ...]:
    return tuple(str(item.value) for item in enum_cls)


def enum_names(enum_cls: Type[Enum]) -> Tuple[str, ...]:
    return tuple(item.name for item in enum_cls)


def enum_info(enum_cls: Type[Enum]) -> EnumInfo:
    return EnumInfo(
        name=enum_cls.__name__,
        module=enum_cls.__module__,
        values=enum_values(enum_cls),
        names=enum_names(enum_cls),
    )


def enum_catalog() -> Mapping[str, EnumInfo]:
    """Retorna catálogo dos enums públicos deste módulo."""
    catalog: Dict[str, EnumInfo] = {}
    for value in __all__:
        obj = globals().get(value)
        if isinstance(obj, type) and issubclass(obj, Enum):
            catalog[value] = enum_info(obj)
    return catalog


def enum_catalog_json(indent: int = 2) -> str:
    return json.dumps({name: info.to_dict() for name, info in enum_catalog().items()}, ensure_ascii=False, indent=indent)


def is_success_status(value: Any) -> bool:
    status = Status.parse(value, default=Status.UNKNOWN)
    return status in {Status.SUCCEEDED}


def is_failure_status(value: Any) -> bool:
    status = Status.parse(value, default=Status.UNKNOWN)
    return status in {Status.FAILED, Status.ERROR, Status.TIMEOUT, Status.CANCELLED}


def severity_rank(value: Any) -> int:
    severity = Severity.parse(value, default=Severity.INFO)
    order = {
        Severity.TRACE: 0,
        Severity.DEBUG: 1,
        Severity.INFO: 2,
        Severity.NOTICE: 3,
        Severity.WARNING: 4,
        Severity.ERROR: 5,
        Severity.CRITICAL: 6,
        Severity.FATAL: 7,
    }
    return order[severity]


def max_severity(*values: Any) -> Severity:
    if not values:
        return Severity.INFO
    parsed = [Severity.parse(value, default=Severity.INFO) for value in values]
    return max(parsed, key=severity_rank)


__all__ = [
    "AIProvider",
    "AuditAction",
    "AuthType",
    "CompressionFormat",
    "DataDomain",
    "DataFreshness",
    "DataLayer",
    "DataOperation",
    "DataSensitivity",
    "DatabaseEngine",
    "Decision",
    "DeliverySemantics",
    "DriftType",
    "Encoding",
    "EnumInfo",
    "Environment",
    "FileFormat",
    "HallucinationRisk",
    "HealthStatus",
    "IngestionStrategy",
    "LoadMode",
    "LogFormat",
    "MaskingStrategy",
    "MessageBroker",
    "MetricType",
    "ModelTask",
    "PIIType",
    "Permission",
    "Region",
    "RetrievalStrategy",
    "RuleAction",
    "RuntimeMode",
    "SchemaCompatibilityMode",
    "SerializationFormat",
    "Severity",
    "SourceType",
    "Status",
    "StorageBackend",
    "StrEnum",
    "TracePropagation",
    "ValidationDimension",
    "ValidationDomain",
    "VectorMetric",
    "WriteMode",
    "enum_catalog",
    "enum_catalog_json",
    "enum_info",
    "enum_names",
    "enum_values",
    "is_failure_status",
    "is_success_status",
    "max_severity",
    "parse_enum",
    "severity_rank",
]
