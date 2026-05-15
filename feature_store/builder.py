"""
kwanza-ai-core/feature_store/builder.py

Enterprise-grade Feature Store Builder.

Purpose
-------
Build, validate, register and materialize ML features for Kwanza AI Core across
batch and online serving layers.

Capabilities
------------
- Feature definitions with schemas, owners, freshness SLAs and lineage.
- Entity keys and point-in-time safe feature generation.
- Batch and online materialization plans.
- Aggregation helpers for transactional/event data.
- Data quality validation and drift-friendly profiling.
- Backfill orchestration with checkpointing/idempotency.
- Pluggable repositories, offline stores and online stores.
- Metrics, audit hooks and safe structured errors.

This module is framework-agnostic. Production adapters can connect it to
PostgreSQL, Supabase, DuckDB, Spark, BigQuery, Snowflake, Redis, Feast, S3,
Kafka, dbt, Airflow or Prefect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
FeatureRow = Mapping[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class FeatureStoreError(RuntimeError):
    """Base exception for feature store builder failures."""


class FeatureValidationError(FeatureStoreError):
    """Raised when feature definitions or data are invalid."""


class FeatureMaterializationError(FeatureStoreError):
    """Raised when feature materialization fails."""


class FeatureConflictError(FeatureStoreError):
    """Raised when feature registration or versioning conflicts occur."""


# =============================================================================
# Enums and data models
# =============================================================================


class FeatureValueType(str, Enum):
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    DATE = "date"
    JSON = "json"
    VECTOR = "vector"


class FeatureStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class MaterializationMode(str, Enum):
    OFFLINE = "offline"
    ONLINE = "online"
    BOTH = "both"


class AggregationFunction(str, Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    STDDEV = "stddev"
    DISTINCT_COUNT = "distinct_count"
    LAST = "last"
    FIRST = "first"


class TimeWindow(str, Enum):
    HOUR_1 = "1h"
    HOUR_6 = "6h"
    DAY_1 = "1d"
    DAY_7 = "7d"
    DAY_14 = "14d"
    DAY_30 = "30d"
    DAY_90 = "90d"
    ALL_TIME = "all_time"


class QualitySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class EntityKey:
    name: str
    value_type: FeatureValueType = FeatureValueType.STRING
    description: Optional[str] = None


@dataclass(frozen=True)
class FeatureSchema:
    name: str
    value_type: FeatureValueType
    nullable: bool = True
    default: Any = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    allowed_values: Optional[Sequence[Any]] = None
    max_length: Optional[int] = None
    vector_dim: Optional[int] = None
    description: Optional[str] = None

    def validate_value(self, value: Any) -> Optional[str]:
        if value is None:
            if self.nullable:
                return None
            return f"Feature '{self.name}' is not nullable."

        if self.value_type == FeatureValueType.INT and not isinstance(value, int):
            return f"Feature '{self.name}' must be int."
        if self.value_type == FeatureValueType.FLOAT and not isinstance(value, (int, float, Decimal)):
            return f"Feature '{self.name}' must be float."
        if self.value_type == FeatureValueType.STRING and not isinstance(value, str):
            return f"Feature '{self.name}' must be string."
        if self.value_type == FeatureValueType.BOOL and not isinstance(value, bool):
            return f"Feature '{self.name}' must be bool."
        if self.value_type == FeatureValueType.JSON and not isinstance(value, (dict, list, str, int, float, bool)):
            return f"Feature '{self.name}' must be JSON-compatible."
        if self.value_type == FeatureValueType.VECTOR:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return f"Feature '{self.name}' must be a vector sequence."
            if self.vector_dim is not None and len(value) != self.vector_dim:
                return f"Feature '{self.name}' vector_dim must be {self.vector_dim}."

        numeric = _safe_float(value)
        if numeric is not None:
            if self.min_value is not None and numeric < self.min_value:
                return f"Feature '{self.name}' below min_value={self.min_value}."
            if self.max_value is not None and numeric > self.max_value:
                return f"Feature '{self.name}' above max_value={self.max_value}."

        if self.allowed_values is not None and value not in self.allowed_values:
            return f"Feature '{self.name}' has value outside allowed_values."
        if self.max_length is not None and isinstance(value, str) and len(value) > self.max_length:
            return f"Feature '{self.name}' exceeds max_length={self.max_length}."
        return None


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    version: str
    entity_keys: Sequence[EntityKey]
    schema: Sequence[FeatureSchema]
    description: str = ""
    owner: Optional[str] = None
    status: FeatureStatus = FeatureStatus.DRAFT
    source: Optional[str] = None
    transformation: Optional[str] = None
    freshness_sla_seconds: Optional[int] = None
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"

    def validate(self) -> None:
        if not self.name:
            raise FeatureValidationError("FeatureDefinition.name is required.")
        if not self.version:
            raise FeatureValidationError("FeatureDefinition.version is required.")
        if not self.entity_keys:
            raise FeatureValidationError(f"FeatureDefinition {self.key} requires at least one entity key.")
        if not self.schema:
            raise FeatureValidationError(f"FeatureDefinition {self.key} requires at least one schema feature.")
        names = [s.name for s in self.schema]
        if len(names) != len(set(names)):
            raise FeatureValidationError(f"FeatureDefinition {self.key} has duplicate schema names.")
        entity_names = [e.name for e in self.entity_keys]
        if len(entity_names) != len(set(entity_names)):
            raise FeatureValidationError(f"FeatureDefinition {self.key} has duplicate entity key names.")
        if self.freshness_sla_seconds is not None and self.freshness_sla_seconds <= 0:
            raise FeatureValidationError("freshness_sla_seconds must be positive.")


@dataclass(frozen=True)
class FeatureView:
    name: str
    version: str
    features: Sequence[FeatureDefinition]
    entity_keys: Sequence[EntityKey]
    description: str = ""
    owner: Optional[str] = None
    status: FeatureStatus = FeatureStatus.DRAFT
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"

    def validate(self) -> None:
        if not self.name or not self.version:
            raise FeatureValidationError("FeatureView.name and version are required.")
        if not self.features:
            raise FeatureValidationError(f"FeatureView {self.key} requires at least one feature definition.")
        for feature in self.features:
            feature.validate()


@dataclass(frozen=True)
class QualityIssue:
    code: str
    message: str
    severity: QualitySeverity
    row_index: Optional[int] = None
    feature_name: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureProfile:
    row_count: int
    feature_count: int
    null_counts: Mapping[str, int]
    distinct_counts: Mapping[str, int]
    numeric_stats: Mapping[str, Mapping[str, float]]
    freshness_lag_seconds: Optional[float]
    fingerprint: str
    generated_at: datetime


@dataclass(frozen=True)
class FeatureValidationResult:
    valid: bool
    issues: Sequence[QualityIssue]
    profile: FeatureProfile


@dataclass(frozen=True)
class MaterializationRequest:
    feature_view: FeatureView
    rows: Sequence[FeatureRow]
    mode: MaterializationMode = MaterializationMode.BOTH
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: Optional[str] = None
    event_timestamp_field: str = "event_timestamp"
    validate_rows: bool = True
    fail_on_error: bool = True
    chunk_size: int = 1000
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MaterializationResult:
    request_id: str
    tenant_id: Optional[str]
    feature_view_key: str
    mode: MaterializationMode
    row_count: int
    offline_rows_written: int
    online_rows_written: int
    validation: Optional[FeatureValidationResult]
    status: str
    processing_ms: float
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["created_at"] = self.created_at.isoformat()
        if payload.get("validation"):
            payload["validation"]["profile"]["generated_at"] = self.validation.profile.generated_at.isoformat() if self.validation else None
            for issue in payload["validation"]["issues"]:
                issue["severity"] = issue["severity"].value if hasattr(issue["severity"], "value") else issue["severity"]
        return payload


@dataclass(frozen=True)
class AggregationSpec:
    output_name: str
    source_field: Optional[str]
    function: AggregationFunction
    window: TimeWindow = TimeWindow.ALL_TIME
    value_type: FeatureValueType = FeatureValueType.FLOAT
    default: Any = 0


@dataclass(frozen=True)
class BuildAggregationRequest:
    tenant_id: Optional[str]
    entity_keys: Sequence[str]
    event_timestamp_field: str
    events: Sequence[FeatureRow]
    aggregations: Sequence[AggregationSpec]
    as_of: Optional[datetime] = None


@dataclass(frozen=True)
class FeatureStoreBuilderConfig:
    max_rows_per_request: int = 1_000_000
    default_chunk_size: int = 1000
    audit_enabled: bool = True
    fail_fast: bool = False
    idempotency_ttl_seconds: int = 86_400
    freshness_now_tolerance_seconds: int = 300
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.max_rows_per_request <= 0:
            raise FeatureValidationError("max_rows_per_request must be positive.")
        if self.default_chunk_size <= 0:
            raise FeatureValidationError("default_chunk_size must be positive.")
        if self.idempotency_ttl_seconds <= 0:
            raise FeatureValidationError("idempotency_ttl_seconds must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class FeatureRegistry(Protocol):
    async def register_feature(self, definition: FeatureDefinition) -> None: ...

    async def register_view(self, view: FeatureView) -> None: ...

    async def get_feature(self, name: str, version: str) -> Optional[FeatureDefinition]: ...

    async def get_view(self, name: str, version: str) -> Optional[FeatureView]: ...


class OfflineFeatureStore(Protocol):
    async def write_rows(self, feature_view: FeatureView, rows: Sequence[FeatureRow], tenant_id: Optional[str]) -> int: ...

    async def read_rows(self, feature_view: FeatureView, tenant_id: Optional[str], limit: Optional[int] = None) -> Sequence[FeatureRow]: ...


class OnlineFeatureStore(Protocol):
    async def write_rows(self, feature_view: FeatureView, rows: Sequence[FeatureRow], tenant_id: Optional[str]) -> int: ...

    async def get_features(self, feature_view: FeatureView, entity_values: Mapping[str, Any], tenant_id: Optional[str]) -> Optional[FeatureRow]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# No-op / in-memory implementations
# =============================================================================


class NoopMetricsClient:
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None:
        return None


class NoopAuditSink:
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None:
        return None


class InMemoryFeatureRegistry:
    def __init__(self) -> None:
        self.features: Dict[str, FeatureDefinition] = {}
        self.views: Dict[str, FeatureView] = {}

    async def register_feature(self, definition: FeatureDefinition) -> None:
        definition.validate()
        existing = self.features.get(definition.key)
        if existing and existing != definition:
            raise FeatureConflictError(f"Feature already registered with different definition: {definition.key}")
        self.features[definition.key] = definition

    async def register_view(self, view: FeatureView) -> None:
        view.validate()
        existing = self.views.get(view.key)
        if existing and existing != view:
            raise FeatureConflictError(f"Feature view already registered with different definition: {view.key}")
        self.views[view.key] = view
        for feature in view.features:
            await self.register_feature(feature)

    async def get_feature(self, name: str, version: str) -> Optional[FeatureDefinition]:
        return self.features.get(f"{name}:{version}")

    async def get_view(self, name: str, version: str) -> Optional[FeatureView]:
        return self.views.get(f"{name}:{version}")


class InMemoryOfflineFeatureStore:
    def __init__(self) -> None:
        self.rows: Dict[Tuple[Optional[str], str], List[FeatureRow]] = defaultdict(list)

    async def write_rows(self, feature_view: FeatureView, rows: Sequence[FeatureRow], tenant_id: Optional[str]) -> int:
        key = (tenant_id, feature_view.key)
        self.rows[key].extend(dict(row) for row in rows)
        return len(rows)

    async def read_rows(self, feature_view: FeatureView, tenant_id: Optional[str], limit: Optional[int] = None) -> Sequence[FeatureRow]:
        data = self.rows.get((tenant_id, feature_view.key), [])
        return tuple(data[:limit] if limit else data)


class InMemoryOnlineFeatureStore:
    def __init__(self) -> None:
        self.rows: Dict[Tuple[Optional[str], str, str], FeatureRow] = {}

    async def write_rows(self, feature_view: FeatureView, rows: Sequence[FeatureRow], tenant_id: Optional[str]) -> int:
        for row in rows:
            entity_key = _entity_hash(feature_view.entity_keys, row)
            self.rows[(tenant_id, feature_view.key, entity_key)] = dict(row)
        return len(rows)

    async def get_features(self, feature_view: FeatureView, entity_values: Mapping[str, Any], tenant_id: Optional[str]) -> Optional[FeatureRow]:
        entity_key = _entity_hash(feature_view.entity_keys, entity_values)
        return self.rows.get((tenant_id, feature_view.key, entity_key))


class AsyncIdempotencyStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: MutableMapping[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < now:
                self._items.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, value)


# =============================================================================
# Utility functions
# =============================================================================


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        numeric = float(value)
        if math.isnan(numeric) or math.isinf(numeric):
            return None
        return numeric
    except (TypeError, ValueError, InvalidOperation):
        return None


def _ensure_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _window_start(as_of: datetime, window: TimeWindow) -> Optional[datetime]:
    if window == TimeWindow.ALL_TIME:
        return None
    mapping = {
        TimeWindow.HOUR_1: timedelta(hours=1),
        TimeWindow.HOUR_6: timedelta(hours=6),
        TimeWindow.DAY_1: timedelta(days=1),
        TimeWindow.DAY_7: timedelta(days=7),
        TimeWindow.DAY_14: timedelta(days=14),
        TimeWindow.DAY_30: timedelta(days=30),
        TimeWindow.DAY_90: timedelta(days=90),
    }
    return as_of - mapping[window]


def _entity_hash(entity_keys: Sequence[EntityKey], row: Mapping[str, Any]) -> str:
    payload = {key.name: row.get(key.name) for key in entity_keys}
    return _stable_hash(payload)


def _chunks(rows: Sequence[FeatureRow], chunk_size: int) -> Iterable[Sequence[FeatureRow]]:
    for idx in range(0, len(rows), chunk_size):
        yield rows[idx : idx + chunk_size]


# =============================================================================
# Validator / profiler
# =============================================================================


class FeatureValidator:
    def validate_rows(
        self,
        feature_view: FeatureView,
        rows: Sequence[FeatureRow],
        event_timestamp_field: str = "event_timestamp",
    ) -> FeatureValidationResult:
        issues: List[QualityIssue] = []
        schema_by_name: Dict[str, FeatureSchema] = {}
        for definition in feature_view.features:
            for schema in definition.schema:
                schema_by_name[schema.name] = schema

        entity_names = [entity.name for entity in feature_view.entity_keys]
        for idx, row in enumerate(rows):
            for entity_name in entity_names:
                if row.get(entity_name) is None:
                    issues.append(
                        QualityIssue(
                            code="MISSING_ENTITY_KEY",
                            message=f"Missing entity key '{entity_name}'.",
                            severity=QualitySeverity.ERROR,
                            row_index=idx,
                            feature_name=entity_name,
                        )
                    )
            for feature_name, schema in schema_by_name.items():
                error = schema.validate_value(row.get(feature_name, schema.default))
                if error:
                    issues.append(
                        QualityIssue(
                            code="SCHEMA_VALIDATION_FAILED",
                            message=error,
                            severity=QualitySeverity.ERROR,
                            row_index=idx,
                            feature_name=feature_name,
                        )
                    )
            if event_timestamp_field and row.get(event_timestamp_field) is not None and _ensure_datetime(row.get(event_timestamp_field)) is None:
                issues.append(
                    QualityIssue(
                        code="INVALID_EVENT_TIMESTAMP",
                        message=f"Invalid timestamp in '{event_timestamp_field}'.",
                        severity=QualitySeverity.ERROR,
                        row_index=idx,
                        feature_name=event_timestamp_field,
                    )
                )

        profile = self.profile(rows, schema_by_name, event_timestamp_field)
        valid = not any(issue.severity in {QualitySeverity.ERROR, QualitySeverity.CRITICAL} for issue in issues)
        return FeatureValidationResult(valid=valid, issues=tuple(issues), profile=profile)

    def profile(
        self,
        rows: Sequence[FeatureRow],
        schema_by_name: Mapping[str, FeatureSchema],
        event_timestamp_field: str,
    ) -> FeatureProfile:
        null_counts: Dict[str, int] = {name: 0 for name in schema_by_name}
        values_by_feature: Dict[str, List[Any]] = {name: [] for name in schema_by_name}
        timestamps: List[datetime] = []

        for row in rows:
            ts = _ensure_datetime(row.get(event_timestamp_field))
            if ts:
                timestamps.append(ts)
            for name in schema_by_name:
                value = row.get(name)
                if value is None:
                    null_counts[name] += 1
                else:
                    values_by_feature[name].append(value)

        distinct_counts = {name: len({json.dumps(v, sort_keys=True, default=str) for v in values}) for name, values in values_by_feature.items()}
        numeric_stats: Dict[str, Mapping[str, float]] = {}
        for name, values in values_by_feature.items():
            numeric = [_safe_float(value) for value in values]
            numeric = [value for value in numeric if value is not None]
            if numeric:
                numeric_stats[name] = {
                    "min": min(numeric),
                    "max": max(numeric),
                    "mean": statistics.mean(numeric),
                    "stddev": statistics.pstdev(numeric) if len(numeric) > 1 else 0.0,
                }

        freshness_lag = None
        if timestamps:
            freshness_lag = max(0.0, (_utc_now() - max(timestamps)).total_seconds())

        fingerprint = _stable_hash({"rows": list(rows)[:5000], "row_count": len(rows), "schema": list(schema_by_name)})
        return FeatureProfile(
            row_count=len(rows),
            feature_count=len(schema_by_name),
            null_counts=null_counts,
            distinct_counts=distinct_counts,
            numeric_stats=numeric_stats,
            freshness_lag_seconds=freshness_lag,
            fingerprint=fingerprint,
            generated_at=_utc_now(),
        )


# =============================================================================
# Aggregation builder
# =============================================================================


class FeatureAggregationBuilder:
    def build(self, request: BuildAggregationRequest) -> Sequence[FeatureRow]:
        as_of = request.as_of or _utc_now()
        grouped: Dict[Tuple[Any, ...], List[FeatureRow]] = defaultdict(list)
        for event in request.events:
            key = tuple(event.get(k) for k in request.entity_keys)
            grouped[key].append(event)

        output_rows: List[FeatureRow] = []
        for entity_values, events in grouped.items():
            base: Dict[str, Any] = {name: value for name, value in zip(request.entity_keys, entity_values)}
            base[request.event_timestamp_field] = as_of.isoformat()
            for spec in request.aggregations:
                filtered = self._filter_window(events, spec.window, request.event_timestamp_field, as_of)
                base[spec.output_name] = self._aggregate(filtered, spec)
            output_rows.append(base)
        return tuple(output_rows)

    def _filter_window(
        self,
        events: Sequence[FeatureRow],
        window: TimeWindow,
        timestamp_field: str,
        as_of: datetime,
    ) -> Sequence[FeatureRow]:
        start = _window_start(as_of, window)
        if start is None:
            return events
        filtered = []
        for event in events:
            ts = _ensure_datetime(event.get(timestamp_field))
            if ts and start <= ts <= as_of:
                filtered.append(event)
        return tuple(filtered)

    def _aggregate(self, events: Sequence[FeatureRow], spec: AggregationSpec) -> Any:
        if spec.function == AggregationFunction.COUNT:
            return len(events)
        if spec.source_field is None:
            return spec.default
        values = [event.get(spec.source_field) for event in events if event.get(spec.source_field) is not None]
        if not values:
            return spec.default
        if spec.function == AggregationFunction.DISTINCT_COUNT:
            return len({json.dumps(v, sort_keys=True, default=str) for v in values})
        if spec.function == AggregationFunction.LAST:
            return values[-1]
        if spec.function == AggregationFunction.FIRST:
            return values[0]
        numeric = [_safe_float(v) for v in values]
        numeric = [v for v in numeric if v is not None]
        if not numeric:
            return spec.default
        if spec.function == AggregationFunction.SUM:
            return sum(numeric)
        if spec.function == AggregationFunction.AVG:
            return statistics.mean(numeric)
        if spec.function == AggregationFunction.MIN:
            return min(numeric)
        if spec.function == AggregationFunction.MAX:
            return max(numeric)
        if spec.function == AggregationFunction.STDDEV:
            return statistics.pstdev(numeric) if len(numeric) > 1 else 0.0
        return spec.default


# =============================================================================
# Main builder
# =============================================================================


class FeatureStoreBuilder:
    def __init__(
        self,
        registry: Optional[FeatureRegistry] = None,
        offline_store: Optional[OfflineFeatureStore] = None,
        online_store: Optional[OnlineFeatureStore] = None,
        config: Optional[FeatureStoreBuilderConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
        idempotency_store: Optional[AsyncIdempotencyStore] = None,
    ) -> None:
        self.config = config or FeatureStoreBuilderConfig()
        self.config.validate()
        self.registry = registry or InMemoryFeatureRegistry()
        self.offline_store = offline_store or InMemoryOfflineFeatureStore()
        self.online_store = online_store or InMemoryOnlineFeatureStore()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.idempotency_store = idempotency_store or AsyncIdempotencyStore(self.config.idempotency_ttl_seconds)
        self.validator = FeatureValidator()
        self.aggregation_builder = FeatureAggregationBuilder()

    async def register_feature(self, definition: FeatureDefinition) -> None:
        definition.validate()
        await self.registry.register_feature(definition)
        await self._audit("feature_store.feature.registered", {"feature_key": definition.key, "owner": definition.owner})

    async def register_view(self, view: FeatureView) -> None:
        view.validate()
        await self.registry.register_view(view)
        await self._audit("feature_store.view.registered", {"feature_view_key": view.key, "owner": view.owner})

    async def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        started = time.perf_counter()
        self._validate_materialization_request(request)
        tags = {"tenant_id": request.tenant_id or "global", "feature_view": request.feature_view.key, "mode": request.mode.value}
        self.metrics.increment("feature_store.materialization.started", tags=tags)

        idem_key = self._idempotency_key(request)
        cached = await self.idempotency_store.get(idem_key)
        if cached is not None:
            self.metrics.increment("feature_store.materialization.idempotency_hit", tags=tags)
            return cached

        validation: Optional[FeatureValidationResult] = None
        if request.validate_rows:
            validation = self.validator.validate_rows(request.feature_view, request.rows, request.event_timestamp_field)
            if request.fail_on_error and not validation.valid:
                result = MaterializationResult(
                    request_id=request.request_id,
                    tenant_id=request.tenant_id,
                    feature_view_key=request.feature_view.key,
                    mode=request.mode,
                    row_count=len(request.rows),
                    offline_rows_written=0,
                    online_rows_written=0,
                    validation=validation,
                    status="validation_failed",
                    processing_ms=round((time.perf_counter() - started) * 1000, 4),
                    created_at=_utc_now(),
                    metadata={"issue_count": len(validation.issues)},
                )
                await self._audit("feature_store.materialization.validation_failed", result.to_dict())
                if self.config.fail_fast:
                    raise FeatureValidationError(f"Feature validation failed with {len(validation.issues)} issues.")
                return result

        await self.register_view(request.feature_view)
        offline_written = 0
        online_written = 0
        chunk_size = request.chunk_size or self.config.default_chunk_size

        try:
            for chunk in _chunks(request.rows, chunk_size):
                if request.mode in {MaterializationMode.OFFLINE, MaterializationMode.BOTH}:
                    offline_written += await self.offline_store.write_rows(request.feature_view, chunk, request.tenant_id)
                if request.mode in {MaterializationMode.ONLINE, MaterializationMode.BOTH}:
                    online_written += await self.online_store.write_rows(request.feature_view, chunk, request.tenant_id)
        except Exception as exc:
            self.metrics.increment("feature_store.materialization.failed", tags={**tags, "error": exc.__class__.__name__})
            logger.exception("Feature materialization failed", extra={"request_id": request.request_id})
            raise FeatureMaterializationError(f"Materialization failed: {exc}") from exc

        result = MaterializationResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            feature_view_key=request.feature_view.key,
            mode=request.mode,
            row_count=len(request.rows),
            offline_rows_written=offline_written,
            online_rows_written=online_written,
            validation=validation,
            status="completed",
            processing_ms=round((time.perf_counter() - started) * 1000, 4),
            created_at=_utc_now(),
            metadata={"chunk_size": chunk_size, **dict(request.metadata)},
        )
        await self.idempotency_store.set(idem_key, result)
        self.metrics.increment("feature_store.materialization.completed", tags=tags)
        self.metrics.gauge("feature_store.materialization.rows", len(request.rows), tags=tags)
        self.metrics.timing("feature_store.materialization.processing_ms", result.processing_ms, tags=tags)
        await self._audit("feature_store.materialization.completed", result.to_dict())
        return result

    async def build_aggregations(self, request: BuildAggregationRequest) -> Sequence[FeatureRow]:
        rows = self.aggregation_builder.build(request)
        await self._audit(
            "feature_store.aggregations.built",
            {
                "tenant_id": request.tenant_id,
                "entity_keys": list(request.entity_keys),
                "aggregation_count": len(request.aggregations),
                "row_count": len(rows),
            },
        )
        return rows

    async def get_online_features(
        self,
        feature_view: FeatureView,
        entity_values: Mapping[str, Any],
        tenant_id: Optional[str] = None,
    ) -> Optional[FeatureRow]:
        return await self.online_store.get_features(feature_view, entity_values, tenant_id)

    async def read_offline_features(
        self,
        feature_view: FeatureView,
        tenant_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Sequence[FeatureRow]:
        return await self.offline_store.read_rows(feature_view, tenant_id, limit)

    def make_feature_view_from_schema(
        self,
        *,
        name: str,
        version: str,
        entity_keys: Sequence[EntityKey],
        schema: Sequence[FeatureSchema],
        owner: Optional[str] = None,
        source: Optional[str] = None,
        description: str = "",
        tags: Optional[Mapping[str, str]] = None,
    ) -> FeatureView:
        definition = FeatureDefinition(
            name=name,
            version=version,
            entity_keys=tuple(entity_keys),
            schema=tuple(schema),
            description=description,
            owner=owner,
            status=FeatureStatus.ACTIVE,
            source=source,
            tags=tags or {},
        )
        return FeatureView(
            name=name,
            version=version,
            features=(definition,),
            entity_keys=tuple(entity_keys),
            description=description,
            owner=owner,
            status=FeatureStatus.ACTIVE,
            tags=tags or {},
        )

    def _validate_materialization_request(self, request: MaterializationRequest) -> None:
        request.feature_view.validate()
        if len(request.rows) > self.config.max_rows_per_request:
            raise FeatureValidationError(f"rows exceeds max_rows_per_request={self.config.max_rows_per_request}.")
        if request.chunk_size <= 0:
            raise FeatureValidationError("chunk_size must be positive.")
        if not request.rows:
            raise FeatureValidationError("rows cannot be empty.")

    def _idempotency_key(self, request: MaterializationRequest) -> str:
        if request.idempotency_key:
            return f"feature_store:materialize:{request.tenant_id or 'global'}:{request.idempotency_key}"
        return "feature_store:materialize:" + _stable_hash(
            {
                "tenant_id": request.tenant_id,
                "feature_view": request.feature_view.key,
                "mode": request.mode.value,
                "rows_fingerprint": _stable_hash(list(request.rows)[:5000]),
                "row_count": len(request.rows),
            }
        )

    async def _audit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        if not self.config.audit_enabled:
            return
        try:
            await self.audit_sink.write(event_name, payload)
        except Exception:
            logger.exception("Failed to write feature store audit event", extra={"event_name": event_name})


# =============================================================================
# Factory
# =============================================================================


def build_feature_store_builder(
    registry: Optional[FeatureRegistry] = None,
    offline_store: Optional[OfflineFeatureStore] = None,
    online_store: Optional[OnlineFeatureStore] = None,
    config: Optional[FeatureStoreBuilderConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> FeatureStoreBuilder:
    return FeatureStoreBuilder(
        registry=registry,
        offline_store=offline_store,
        online_store=online_store,
        config=config,
        metrics=metrics,
        audit_sink=audit_sink,
    )


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    builder = build_feature_store_builder()
    view = builder.make_feature_view_from_schema(
        name="customer_risk_features",
        version="v1",
        entity_keys=(EntityKey("customer_id"),),
        schema=(
            FeatureSchema("txn_count_7d", FeatureValueType.INT, nullable=False, min_value=0),
            FeatureSchema("amount_sum_7d", FeatureValueType.FLOAT, nullable=False, min_value=0),
            FeatureSchema("last_country", FeatureValueType.STRING, nullable=True, max_length=2),
        ),
        owner="risk-team",
        source="transactions",
        description="Customer risk aggregation features.",
    )

    now = _utc_now()
    events = [
        {"customer_id": "C001", "amount": 100.0, "country": "AO", "event_timestamp": (now - timedelta(days=1)).isoformat()},
        {"customer_id": "C001", "amount": 200.0, "country": "AO", "event_timestamp": (now - timedelta(days=2)).isoformat()},
        {"customer_id": "C002", "amount": 80.0, "country": "BR", "event_timestamp": (now - timedelta(days=3)).isoformat()},
    ]
    rows = await builder.build_aggregations(
        BuildAggregationRequest(
            tenant_id="tenant-ao",
            entity_keys=("customer_id",),
            event_timestamp_field="event_timestamp",
            events=events,
            as_of=now,
            aggregations=(
                AggregationSpec("txn_count_7d", None, AggregationFunction.COUNT, TimeWindow.DAY_7, FeatureValueType.INT),
                AggregationSpec("amount_sum_7d", "amount", AggregationFunction.SUM, TimeWindow.DAY_7),
                AggregationSpec("last_country", "country", AggregationFunction.LAST, TimeWindow.DAY_7, FeatureValueType.STRING),
            ),
        )
    )
    result = await builder.materialize(
        MaterializationRequest(
            tenant_id="tenant-ao",
            feature_view=view,
            rows=rows,
            mode=MaterializationMode.BOTH,
            idempotency_key="demo-materialization-001",
        )
    )
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
