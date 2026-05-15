"""
metadata_catalog.py
===================

Enterprise-grade metadata catalog module for data governance platforms.

Core capabilities
-----------------
- Catalog assets: datasets, tables, views, files, streams, dashboards, reports, models and APIs.
- Technical metadata: schemas, columns, data types, partitions, storage, freshness and statistics.
- Business metadata: owners, stewards, domains, glossary terms, descriptions and certifications.
- Governance metadata: classifications, sensitivity, tags, policies, controls and retention hints.
- Search, filters, faceted discovery and relationship graph references.
- Versioned metadata changes with audit events and immutable change records.
- Dataset certification, deprecation and lifecycle state management.
- Import/export helpers for JSON and JSONL.
- Pluggable repository architecture with in-memory default.

This module is vendor-neutral and dependency-light. It can integrate with DataHub,
OpenMetadata, Apache Atlas, Collibra, dbt artifacts, lakehouse catalogs, BI tools,
lineage trackers and compliance engines.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class MetadataCatalogError(Exception):
    """Base exception for metadata catalog failures."""


class AssetNotFoundError(MetadataCatalogError):
    """Raised when a catalog asset cannot be found."""


class MetadataValidationError(MetadataCatalogError):
    """Raised when metadata is invalid."""


class AssetType(str, enum.Enum):
    DATASET = "dataset"
    TABLE = "table"
    VIEW = "view"
    FILE = "file"
    STREAM = "stream"
    TOPIC = "topic"
    DASHBOARD = "dashboard"
    REPORT = "report"
    MODEL = "model"
    FEATURE_SET = "feature_set"
    API = "api"
    SERVICE = "service"
    PIPELINE = "pipeline"
    JOB = "job"
    UNKNOWN = "unknown"


class AssetLifecycleState(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CERTIFIED = "certified"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    DELETED = "deleted"


class SensitivityLevel(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    HIGHLY_RESTRICTED = "highly_restricted"


class CertificationStatus(str, enum.Enum):
    NONE = "none"
    REQUESTED = "requested"
    CERTIFIED = "certified"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RelationshipType(str, enum.Enum):
    OWNED_BY = "owned_by"
    STEWARDED_BY = "stewarded_by"
    CONTAINS = "contains"
    PART_OF = "part_of"
    DERIVES_FROM = "derives_from"
    PRODUCED_BY = "produced_by"
    CONSUMED_BY = "consumed_by"
    DOCUMENTED_BY = "documented_by"
    GOVERNED_BY = "governed_by"
    CERTIFIED_BY = "certified_by"
    RELATED_TO = "related_to"


class SearchSort(str, enum.Enum):
    RELEVANCE = "relevance"
    NAME = "name"
    UPDATED_AT = "updated_at"
    CREATED_AT = "created_at"
    QUALITY_SCORE = "quality_score"
    POPULARITY = "popularity"


class ExportFormat(str, enum.Enum):
    JSON = "json"
    JSONL = "jsonl"


@dataclass(frozen=True)
class CatalogOwner:
    owner_id: str
    display_name: str
    email: Optional[str] = None
    owner_type: str = "user"
    department: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class SchemaField:
    name: str
    data_type: str
    nullable: bool = True
    description: str = ""
    ordinal: Optional[int] = None
    default: Optional[Any] = None
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    glossary_terms: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    statistics: JsonDict = field(default_factory=dict)
    constraints: JsonDict = field(default_factory=dict)
    attributes: JsonDict = field(default_factory=dict)

    def fingerprint(self) -> str:
        payload = {
            "name": self.name,
            "data_type": self.data_type,
            "nullable": self.nullable,
            "ordinal": self.ordinal,
            "classifications": self.classifications,
            "sensitivity": self.sensitivity.value,
            "constraints": self.constraints,
        }
        return stable_hash(payload)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class AssetSchema:
    fields: Tuple[SchemaField, ...] = field(default_factory=tuple)
    schema_version: str = "1.0"
    schema_hash: Optional[str] = None
    raw_schema: Optional[JsonDict] = None
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def computed_hash(self) -> str:
        return stable_hash([field.fingerprint() for field in self.fields])

    def with_hash(self) -> "AssetSchema":
        return dataclasses.replace(self, schema_hash=self.computed_hash())

    def field_names(self) -> List[str]:
        return [field.name for field in self.fields]

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class StorageDescriptor:
    platform: str
    location: Optional[str] = None
    database: Optional[str] = None
    schema_name: Optional[str] = None
    table_name: Optional[str] = None
    format: Optional[str] = None
    partition_keys: Tuple[str, ...] = field(default_factory=tuple)
    size_bytes: Optional[int] = None
    row_count: Optional[int] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class QualitySummary:
    score: Optional[float] = None
    freshness_at: Optional[dt.datetime] = None
    completeness: Optional[float] = None
    validity: Optional[float] = None
    uniqueness: Optional[float] = None
    consistency: Optional[float] = None
    open_issues: int = 0
    sla_status: Optional[str] = None
    attributes: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class CertificationRecord:
    status: CertificationStatus = CertificationStatus.NONE
    certified_by: Optional[str] = None
    certified_at: Optional[dt.datetime] = None
    expires_at: Optional[dt.datetime] = None
    reason: Optional[str] = None
    evidence_ids: Tuple[str, ...] = field(default_factory=tuple)

    def is_expired(self, as_of: Optional[dt.datetime] = None) -> bool:
        as_of = as_of or dt.datetime.now(dt.timezone.utc)
        return bool(self.expires_at and self.expires_at < as_of)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class GlossaryTerm:
    term_id: str
    name: str
    definition: str
    domain: Optional[str] = None
    owner_id: Optional[str] = None
    synonyms: Tuple[str, ...] = field(default_factory=tuple)
    related_terms: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class CatalogRelationship:
    relationship_id: str
    source_asset_id: str
    target_asset_id: str
    relationship_type: RelationshipType
    confidence: float = 1.0
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    created_by: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class CatalogAsset:
    asset_id: str
    name: str
    asset_type: AssetType
    qualified_name: Optional[str] = None
    display_name: Optional[str] = None
    description: str = ""
    domain: Optional[str] = None
    tenant_id: Optional[str] = None
    lifecycle_state: AssetLifecycleState = AssetLifecycleState.ACTIVE
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    glossary_terms: Tuple[str, ...] = field(default_factory=tuple)
    owner_ids: Tuple[str, ...] = field(default_factory=tuple)
    steward_ids: Tuple[str, ...] = field(default_factory=tuple)
    storage: Optional[StorageDescriptor] = None
    schema: Optional[AssetSchema] = None
    quality: QualitySummary = field(default_factory=QualitySummary)
    certification: CertificationRecord = field(default_factory=CertificationRecord)
    lineage_node_id: Optional[str] = None
    policy_ids: Tuple[str, ...] = field(default_factory=tuple)
    control_ids: Tuple[str, ...] = field(default_factory=tuple)
    popularity_score: float = 0.0
    version: int = 1
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    metadata: JsonDict = field(default_factory=dict)

    def identity(self) -> str:
        return self.qualified_name or f"{self.tenant_id or 'global'}:{self.asset_type.value}:{self.name}"

    def with_updated_timestamp(self) -> "CatalogAsset":
        return dataclasses.replace(self, updated_at=dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class MetadataChangeRecord:
    change_id: str
    asset_id: str
    actor_id: Optional[str]
    changed_at: dt.datetime
    change_type: str
    before_hash: Optional[str]
    after_hash: Optional[str]
    before: Optional[JsonDict] = None
    after: Optional[JsonDict] = None
    reason: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class CatalogQuery:
    text: Optional[str] = None
    asset_ids: Tuple[str, ...] = field(default_factory=tuple)
    asset_types: Tuple[AssetType, ...] = field(default_factory=tuple)
    domains: Tuple[str, ...] = field(default_factory=tuple)
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    steward_id: Optional[str] = None
    lifecycle_states: Tuple[AssetLifecycleState, ...] = field(default_factory=tuple)
    sensitivities: Tuple[SensitivityLevel, ...] = field(default_factory=tuple)
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    glossary_terms: Tuple[str, ...] = field(default_factory=tuple)
    certification_statuses: Tuple[CertificationStatus, ...] = field(default_factory=tuple)
    updated_after: Optional[dt.datetime] = None
    updated_before: Optional[dt.datetime] = None
    sort: SearchSort = SearchSort.RELEVANCE
    limit: int = 100
    offset: int = 0


@dataclass
class CatalogSearchResult:
    assets: List[CatalogAsset]
    total: int
    facets: JsonDict
    query: CatalogQuery
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "total": self.total,
            "facets": self.facets,
            "generated_at": self.generated_at.isoformat(),
            "assets": [asset.to_dict() for asset in self.assets],
        }


@dataclass
class CatalogHealthReport:
    asset_count: int
    assets_by_type: JsonDict
    assets_by_domain: JsonDict
    assets_by_state: JsonDict
    unowned_assets: List[str]
    undocumented_assets: List[str]
    stale_assets: List[str]
    uncertified_sensitive_assets: List[str]
    duplicate_qualified_names: List[str]
    score: float
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@runtime_checkable
class MetadataCatalogRepository(Protocol):
    def upsert_asset(self, asset: CatalogAsset) -> None:
        ...

    def get_asset(self, asset_id: str) -> Optional[CatalogAsset]:
        ...

    def delete_asset(self, asset_id: str) -> bool:
        ...

    def list_assets(self) -> List[CatalogAsset]:
        ...

    def upsert_owner(self, owner: CatalogOwner) -> None:
        ...

    def get_owner(self, owner_id: str) -> Optional[CatalogOwner]:
        ...

    def upsert_glossary_term(self, term: GlossaryTerm) -> None:
        ...

    def get_glossary_term(self, term_id: str) -> Optional[GlossaryTerm]:
        ...

    def upsert_relationship(self, relationship: CatalogRelationship) -> None:
        ...

    def list_relationships(self, asset_id: Optional[str] = None) -> List[CatalogRelationship]:
        ...

    def add_change(self, change: MetadataChangeRecord) -> None:
        ...

    def list_changes(self, asset_id: Optional[str] = None) -> List[MetadataChangeRecord]:
        ...


class InMemoryMetadataCatalogRepository(MetadataCatalogRepository):
    """In-memory catalog repository for tests, local mode and fallback use."""

    def __init__(self) -> None:
        self.assets: Dict[str, CatalogAsset] = {}
        self.owners: Dict[str, CatalogOwner] = {}
        self.terms: Dict[str, GlossaryTerm] = {}
        self.relationships: Dict[str, CatalogRelationship] = {}
        self.changes: Dict[str, MetadataChangeRecord] = {}

    def upsert_asset(self, asset: CatalogAsset) -> None:
        self.assets[asset.asset_id] = asset

    def get_asset(self, asset_id: str) -> Optional[CatalogAsset]:
        return self.assets.get(asset_id)

    def delete_asset(self, asset_id: str) -> bool:
        return self.assets.pop(asset_id, None) is not None

    def list_assets(self) -> List[CatalogAsset]:
        return list(self.assets.values())

    def upsert_owner(self, owner: CatalogOwner) -> None:
        self.owners[owner.owner_id] = owner

    def get_owner(self, owner_id: str) -> Optional[CatalogOwner]:
        return self.owners.get(owner_id)

    def upsert_glossary_term(self, term: GlossaryTerm) -> None:
        self.terms[term.term_id] = term

    def get_glossary_term(self, term_id: str) -> Optional[GlossaryTerm]:
        return self.terms.get(term_id)

    def upsert_relationship(self, relationship: CatalogRelationship) -> None:
        self.relationships[relationship.relationship_id] = relationship

    def list_relationships(self, asset_id: Optional[str] = None) -> List[CatalogRelationship]:
        values = list(self.relationships.values())
        if asset_id:
            values = [rel for rel in values if rel.source_asset_id == asset_id or rel.target_asset_id == asset_id]
        return sorted(values, key=lambda rel: rel.created_at)

    def add_change(self, change: MetadataChangeRecord) -> None:
        self.changes[change.change_id] = change

    def list_changes(self, asset_id: Optional[str] = None) -> List[MetadataChangeRecord]:
        values = list(self.changes.values())
        if asset_id:
            values = [change for change in values if change.asset_id == asset_id]
        return sorted(values, key=lambda change: change.changed_at)


@runtime_checkable
class CatalogAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingCatalogAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("metadata_catalog_audit", extra={"event_type": event_type, "payload": dict(payload)})


class MetadataCatalog:
    """Main enterprise metadata catalog service."""

    def __init__(
        self,
        repository: Optional[MetadataCatalogRepository] = None,
        *,
        audit_sink: Optional[CatalogAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryMetadataCatalogRepository()
        self.audit = audit_sink or LoggingCatalogAuditSink()
        self.log = log or logger

    def register_asset(self, asset: CatalogAsset, *, actor_id: Optional[str] = None, reason: Optional[str] = None) -> CatalogAsset:
        self._validate_asset(asset)
        existing = self.repository.get_asset(asset.asset_id)
        normalized = self._normalize_asset(asset, existing)
        self.repository.upsert_asset(normalized)
        self._record_change(
            asset_id=normalized.asset_id,
            actor_id=actor_id,
            change_type="asset_registered" if existing is None else "asset_updated",
            before=existing.to_dict() if existing else None,
            after=normalized.to_dict(),
            reason=reason,
        )
        self.audit.emit("asset_registered" if existing is None else "asset_updated", normalized.to_dict())
        return normalized

    def get_asset(self, asset_id: str) -> CatalogAsset:
        asset = self.repository.get_asset(asset_id)
        if not asset:
            raise AssetNotFoundError(f"Asset not found: {asset_id}")
        return asset

    def update_asset_metadata(self, asset_id: str, *, actor_id: Optional[str] = None, reason: Optional[str] = None, **updates: Any) -> CatalogAsset:
        asset = self.get_asset(asset_id)
        before = asset.to_dict()
        allowed = {field.name for field in dataclasses.fields(CatalogAsset)}
        invalid = set(updates) - allowed
        if invalid:
            raise MetadataValidationError(f"Invalid asset fields: {sorted(invalid)}")
        updated = dataclasses.replace(asset, **updates, version=asset.version + 1, updated_at=dt.datetime.now(dt.timezone.utc))
        self.repository.upsert_asset(updated)
        self._record_change(asset_id=asset_id, actor_id=actor_id, change_type="metadata_updated", before=before, after=updated.to_dict(), reason=reason)
        self.audit.emit("metadata_updated", {"asset_id": asset_id, "updates": to_json_safe(updates)})
        return updated

    def update_schema(self, asset_id: str, schema: AssetSchema, *, actor_id: Optional[str] = None, reason: Optional[str] = None) -> CatalogAsset:
        schema = schema.with_hash()
        return self.update_asset_metadata(asset_id, actor_id=actor_id, reason=reason, schema=schema)

    def certify_asset(
        self,
        asset_id: str,
        *,
        certified_by: str,
        expires_at: Optional[dt.datetime] = None,
        reason: Optional[str] = None,
        evidence_ids: Sequence[str] = (),
    ) -> CatalogAsset:
        certification = CertificationRecord(
            status=CertificationStatus.CERTIFIED,
            certified_by=certified_by,
            certified_at=dt.datetime.now(dt.timezone.utc),
            expires_at=expires_at,
            reason=reason,
            evidence_ids=tuple(evidence_ids),
        )
        asset = self.update_asset_metadata(
            asset_id,
            actor_id=certified_by,
            reason=reason,
            certification=certification,
            lifecycle_state=AssetLifecycleState.CERTIFIED,
        )
        self.audit.emit("asset_certified", {"asset_id": asset_id, "certification": certification.to_dict()})
        return asset

    def deprecate_asset(self, asset_id: str, *, actor_id: str, reason: str) -> CatalogAsset:
        asset = self.update_asset_metadata(asset_id, actor_id=actor_id, reason=reason, lifecycle_state=AssetLifecycleState.DEPRECATED)
        self.audit.emit("asset_deprecated", {"asset_id": asset_id, "reason": reason, "actor_id": actor_id})
        return asset

    def register_owner(self, owner: CatalogOwner) -> CatalogOwner:
        self.repository.upsert_owner(owner)
        self.audit.emit("owner_registered", owner.to_dict())
        return owner

    def register_glossary_term(self, term: GlossaryTerm) -> GlossaryTerm:
        self.repository.upsert_glossary_term(dataclasses.replace(term, updated_at=dt.datetime.now(dt.timezone.utc)))
        self.audit.emit("glossary_term_registered", term.to_dict())
        return term

    def relate_assets(
        self,
        source_asset_id: str,
        target_asset_id: str,
        relationship_type: RelationshipType,
        *,
        created_by: Optional[str] = None,
        confidence: float = 1.0,
        metadata: Optional[JsonDict] = None,
    ) -> CatalogRelationship:
        self.get_asset(source_asset_id)
        self.get_asset(target_asset_id)
        relationship = CatalogRelationship(
            relationship_id=str(uuid.uuid4()),
            source_asset_id=source_asset_id,
            target_asset_id=target_asset_id,
            relationship_type=relationship_type,
            confidence=confidence,
            created_by=created_by,
            metadata=metadata or {},
        )
        self.repository.upsert_relationship(relationship)
        self.audit.emit("asset_relationship_registered", relationship.to_dict())
        return relationship

    def search(self, query: CatalogQuery) -> CatalogSearchResult:
        assets = [asset for asset in self.repository.list_assets() if self._matches(asset, query)]
        total = len(assets)
        assets = self._sort_assets(assets, query)
        sliced = assets[query.offset : query.offset + query.limit]
        return CatalogSearchResult(assets=sliced, total=total, facets=self._facets(assets), query=query)

    def browse_domain(self, domain: str, *, limit: int = 100) -> CatalogSearchResult:
        return self.search(CatalogQuery(domains=(domain,), limit=limit, sort=SearchSort.UPDATED_AT))

    def get_related_assets(self, asset_id: str, relationship_type: Optional[RelationshipType] = None) -> List[CatalogAsset]:
        relationships = self.repository.list_relationships(asset_id)
        if relationship_type:
            relationships = [rel for rel in relationships if rel.relationship_type == relationship_type]
        related_ids = []
        for rel in relationships:
            related_ids.append(rel.target_asset_id if rel.source_asset_id == asset_id else rel.source_asset_id)
        return [asset for asset_id_ in related_ids if (asset := self.repository.get_asset(asset_id_)) is not None]

    def change_history(self, asset_id: str) -> List[MetadataChangeRecord]:
        return self.repository.list_changes(asset_id)

    def health_report(self, *, stale_days: int = 30) -> CatalogHealthReport:
        assets = self.repository.list_assets()
        now = dt.datetime.now(dt.timezone.utc)
        unowned = [asset.asset_id for asset in assets if not asset.owner_ids]
        undocumented = [asset.asset_id for asset in assets if not asset.description.strip()]
        stale = [asset.asset_id for asset in assets if asset.updated_at < now - dt.timedelta(days=stale_days)]
        sensitive_uncertified = [
            asset.asset_id
            for asset in assets
            if asset.sensitivity in {SensitivityLevel.RESTRICTED, SensitivityLevel.HIGHLY_RESTRICTED}
            and asset.certification.status != CertificationStatus.CERTIFIED
        ]
        qnames = [asset.qualified_name for asset in assets if asset.qualified_name]
        duplicate_qnames = sorted([name for name, count in Counter(qnames).items() if count > 1])
        penalties = min(
            0.95,
            len(unowned) * 0.03
            + len(undocumented) * 0.02
            + len(stale) * 0.01
            + len(sensitive_uncertified) * 0.05
            + len(duplicate_qnames) * 0.10,
        )
        score = round(max(0.0, 1.0 - penalties), 6)
        return CatalogHealthReport(
            asset_count=len(assets),
            assets_by_type=dict(Counter(asset.asset_type.value for asset in assets)),
            assets_by_domain=dict(Counter(asset.domain or "unknown" for asset in assets)),
            assets_by_state=dict(Counter(asset.lifecycle_state.value for asset in assets)),
            unowned_assets=unowned,
            undocumented_assets=undocumented,
            stale_assets=stale,
            uncertified_sensitive_assets=sensitive_uncertified,
            duplicate_qualified_names=duplicate_qnames,
            score=score,
        )

    def export(self, *, fmt: ExportFormat = ExportFormat.JSON, query: Optional[CatalogQuery] = None) -> str:
        assets = self.search(query or CatalogQuery(limit=100000)).assets
        if fmt == ExportFormat.JSON:
            payload = {
                "assets": [asset.to_dict() for asset in assets],
                "owners": [to_json_safe(owner) for owner in getattr(self.repository, "owners", {}).values()],
                "glossary_terms": [to_json_safe(term) for term in getattr(self.repository, "terms", {}).values()],
                "relationships": [rel.to_dict() for rel in self.repository.list_relationships()],
            }
            return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if fmt == ExportFormat.JSONL:
            rows = []
            rows.extend(json.dumps({"type": "asset", "payload": asset.to_dict()}, ensure_ascii=False, default=str) for asset in assets)
            rows.extend(json.dumps({"type": "relationship", "payload": rel.to_dict()}, ensure_ascii=False, default=str) for rel in self.repository.list_relationships())
            return "\n".join(rows)
        raise MetadataCatalogError(f"Unsupported export format: {fmt}")

    def import_assets(self, payload: Union[str, Mapping[str, Any], Sequence[Mapping[str, Any]]], *, actor_id: Optional[str] = None) -> List[CatalogAsset]:
        data = json.loads(payload) if isinstance(payload, str) else payload
        if isinstance(data, Mapping) and "assets" in data:
            items = data["assets"]
        elif isinstance(data, Sequence):
            items = data
        else:
            raise MetadataValidationError("Import payload must be a list or object with 'assets'")
        imported = []
        for item in items:
            imported.append(self.register_asset(asset_from_dict(item), actor_id=actor_id, reason="import"))
        self.audit.emit("assets_imported", {"count": len(imported), "actor_id": actor_id})
        return imported

    def _validate_asset(self, asset: CatalogAsset) -> None:
        if not asset.asset_id:
            raise MetadataValidationError("asset_id is required")
        if not asset.name:
            raise MetadataValidationError("name is required")
        if asset.schema:
            field_names = asset.schema.field_names()
            duplicates = [name for name, count in Counter(field_names).items() if count > 1]
            if duplicates:
                raise MetadataValidationError(f"Duplicate schema field names: {duplicates}")

    def _normalize_asset(self, asset: CatalogAsset, existing: Optional[CatalogAsset]) -> CatalogAsset:
        schema = asset.schema.with_hash() if asset.schema else None
        version = (existing.version + 1) if existing else asset.version
        return dataclasses.replace(asset, schema=schema, version=version, updated_at=dt.datetime.now(dt.timezone.utc))

    def _record_change(
        self,
        *,
        asset_id: str,
        actor_id: Optional[str],
        change_type: str,
        before: Optional[JsonDict],
        after: Optional[JsonDict],
        reason: Optional[str],
    ) -> None:
        change = MetadataChangeRecord(
            change_id=str(uuid.uuid4()),
            asset_id=asset_id,
            actor_id=actor_id,
            changed_at=dt.datetime.now(dt.timezone.utc),
            change_type=change_type,
            before_hash=stable_hash(before) if before else None,
            after_hash=stable_hash(after) if after else None,
            before=before,
            after=after,
            reason=reason,
        )
        self.repository.add_change(change)

    def _matches(self, asset: CatalogAsset, query: CatalogQuery) -> bool:
        if query.asset_ids and asset.asset_id not in query.asset_ids:
            return False
        if query.asset_types and asset.asset_type not in query.asset_types:
            return False
        if query.domains and (asset.domain or "") not in query.domains:
            return False
        if query.tenant_id and asset.tenant_id != query.tenant_id:
            return False
        if query.owner_id and query.owner_id not in asset.owner_ids:
            return False
        if query.steward_id and query.steward_id not in asset.steward_ids:
            return False
        if query.lifecycle_states and asset.lifecycle_state not in query.lifecycle_states:
            return False
        if query.sensitivities and asset.sensitivity not in query.sensitivities:
            return False
        if query.classifications and not set(query.classifications).intersection(asset.classifications):
            return False
        if query.tags and not set(query.tags).issubset(asset.tags):
            return False
        if query.glossary_terms and not set(query.glossary_terms).intersection(asset.glossary_terms):
            return False
        if query.certification_statuses and asset.certification.status not in query.certification_statuses:
            return False
        if query.updated_after and asset.updated_at < query.updated_after:
            return False
        if query.updated_before and asset.updated_at > query.updated_before:
            return False
        if query.text and query.text.lower() not in searchable_text(asset):
            return False
        return True

    def _sort_assets(self, assets: List[CatalogAsset], query: CatalogQuery) -> List[CatalogAsset]:
        if query.sort == SearchSort.NAME:
            return sorted(assets, key=lambda asset: asset.display_name or asset.name)
        if query.sort == SearchSort.UPDATED_AT:
            return sorted(assets, key=lambda asset: asset.updated_at, reverse=True)
        if query.sort == SearchSort.CREATED_AT:
            return sorted(assets, key=lambda asset: asset.created_at, reverse=True)
        if query.sort == SearchSort.QUALITY_SCORE:
            return sorted(assets, key=lambda asset: asset.quality.score or 0.0, reverse=True)
        if query.sort == SearchSort.POPULARITY:
            return sorted(assets, key=lambda asset: asset.popularity_score, reverse=True)
        if query.text:
            return sorted(assets, key=lambda asset: relevance_score(asset, query.text or ""), reverse=True)
        return sorted(assets, key=lambda asset: asset.updated_at, reverse=True)

    @staticmethod
    def _facets(assets: Sequence[CatalogAsset]) -> JsonDict:
        return {
            "asset_types": dict(Counter(asset.asset_type.value for asset in assets)),
            "domains": dict(Counter(asset.domain or "unknown" for asset in assets)),
            "lifecycle_states": dict(Counter(asset.lifecycle_state.value for asset in assets)),
            "sensitivities": dict(Counter(asset.sensitivity.value for asset in assets)),
            "certifications": dict(Counter(asset.certification.status.value for asset in assets)),
            "classifications": dict(Counter(cls for asset in assets for cls in asset.classifications)),
            "tags": dict(Counter(tag for asset in assets for tag in asset.tags)),
        }


# -----------------------------------------------------------------------------
# Builders and conversion helpers
# -----------------------------------------------------------------------------


def build_asset(
    name: str,
    asset_type: AssetType,
    *,
    qualified_name: Optional[str] = None,
    tenant_id: Optional[str] = None,
    domain: Optional[str] = None,
    owner_ids: Sequence[str] = (),
    description: str = "",
    tags: Sequence[str] = (),
    classifications: Sequence[str] = (),
    sensitivity: SensitivityLevel = SensitivityLevel.INTERNAL,
    schema: Optional[AssetSchema] = None,
    storage: Optional[StorageDescriptor] = None,
    metadata: Optional[JsonDict] = None,
) -> CatalogAsset:
    identity = qualified_name or f"{tenant_id or 'global'}:{asset_type.value}:{name}"
    asset_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return CatalogAsset(
        asset_id=asset_id,
        name=name,
        asset_type=asset_type,
        qualified_name=qualified_name,
        description=description,
        domain=domain,
        tenant_id=tenant_id,
        owner_ids=tuple(owner_ids),
        tags=tuple(tags),
        classifications=tuple(classifications),
        sensitivity=sensitivity,
        schema=schema,
        storage=storage,
        metadata=metadata or {},
    )


def build_schema(fields: Sequence[Union[SchemaField, Mapping[str, Any]]], *, schema_version: str = "1.0") -> AssetSchema:
    schema_fields = tuple(field if isinstance(field, SchemaField) else schema_field_from_dict(field) for field in fields)
    return AssetSchema(fields=schema_fields, schema_version=schema_version).with_hash()


def schema_field_from_dict(data: Mapping[str, Any]) -> SchemaField:
    payload = dict(data)
    if "sensitivity" in payload and not isinstance(payload["sensitivity"], SensitivityLevel):
        payload["sensitivity"] = SensitivityLevel(payload["sensitivity"])
    for key in ("classifications", "glossary_terms", "tags"):
        if key in payload:
            payload[key] = tuple(payload[key])
    return SchemaField(**payload)


def asset_from_dict(data: Mapping[str, Any]) -> CatalogAsset:
    payload = dict(data)
    payload["asset_type"] = AssetType(payload["asset_type"])
    payload["lifecycle_state"] = AssetLifecycleState(payload.get("lifecycle_state", AssetLifecycleState.ACTIVE))
    payload["sensitivity"] = SensitivityLevel(payload.get("sensitivity", SensitivityLevel.INTERNAL))
    for key in ("classifications", "tags", "glossary_terms", "owner_ids", "steward_ids", "policy_ids", "control_ids"):
        if key in payload:
            payload[key] = tuple(payload[key])
    if payload.get("schema"):
        schema_payload = dict(payload["schema"])
        schema_payload["fields"] = tuple(schema_field_from_dict(field) for field in schema_payload.get("fields", []))
        if schema_payload.get("updated_at") and isinstance(schema_payload["updated_at"], str):
            schema_payload["updated_at"] = parse_datetime(schema_payload["updated_at"])
        payload["schema"] = AssetSchema(**schema_payload)
    if payload.get("storage"):
        storage_payload = dict(payload["storage"])
        if "partition_keys" in storage_payload:
            storage_payload["partition_keys"] = tuple(storage_payload["partition_keys"])
        payload["storage"] = StorageDescriptor(**storage_payload)
    if payload.get("quality"):
        quality_payload = dict(payload["quality"])
        if quality_payload.get("freshness_at") and isinstance(quality_payload["freshness_at"], str):
            quality_payload["freshness_at"] = parse_datetime(quality_payload["freshness_at"])
        payload["quality"] = QualitySummary(**quality_payload)
    if payload.get("certification"):
        cert_payload = dict(payload["certification"])
        cert_payload["status"] = CertificationStatus(cert_payload.get("status", CertificationStatus.NONE))
        for key in ("certified_at", "expires_at"):
            if cert_payload.get(key) and isinstance(cert_payload[key], str):
                cert_payload[key] = parse_datetime(cert_payload[key])
        if "evidence_ids" in cert_payload:
            cert_payload["evidence_ids"] = tuple(cert_payload["evidence_ids"])
        payload["certification"] = CertificationRecord(**cert_payload)
    for key in ("created_at", "updated_at"):
        if payload.get(key) and isinstance(payload[key], str):
            payload[key] = parse_datetime(payload[key])
    return CatalogAsset(**payload)


# -----------------------------------------------------------------------------
# Utility helpers
# -----------------------------------------------------------------------------


def searchable_text(asset: CatalogAsset) -> str:
    parts = [
        asset.asset_id,
        asset.name,
        asset.display_name or "",
        asset.qualified_name or "",
        asset.description,
        asset.domain or "",
        asset.asset_type.value,
        " ".join(asset.tags),
        " ".join(asset.classifications),
        " ".join(asset.glossary_terms),
    ]
    if asset.schema:
        for field in asset.schema.fields:
            parts.extend([field.name, field.data_type, field.description, " ".join(field.tags), " ".join(field.classifications)])
    return " ".join(parts).lower()


def relevance_score(asset: CatalogAsset, text: str) -> float:
    query = text.lower().strip()
    if not query:
        return 0.0
    score = 0.0
    if query == asset.name.lower():
        score += 100
    if query in asset.name.lower():
        score += 50
    if asset.display_name and query in asset.display_name.lower():
        score += 40
    if asset.qualified_name and query in asset.qualified_name.lower():
        score += 35
    if query in asset.description.lower():
        score += 20
    score += sum(10 for tag in asset.tags if query in tag.lower())
    score += sum(8 for term in asset.glossary_terms if query in term.lower())
    if asset.schema:
        score += sum(5 for field in asset.schema.fields if query in field.name.lower())
    score += asset.popularity_score * 0.1
    return score


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


def parse_datetime(value: Any) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_default_metadata_catalog() -> MetadataCatalog:
    catalog = MetadataCatalog()
    catalog.register_owner(CatalogOwner(owner_id="data-eng", display_name="Data Engineering", email="data-eng@example.com", owner_type="team"))
    catalog.register_owner(CatalogOwner(owner_id="analytics", display_name="Analytics", email="analytics@example.com", owner_type="team"))
    catalog.register_glossary_term(
        GlossaryTerm(
            term_id="term-sales",
            name="Sales",
            definition="Commercial transaction amount recognized by the business.",
            domain="retail",
            owner_id="analytics",
            tags=("finance", "retail"),
        )
    )
    return catalog


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    catalog = build_default_metadata_catalog()
    schema = build_schema(
        [
            {"name": "sale_id", "data_type": "string", "nullable": False, "tags": ["identifier"]},
            {"name": "customer_email", "data_type": "string", "classifications": ["pii"], "sensitivity": "confidential"},
            {"name": "amount", "data_type": "decimal(12,2)", "classifications": ["financial_sensitive"], "sensitivity": "restricted"},
            {"name": "sale_date", "data_type": "date"},
        ]
    )
    asset = build_asset(
        "sales_daily",
        AssetType.TABLE,
        qualified_name="warehouse.curated.sales_daily",
        domain="retail",
        owner_ids=("analytics",),
        description="Daily curated sales dataset.",
        tags=("gold", "sales"),
        classifications=("financial_sensitive",),
        sensitivity=SensitivityLevel.RESTRICTED,
        glossary_terms=("term-sales",),
        schema=schema,
        storage=StorageDescriptor(platform="postgres", database="warehouse", schema_name="curated", table_name="sales_daily"),
    )
    catalog.register_asset(asset, actor_id="system")
    catalog.certify_asset(asset.asset_id, certified_by="analytics", reason="Validated by data steward")

    result = catalog.search(CatalogQuery(text="sales", limit=10))
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    print(json.dumps(catalog.health_report().to_dict(), indent=2, ensure_ascii=False, default=str))
