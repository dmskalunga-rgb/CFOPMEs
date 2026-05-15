"""
data/quality/schema_drift_checker.py

Enterprise-grade Schema Drift Checker.

This module detects, classifies, scores, and reports schema drift between an
observed dataset schema and an expected/baseline schema. It is designed for
enterprise data quality gates, lakehouse contracts, ETL/ELT orchestration,
CDC validation, data contracts, governance workflows, and production data
observability.

Main capabilities:
- Baseline vs observed schema comparison
- Added, removed, renamed and reordered column detection
- Type drift detection with compatibility matrix
- Nullability drift detection
- Constraint drift detection
- Primary key / unique key / partition key drift
- Semantic type drift detection
- Backward/forward compatibility classification
- Severity-based findings
- Drift score and contract score
- Audit-ready JSON reports
- Metrics sink integration
- Pandas schema inference support
- Pure-Python baseline model with optional pandas adapter
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Set, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional dependency isolation
    pd = None  # type: ignore


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class SchemaDriftCheckerError(Exception):
    """Base exception for schema drift checker failures."""


class SchemaDriftConfigurationError(SchemaDriftCheckerError):
    """Raised when checker configuration is invalid."""


class SchemaDriftExecutionError(SchemaDriftCheckerError):
    """Raised when checker execution fails."""


class SchemaInferenceError(SchemaDriftCheckerError):
    """Raised when schema cannot be inferred from input."""


# =============================================================================
# Enums
# =============================================================================


class Severity(str, Enum):
    """Severity level for drift findings."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftStatus(str, Enum):
    """Overall schema drift status."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class DriftType(str, Enum):
    """Supported schema drift types."""

    COLUMN_ADDED = "column_added"
    COLUMN_REMOVED = "column_removed"
    COLUMN_RENAMED = "column_renamed"
    COLUMN_REORDERED = "column_reordered"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"
    DEFAULT_CHANGED = "default_changed"
    CONSTRAINT_ADDED = "constraint_added"
    CONSTRAINT_REMOVED = "constraint_removed"
    CONSTRAINT_CHANGED = "constraint_changed"
    PRIMARY_KEY_CHANGED = "primary_key_changed"
    UNIQUE_KEY_CHANGED = "unique_key_changed"
    PARTITION_KEY_CHANGED = "partition_key_changed"
    SEMANTIC_TYPE_CHANGED = "semantic_type_changed"
    COMMENT_CHANGED = "comment_changed"
    METADATA_CHANGED = "metadata_changed"


class Compatibility(str, Enum):
    """Compatibility impact classification."""

    BACKWARD_COMPATIBLE = "backward_compatible"
    FORWARD_COMPATIBLE = "forward_compatible"
    FULLY_COMPATIBLE = "fully_compatible"
    BREAKING = "breaking"
    UNKNOWN = "unknown"


class SchemaType(str, Enum):
    """Normalized schema type categories."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIMESTAMP = "timestamp"
    TIME = "time"
    BINARY = "binary"
    JSON = "json"
    ARRAY = "array"
    STRUCT = "struct"
    MAP = "map"
    OBJECT = "object"
    UNKNOWN = "unknown"


class SemanticType(str, Enum):
    """Optional semantic type metadata."""

    UNKNOWN = "unknown"
    IDENTIFIER = "identifier"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    CATEGORY = "category"
    FREE_TEXT = "free_text"
    TIMESTAMP = "timestamp"
    FLAG = "flag"


class Nullability(str, Enum):
    """Column nullability."""

    NULLABLE = "nullable"
    REQUIRED = "required"
    UNKNOWN = "unknown"


class ConstraintType(str, Enum):
    """Supported constraint types."""

    NOT_NULL = "not_null"
    UNIQUE = "unique"
    PRIMARY_KEY = "primary_key"
    FOREIGN_KEY = "foreign_key"
    CHECK = "check"
    REGEX = "regex"
    ENUM = "enum"
    MIN = "min"
    MAX = "max"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    PRECISION = "precision"
    SCALE = "scale"
    CUSTOM = "custom"


# =============================================================================
# Protocols
# =============================================================================


class MetricsSink(Protocol):
    """Optional sink for publishing checker metrics."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        ...


class AuditSink(Protocol):
    """Optional sink for audit events."""

    def write_event(self, event: Mapping[str, Any]) -> None:
        ...


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class SchemaConstraint:
    """Column or table-level schema constraint."""

    constraint_type: ConstraintType
    name: Optional[str] = None
    value: Optional[Any] = None
    values: Optional[Sequence[Any]] = None
    expression: Optional[str] = None
    reference: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = self.to_dict()
        return _stable_hash(payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "constraint_type": self.constraint_type.value,
            "name": self.name,
            "value": _json_safe(self.value),
            "values": _json_safe(list(self.values) if self.values is not None else None),
            "expression": self.expression,
            "reference": _json_safe(self.reference),
            "metadata": _json_safe(self.metadata),
        }


@dataclass(frozen=True)
class ColumnSchema:
    """Canonical column schema model."""

    name: str
    schema_type: SchemaType
    nullable: Nullability = Nullability.UNKNOWN
    ordinal_position: Optional[int] = None
    physical_type: Optional[str] = None
    semantic_type: SemanticType = SemanticType.UNKNOWN
    precision: Optional[int] = None
    scale: Optional[int] = None
    max_length: Optional[int] = None
    default: Optional[Any] = None
    comment: Optional[str] = None
    constraints: Sequence[SchemaConstraint] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.name.strip():
            raise SchemaDriftConfigurationError("Column name is required.")
        if self.precision is not None and self.precision < 0:
            raise SchemaDriftConfigurationError(f"Column '{self.name}' precision cannot be negative.")
        if self.scale is not None and self.scale < 0:
            raise SchemaDriftConfigurationError(f"Column '{self.name}' scale cannot be negative.")
        if self.max_length is not None and self.max_length < 0:
            raise SchemaDriftConfigurationError(f"Column '{self.name}' max_length cannot be negative.")

    def constraint_map(self) -> Dict[str, SchemaConstraint]:
        result: Dict[str, SchemaConstraint] = {}
        for constraint in self.constraints:
            key = constraint.name or constraint.constraint_type.value
            result[key] = constraint
        return result

    def fingerprint(self, *, include_position: bool = True, include_comment: bool = True) -> str:
        payload = self.to_dict()
        if not include_position:
            payload.pop("ordinal_position", None)
        if not include_comment:
            payload.pop("comment", None)
        return _stable_hash(payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_type": self.schema_type.value,
            "nullable": self.nullable.value,
            "ordinal_position": self.ordinal_position,
            "physical_type": self.physical_type,
            "semantic_type": self.semantic_type.value,
            "precision": self.precision,
            "scale": self.scale,
            "max_length": self.max_length,
            "default": _json_safe(self.default),
            "comment": self.comment,
            "constraints": [c.to_dict() for c in self.constraints],
            "metadata": _json_safe(self.metadata),
        }


@dataclass(frozen=True)
class DatasetSchema:
    """Canonical dataset schema model."""

    dataset_name: str
    columns: Sequence[ColumnSchema]
    version: Optional[str] = None
    primary_key: Sequence[str] = field(default_factory=list)
    unique_keys: Sequence[Sequence[str]] = field(default_factory=list)
    partition_keys: Sequence[str] = field(default_factory=list)
    table_constraints: Sequence[SchemaConstraint] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    def validate(self) -> None:
        if not self.dataset_name.strip():
            raise SchemaDriftConfigurationError("dataset_name is required.")
        names = [c.name for c in self.columns]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise SchemaDriftConfigurationError(f"Duplicate columns in schema '{self.dataset_name}': {duplicates}")
        for column in self.columns:
            column.validate()
        missing_pk = [col for col in self.primary_key if col not in names]
        if missing_pk:
            raise SchemaDriftConfigurationError(f"Primary key columns missing from schema: {missing_pk}")
        missing_partitions = [col for col in self.partition_keys if col not in names]
        if missing_partitions:
            raise SchemaDriftConfigurationError(f"Partition key columns missing from schema: {missing_partitions}")

    def column_map(self) -> Dict[str, ColumnSchema]:
        return {column.name: column for column in self.columns}

    def fingerprint(self, *, include_position: bool = True, include_comments: bool = True) -> str:
        payload = self.to_dict()
        if not include_position:
            for column in payload["columns"]:
                column.pop("ordinal_position", None)
        if not include_comments:
            for column in payload["columns"]:
                column.pop("comment", None)
        return _stable_hash(payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "version": self.version,
            "columns": [column.to_dict() for column in self.columns],
            "primary_key": list(self.primary_key),
            "unique_keys": [list(key) for key in self.unique_keys],
            "partition_keys": list(self.partition_keys),
            "table_constraints": [c.to_dict() for c in self.table_constraints],
            "metadata": _json_safe(self.metadata),
            "created_at": self.created_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(frozen=True)
class DriftPolicy:
    """Policy defining which drift types are tolerated and how they are scored."""

    allow_added_nullable_columns: bool = True
    allow_added_required_columns: bool = False
    allow_removed_columns: bool = False
    allow_type_widening: bool = True
    allow_type_narrowing: bool = False
    allow_nullable_to_required: bool = False
    allow_required_to_nullable: bool = True
    allow_column_reorder: bool = True
    allow_comment_changes: bool = True
    allow_metadata_changes: bool = True
    ignore_columns: Sequence[str] = field(default_factory=list)
    ignore_column_patterns: Sequence[str] = field(default_factory=list)
    compare_order: bool = True
    compare_comments: bool = False
    compare_metadata: bool = False
    compare_defaults: bool = True
    compare_constraints: bool = True
    compare_semantic_types: bool = True
    critical_columns: Sequence[str] = field(default_factory=list)

    def should_ignore_column(self, column_name: str) -> bool:
        if column_name in set(self.ignore_columns):
            return True
        for pattern in self.ignore_column_patterns:
            if re.fullmatch(pattern, column_name):
                return True
        return False


@dataclass(frozen=True)
class DriftThreshold:
    """Thresholds for schema drift score/status."""

    min_schema_score: float = 0.95
    warning_schema_score: float = 0.98
    max_breaking_changes: int = 0
    max_critical_findings: int = 0

    def validate(self) -> None:
        if not 0 <= self.min_schema_score <= 1:
            raise SchemaDriftConfigurationError("min_schema_score must be between 0 and 1.")
        if not 0 <= self.warning_schema_score <= 1:
            raise SchemaDriftConfigurationError("warning_schema_score must be between 0 and 1.")
        if self.warning_schema_score < self.min_schema_score:
            raise SchemaDriftConfigurationError("warning_schema_score must be greater than or equal to min_schema_score.")
        if self.max_breaking_changes < 0 or self.max_critical_findings < 0:
            raise SchemaDriftConfigurationError("max_breaking_changes and max_critical_findings cannot be negative.")


@dataclass(frozen=True)
class SchemaDriftConfig:
    """Schema drift checker configuration."""

    policy: DriftPolicy = field(default_factory=DriftPolicy)
    threshold: DriftThreshold = field(default_factory=DriftThreshold)
    max_findings: int = 1_000
    fail_on_inference_error: bool = True
    include_column_fingerprints: bool = True

    def validate(self) -> None:
        self.threshold.validate()
        if self.max_findings < 0:
            raise SchemaDriftConfigurationError("max_findings cannot be negative.")


@dataclass
class SchemaDriftFinding:
    """Single schema drift finding."""

    finding_id: str
    drift_type: DriftType
    severity: Severity
    compatibility: Compatibility
    message: str
    column_name: Optional[str] = None
    expected_value: Optional[Any] = None
    observed_value: Optional[Any] = None
    breaking: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["drift_type"] = self.drift_type.value
        data["severity"] = self.severity.value
        data["compatibility"] = self.compatibility.value
        return _json_safe(data)


@dataclass
class SchemaDriftReport:
    """Complete schema drift report."""

    report_id: str
    dataset_name: str
    status: DriftStatus
    started_at: str
    finished_at: str
    duration_ms: float
    schema_score: float
    compatibility: Compatibility
    expected_fingerprint: str
    observed_fingerprint: str
    expected_version: Optional[str]
    observed_version: Optional[str]
    total_expected_columns: int
    total_observed_columns: int
    added_columns: int
    removed_columns: int
    changed_columns: int
    reordered_columns: int
    breaking_changes: int
    critical_findings: int
    findings: List[SchemaDriftFinding]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_findings: bool = True) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "dataset_name": self.dataset_name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "schema_score": self.schema_score,
            "compatibility": self.compatibility.value,
            "expected_fingerprint": self.expected_fingerprint,
            "observed_fingerprint": self.observed_fingerprint,
            "expected_version": self.expected_version,
            "observed_version": self.observed_version,
            "total_expected_columns": self.total_expected_columns,
            "total_observed_columns": self.total_observed_columns,
            "added_columns": self.added_columns,
            "removed_columns": self.removed_columns,
            "changed_columns": self.changed_columns,
            "reordered_columns": self.reordered_columns,
            "breaking_changes": self.breaking_changes,
            "critical_findings": self.critical_findings,
            "metadata": _json_safe(self.metadata),
            "findings": [f.to_dict() for f in self.findings] if include_findings else [],
        }

    def to_json(self, *, include_findings: bool = True, indent: int = 2) -> str:
        return json.dumps(self.to_dict(include_findings=include_findings), indent=indent, ensure_ascii=False)

    def save_json(self, path: str | Path, *, include_findings: bool = True, indent: int = 2) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(include_findings=include_findings, indent=indent), encoding="utf-8")
        return output


# =============================================================================
# Helpers
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finding_id(*parts: Any) -> str:
    return "schema_drift_" + _stable_hash(parts)[:16]


def _normalize_type(value: Any) -> SchemaType:
    raw = str(value or "unknown").strip().lower()
    raw = raw.replace("nullable[", "").replace("]", "")
    if raw in {"str", "string", "object", "varchar", "char", "text", "category"}:
        return SchemaType.STRING
    if raw in {"int", "int8", "int16", "int32", "int64", "integer", "bigint", "smallint", "long"}:
        return SchemaType.INTEGER
    if raw in {"float", "float16", "float32", "float64", "double", "real"}:
        return SchemaType.FLOAT
    if raw in {"decimal", "numeric", "number"}:
        return SchemaType.DECIMAL
    if raw in {"bool", "boolean"}:
        return SchemaType.BOOLEAN
    if raw in {"date"}:
        return SchemaType.DATE
    if raw in {"datetime", "datetime64", "datetime64[ns]", "timestamp", "timestamp_ntz", "timestamp_ltz"} or raw.startswith("datetime64"):
        return SchemaType.TIMESTAMP
    if raw in {"time"}:
        return SchemaType.TIME
    if raw in {"bytes", "binary", "bytearray"}:
        return SchemaType.BINARY
    if raw in {"json", "jsonb"}:
        return SchemaType.JSON
    if raw in {"array", "list"}:
        return SchemaType.ARRAY
    if raw in {"struct", "record"}:
        return SchemaType.STRUCT
    if raw in {"map", "dict"}:
        return SchemaType.MAP
    return SchemaType.UNKNOWN


def _is_widening(expected: SchemaType, observed: SchemaType) -> bool:
    if expected == observed:
        return True
    widening = {
        SchemaType.INTEGER: {SchemaType.FLOAT, SchemaType.DECIMAL, SchemaType.STRING},
        SchemaType.FLOAT: {SchemaType.DECIMAL, SchemaType.STRING},
        SchemaType.DECIMAL: {SchemaType.STRING},
        SchemaType.BOOLEAN: {SchemaType.STRING},
        SchemaType.DATE: {SchemaType.DATETIME, SchemaType.TIMESTAMP, SchemaType.STRING},
        SchemaType.DATETIME: {SchemaType.TIMESTAMP, SchemaType.STRING},
        SchemaType.TIMESTAMP: {SchemaType.STRING},
    }
    return observed in widening.get(expected, set())


def _is_narrowing(expected: SchemaType, observed: SchemaType) -> bool:
    return _is_widening(observed, expected) and expected != observed


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.HIGH: 3,
        Severity.CRITICAL: 4,
    }[severity]


# =============================================================================
# Sinks
# =============================================================================


class NoopMetricsSink:
    """Default metrics sink that intentionally does nothing."""

    def increment(self, metric_name: str, value: int = 1, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def gauge(self, metric_name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None

    def timing(self, metric_name: str, value_ms: float, tags: Optional[Dict[str, str]] = None) -> None:
        return None


class InMemoryAuditSink:
    """Simple audit sink useful for tests and local execution."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def write_event(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


# =============================================================================
# Schema Inference / Serialization
# =============================================================================


def schema_constraint_from_dict(payload: Mapping[str, Any]) -> SchemaConstraint:
    return SchemaConstraint(
        constraint_type=ConstraintType(str(payload.get("constraint_type", ConstraintType.CUSTOM.value))),
        name=payload.get("name"),
        value=payload.get("value"),
        values=payload.get("values"),
        expression=payload.get("expression"),
        reference=payload.get("reference"),
        metadata=dict(payload.get("metadata") or {}),
    )


def column_schema_from_dict(payload: Mapping[str, Any]) -> ColumnSchema:
    return ColumnSchema(
        name=str(payload.get("name")),
        schema_type=_normalize_type(payload.get("schema_type") or payload.get("type") or payload.get("physical_type")),
        nullable=Nullability(str(payload.get("nullable", Nullability.UNKNOWN.value))),
        ordinal_position=payload.get("ordinal_position"),
        physical_type=payload.get("physical_type"),
        semantic_type=SemanticType(str(payload.get("semantic_type", SemanticType.UNKNOWN.value))),
        precision=payload.get("precision"),
        scale=payload.get("scale"),
        max_length=payload.get("max_length"),
        default=payload.get("default"),
        comment=payload.get("comment"),
        constraints=[schema_constraint_from_dict(c) for c in payload.get("constraints", [])],
        metadata=dict(payload.get("metadata") or {}),
    )


def dataset_schema_from_dict(payload: Mapping[str, Any]) -> DatasetSchema:
    schema = DatasetSchema(
        dataset_name=str(payload.get("dataset_name") or payload.get("name") or "dataset"),
        version=payload.get("version"),
        columns=[column_schema_from_dict(c) for c in payload.get("columns", [])],
        primary_key=list(payload.get("primary_key") or []),
        unique_keys=[list(key) for key in payload.get("unique_keys", [])],
        partition_keys=list(payload.get("partition_keys") or []),
        table_constraints=[schema_constraint_from_dict(c) for c in payload.get("table_constraints", [])],
        metadata=dict(payload.get("metadata") or {}),
        created_at=payload.get("created_at"),
    )
    schema.validate()
    return schema


def infer_schema_from_pandas(df: Any, *, dataset_name: str = "dataset", version: Optional[str] = None) -> DatasetSchema:
    """Infer DatasetSchema from pandas DataFrame."""
    if pd is None:
        raise SchemaInferenceError("pandas is required to infer schema from a DataFrame.")
    if not isinstance(df, pd.DataFrame):
        raise SchemaInferenceError(f"Expected pandas DataFrame, got: {type(df)!r}")

    columns: List[ColumnSchema] = []
    for idx, column_name in enumerate(df.columns):
        series = df[column_name]
        physical_type = str(series.dtype)
        schema_type = _normalize_type(physical_type)
        nullable = Nullability.NULLABLE if bool(series.isna().any()) else Nullability.REQUIRED
        max_length = None
        if schema_type == SchemaType.STRING:
            try:
                max_length = int(series.dropna().astype(str).str.len().max()) if len(series.dropna()) else None
            except Exception:
                max_length = None

        precision = None
        scale = None
        if schema_type in {SchemaType.FLOAT, SchemaType.DECIMAL}:
            precision, scale = _infer_numeric_precision_scale(series)

        columns.append(
            ColumnSchema(
                name=str(column_name),
                schema_type=schema_type,
                nullable=nullable,
                ordinal_position=idx,
                physical_type=physical_type,
                semantic_type=_infer_semantic_type(str(column_name), series, schema_type),
                precision=precision,
                scale=scale,
                max_length=max_length,
                metadata={"non_null_count": int(series.notna().sum()), "null_count": int(series.isna().sum())},
            )
        )

    schema = DatasetSchema(
        dataset_name=dataset_name,
        version=version,
        columns=columns,
        created_at=utc_now_iso(),
        metadata={"inferred_from": "pandas", "row_count": int(len(df))},
    )
    schema.validate()
    return schema


def _infer_numeric_precision_scale(series: Any) -> Tuple[Optional[int], Optional[int]]:
    try:
        values = series.dropna().head(1000).tolist()
        max_precision = 0
        max_scale = 0
        for value in values:
            text = str(value)
            if "e" in text.lower():
                continue
            if "." in text:
                left, right = text.split(".", 1)
                max_precision = max(max_precision, len(left.replace("-", "")) + len(right.rstrip("0")))
                max_scale = max(max_scale, len(right.rstrip("0")))
            else:
                max_precision = max(max_precision, len(text.replace("-", "")))
        return (max_precision or None, max_scale or None)
    except Exception:
        return None, None


def _infer_semantic_type(column_name: str, series: Any, schema_type: SchemaType) -> SemanticType:
    name = column_name.lower()
    if schema_type in {SchemaType.TIMESTAMP, SchemaType.DATETIME, SchemaType.DATE}:
        return SemanticType.TIMESTAMP
    if schema_type == SchemaType.BOOLEAN:
        return SemanticType.FLAG
    if any(token in name for token in ["email", "e_mail"]):
        return SemanticType.EMAIL
    if any(token in name for token in ["phone", "telefone", "celular"]):
        return SemanticType.PHONE
    if any(token in name for token in ["url", "uri", "link"]):
        return SemanticType.URL
    if any(token in name for token in ["id", "uuid", "key", "codigo", "code"]):
        return SemanticType.IDENTIFIER
    if any(token in name for token in ["amount", "price", "valor", "total", "cost", "revenue"]):
        return SemanticType.CURRENCY
    if any(token in name for token in ["percent", "pct", "rate", "ratio"]):
        return SemanticType.PERCENTAGE
    if schema_type == SchemaType.STRING:
        try:
            nunique = int(series.dropna().nunique())
            if nunique <= 50:
                return SemanticType.CATEGORY
            return SemanticType.FREE_TEXT
        except Exception:
            return SemanticType.UNKNOWN
    return SemanticType.UNKNOWN


# =============================================================================
# Checker
# =============================================================================


class SchemaDriftChecker:
    """Enterprise schema drift checker."""

    def __init__(
        self,
        config: Optional[SchemaDriftConfig] = None,
        *,
        metrics_sink: Optional[MetricsSink] = None,
        audit_sink: Optional[AuditSink] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or SchemaDriftConfig()
        self.config.validate()
        self.metrics_sink = metrics_sink or NoopMetricsSink()
        self.audit_sink = audit_sink
        self.logger = logger_ or logger

    def check(
        self,
        expected_schema: DatasetSchema | Mapping[str, Any],
        observed_schema_or_dataset: DatasetSchema | Mapping[str, Any] | Any,
        *,
        dataset_name: Optional[str] = None,
        observed_version: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SchemaDriftReport:
        """Compare expected schema with observed schema or dataset."""
        started = time.perf_counter()
        started_at = utc_now_iso()
        report_id = str(uuid.uuid4())
        metadata = dict(metadata or {})

        try:
            expected = self._coerce_schema(expected_schema, dataset_name=dataset_name)
            observed = self._coerce_schema(
                observed_schema_or_dataset,
                dataset_name=dataset_name or expected.dataset_name,
                version=observed_version,
            )
            expected.validate()
            observed.validate()

            findings = self._compare(expected, observed)
            findings = sorted(findings, key=lambda f: (_severity_rank(f.severity), f.drift_type.value, f.column_name or ""), reverse=True)
            if self.config.max_findings:
                findings = findings[: self.config.max_findings]

            schema_score = self._score(expected, observed, findings)
            compatibility = self._compatibility(findings)
            status = self._status(schema_score, findings)
            finished_at = utc_now_iso()
            duration_ms = (time.perf_counter() - started) * 1000

            report = SchemaDriftReport(
                report_id=report_id,
                dataset_name=dataset_name or expected.dataset_name,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                schema_score=schema_score,
                compatibility=compatibility,
                expected_fingerprint=expected.fingerprint(
                    include_position=self.config.policy.compare_order,
                    include_comments=self.config.policy.compare_comments,
                ),
                observed_fingerprint=observed.fingerprint(
                    include_position=self.config.policy.compare_order,
                    include_comments=self.config.policy.compare_comments,
                ),
                expected_version=expected.version,
                observed_version=observed.version,
                total_expected_columns=len(expected.columns),
                total_observed_columns=len(observed.columns),
                added_columns=sum(1 for f in findings if f.drift_type == DriftType.COLUMN_ADDED),
                removed_columns=sum(1 for f in findings if f.drift_type == DriftType.COLUMN_REMOVED),
                changed_columns=sum(1 for f in findings if f.drift_type in {DriftType.TYPE_CHANGED, DriftType.NULLABILITY_CHANGED, DriftType.CONSTRAINT_CHANGED, DriftType.SEMANTIC_TYPE_CHANGED, DriftType.DEFAULT_CHANGED}),
                reordered_columns=sum(1 for f in findings if f.drift_type == DriftType.COLUMN_REORDERED),
                breaking_changes=sum(1 for f in findings if f.breaking),
                critical_findings=sum(1 for f in findings if f.severity == Severity.CRITICAL),
                findings=findings,
                metadata={
                    **metadata,
                    "expected_column_fingerprints": self._column_fingerprints(expected) if self.config.include_column_fingerprints else {},
                    "observed_column_fingerprints": self._column_fingerprints(observed) if self.config.include_column_fingerprints else {},
                },
            )
            self._publish_metrics(report)
            self._write_audit(report)
            return report

        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Schema drift check failed")
            self.metrics_sink.increment("data_quality.schema_drift.error")
            if self.config.fail_on_inference_error:
                raise SchemaDriftExecutionError(str(exc)) from exc
            finished_at = utc_now_iso()
            return SchemaDriftReport(
                report_id=report_id,
                dataset_name=dataset_name or "dataset",
                status=DriftStatus.ERROR,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=(time.perf_counter() - started) * 1000,
                schema_score=0.0,
                compatibility=Compatibility.UNKNOWN,
                expected_fingerprint="",
                observed_fingerprint="",
                expected_version=None,
                observed_version=None,
                total_expected_columns=0,
                total_observed_columns=0,
                added_columns=0,
                removed_columns=0,
                changed_columns=0,
                reordered_columns=0,
                breaking_changes=1,
                critical_findings=1,
                findings=[
                    SchemaDriftFinding(
                        finding_id=_finding_id("error", str(exc)),
                        drift_type=DriftType.METADATA_CHANGED,
                        severity=Severity.CRITICAL,
                        compatibility=Compatibility.UNKNOWN,
                        message=f"Schema drift execution failed: {exc}",
                        breaking=True,
                        metadata={"exception_type": type(exc).__name__},
                    )
                ],
                metadata=metadata,
            )

    def _coerce_schema(
        self,
        value: DatasetSchema | Mapping[str, Any] | Any,
        *,
        dataset_name: Optional[str] = None,
        version: Optional[str] = None,
    ) -> DatasetSchema:
        if isinstance(value, DatasetSchema):
            return value
        if isinstance(value, Mapping) and "columns" in value:
            return dataset_schema_from_dict(value)
        if pd is not None and isinstance(value, pd.DataFrame):
            return infer_schema_from_pandas(value, dataset_name=dataset_name or "dataset", version=version)
        raise SchemaInferenceError(f"Unsupported schema input type: {type(value)!r}")

    def _compare(self, expected: DatasetSchema, observed: DatasetSchema) -> List[SchemaDriftFinding]:
        findings: List[SchemaDriftFinding] = []
        policy = self.config.policy
        expected_columns = {k: v for k, v in expected.column_map().items() if not policy.should_ignore_column(k)}
        observed_columns = {k: v for k, v in observed.column_map().items() if not policy.should_ignore_column(k)}

        expected_names = set(expected_columns)
        observed_names = set(observed_columns)

        for column_name in sorted(observed_names - expected_names):
            findings.append(self._added_column_finding(observed_columns[column_name]))

        for column_name in sorted(expected_names - observed_names):
            findings.append(self._removed_column_finding(expected_columns[column_name]))

        for column_name in sorted(expected_names & observed_names):
            findings.extend(self._compare_column(expected_columns[column_name], observed_columns[column_name]))

        if policy.compare_order:
            findings.extend(self._compare_order(expected, observed))

        findings.extend(self._compare_key_set("primary_key", DriftType.PRIMARY_KEY_CHANGED, expected.primary_key, observed.primary_key))
        findings.extend(self._compare_nested_key_set("unique_keys", DriftType.UNIQUE_KEY_CHANGED, expected.unique_keys, observed.unique_keys))
        findings.extend(self._compare_key_set("partition_keys", DriftType.PARTITION_KEY_CHANGED, expected.partition_keys, observed.partition_keys))

        return findings

    def _added_column_finding(self, column: ColumnSchema) -> SchemaDriftFinding:
        policy = self.config.policy
        required = column.nullable == Nullability.REQUIRED
        allowed = policy.allow_added_required_columns if required else policy.allow_added_nullable_columns
        severity = Severity.CRITICAL if required and not allowed else (Severity.LOW if allowed else Severity.HIGH)
        breaking = not allowed
        return SchemaDriftFinding(
            finding_id=_finding_id("added", column.name),
            drift_type=DriftType.COLUMN_ADDED,
            severity=severity,
            compatibility=Compatibility.BREAKING if breaking else Compatibility.BACKWARD_COMPATIBLE,
            message=f"Observed schema contains new column '{column.name}'.",
            column_name=column.name,
            expected_value=None,
            observed_value=column.to_dict(),
            breaking=breaking,
            metadata={"nullable": column.nullable.value},
        )

    def _removed_column_finding(self, column: ColumnSchema) -> SchemaDriftFinding:
        allowed = self.config.policy.allow_removed_columns
        critical = column.name in set(self.config.policy.critical_columns)
        return SchemaDriftFinding(
            finding_id=_finding_id("removed", column.name),
            drift_type=DriftType.COLUMN_REMOVED,
            severity=Severity.CRITICAL if critical or not allowed else Severity.MEDIUM,
            compatibility=Compatibility.BREAKING if not allowed else Compatibility.FORWARD_COMPATIBLE,
            message=f"Expected column '{column.name}' is missing from observed schema.",
            column_name=column.name,
            expected_value=column.to_dict(),
            observed_value=None,
            breaking=not allowed,
            metadata={"critical_column": critical},
        )

    def _compare_column(self, expected: ColumnSchema, observed: ColumnSchema) -> List[SchemaDriftFinding]:
        findings: List[SchemaDriftFinding] = []
        policy = self.config.policy

        if expected.schema_type != observed.schema_type:
            findings.append(self._type_change_finding(expected, observed))

        if expected.nullable != observed.nullable:
            findings.append(self._nullability_finding(expected, observed))

        if policy.compare_defaults and expected.default != observed.default:
            findings.append(
                self._simple_change_finding(
                    DriftType.DEFAULT_CHANGED,
                    expected.name,
                    "Column default value changed.",
                    expected.default,
                    observed.default,
                    Severity.MEDIUM,
                    Compatibility.UNKNOWN,
                    breaking=False,
                )
            )

        if policy.compare_semantic_types and expected.semantic_type != observed.semantic_type:
            findings.append(
                self._simple_change_finding(
                    DriftType.SEMANTIC_TYPE_CHANGED,
                    expected.name,
                    "Column semantic type changed.",
                    expected.semantic_type.value,
                    observed.semantic_type.value,
                    Severity.LOW,
                    Compatibility.UNKNOWN,
                    breaking=False,
                )
            )

        if policy.compare_comments and expected.comment != observed.comment:
            findings.append(
                self._simple_change_finding(
                    DriftType.COMMENT_CHANGED,
                    expected.name,
                    "Column comment changed.",
                    expected.comment,
                    observed.comment,
                    Severity.INFO,
                    Compatibility.FULLY_COMPATIBLE,
                    breaking=False,
                )
            )

        if policy.compare_metadata and expected.metadata != observed.metadata:
            findings.append(
                self._simple_change_finding(
                    DriftType.METADATA_CHANGED,
                    expected.name,
                    "Column metadata changed.",
                    expected.metadata,
                    observed.metadata,
                    Severity.INFO,
                    Compatibility.UNKNOWN,
                    breaking=False,
                )
            )

        if policy.compare_constraints:
            findings.extend(self._compare_constraints(expected, observed))

        return findings

    def _type_change_finding(self, expected: ColumnSchema, observed: ColumnSchema) -> SchemaDriftFinding:
        policy = self.config.policy
        widening = _is_widening(expected.schema_type, observed.schema_type)
        narrowing = _is_narrowing(expected.schema_type, observed.schema_type)
        if widening and policy.allow_type_widening:
            severity = Severity.MEDIUM
            compatibility = Compatibility.BACKWARD_COMPATIBLE
            breaking = False
        elif narrowing and policy.allow_type_narrowing:
            severity = Severity.MEDIUM
            compatibility = Compatibility.FORWARD_COMPATIBLE
            breaking = False
        else:
            severity = Severity.CRITICAL if expected.name in set(policy.critical_columns) else Severity.HIGH
            compatibility = Compatibility.BREAKING
            breaking = True
        return SchemaDriftFinding(
            finding_id=_finding_id("type", expected.name, expected.schema_type.value, observed.schema_type.value),
            drift_type=DriftType.TYPE_CHANGED,
            severity=severity,
            compatibility=compatibility,
            message=f"Column '{expected.name}' type changed from {expected.schema_type.value} to {observed.schema_type.value}.",
            column_name=expected.name,
            expected_value=expected.schema_type.value,
            observed_value=observed.schema_type.value,
            breaking=breaking,
            metadata={"expected_physical_type": expected.physical_type, "observed_physical_type": observed.physical_type},
        )

    def _nullability_finding(self, expected: ColumnSchema, observed: ColumnSchema) -> SchemaDriftFinding:
        policy = self.config.policy
        nullable_to_required = expected.nullable == Nullability.NULLABLE and observed.nullable == Nullability.REQUIRED
        required_to_nullable = expected.nullable == Nullability.REQUIRED and observed.nullable == Nullability.NULLABLE
        if nullable_to_required:
            allowed = policy.allow_nullable_to_required
        elif required_to_nullable:
            allowed = policy.allow_required_to_nullable
        else:
            allowed = True
        critical = expected.name in set(policy.critical_columns)
        return SchemaDriftFinding(
            finding_id=_finding_id("nullability", expected.name, expected.nullable.value, observed.nullable.value),
            drift_type=DriftType.NULLABILITY_CHANGED,
            severity=Severity.CRITICAL if critical and not allowed else (Severity.HIGH if not allowed else Severity.MEDIUM),
            compatibility=Compatibility.BREAKING if not allowed else Compatibility.BACKWARD_COMPATIBLE,
            message=f"Column '{expected.name}' nullability changed from {expected.nullable.value} to {observed.nullable.value}.",
            column_name=expected.name,
            expected_value=expected.nullable.value,
            observed_value=observed.nullable.value,
            breaking=not allowed,
            metadata={"critical_column": critical},
        )

    def _simple_change_finding(
        self,
        drift_type: DriftType,
        column_name: str,
        message: str,
        expected_value: Any,
        observed_value: Any,
        severity: Severity,
        compatibility: Compatibility,
        *,
        breaking: bool,
    ) -> SchemaDriftFinding:
        return SchemaDriftFinding(
            finding_id=_finding_id(drift_type.value, column_name, expected_value, observed_value),
            drift_type=drift_type,
            severity=severity,
            compatibility=compatibility,
            message=f"{message} Column: '{column_name}'.",
            column_name=column_name,
            expected_value=expected_value,
            observed_value=observed_value,
            breaking=breaking,
        )

    def _compare_constraints(self, expected: ColumnSchema, observed: ColumnSchema) -> List[SchemaDriftFinding]:
        findings: List[SchemaDriftFinding] = []
        expected_constraints = expected.constraint_map()
        observed_constraints = observed.constraint_map()
        expected_keys = set(expected_constraints)
        observed_keys = set(observed_constraints)

        for key in sorted(observed_keys - expected_keys):
            findings.append(
                SchemaDriftFinding(
                    finding_id=_finding_id("constraint_added", expected.name, key),
                    drift_type=DriftType.CONSTRAINT_ADDED,
                    severity=Severity.MEDIUM,
                    compatibility=Compatibility.UNKNOWN,
                    message=f"Constraint '{key}' was added to column '{expected.name}'.",
                    column_name=expected.name,
                    observed_value=observed_constraints[key].to_dict(),
                    breaking=False,
                )
            )
        for key in sorted(expected_keys - observed_keys):
            findings.append(
                SchemaDriftFinding(
                    finding_id=_finding_id("constraint_removed", expected.name, key),
                    drift_type=DriftType.CONSTRAINT_REMOVED,
                    severity=Severity.HIGH,
                    compatibility=Compatibility.BREAKING,
                    message=f"Constraint '{key}' was removed from column '{expected.name}'.",
                    column_name=expected.name,
                    expected_value=expected_constraints[key].to_dict(),
                    breaking=True,
                )
            )
        for key in sorted(expected_keys & observed_keys):
            if expected_constraints[key].fingerprint() != observed_constraints[key].fingerprint():
                findings.append(
                    SchemaDriftFinding(
                        finding_id=_finding_id("constraint_changed", expected.name, key),
                        drift_type=DriftType.CONSTRAINT_CHANGED,
                        severity=Severity.HIGH,
                        compatibility=Compatibility.UNKNOWN,
                        message=f"Constraint '{key}' changed on column '{expected.name}'.",
                        column_name=expected.name,
                        expected_value=expected_constraints[key].to_dict(),
                        observed_value=observed_constraints[key].to_dict(),
                        breaking=True,
                    )
                )
        return findings

    def _compare_order(self, expected: DatasetSchema, observed: DatasetSchema) -> List[SchemaDriftFinding]:
        if self.config.policy.allow_column_reorder:
            severity = Severity.INFO
            compatibility = Compatibility.FULLY_COMPATIBLE
            breaking = False
        else:
            severity = Severity.MEDIUM
            compatibility = Compatibility.UNKNOWN
            breaking = True
        findings: List[SchemaDriftFinding] = []
        observed_positions = {c.name: c.ordinal_position for c in observed.columns}
        for expected_col in expected.columns:
            if self.config.policy.should_ignore_column(expected_col.name):
                continue
            if expected_col.name not in observed_positions:
                continue
            if expected_col.ordinal_position is not None and observed_positions[expected_col.name] is not None:
                if expected_col.ordinal_position != observed_positions[expected_col.name]:
                    findings.append(
                        SchemaDriftFinding(
                            finding_id=_finding_id("reordered", expected_col.name, expected_col.ordinal_position, observed_positions[expected_col.name]),
                            drift_type=DriftType.COLUMN_REORDERED,
                            severity=severity,
                            compatibility=compatibility,
                            message=f"Column '{expected_col.name}' position changed.",
                            column_name=expected_col.name,
                            expected_value=expected_col.ordinal_position,
                            observed_value=observed_positions[expected_col.name],
                            breaking=breaking,
                        )
                    )
        return findings

    def _compare_key_set(self, name: str, drift_type: DriftType, expected: Sequence[str], observed: Sequence[str]) -> List[SchemaDriftFinding]:
        if list(expected) == list(observed):
            return []
        return [
            SchemaDriftFinding(
                finding_id=_finding_id(name, expected, observed),
                drift_type=drift_type,
                severity=Severity.CRITICAL,
                compatibility=Compatibility.BREAKING,
                message=f"{name} changed.",
                expected_value=list(expected),
                observed_value=list(observed),
                breaking=True,
            )
        ]

    def _compare_nested_key_set(self, name: str, drift_type: DriftType, expected: Sequence[Sequence[str]], observed: Sequence[Sequence[str]]) -> List[SchemaDriftFinding]:
        expected_norm = sorted([tuple(key) for key in expected])
        observed_norm = sorted([tuple(key) for key in observed])
        if expected_norm == observed_norm:
            return []
        return [
            SchemaDriftFinding(
                finding_id=_finding_id(name, expected_norm, observed_norm),
                drift_type=drift_type,
                severity=Severity.HIGH,
                compatibility=Compatibility.BREAKING,
                message=f"{name} changed.",
                expected_value=[list(key) for key in expected_norm],
                observed_value=[list(key) for key in observed_norm],
                breaking=True,
            )
        ]

    def _score(self, expected: DatasetSchema, observed: DatasetSchema, findings: Sequence[SchemaDriftFinding]) -> float:
        expected_count = max(1, len(expected.columns))
        weights = {
            DriftType.COLUMN_REMOVED: 0.20,
            DriftType.COLUMN_ADDED: 0.05,
            DriftType.TYPE_CHANGED: 0.18,
            DriftType.NULLABILITY_CHANGED: 0.10,
            DriftType.CONSTRAINT_REMOVED: 0.12,
            DriftType.CONSTRAINT_CHANGED: 0.10,
            DriftType.PRIMARY_KEY_CHANGED: 0.25,
            DriftType.UNIQUE_KEY_CHANGED: 0.15,
            DriftType.PARTITION_KEY_CHANGED: 0.15,
            DriftType.COLUMN_REORDERED: 0.02,
            DriftType.DEFAULT_CHANGED: 0.03,
            DriftType.SEMANTIC_TYPE_CHANGED: 0.03,
            DriftType.COMMENT_CHANGED: 0.00,
            DriftType.METADATA_CHANGED: 0.01,
            DriftType.CONSTRAINT_ADDED: 0.03,
            DriftType.COLUMN_RENAMED: 0.15,
        }
        severity_multiplier = {
            Severity.INFO: 0.25,
            Severity.LOW: 0.50,
            Severity.MEDIUM: 1.00,
            Severity.HIGH: 1.50,
            Severity.CRITICAL: 2.00,
        }
        penalty = 0.0
        for finding in findings:
            base = weights.get(finding.drift_type, 0.05)
            penalty += (base * severity_multiplier[finding.severity]) / expected_count
            if finding.breaking:
                penalty += 0.02
        return round(max(0.0, min(1.0, 1.0 - penalty)), 8)

    def _compatibility(self, findings: Sequence[SchemaDriftFinding]) -> Compatibility:
        if any(f.compatibility == Compatibility.BREAKING or f.breaking for f in findings):
            return Compatibility.BREAKING
        if not findings:
            return Compatibility.FULLY_COMPATIBLE
        if all(f.compatibility in {Compatibility.BACKWARD_COMPATIBLE, Compatibility.FULLY_COMPATIBLE} for f in findings):
            return Compatibility.BACKWARD_COMPATIBLE
        return Compatibility.UNKNOWN

    def _status(self, score: float, findings: Sequence[SchemaDriftFinding]) -> DriftStatus:
        breaking_changes = sum(1 for f in findings if f.breaking)
        critical_findings = sum(1 for f in findings if f.severity == Severity.CRITICAL)
        threshold = self.config.threshold
        if breaking_changes > threshold.max_breaking_changes or critical_findings > threshold.max_critical_findings:
            return DriftStatus.FAILED
        if score < threshold.min_schema_score:
            return DriftStatus.FAILED
        if score < threshold.warning_schema_score or findings:
            return DriftStatus.WARNING
        return DriftStatus.PASSED

    def _column_fingerprints(self, schema: DatasetSchema) -> Dict[str, str]:
        return {
            column.name: column.fingerprint(
                include_position=self.config.policy.compare_order,
                include_comment=self.config.policy.compare_comments,
            )
            for column in schema.columns
        }

    def _publish_metrics(self, report: SchemaDriftReport) -> None:
        tags = {"dataset": report.dataset_name, "status": report.status.value, "compatibility": report.compatibility.value}
        self.metrics_sink.gauge("data_quality.schema_drift.score", report.schema_score, tags=tags)
        self.metrics_sink.gauge("data_quality.schema_drift.findings", len(report.findings), tags=tags)
        self.metrics_sink.gauge("data_quality.schema_drift.breaking_changes", report.breaking_changes, tags=tags)
        self.metrics_sink.gauge("data_quality.schema_drift.added_columns", report.added_columns, tags=tags)
        self.metrics_sink.gauge("data_quality.schema_drift.removed_columns", report.removed_columns, tags=tags)
        self.metrics_sink.timing("data_quality.schema_drift.duration_ms", report.duration_ms, tags=tags)
        self.metrics_sink.increment("data_quality.schema_drift.completed", tags=tags)

    def _write_audit(self, report: SchemaDriftReport) -> None:
        if not self.audit_sink:
            return
        self.audit_sink.write_event(
            {
                "event_type": "schema_drift_check_completed",
                "report_id": report.report_id,
                "dataset_name": report.dataset_name,
                "timestamp": utc_now_iso(),
                "status": report.status.value,
                "schema_score": report.schema_score,
                "compatibility": report.compatibility.value,
                "finding_count": len(report.findings),
                "breaking_changes": report.breaking_changes,
            }
        )


# =============================================================================
# Convenience API
# =============================================================================


def check_schema_drift(
    expected_schema: DatasetSchema | Mapping[str, Any],
    observed_schema_or_dataset: DatasetSchema | Mapping[str, Any] | Any,
    *,
    dataset_name: Optional[str] = None,
    config: Optional[SchemaDriftConfig] = None,
) -> SchemaDriftReport:
    """Convenience function for one-shot schema drift checking."""
    return SchemaDriftChecker(config).check(expected_schema, observed_schema_or_dataset, dataset_name=dataset_name)


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    expected = DatasetSchema(
        dataset_name="customers",
        version="1.0.0",
        columns=[
            ColumnSchema("customer_id", SchemaType.INTEGER, Nullability.REQUIRED, 0, semantic_type=SemanticType.IDENTIFIER),
            ColumnSchema("name", SchemaType.STRING, Nullability.REQUIRED, 1, max_length=120),
            ColumnSchema("email", SchemaType.STRING, Nullability.NULLABLE, 2, semantic_type=SemanticType.EMAIL),
            ColumnSchema("created_at", SchemaType.TIMESTAMP, Nullability.REQUIRED, 3, semantic_type=SemanticType.TIMESTAMP),
        ],
        primary_key=["customer_id"],
    )

    observed = DatasetSchema(
        dataset_name="customers",
        version="1.1.0",
        columns=[
            ColumnSchema("customer_id", SchemaType.STRING, Nullability.REQUIRED, 0, semantic_type=SemanticType.IDENTIFIER),
            ColumnSchema("name", SchemaType.STRING, Nullability.NULLABLE, 1, max_length=200),
            ColumnSchema("created_at", SchemaType.TIMESTAMP, Nullability.REQUIRED, 2, semantic_type=SemanticType.TIMESTAMP),
            ColumnSchema("phone", SchemaType.STRING, Nullability.NULLABLE, 3, semantic_type=SemanticType.PHONE),
        ],
        primary_key=["customer_id"],
    )

    checker = SchemaDriftChecker(
        SchemaDriftConfig(
            policy=DriftPolicy(
                critical_columns=["customer_id"],
                allow_added_nullable_columns=True,
                allow_type_widening=False,
            )
        ),
        audit_sink=InMemoryAuditSink(),
    )
    report = checker.check(expected, observed)
    print(report.to_json())
