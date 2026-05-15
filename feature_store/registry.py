"""
kwanza-ai-core/feature_store/registry.py

Enterprise-grade Feature Store Registry.

Purpose
-------
Maintain the canonical catalog of feature definitions, feature views, entities,
versions, lifecycle status, lineage, ownership, tags and governance metadata for
Kwanza AI Core.

Capabilities
------------
- Register and version feature definitions.
- Register feature views composed of one or more features.
- Entity catalog management.
- Schema compatibility checks.
- Lifecycle transitions: draft, active, deprecated, archived.
- Search by name, owner, tag, status, entity, source and feature type.
- Lineage graph between sources, features, feature views and models.
- Audit and metrics hooks.
- Async repository abstraction with in-memory implementation.
- Import/export snapshots for migration and disaster recovery.

This module is framework-agnostic. Production repositories can use PostgreSQL,
Supabase, DynamoDB, Redis, MongoDB, data catalogs or dedicated governance tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
MetricTags = Mapping[str, str]


# =============================================================================
# Exceptions
# =============================================================================


class FeatureRegistryError(RuntimeError):
    """Base exception for feature registry failures."""


class FeatureRegistryValidationError(FeatureRegistryError):
    """Raised when registry input is invalid."""


class FeatureRegistryConflictError(FeatureRegistryError):
    """Raised when an operation conflicts with existing metadata."""


class FeatureRegistryNotFoundError(FeatureRegistryError):
    """Raised when a requested registry object does not exist."""


class FeatureRegistryLifecycleError(FeatureRegistryError):
    """Raised when a lifecycle transition is invalid."""


# =============================================================================
# Enums and models
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


class RegistryObjectType(str, Enum):
    ENTITY = "entity"
    FEATURE = "feature"
    FEATURE_VIEW = "feature_view"
    SOURCE = "source"
    MODEL = "model"
    PIPELINE = "pipeline"


class RegistryStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    DELETED = "deleted"


class CompatibilityLevel(str, Enum):
    FULL = "full"
    BACKWARD = "backward"
    FORWARD = "forward"
    NONE = "none"


class LineageRelationType(str, Enum):
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    DERIVED_FROM = "derived_from"
    SERVES = "serves"
    TRAINS = "trains"
    USED_BY = "used_by"
    OWNS = "owns"


class ChangeType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    STATUS_CHANGE = "status_change"
    DELETE = "delete"
    IMPORT = "import"
    EXPORT = "export"


@dataclass(frozen=True)
class EntityDefinition:
    name: str
    value_type: FeatureValueType = FeatureValueType.STRING
    description: str = ""
    owner: Optional[str] = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return self.name

    def validate(self) -> None:
        _validate_name(self.name, "entity.name")


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
    description: str = ""

    def validate(self) -> None:
        _validate_name(self.name, "schema.name")
        if self.min_value is not None and self.max_value is not None and self.max_value < self.min_value:
            raise FeatureRegistryValidationError(f"schema {self.name}: max_value cannot be less than min_value.")
        if self.max_length is not None and self.max_length <= 0:
            raise FeatureRegistryValidationError(f"schema {self.name}: max_length must be positive.")
        if self.value_type == FeatureValueType.VECTOR and self.vector_dim is not None and self.vector_dim <= 0:
            raise FeatureRegistryValidationError(f"schema {self.name}: vector_dim must be positive.")


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    version: str
    entities: Sequence[str]
    schema: FeatureSchema
    description: str = ""
    owner: Optional[str] = None
    status: RegistryStatus = RegistryStatus.DRAFT
    source: Optional[str] = None
    transformation: Optional[str] = None
    freshness_sla_seconds: Optional[int] = None
    compatibility_level: CompatibilityLevel = CompatibilityLevel.BACKWARD
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"

    def validate(self) -> None:
        _validate_name(self.name, "feature.name")
        _validate_version(self.version)
        if not self.entities:
            raise FeatureRegistryValidationError(f"feature {self.key}: at least one entity is required.")
        for entity in self.entities:
            _validate_name(entity, "feature.entities[]")
        self.schema.validate()
        if self.freshness_sla_seconds is not None and self.freshness_sla_seconds <= 0:
            raise FeatureRegistryValidationError("freshness_sla_seconds must be positive.")


@dataclass(frozen=True)
class FeatureViewDefinition:
    name: str
    version: str
    features: Sequence[str]
    entities: Sequence[str]
    description: str = ""
    owner: Optional[str] = None
    status: RegistryStatus = RegistryStatus.DRAFT
    online_enabled: bool = True
    offline_enabled: bool = True
    ttl_seconds: Optional[int] = None
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return f"{self.name}:{self.version}"

    def validate(self) -> None:
        _validate_name(self.name, "feature_view.name")
        _validate_version(self.version)
        if not self.features:
            raise FeatureRegistryValidationError(f"feature_view {self.key}: at least one feature is required.")
        if not self.entities:
            raise FeatureRegistryValidationError(f"feature_view {self.key}: at least one entity is required.")
        if self.ttl_seconds is not None and self.ttl_seconds <= 0:
            raise FeatureRegistryValidationError("ttl_seconds must be positive.")


@dataclass(frozen=True)
class DataSourceDefinition:
    name: str
    source_type: str
    uri: Optional[str] = None
    description: str = ""
    owner: Optional[str] = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    schema_fingerprint: Optional[str] = None
    tags: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def key(self) -> str:
        return self.name

    def validate(self) -> None:
        _validate_name(self.name, "source.name")
        if not self.source_type:
            raise FeatureRegistryValidationError("source_type is required.")


@dataclass(frozen=True)
class LineageEdge:
    edge_id: str
    from_type: RegistryObjectType
    from_key: str
    to_type: RegistryObjectType
    to_key: str
    relation: LineageRelationType
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> None:
        if not self.from_key or not self.to_key:
            raise FeatureRegistryValidationError("lineage edges require from_key and to_key.")
        if self.from_type == self.to_type and self.from_key == self.to_key:
            raise FeatureRegistryValidationError("lineage edge cannot point to itself.")


@dataclass(frozen=True)
class RegistryAuditEvent:
    event_id: str
    object_type: RegistryObjectType
    object_key: str
    change_type: ChangeType
    actor_id: Optional[str]
    before_hash: Optional[str]
    after_hash: Optional[str]
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistrySearchQuery:
    text: Optional[str] = None
    object_types: Sequence[RegistryObjectType] = field(default_factory=tuple)
    owner: Optional[str] = None
    status: Optional[RegistryStatus] = None
    tags: Mapping[str, str] = field(default_factory=dict)
    entity: Optional[str] = None
    source: Optional[str] = None
    limit: int = 100


@dataclass(frozen=True)
class RegistrySearchResult:
    object_type: RegistryObjectType
    key: str
    name: str
    version: Optional[str]
    status: RegistryStatus
    owner: Optional[str]
    description: str
    tags: Mapping[str, str]
    score: float
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistrySnapshot:
    snapshot_id: str
    entities: Sequence[EntityDefinition]
    features: Sequence[FeatureDefinition]
    feature_views: Sequence[FeatureViewDefinition]
    sources: Sequence[DataSourceDefinition]
    lineage_edges: Sequence[LineageEdge]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        _convert_datetimes_and_enums(payload)
        return payload


@dataclass(frozen=True)
class FeatureRegistryConfig:
    audit_enabled: bool = True
    allow_overwrite_draft: bool = True
    enforce_schema_compatibility: bool = True
    max_search_results: int = 1000
    privacy_hash_salt: str = "change-me-in-production"

    def validate(self) -> None:
        if self.max_search_results <= 0:
            raise FeatureRegistryValidationError("max_search_results must be positive.")


# =============================================================================
# Protocols
# =============================================================================


class FeatureRegistryRepository(Protocol):
    async def upsert_entity(self, entity: EntityDefinition) -> None: ...
    async def get_entity(self, name: str) -> Optional[EntityDefinition]: ...
    async def list_entities(self) -> Sequence[EntityDefinition]: ...

    async def upsert_feature(self, feature: FeatureDefinition) -> None: ...
    async def get_feature(self, name: str, version: str) -> Optional[FeatureDefinition]: ...
    async def list_features(self, name: Optional[str] = None) -> Sequence[FeatureDefinition]: ...

    async def upsert_feature_view(self, view: FeatureViewDefinition) -> None: ...
    async def get_feature_view(self, name: str, version: str) -> Optional[FeatureViewDefinition]: ...
    async def list_feature_views(self, name: Optional[str] = None) -> Sequence[FeatureViewDefinition]: ...

    async def upsert_source(self, source: DataSourceDefinition) -> None: ...
    async def get_source(self, name: str) -> Optional[DataSourceDefinition]: ...
    async def list_sources(self) -> Sequence[DataSourceDefinition]: ...

    async def add_lineage_edge(self, edge: LineageEdge) -> None: ...
    async def list_lineage_edges(self, key: Optional[str] = None) -> Sequence[LineageEdge]: ...

    async def add_audit_event(self, event: RegistryAuditEvent) -> None: ...
    async def list_audit_events(self, object_key: Optional[str] = None, limit: int = 100) -> Sequence[RegistryAuditEvent]: ...


class MetricsClient(Protocol):
    def increment(self, name: str, value: int = 1, tags: Optional[MetricTags] = None) -> None: ...
    def timing(self, name: str, value_ms: float, tags: Optional[MetricTags] = None) -> None: ...
    def gauge(self, name: str, value: float, tags: Optional[MetricTags] = None) -> None: ...


class AuditSink(Protocol):
    async def write(self, event_name: str, payload: Mapping[str, Any]) -> None: ...


# =============================================================================
# No-op/in-memory implementations
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


class InMemoryFeatureRegistryRepository:
    def __init__(self) -> None:
        self.entities: Dict[str, EntityDefinition] = {}
        self.features: Dict[str, FeatureDefinition] = {}
        self.feature_views: Dict[str, FeatureViewDefinition] = {}
        self.sources: Dict[str, DataSourceDefinition] = {}
        self.lineage_edges: Dict[str, LineageEdge] = {}
        self.audit_events: List[RegistryAuditEvent] = []
        self._lock = asyncio.Lock()

    async def upsert_entity(self, entity: EntityDefinition) -> None:
        async with self._lock:
            self.entities[entity.key] = entity

    async def get_entity(self, name: str) -> Optional[EntityDefinition]:
        return self.entities.get(name)

    async def list_entities(self) -> Sequence[EntityDefinition]:
        return tuple(self.entities.values())

    async def upsert_feature(self, feature: FeatureDefinition) -> None:
        async with self._lock:
            self.features[feature.key] = feature

    async def get_feature(self, name: str, version: str) -> Optional[FeatureDefinition]:
        return self.features.get(f"{name}:{version}")

    async def list_features(self, name: Optional[str] = None) -> Sequence[FeatureDefinition]:
        rows = list(self.features.values())
        if name:
            rows = [f for f in rows if f.name == name]
        return tuple(sorted(rows, key=lambda f: (f.name, f.version)))

    async def upsert_feature_view(self, view: FeatureViewDefinition) -> None:
        async with self._lock:
            self.feature_views[view.key] = view

    async def get_feature_view(self, name: str, version: str) -> Optional[FeatureViewDefinition]:
        return self.feature_views.get(f"{name}:{version}")

    async def list_feature_views(self, name: Optional[str] = None) -> Sequence[FeatureViewDefinition]:
        rows = list(self.feature_views.values())
        if name:
            rows = [v for v in rows if v.name == name]
        return tuple(sorted(rows, key=lambda v: (v.name, v.version)))

    async def upsert_source(self, source: DataSourceDefinition) -> None:
        async with self._lock:
            self.sources[source.key] = source

    async def get_source(self, name: str) -> Optional[DataSourceDefinition]:
        return self.sources.get(name)

    async def list_sources(self) -> Sequence[DataSourceDefinition]:
        return tuple(self.sources.values())

    async def add_lineage_edge(self, edge: LineageEdge) -> None:
        async with self._lock:
            self.lineage_edges[edge.edge_id] = edge

    async def list_lineage_edges(self, key: Optional[str] = None) -> Sequence[LineageEdge]:
        rows = list(self.lineage_edges.values())
        if key:
            rows = [edge for edge in rows if edge.from_key == key or edge.to_key == key]
        return tuple(rows)

    async def add_audit_event(self, event: RegistryAuditEvent) -> None:
        async with self._lock:
            self.audit_events.append(event)

    async def list_audit_events(self, object_key: Optional[str] = None, limit: int = 100) -> Sequence[RegistryAuditEvent]:
        rows = self.audit_events
        if object_key:
            rows = [event for event in rows if event.object_key == object_key]
        return tuple(rows[-limit:])


# =============================================================================
# Utility functions
# =============================================================================


_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{1,127}$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,64}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_name(value: str, field_name: str) -> None:
    if not value or not _NAME_RE.match(value):
        raise FeatureRegistryValidationError(
            f"{field_name} must start with a letter and contain only letters, numbers and underscores."
        )


def _validate_version(value: str) -> None:
    if not value or not _VERSION_RE.match(value):
        raise FeatureRegistryValidationError("version must contain only letters, numbers, dots, hyphens or underscores.")


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _object_hash(obj: Any) -> str:
    return _stable_hash(asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj)


def _hash_value(value: Optional[str], salt: str) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:20]


def _convert_datetimes_and_enums(obj: Any) -> None:
    if isinstance(obj, MutableMapping):
        for key, value in list(obj.items()):
            if isinstance(value, datetime):
                obj[key] = value.isoformat()
            elif isinstance(value, Enum):
                obj[key] = value.value
            else:
                _convert_datetimes_and_enums(value)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            if isinstance(value, datetime):
                obj[idx] = value.isoformat()
            elif isinstance(value, Enum):
                obj[idx] = value.value
            else:
                _convert_datetimes_and_enums(value)


def _with_updated_at(obj: Any, **changes: Any) -> Any:
    payload = asdict(obj)
    payload.update(changes)
    payload["updated_at"] = _utc_now()
    cls = obj.__class__
    return cls(**payload)


# =============================================================================
# Compatibility checker
# =============================================================================


class SchemaCompatibilityChecker:
    def check(self, previous: FeatureDefinition, candidate: FeatureDefinition) -> Tuple[bool, Sequence[str]]:
        issues: List[str] = []
        if previous.name != candidate.name:
            issues.append("Feature name cannot change across versions.")
        if previous.schema.name != candidate.schema.name:
            issues.append("Schema field name changed.")
        if previous.schema.value_type != candidate.schema.value_type:
            issues.append(f"Value type changed: {previous.schema.value_type.value} -> {candidate.schema.value_type.value}.")
        if previous.schema.nullable and not candidate.schema.nullable:
            issues.append("Changing nullable=true to nullable=false is not backward compatible.")
        if previous.schema.allowed_values is not None and candidate.schema.allowed_values is not None:
            previous_values = set(map(str, previous.schema.allowed_values))
            candidate_values = set(map(str, candidate.schema.allowed_values))
            if not previous_values.issubset(candidate_values):
                issues.append("Candidate removed allowed_values from previous schema.")
        if previous.schema.vector_dim is not None and candidate.schema.vector_dim is not None:
            if previous.schema.vector_dim != candidate.schema.vector_dim:
                issues.append("Vector dimension changed.")
        return len(issues) == 0, tuple(issues)


# =============================================================================
# Main registry
# =============================================================================


class FeatureRegistry:
    def __init__(
        self,
        repository: Optional[FeatureRegistryRepository] = None,
        config: Optional[FeatureRegistryConfig] = None,
        metrics: Optional[MetricsClient] = None,
        audit_sink: Optional[AuditSink] = None,
    ) -> None:
        self.repository = repository or InMemoryFeatureRegistryRepository()
        self.config = config or FeatureRegistryConfig()
        self.config.validate()
        self.metrics = metrics or NoopMetricsClient()
        self.audit_sink = audit_sink or NoopAuditSink()
        self.compatibility_checker = SchemaCompatibilityChecker()

    async def register_entity(self, entity: EntityDefinition, actor_id: Optional[str] = None) -> EntityDefinition:
        started = time.perf_counter()
        entity.validate()
        before = await self.repository.get_entity(entity.name)
        await self.repository.upsert_entity(entity)
        await self._record_change(RegistryObjectType.ENTITY, entity.key, ChangeType.CREATE if before is None else ChangeType.UPDATE, actor_id, before, entity)
        self.metrics.increment("feature_registry.entity.registered", tags={"status": entity.status.value})
        self.metrics.timing("feature_registry.operation_ms", (time.perf_counter() - started) * 1000, tags={"operation": "register_entity"})
        return entity

    async def register_source(self, source: DataSourceDefinition, actor_id: Optional[str] = None) -> DataSourceDefinition:
        source.validate()
        before = await self.repository.get_source(source.name)
        await self.repository.upsert_source(source)
        await self._record_change(RegistryObjectType.SOURCE, source.key, ChangeType.CREATE if before is None else ChangeType.UPDATE, actor_id, before, source)
        return source

    async def register_feature(self, feature: FeatureDefinition, actor_id: Optional[str] = None) -> FeatureDefinition:
        started = time.perf_counter()
        feature.validate()
        await self._ensure_entities_exist(feature.entities)

        before = await self.repository.get_feature(feature.name, feature.version)
        if before and before != feature:
            if before.status != RegistryStatus.DRAFT or not self.config.allow_overwrite_draft:
                raise FeatureRegistryConflictError(f"Feature already exists and cannot be overwritten: {feature.key}")

        if self.config.enforce_schema_compatibility:
            await self._check_compatibility(feature)

        await self.repository.upsert_feature(feature)
        if feature.source:
            await self.add_lineage(
                LineageEdge(
                    edge_id=str(uuid.uuid4()),
                    from_type=RegistryObjectType.SOURCE,
                    from_key=feature.source,
                    to_type=RegistryObjectType.FEATURE,
                    to_key=feature.key,
                    relation=LineageRelationType.DERIVED_FROM,
                ),
                actor_id=actor_id,
            )
        await self._record_change(RegistryObjectType.FEATURE, feature.key, ChangeType.CREATE if before is None else ChangeType.UPDATE, actor_id, before, feature)
        self.metrics.increment("feature_registry.feature.registered", tags={"status": feature.status.value})
        self.metrics.timing("feature_registry.operation_ms", (time.perf_counter() - started) * 1000, tags={"operation": "register_feature"})
        return feature

    async def register_feature_view(self, view: FeatureViewDefinition, actor_id: Optional[str] = None) -> FeatureViewDefinition:
        started = time.perf_counter()
        view.validate()
        await self._ensure_entities_exist(view.entities)
        for feature_key in view.features:
            name, version = self._split_key(feature_key)
            feature = await self.repository.get_feature(name, version)
            if not feature:
                raise FeatureRegistryNotFoundError(f"Feature not found for view {view.key}: {feature_key}")

        before = await self.repository.get_feature_view(view.name, view.version)
        if before and before != view:
            if before.status != RegistryStatus.DRAFT or not self.config.allow_overwrite_draft:
                raise FeatureRegistryConflictError(f"Feature view already exists and cannot be overwritten: {view.key}")

        await self.repository.upsert_feature_view(view)
        for feature_key in view.features:
            await self.add_lineage(
                LineageEdge(
                    edge_id=str(uuid.uuid4()),
                    from_type=RegistryObjectType.FEATURE,
                    from_key=feature_key,
                    to_type=RegistryObjectType.FEATURE_VIEW,
                    to_key=view.key,
                    relation=LineageRelationType.SERVES,
                ),
                actor_id=actor_id,
            )
        await self._record_change(RegistryObjectType.FEATURE_VIEW, view.key, ChangeType.CREATE if before is None else ChangeType.UPDATE, actor_id, before, view)
        self.metrics.increment("feature_registry.view.registered", tags={"status": view.status.value})
        self.metrics.timing("feature_registry.operation_ms", (time.perf_counter() - started) * 1000, tags={"operation": "register_feature_view"})
        return view

    async def set_feature_status(self, name: str, version: str, status: RegistryStatus, actor_id: Optional[str] = None) -> FeatureDefinition:
        feature = await self.repository.get_feature(name, version)
        if not feature:
            raise FeatureRegistryNotFoundError(f"Feature not found: {name}:{version}")
        self._validate_transition(feature.status, status)
        updated = _with_updated_at(feature, status=status)
        await self.repository.upsert_feature(updated)
        await self._record_change(RegistryObjectType.FEATURE, updated.key, ChangeType.STATUS_CHANGE, actor_id, feature, updated)
        return updated

    async def set_feature_view_status(self, name: str, version: str, status: RegistryStatus, actor_id: Optional[str] = None) -> FeatureViewDefinition:
        view = await self.repository.get_feature_view(name, version)
        if not view:
            raise FeatureRegistryNotFoundError(f"Feature view not found: {name}:{version}")
        self._validate_transition(view.status, status)
        updated = _with_updated_at(view, status=status)
        await self.repository.upsert_feature_view(updated)
        await self._record_change(RegistryObjectType.FEATURE_VIEW, updated.key, ChangeType.STATUS_CHANGE, actor_id, view, updated)
        return updated

    async def get_feature(self, name: str, version: str = "latest") -> FeatureDefinition:
        if version == "latest":
            rows = await self.repository.list_features(name)
            rows = [row for row in rows if row.status != RegistryStatus.DELETED]
            if not rows:
                raise FeatureRegistryNotFoundError(f"Feature not found: {name}")
            return sorted(rows, key=lambda f: f.created_at)[-1]
        feature = await self.repository.get_feature(name, version)
        if not feature:
            raise FeatureRegistryNotFoundError(f"Feature not found: {name}:{version}")
        return feature

    async def get_feature_view(self, name: str, version: str = "latest") -> FeatureViewDefinition:
        if version == "latest":
            rows = await self.repository.list_feature_views(name)
            rows = [row for row in rows if row.status != RegistryStatus.DELETED]
            if not rows:
                raise FeatureRegistryNotFoundError(f"Feature view not found: {name}")
            return sorted(rows, key=lambda v: v.created_at)[-1]
        view = await self.repository.get_feature_view(name, version)
        if not view:
            raise FeatureRegistryNotFoundError(f"Feature view not found: {name}:{version}")
        return view

    async def search(self, query: RegistrySearchQuery) -> Sequence[RegistrySearchResult]:
        limit = min(query.limit, self.config.max_search_results)
        results: List[RegistrySearchResult] = []
        object_types = set(query.object_types or list(RegistryObjectType))

        if RegistryObjectType.ENTITY in object_types:
            for entity in await self.repository.list_entities():
                result = self._match_entity(entity, query)
                if result:
                    results.append(result)
        if RegistryObjectType.SOURCE in object_types:
            for source in await self.repository.list_sources():
                result = self._match_source(source, query)
                if result:
                    results.append(result)
        if RegistryObjectType.FEATURE in object_types:
            for feature in await self.repository.list_features():
                result = self._match_feature(feature, query)
                if result:
                    results.append(result)
        if RegistryObjectType.FEATURE_VIEW in object_types:
            for view in await self.repository.list_feature_views():
                result = self._match_feature_view(view, query)
                if result:
                    results.append(result)

        return tuple(sorted(results, key=lambda r: r.score, reverse=True)[:limit])

    async def add_lineage(self, edge: LineageEdge, actor_id: Optional[str] = None) -> LineageEdge:
        edge.validate()
        await self.repository.add_lineage_edge(edge)
        await self._record_change(RegistryObjectType.PIPELINE, edge.edge_id, ChangeType.CREATE, actor_id, None, edge, message="lineage edge added")
        return edge

    async def lineage(self, key: str) -> Sequence[LineageEdge]:
        return await self.repository.list_lineage_edges(key)

    async def audit_history(self, object_key: Optional[str] = None, limit: int = 100) -> Sequence[RegistryAuditEvent]:
        return await self.repository.list_audit_events(object_key, limit)

    async def export_snapshot(self, actor_id: Optional[str] = None) -> RegistrySnapshot:
        snapshot = RegistrySnapshot(
            snapshot_id=str(uuid.uuid4()),
            entities=await self.repository.list_entities(),
            features=await self.repository.list_features(),
            feature_views=await self.repository.list_feature_views(),
            sources=await self.repository.list_sources(),
            lineage_edges=await self.repository.list_lineage_edges(),
            metadata={"exported_by_hash": _hash_value(actor_id, self.config.privacy_hash_salt)},
        )
        await self._record_change(RegistryObjectType.PIPELINE, snapshot.snapshot_id, ChangeType.EXPORT, actor_id, None, snapshot, message="registry snapshot exported")
        return snapshot

    async def import_snapshot(self, snapshot: RegistrySnapshot, actor_id: Optional[str] = None) -> None:
        for entity in snapshot.entities:
            await self.repository.upsert_entity(entity)
        for source in snapshot.sources:
            await self.repository.upsert_source(source)
        for feature in snapshot.features:
            await self.repository.upsert_feature(feature)
        for view in snapshot.feature_views:
            await self.repository.upsert_feature_view(view)
        for edge in snapshot.lineage_edges:
            await self.repository.add_lineage_edge(edge)
        await self._record_change(RegistryObjectType.PIPELINE, snapshot.snapshot_id, ChangeType.IMPORT, actor_id, None, snapshot, message="registry snapshot imported")

    async def _ensure_entities_exist(self, entity_names: Sequence[str]) -> None:
        for name in entity_names:
            if not await self.repository.get_entity(name):
                await self.repository.upsert_entity(EntityDefinition(name=name, status=RegistryStatus.ACTIVE))

    async def _check_compatibility(self, candidate: FeatureDefinition) -> None:
        previous_versions = [f for f in await self.repository.list_features(candidate.name) if f.version != candidate.version]
        if not previous_versions:
            return
        previous = sorted(previous_versions, key=lambda f: f.created_at)[-1]
        ok, issues = self.compatibility_checker.check(previous, candidate)
        if not ok and candidate.compatibility_level != CompatibilityLevel.NONE:
            raise FeatureRegistryConflictError(
                f"Feature schema is not compatible with previous version {previous.version}: {'; '.join(issues)}"
            )

    def _validate_transition(self, current: RegistryStatus, target: RegistryStatus) -> None:
        allowed = {
            RegistryStatus.DRAFT: {RegistryStatus.ACTIVE, RegistryStatus.ARCHIVED, RegistryStatus.DELETED},
            RegistryStatus.ACTIVE: {RegistryStatus.DEPRECATED, RegistryStatus.ARCHIVED, RegistryStatus.DELETED},
            RegistryStatus.DEPRECATED: {RegistryStatus.ACTIVE, RegistryStatus.ARCHIVED, RegistryStatus.DELETED},
            RegistryStatus.ARCHIVED: {RegistryStatus.DELETED},
            RegistryStatus.DELETED: set(),
        }
        if current == target:
            return
        if target not in allowed[current]:
            raise FeatureRegistryLifecycleError(f"Invalid status transition: {current.value} -> {target.value}")

    def _split_key(self, key: str) -> Tuple[str, str]:
        if ":" not in key:
            raise FeatureRegistryValidationError(f"Expected key format name:version, got: {key}")
        name, version = key.split(":", 1)
        return name, version

    def _match_entity(self, entity: EntityDefinition, query: RegistrySearchQuery) -> Optional[RegistrySearchResult]:
        if not self._base_match(entity.name, None, entity.owner, entity.status, entity.tags, query):
            return None
        return RegistrySearchResult(RegistryObjectType.ENTITY, entity.key, entity.name, None, entity.status, entity.owner, entity.description, entity.tags, self._score(entity.name, entity.description, query.text))

    def _match_source(self, source: DataSourceDefinition, query: RegistrySearchQuery) -> Optional[RegistrySearchResult]:
        if not self._base_match(source.name, None, source.owner, source.status, source.tags, query):
            return None
        return RegistrySearchResult(RegistryObjectType.SOURCE, source.key, source.name, None, source.status, source.owner, source.description, source.tags, self._score(source.name, source.description, query.text), {"source_type": source.source_type})

    def _match_feature(self, feature: FeatureDefinition, query: RegistrySearchQuery) -> Optional[RegistrySearchResult]:
        if not self._base_match(feature.name, feature.version, feature.owner, feature.status, feature.tags, query):
            return None
        if query.entity and query.entity not in feature.entities:
            return None
        if query.source and query.source != feature.source:
            return None
        return RegistrySearchResult(RegistryObjectType.FEATURE, feature.key, feature.name, feature.version, feature.status, feature.owner, feature.description, feature.tags, self._score(feature.name, feature.description, query.text), {"entities": list(feature.entities), "value_type": feature.schema.value_type.value})

    def _match_feature_view(self, view: FeatureViewDefinition, query: RegistrySearchQuery) -> Optional[RegistrySearchResult]:
        if not self._base_match(view.name, view.version, view.owner, view.status, view.tags, query):
            return None
        if query.entity and query.entity not in view.entities:
            return None
        return RegistrySearchResult(RegistryObjectType.FEATURE_VIEW, view.key, view.name, view.version, view.status, view.owner, view.description, view.tags, self._score(view.name, view.description, query.text), {"entities": list(view.entities), "features": list(view.features)})

    def _base_match(
        self,
        name: str,
        version: Optional[str],
        owner: Optional[str],
        status: RegistryStatus,
        tags: Mapping[str, str],
        query: RegistrySearchQuery,
    ) -> bool:
        if query.status and status != query.status:
            return False
        if query.owner and owner != query.owner:
            return False
        for key, value in query.tags.items():
            if tags.get(key) != value:
                return False
        if query.text:
            text = query.text.lower()
            if text not in name.lower() and (version is None or text not in version.lower()):
                return False
        return True

    def _score(self, name: str, description: str, text: Optional[str]) -> float:
        if not text:
            return 1.0
        text = text.lower()
        score = 0.0
        if name.lower() == text:
            score += 10.0
        if name.lower().startswith(text):
            score += 5.0
        if text in name.lower():
            score += 3.0
        if text in description.lower():
            score += 1.0
        return score

    async def _record_change(
        self,
        object_type: RegistryObjectType,
        object_key: str,
        change_type: ChangeType,
        actor_id: Optional[str],
        before: Any,
        after: Any,
        message: Optional[str] = None,
    ) -> None:
        event = RegistryAuditEvent(
            event_id=str(uuid.uuid4()),
            object_type=object_type,
            object_key=object_key,
            change_type=change_type,
            actor_id=_hash_value(actor_id, self.config.privacy_hash_salt),
            before_hash=_object_hash(before) if before is not None else None,
            after_hash=_object_hash(after) if after is not None else None,
            message=message or f"{change_type.value} {object_type.value} {object_key}",
        )
        if self.config.audit_enabled:
            await self.repository.add_audit_event(event)
            try:
                await self.audit_sink.write("feature_registry.change", self._audit_event_payload(event))
            except Exception:
                logger.exception("Failed to write external feature registry audit event")

    def _audit_event_payload(self, event: RegistryAuditEvent) -> JsonDict:
        payload = asdict(event)
        payload["object_type"] = event.object_type.value
        payload["change_type"] = event.change_type.value
        payload["created_at"] = event.created_at.isoformat()
        return payload


# =============================================================================
# Factory
# =============================================================================


def build_feature_registry(
    repository: Optional[FeatureRegistryRepository] = None,
    config: Optional[FeatureRegistryConfig] = None,
    metrics: Optional[MetricsClient] = None,
    audit_sink: Optional[AuditSink] = None,
) -> FeatureRegistry:
    return FeatureRegistry(repository=repository, config=config, metrics=metrics, audit_sink=audit_sink)


# =============================================================================
# Manual smoke test
# =============================================================================


async def _demo() -> None:
    logging.basicConfig(level=logging.INFO)
    registry = build_feature_registry()

    await registry.register_entity(EntityDefinition(name="customer_id", value_type=FeatureValueType.STRING, owner="risk-team"))
    await registry.register_source(DataSourceDefinition(name="transactions", source_type="table", uri="public.transactions", owner="data-team"))

    feature = await registry.register_feature(
        FeatureDefinition(
            name="customer_transaction_count_7d",
            version="v1",
            entities=("customer_id",),
            schema=FeatureSchema(name="txn_count_7d", value_type=FeatureValueType.INT, nullable=False, min_value=0),
            description="Number of customer transactions in the last 7 days.",
            owner="risk-team",
            status=RegistryStatus.ACTIVE,
            source="transactions",
            tags={"domain": "risk", "window": "7d"},
        ),
        actor_id="admin-1",
    )

    view = await registry.register_feature_view(
        FeatureViewDefinition(
            name="customer_risk_view",
            version="v1",
            features=(feature.key,),
            entities=("customer_id",),
            description="Customer risk serving view.",
            owner="risk-team",
            status=RegistryStatus.ACTIVE,
            tags={"domain": "risk"},
        ),
        actor_id="admin-1",
    )

    results = await registry.search(RegistrySearchQuery(text="customer", tags={"domain": "risk"}))
    print(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False, default=str))
    snapshot = await registry.export_snapshot(actor_id="admin-1")
    print(json.dumps(snapshot.to_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(_demo())
