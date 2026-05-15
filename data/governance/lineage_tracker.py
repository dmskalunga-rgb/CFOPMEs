"""
lineage_tracker.py
==================

Enterprise-grade lineage tracking module for data governance platforms.

Core capabilities
-----------------
- Dataset, column, job, pipeline, report and model lineage graph.
- Column-level lineage mappings with transformation metadata.
- Upstream/downstream impact analysis with configurable traversal depth.
- Lineage event ingestion from ETL/ELT jobs, dbt artifacts, Spark, Airflow,
  Dagster, APIs, SQL parsers, notebooks and ML pipelines.
- Snapshotting and versioned lineage history.
- Cycle detection, orphan detection and lineage quality scoring.
- Audit-friendly export to JSON, JSONL and DOT graph format.
- Pluggable repository architecture with in-memory default.

This module is vendor-neutral and dependency-light. It can feed catalogs,
compliance reports, incident impact analysis, data contracts and stewardship
workflows.
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
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class LineageTrackerError(Exception):
    """Base exception for lineage tracker failures."""


class LineageValidationError(LineageTrackerError):
    """Raised when lineage graph validation fails."""


class LineageNotFoundError(LineageTrackerError):
    """Raised when a requested lineage entity does not exist."""


class LineageNodeType(str, enum.Enum):
    DATASET = "dataset"
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"
    FILE = "file"
    STREAM = "stream"
    TOPIC = "topic"
    JOB = "job"
    PIPELINE = "pipeline"
    DASHBOARD = "dashboard"
    REPORT = "report"
    MODEL = "model"
    FEATURE = "feature"
    API = "api"
    SERVICE = "service"
    UNKNOWN = "unknown"


class LineageEdgeType(str, enum.Enum):
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    DERIVES_FROM = "derives_from"
    TRANSFORMS = "transforms"
    JOINS = "joins"
    AGGREGATES = "aggregates"
    FILTERS = "filters"
    MASKS = "masks"
    ENCRYPTS = "encrypts"
    VALIDATES = "validates"
    PUBLISHES_TO = "publishes_to"
    CONSUMES_FROM = "consumes_from"
    TRAINS_FROM = "trains_from"
    SERVES = "serves"
    DEPENDS_ON = "depends_on"


class TransformationType(str, enum.Enum):
    SELECT = "select"
    PROJECT = "project"
    FILTER = "filter"
    JOIN = "join"
    UNION = "union"
    AGGREGATE = "aggregate"
    WINDOW = "window"
    NORMALIZE = "normalize"
    MASK = "mask"
    ENCRYPT = "encrypt"
    HASH = "hash"
    CAST = "cast"
    RENAME = "rename"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class LineageEventType(str, enum.Enum):
    NODE_REGISTERED = "node_registered"
    EDGE_REGISTERED = "edge_registered"
    JOB_STARTED = "job_started"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    DATASET_CREATED = "dataset_created"
    DATASET_UPDATED = "dataset_updated"
    SCHEMA_CHANGED = "schema_changed"
    COLUMN_MAPPING_RECORDED = "column_mapping_recorded"
    SNAPSHOT_CREATED = "snapshot_created"


class LineageDirection(str, enum.Enum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class ExportFormat(str, enum.Enum):
    JSON = "json"
    JSONL = "jsonl"
    DOT = "dot"


@dataclass(frozen=True)
class LineageNode:
    node_id: str
    name: str
    node_type: LineageNodeType
    qualified_name: Optional[str] = None
    namespace: str = "default"
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    domain: Optional[str] = None
    description: str = ""
    version: Optional[str] = None
    schema_hash: Optional[str] = None
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    tags: Tuple[str, ...] = field(default_factory=tuple)
    attributes: JsonDict = field(default_factory=dict)
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    updated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def identity(self) -> str:
        return self.qualified_name or f"{self.namespace}:{self.node_type.value}:{self.name}"

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class ColumnLineageMapping:
    source_column_id: str
    target_column_id: str
    transformation_type: TransformationType = TransformationType.UNKNOWN
    expression: Optional[str] = None
    confidence: float = 1.0
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class LineageEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: LineageEdgeType
    transformation_type: TransformationType = TransformationType.UNKNOWN
    job_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    expression: Optional[str] = None
    column_mappings: Tuple[ColumnLineageMapping, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    observed_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    created_by: Optional[str] = None
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class LineageEvent:
    event_id: str
    event_type: LineageEventType
    timestamp: dt.datetime
    actor_id: Optional[str] = None
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    job_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message: str = ""
    payload: JsonDict = field(default_factory=dict)
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass(frozen=True)
class LineageSnapshot:
    snapshot_id: str
    created_at: dt.datetime
    node_count: int
    edge_count: int
    nodes_hash: str
    edges_hash: str
    created_by: Optional[str] = None
    description: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return to_json_safe(dataclasses.asdict(self))


@dataclass
class ImpactAnalysisResult:
    root_node_id: str
    direction: LineageDirection
    max_depth: int
    impacted_nodes: List[LineageNode]
    impacted_edges: List[LineageEdge]
    paths: List[List[str]]
    risk_score: float
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "root_node_id": self.root_node_id,
            "direction": self.direction.value,
            "max_depth": self.max_depth,
            "impacted_nodes": [node.to_dict() for node in self.impacted_nodes],
            "impacted_edges": [edge.to_dict() for edge in self.impacted_edges],
            "paths": [list(path) for path in self.paths],
            "risk_score": self.risk_score,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class LineageQualityReport:
    node_count: int
    edge_count: int
    orphan_nodes: List[str]
    cycles: List[List[str]]
    missing_owner_nodes: List[str]
    low_confidence_edges: List[str]
    column_lineage_coverage: float
    score: float
    generated_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "orphan_nodes": list(self.orphan_nodes),
            "cycles": [list(cycle) for cycle in self.cycles],
            "missing_owner_nodes": list(self.missing_owner_nodes),
            "low_confidence_edges": list(self.low_confidence_edges),
            "column_lineage_coverage": self.column_lineage_coverage,
            "score": self.score,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass(frozen=True)
class LineageQuery:
    node_ids: Tuple[str, ...] = field(default_factory=tuple)
    node_types: Tuple[LineageNodeType, ...] = field(default_factory=tuple)
    edge_types: Tuple[LineageEdgeType, ...] = field(default_factory=tuple)
    namespace: Optional[str] = None
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    owner_id: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    classifications: Tuple[str, ...] = field(default_factory=tuple)
    text: Optional[str] = None
    limit: int = 10000
    offset: int = 0


@runtime_checkable
class LineageRepository(Protocol):
    def upsert_node(self, node: LineageNode) -> None:
        ...

    def upsert_edge(self, edge: LineageEdge) -> None:
        ...

    def get_node(self, node_id: str) -> Optional[LineageNode]:
        ...

    def get_edge(self, edge_id: str) -> Optional[LineageEdge]:
        ...

    def list_nodes(self, query: Optional[LineageQuery] = None) -> List[LineageNode]:
        ...

    def list_edges(self, query: Optional[LineageQuery] = None) -> List[LineageEdge]:
        ...

    def add_event(self, event: LineageEvent) -> None:
        ...

    def list_events(self) -> List[LineageEvent]:
        ...

    def add_snapshot(self, snapshot: LineageSnapshot) -> None:
        ...


class InMemoryLineageRepository(LineageRepository):
    """In-memory lineage repository for tests, local mode and fallback use."""

    def __init__(self) -> None:
        self.nodes: Dict[str, LineageNode] = {}
        self.edges: Dict[str, LineageEdge] = {}
        self.events: Dict[str, LineageEvent] = {}
        self.snapshots: Dict[str, LineageSnapshot] = {}

    def upsert_node(self, node: LineageNode) -> None:
        self.nodes[node.node_id] = dataclasses.replace(node, updated_at=dt.datetime.now(dt.timezone.utc))

    def upsert_edge(self, edge: LineageEdge) -> None:
        self.edges[edge.edge_id] = edge

    def get_node(self, node_id: str) -> Optional[LineageNode]:
        return self.nodes.get(node_id)

    def get_edge(self, edge_id: str) -> Optional[LineageEdge]:
        return self.edges.get(edge_id)

    def list_nodes(self, query: Optional[LineageQuery] = None) -> List[LineageNode]:
        query = query or LineageQuery()
        matched = [node for node in self.nodes.values() if matches_node_query(node, query)]
        matched.sort(key=lambda node: node.qualified_name or node.name)
        return matched[query.offset : query.offset + query.limit]

    def list_edges(self, query: Optional[LineageQuery] = None) -> List[LineageEdge]:
        query = query or LineageQuery()
        matched = [edge for edge in self.edges.values() if matches_edge_query(edge, query, self.nodes)]
        matched.sort(key=lambda edge: edge.observed_at)
        return matched[query.offset : query.offset + query.limit]

    def add_event(self, event: LineageEvent) -> None:
        self.events[event.event_id] = event

    def list_events(self) -> List[LineageEvent]:
        return sorted(self.events.values(), key=lambda event: event.timestamp)

    def add_snapshot(self, snapshot: LineageSnapshot) -> None:
        self.snapshots[snapshot.snapshot_id] = snapshot


@runtime_checkable
class LineageAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingLineageAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("lineage_audit", extra={"event_type": event_type, "payload": dict(payload)})


class LineageTracker:
    """Main enterprise lineage tracker service."""

    def __init__(
        self,
        repository: Optional[LineageRepository] = None,
        *,
        audit_sink: Optional[LineageAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.repository = repository or InMemoryLineageRepository()
        self.audit = audit_sink or LoggingLineageAuditSink()
        self.log = log or logger

    def register_node(self, node: LineageNode, *, actor_id: Optional[str] = None, correlation_id: Optional[str] = None) -> LineageNode:
        self.repository.upsert_node(node)
        self._event(
            LineageEventType.NODE_REGISTERED,
            actor_id=actor_id,
            node_id=node.node_id,
            correlation_id=correlation_id,
            message=f"Lineage node registered: {node.name}",
            payload=node.to_dict(),
        )
        self.audit.emit("lineage_node_registered", node.to_dict())
        return node

    def register_edge(self, edge: LineageEdge, *, actor_id: Optional[str] = None, correlation_id: Optional[str] = None) -> LineageEdge:
        if not self.repository.get_node(edge.source_node_id):
            raise LineageValidationError(f"Source node not found: {edge.source_node_id}")
        if not self.repository.get_node(edge.target_node_id):
            raise LineageValidationError(f"Target node not found: {edge.target_node_id}")
        self.repository.upsert_edge(edge)
        self._event(
            LineageEventType.EDGE_REGISTERED,
            actor_id=actor_id,
            edge_id=edge.edge_id,
            job_id=edge.job_id,
            pipeline_id=edge.pipeline_id,
            correlation_id=correlation_id,
            message=f"Lineage edge registered: {edge.source_node_id} -> {edge.target_node_id}",
            payload=edge.to_dict(),
        )
        self.audit.emit("lineage_edge_registered", edge.to_dict())
        return edge

    def record_dataset_lineage(
        self,
        *,
        source: LineageNode,
        target: LineageNode,
        edge_type: LineageEdgeType = LineageEdgeType.DERIVES_FROM,
        transformation_type: TransformationType = TransformationType.UNKNOWN,
        job_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        expression: Optional[str] = None,
        column_mappings: Sequence[ColumnLineageMapping] = (),
        actor_id: Optional[str] = None,
        metadata: Optional[JsonDict] = None,
    ) -> LineageEdge:
        self.register_node(source, actor_id=actor_id)
        self.register_node(target, actor_id=actor_id)
        edge = LineageEdge(
            edge_id=str(uuid.uuid4()),
            source_node_id=source.node_id,
            target_node_id=target.node_id,
            edge_type=edge_type,
            transformation_type=transformation_type,
            job_id=job_id,
            pipeline_id=pipeline_id,
            expression=expression,
            column_mappings=tuple(column_mappings),
            created_by=actor_id,
            metadata=metadata or {},
        )
        return self.register_edge(edge, actor_id=actor_id)

    def record_column_mapping(
        self,
        mapping: ColumnLineageMapping,
        *,
        parent_edge_id: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> ColumnLineageMapping:
        if not self.repository.get_node(mapping.source_column_id):
            raise LineageValidationError(f"Source column node not found: {mapping.source_column_id}")
        if not self.repository.get_node(mapping.target_column_id):
            raise LineageValidationError(f"Target column node not found: {mapping.target_column_id}")
        self._event(
            LineageEventType.COLUMN_MAPPING_RECORDED,
            actor_id=actor_id,
            edge_id=parent_edge_id,
            message=f"Column mapping recorded: {mapping.source_column_id} -> {mapping.target_column_id}",
            payload=mapping.to_dict(),
        )
        return mapping

    def get_upstream(self, node_id: str, *, max_depth: int = 5) -> ImpactAnalysisResult:
        return self.analyze_impact(node_id, direction=LineageDirection.UPSTREAM, max_depth=max_depth)

    def get_downstream(self, node_id: str, *, max_depth: int = 5) -> ImpactAnalysisResult:
        return self.analyze_impact(node_id, direction=LineageDirection.DOWNSTREAM, max_depth=max_depth)

    def analyze_impact(self, node_id: str, *, direction: LineageDirection = LineageDirection.DOWNSTREAM, max_depth: int = 5) -> ImpactAnalysisResult:
        if not self.repository.get_node(node_id):
            raise LineageNotFoundError(f"Node not found: {node_id}")
        edges = self.repository.list_edges()
        adjacency = self._adjacency(edges, direction)
        visited_nodes: Set[str] = set()
        visited_edges: Set[str] = set()
        paths: List[List[str]] = []
        queue: Deque[Tuple[str, int, List[str]]] = deque([(node_id, 0, [node_id])])

        while queue:
            current, depth, path = queue.popleft()
            if depth >= max_depth:
                paths.append(path)
                continue
            next_edges = adjacency.get(current, [])
            if not next_edges:
                paths.append(path)
            for edge in next_edges:
                next_node = edge.source_node_id if direction == LineageDirection.UPSTREAM else edge.target_node_id
                if next_node in path:
                    continue
                visited_nodes.add(next_node)
                visited_edges.add(edge.edge_id)
                queue.append((next_node, depth + 1, path + [next_node]))

        impacted_nodes = [self.repository.get_node(nid) for nid in visited_nodes]
        impacted_edges = [self.repository.get_edge(eid) for eid in visited_edges]
        safe_nodes = [node for node in impacted_nodes if node is not None]
        safe_edges = [edge for edge in impacted_edges if edge is not None]
        risk_score = self._impact_risk_score(safe_nodes, safe_edges)
        return ImpactAnalysisResult(
            root_node_id=node_id,
            direction=direction,
            max_depth=max_depth,
            impacted_nodes=safe_nodes,
            impacted_edges=safe_edges,
            paths=paths,
            risk_score=risk_score,
        )

    def create_snapshot(self, *, created_by: Optional[str] = None, description: str = "", metadata: Optional[JsonDict] = None) -> LineageSnapshot:
        nodes = self.repository.list_nodes()
        edges = self.repository.list_edges()
        snapshot = LineageSnapshot(
            snapshot_id=str(uuid.uuid4()),
            created_at=dt.datetime.now(dt.timezone.utc),
            node_count=len(nodes),
            edge_count=len(edges),
            nodes_hash=stable_hash([node.to_dict() for node in nodes]),
            edges_hash=stable_hash([edge.to_dict() for edge in edges]),
            created_by=created_by,
            description=description,
            metadata=metadata or {},
        )
        self.repository.add_snapshot(snapshot)
        self._event(
            LineageEventType.SNAPSHOT_CREATED,
            actor_id=created_by,
            message="Lineage snapshot created",
            payload=snapshot.to_dict(),
        )
        self.audit.emit("lineage_snapshot_created", snapshot.to_dict())
        return snapshot

    def quality_report(self) -> LineageQualityReport:
        nodes = self.repository.list_nodes()
        edges = self.repository.list_edges()
        node_ids = {node.node_id for node in nodes}
        connected = {edge.source_node_id for edge in edges}.union({edge.target_node_id for edge in edges})
        orphan_nodes = sorted(node_ids - connected)
        cycles = self.detect_cycles()
        missing_owner = sorted(node.node_id for node in nodes if not node.owner_id and node.node_type in {LineageNodeType.DATASET, LineageNodeType.TABLE, LineageNodeType.VIEW})
        low_confidence = sorted(edge.edge_id for edge in edges if edge.confidence < 0.75)
        dataset_edges = [edge for edge in edges if edge.edge_type in {LineageEdgeType.DERIVES_FROM, LineageEdgeType.TRANSFORMS, LineageEdgeType.AGGREGATES, LineageEdgeType.JOINS}]
        with_column = [edge for edge in dataset_edges if edge.column_mappings]
        column_coverage = round(len(with_column) / len(dataset_edges), 6) if dataset_edges else 1.0
        penalties = min(0.90, len(orphan_nodes) * 0.02 + len(cycles) * 0.10 + len(missing_owner) * 0.03 + len(low_confidence) * 0.02 + (1 - column_coverage) * 0.20)
        score = round(max(0.0, 1.0 - penalties), 6)
        return LineageQualityReport(
            node_count=len(nodes),
            edge_count=len(edges),
            orphan_nodes=orphan_nodes,
            cycles=cycles,
            missing_owner_nodes=missing_owner,
            low_confidence_edges=low_confidence,
            column_lineage_coverage=column_coverage,
            score=score,
        )

    def detect_cycles(self) -> List[List[str]]:
        edges = self.repository.list_edges()
        adjacency: Dict[str, List[str]] = defaultdict(list)
        for edge in edges:
            adjacency[edge.source_node_id].append(edge.target_node_id)

        visited: Set[str] = set()
        stack: Set[str] = set()
        path: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            stack.add(node_id)
            path.append(node_id)
            for neighbor in adjacency.get(node_id, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in stack:
                    start = path.index(neighbor)
                    cycles.append(path[start:] + [neighbor])
            stack.remove(node_id)
            path.pop()

        for node in self.repository.list_nodes():
            if node.node_id not in visited:
                dfs(node.node_id)
        return unique_paths(cycles)

    def export(self, *, fmt: ExportFormat = ExportFormat.JSON, query: Optional[LineageQuery] = None) -> str:
        nodes = self.repository.list_nodes(query)
        edges = self.repository.list_edges(query)
        if fmt == ExportFormat.JSON:
            return json.dumps({"nodes": [n.to_dict() for n in nodes], "edges": [e.to_dict() for e in edges]}, indent=2, ensure_ascii=False, default=str)
        if fmt == ExportFormat.JSONL:
            rows = []
            rows.extend(json.dumps({"type": "node", "payload": n.to_dict()}, ensure_ascii=False, default=str) for n in nodes)
            rows.extend(json.dumps({"type": "edge", "payload": e.to_dict()}, ensure_ascii=False, default=str) for e in edges)
            return "\n".join(rows)
        if fmt == ExportFormat.DOT:
            return self._export_dot(nodes, edges)
        raise LineageTrackerError(f"Unsupported export format: {fmt}")

    def summary(self) -> JsonDict:
        nodes = self.repository.list_nodes()
        edges = self.repository.list_edges()
        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes_by_type": dict(Counter(node.node_type.value for node in nodes)),
            "edges_by_type": dict(Counter(edge.edge_type.value for edge in edges)),
            "domains": dict(Counter(node.domain or "unknown" for node in nodes)),
            "owners": dict(Counter(node.owner_id or "unassigned" for node in nodes)),
            "quality": self.quality_report().to_dict(),
        }

    def _adjacency(self, edges: Sequence[LineageEdge], direction: LineageDirection) -> Dict[str, List[LineageEdge]]:
        adjacency: Dict[str, List[LineageEdge]] = defaultdict(list)
        for edge in edges:
            if direction == LineageDirection.UPSTREAM:
                adjacency[edge.target_node_id].append(edge)
            elif direction == LineageDirection.DOWNSTREAM:
                adjacency[edge.source_node_id].append(edge)
            else:
                adjacency[edge.source_node_id].append(edge)
                adjacency[edge.target_node_id].append(edge)
        return adjacency

    @staticmethod
    def _impact_risk_score(nodes: Sequence[LineageNode], edges: Sequence[LineageEdge]) -> float:
        score = len(nodes) * 3 + len(edges) * 2
        score += sum(10 for node in nodes if set(node.classifications).intersection({"pii", "pci", "phi", "restricted", "financial_sensitive"}))
        score += sum(5 for node in nodes if node.node_type in {LineageNodeType.REPORT, LineageNodeType.DASHBOARD, LineageNodeType.MODEL})
        return round(min(100.0, score), 6)

    def _event(
        self,
        event_type: LineageEventType,
        *,
        actor_id: Optional[str] = None,
        node_id: Optional[str] = None,
        edge_id: Optional[str] = None,
        job_id: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        message: str = "",
        payload: Optional[JsonDict] = None,
    ) -> LineageEvent:
        event = LineageEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=dt.datetime.now(dt.timezone.utc),
            actor_id=actor_id,
            node_id=node_id,
            edge_id=edge_id,
            job_id=job_id,
            pipeline_id=pipeline_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
            message=message,
            payload=payload or {},
        )
        sealed = dataclasses.replace(event, audit_hash=stable_hash(event.to_dict()))
        self.repository.add_event(sealed)
        return sealed

    @staticmethod
    def _export_dot(nodes: Sequence[LineageNode], edges: Sequence[LineageEdge]) -> str:
        node_lookup = {node.node_id: node for node in nodes}
        lines = ["digraph lineage {", "  rankdir=LR;", "  node [shape=box, style=rounded];"]
        for node in nodes:
            label = f"{node.name}\\n{node.node_type.value}"
            lines.append(f'  "{escape_dot(node.node_id)}" [label="{escape_dot(label)}"];')
        for edge in edges:
            if edge.source_node_id in node_lookup and edge.target_node_id in node_lookup:
                label = edge.edge_type.value
                lines.append(f'  "{escape_dot(edge.source_node_id)}" -> "{escape_dot(edge.target_node_id)}" [label="{escape_dot(label)}"];')
        lines.append("}")
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# Query helpers
# -----------------------------------------------------------------------------


def matches_node_query(node: LineageNode, query: LineageQuery) -> bool:
    if query.node_ids and node.node_id not in query.node_ids:
        return False
    if query.node_types and node.node_type not in query.node_types:
        return False
    if query.namespace and node.namespace != query.namespace:
        return False
    if query.tenant_id and node.tenant_id != query.tenant_id:
        return False
    if query.domain and node.domain != query.domain:
        return False
    if query.owner_id and node.owner_id != query.owner_id:
        return False
    if query.tags and not set(query.tags).issubset(set(node.tags)):
        return False
    if query.classifications and not set(query.classifications).intersection(set(node.classifications)):
        return False
    if query.text:
        text = json.dumps(node.to_dict(), ensure_ascii=False, default=str).lower()
        if query.text.lower() not in text:
            return False
    return True


def matches_edge_query(edge: LineageEdge, query: LineageQuery, nodes: Mapping[str, LineageNode]) -> bool:
    if query.edge_types and edge.edge_type not in query.edge_types:
        return False
    if query.node_ids and edge.source_node_id not in query.node_ids and edge.target_node_id not in query.node_ids:
        return False
    source = nodes.get(edge.source_node_id)
    target = nodes.get(edge.target_node_id)
    if query.tenant_id:
        if not ((source and source.tenant_id == query.tenant_id) or (target and target.tenant_id == query.tenant_id)):
            return False
    if query.domain:
        if not ((source and source.domain == query.domain) or (target and target.domain == query.domain)):
            return False
    if query.owner_id:
        if not ((source and source.owner_id == query.owner_id) or (target and target.owner_id == query.owner_id)):
            return False
    if query.text:
        text = json.dumps(edge.to_dict(), ensure_ascii=False, default=str).lower()
        if query.text.lower() not in text:
            return False
    return True


# -----------------------------------------------------------------------------
# Builders and utilities
# -----------------------------------------------------------------------------


def build_node(
    name: str,
    node_type: LineageNodeType,
    *,
    qualified_name: Optional[str] = None,
    namespace: str = "default",
    tenant_id: Optional[str] = None,
    owner_id: Optional[str] = None,
    domain: Optional[str] = None,
    classifications: Sequence[str] = (),
    tags: Sequence[str] = (),
    attributes: Optional[JsonDict] = None,
) -> LineageNode:
    identity = qualified_name or f"{namespace}:{node_type.value}:{name}"
    node_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return LineageNode(
        node_id=node_id,
        name=name,
        node_type=node_type,
        qualified_name=qualified_name,
        namespace=namespace,
        tenant_id=tenant_id,
        owner_id=owner_id,
        domain=domain,
        classifications=tuple(classifications),
        tags=tuple(tags),
        attributes=attributes or {},
    )


def build_column_node(parent: LineageNode, column_name: str, *, data_type: Optional[str] = None, classifications: Sequence[str] = ()) -> LineageNode:
    qualified = f"{parent.identity()}.{column_name}"
    return build_node(
        column_name,
        LineageNodeType.COLUMN,
        qualified_name=qualified,
        namespace=parent.namespace,
        tenant_id=parent.tenant_id,
        owner_id=parent.owner_id,
        domain=parent.domain,
        classifications=classifications,
        tags=("column",),
        attributes={"parent_node_id": parent.node_id, "data_type": data_type},
    )


def unique_paths(paths: Sequence[Sequence[str]]) -> List[List[str]]:
    seen: Set[Tuple[str, ...]] = set()
    output: List[List[str]] = []
    for path in paths:
        key = tuple(path)
        if key not in seen:
            seen.add(key)
            output.append(list(path))
    return output


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


def escape_dot(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_default_lineage_tracker() -> LineageTracker:
    return LineageTracker()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    tracker = build_default_lineage_tracker()
    raw_sales = build_node(
        "raw_sales",
        LineageNodeType.TABLE,
        qualified_name="warehouse.raw.raw_sales",
        owner_id="data-eng",
        domain="retail",
        classifications=("financial_sensitive",),
    )
    curated_sales = build_node(
        "curated_sales_daily",
        LineageNodeType.TABLE,
        qualified_name="warehouse.curated.sales_daily",
        owner_id="analytics",
        domain="retail",
        classifications=("financial_sensitive",),
    )
    report = build_node(
        "sales_dashboard",
        LineageNodeType.DASHBOARD,
        qualified_name="bi.sales.dashboard",
        owner_id="bi-team",
        domain="retail",
    )

    tracker.record_dataset_lineage(
        source=raw_sales,
        target=curated_sales,
        edge_type=LineageEdgeType.TRANSFORMS,
        transformation_type=TransformationType.AGGREGATE,
        job_id="job-sales-daily",
        pipeline_id="pipeline-sales",
        expression="group by date, store_id",
        actor_id="airflow",
    )
    tracker.record_dataset_lineage(
        source=curated_sales,
        target=report,
        edge_type=LineageEdgeType.PUBLISHES_TO,
        transformation_type=TransformationType.SELECT,
        actor_id="bi-service",
    )

    impact = tracker.get_downstream(raw_sales.node_id, max_depth=5)
    print(json.dumps(impact.to_dict(), indent=2, ensure_ascii=False, default=str))
    print(json.dumps(tracker.summary(), indent=2, ensure_ascii=False, default=str))
    print(tracker.export(fmt=ExportFormat.DOT))
