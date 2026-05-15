"""
data/processing/graph_processor.py

Enterprise-grade graph processing engine for data platforms.

Purpose
-------
Provides a dependency-light graph processing module for data pipelines,
knowledge graphs, lineage graphs, relationship analysis, fraud/risk networks,
recommendation preprocessing, dependency graphs and operational topology maps.

Core capabilities
-----------------
- Directed and undirected graph model.
- Nodes and edges with safe attributes.
- Build graphs from edge lists, records and adjacency maps.
- BFS/DFS traversal.
- Shortest path and weighted Dijkstra path.
- Connected components and strongly connected components.
- Cycle detection and topological sort.
- Degree metrics, centrality-lite and PageRank.
- Neighborhood expansion and subgraph extraction.
- Graph validation and duplicate handling.
- JSON snapshot/report export.
- Optional telemetry integration.
- Standard library only.

Example
-------
processor = GraphProcessor()
graph = processor.build_from_edges([
    ("A", "B"),
    ("B", "C"),
])
print(processor.shortest_path(graph, "A", "C"))
"""

from __future__ import annotations

import contextlib
import dataclasses
import heapq
import json
import logging
import math
import os
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)

MAX_TEXT_LENGTH = 16_384
DEFAULT_MAX_NODES = 1_000_000
DEFAULT_MAX_EDGES = 5_000_000


class GraphType(str, Enum):
    DIRECTED = "directed"
    UNDIRECTED = "undirected"


class DuplicatePolicy(str, Enum):
    KEEP_FIRST = "keep_first"
    KEEP_LAST = "keep_last"
    MERGE = "merge"
    ERROR = "error"


class GraphStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    EMPTY = "empty"


class TraversalStrategy(str, Enum):
    BFS = "bfs"
    DFS = "dfs"


@dataclass(frozen=True)
class GraphProcessorConfig:
    graph_type: GraphType = GraphType.DIRECTED
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.MERGE
    max_nodes: int = DEFAULT_MAX_NODES
    max_edges: int = DEFAULT_MAX_EDGES
    telemetry_enabled: bool = True
    report_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "GraphProcessorConfig":
        return cls(
            graph_type=GraphType(os.getenv("GRAPH_PROCESSOR_TYPE", GraphType.DIRECTED.value)),
            duplicate_policy=DuplicatePolicy(os.getenv("GRAPH_DUPLICATE_POLICY", DuplicatePolicy.MERGE.value)),
            max_nodes=int_env("GRAPH_MAX_NODES", DEFAULT_MAX_NODES),
            max_edges=int_env("GRAPH_MAX_EDGES", DEFAULT_MAX_EDGES),
            telemetry_enabled=bool_env("GRAPH_TELEMETRY_ENABLED", True),
            report_path=os.getenv("GRAPH_REPORT_PATH"),
        )


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    weight: float = 1.0
    label: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return edge_id(self.source, self.target, self.label)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass
class Graph:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    graph_type: GraphType = GraphType.DIRECTED
    nodes: Dict[str, GraphNode] = field(default_factory=dict)
    edges: Dict[str, GraphEdge] = field(default_factory=dict)
    adjacency: Dict[str, Dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    reverse_adjacency: Dict[str, Dict[str, float]] = field(default_factory=lambda: defaultdict(dict))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def node_count(self) -> int:
        return len(self.nodes)

    def edge_count(self) -> int:
        return len(self.edges)

    def neighbors(self, node_id: str) -> Dict[str, float]:
        return dict(self.adjacency.get(node_id, {}))

    def predecessors(self, node_id: str) -> Dict[str, float]:
        return dict(self.reverse_adjacency.get(node_id, {}))

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping({
            "id": self.id,
            "graph_type": self.graph_type.value,
            "node_count": self.node_count(),
            "edge_count": self.edge_count(),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
            "metadata": self.metadata,
        })

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


@dataclass(frozen=True)
class GraphValidationResult:
    status: GraphStatus
    node_count: int
    edge_count: int
    orphan_count: int
    self_loop_count: int
    duplicate_edge_count: int
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return sanitize_mapping(data)


@dataclass(frozen=True)
class GraphProcessingResult:
    id: str
    operation: str
    started_at: str
    finished_at: str
    duration_ms: float
    result: Any
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class GraphProcessingError(Exception):
    """Base graph processing error."""


class GraphConfigError(GraphProcessingError):
    """Invalid graph processor configuration."""


class GraphLimitError(GraphProcessingError):
    """Graph node/edge limit exceeded."""


class GraphNotFoundError(GraphProcessingError):
    """Graph node/path not found."""


class GraphProcessor:
    """Enterprise graph processing engine."""

    def __init__(self, config: Optional[GraphProcessorConfig] = None) -> None:
        self.config = config or GraphProcessorConfig.from_env()

    def create_graph(self, *, graph_type: Optional[GraphType] = None, metadata: Optional[Mapping[str, Any]] = None) -> Graph:
        return Graph(graph_type=graph_type or self.config.graph_type, metadata=sanitize_mapping(dict(metadata or {})))

    def build_from_edges(
        self,
        edges: Iterable[Any],
        *,
        graph_type: Optional[GraphType] = None,
        source_field: str = "source",
        target_field: str = "target",
        weight_field: str = "weight",
        label_field: str = "label",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Graph:
        graph = self.create_graph(graph_type=graph_type, metadata=metadata)
        with telemetry_operation("graph_processor.build_from_edges", self.config.telemetry_enabled):
            for raw in edges:
                if isinstance(raw, tuple) and len(raw) >= 2:
                    source = raw[0]
                    target = raw[1]
                    weight = raw[2] if len(raw) > 2 else 1.0
                    label = raw[3] if len(raw) > 3 else None
                    attrs: Dict[str, Any] = {}
                else:
                    row = to_mapping(raw)
                    source = get_field(row, source_field)
                    target = get_field(row, target_field)
                    weight = get_field(row, weight_field) or 1.0
                    label = get_field(row, label_field)
                    attrs = dict(row)
                self.add_edge(graph, str(source), str(target), weight=float(weight), label=str(label) if label is not None else None, attributes=attrs)
        return graph

    def build_from_adjacency(self, adjacency: Mapping[str, Sequence[Any]], *, graph_type: Optional[GraphType] = None) -> Graph:
        graph = self.create_graph(graph_type=graph_type)
        for source, targets in adjacency.items():
            self.add_node(graph, str(source))
            for target in targets:
                if isinstance(target, tuple):
                    self.add_edge(graph, str(source), str(target[0]), weight=float(target[1]) if len(target) > 1 else 1.0)
                else:
                    self.add_edge(graph, str(source), str(target))
        return graph

    def add_node(self, graph: Graph, node_id: str, *, label: Optional[str] = None, attributes: Optional[Mapping[str, Any]] = None) -> GraphNode:
        if node_id not in graph.nodes and graph.node_count() >= self.config.max_nodes:
            raise GraphLimitError(f"max_nodes exceeded: {self.config.max_nodes}")
        node = GraphNode(id=str(node_id), label=label, attributes=sanitize_mapping(dict(attributes or {})))
        if node.id in graph.nodes:
            graph.nodes[node.id] = merge_node(graph.nodes[node.id], node, self.config.duplicate_policy)
        else:
            graph.nodes[node.id] = node
        graph.adjacency.setdefault(node.id, {})
        graph.reverse_adjacency.setdefault(node.id, {})
        return graph.nodes[node.id]

    def add_edge(
        self,
        graph: Graph,
        source: str,
        target: str,
        *,
        weight: float = 1.0,
        label: Optional[str] = None,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> GraphEdge:
        if graph.edge_count() >= self.config.max_edges:
            raise GraphLimitError(f"max_edges exceeded: {self.config.max_edges}")
        self.add_node(graph, source)
        self.add_node(graph, target)
        edge = GraphEdge(source=str(source), target=str(target), weight=float(weight), label=label, attributes=sanitize_mapping(dict(attributes or {})))
        existing = graph.edges.get(edge.id)
        if existing:
            edge = merge_edge(existing, edge, self.config.duplicate_policy)
        graph.edges[edge.id] = edge
        graph.adjacency[edge.source][edge.target] = edge.weight
        graph.reverse_adjacency[edge.target][edge.source] = edge.weight
        if graph.graph_type == GraphType.UNDIRECTED:
            graph.adjacency[edge.target][edge.source] = edge.weight
            graph.reverse_adjacency[edge.source][edge.target] = edge.weight
        return edge

    def validate(self, graph: Graph) -> GraphValidationResult:
        issues: List[str] = []
        self_loops = sum(1 for edge in graph.edges.values() if edge.source == edge.target)
        orphan_nodes = [node_id for node_id in graph.nodes if not graph.adjacency.get(node_id) and not graph.reverse_adjacency.get(node_id)]
        for edge in graph.edges.values():
            if edge.source not in graph.nodes:
                issues.append(f"edge source missing: {edge.source}")
            if edge.target not in graph.nodes:
                issues.append(f"edge target missing: {edge.target}")
        status = GraphStatus.EMPTY if graph.node_count() == 0 else GraphStatus.INVALID if issues else GraphStatus.VALID
        return GraphValidationResult(status, graph.node_count(), graph.edge_count(), len(orphan_nodes), self_loops, 0, issues)

    def traverse(self, graph: Graph, start: str, *, strategy: TraversalStrategy = TraversalStrategy.BFS, max_depth: Optional[int] = None) -> List[str]:
        if start not in graph.nodes:
            raise GraphNotFoundError(f"node not found: {start}")
        visited: Set[str] = set()
        output: List[str] = []
        if strategy == TraversalStrategy.BFS:
            q: Deque[Tuple[str, int]] = deque([(start, 0)])
            while q:
                node, depth = q.popleft()
                if node in visited:
                    continue
                visited.add(node)
                output.append(node)
                if max_depth is not None and depth >= max_depth:
                    continue
                for neighbor in graph.adjacency.get(node, {}):
                    if neighbor not in visited:
                        q.append((neighbor, depth + 1))
        else:
            stack: List[Tuple[str, int]] = [(start, 0)]
            while stack:
                node, depth = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                output.append(node)
                if max_depth is not None and depth >= max_depth:
                    continue
                for neighbor in reversed(list(graph.adjacency.get(node, {}))):
                    if neighbor not in visited:
                        stack.append((neighbor, depth + 1))
        return output

    def shortest_path(self, graph: Graph, source: str, target: str) -> List[str]:
        if source not in graph.nodes or target not in graph.nodes:
            raise GraphNotFoundError("source or target node not found")
        previous: Dict[str, Optional[str]] = {source: None}
        q: Deque[str] = deque([source])
        while q:
            node = q.popleft()
            if node == target:
                break
            for neighbor in graph.adjacency.get(node, {}):
                if neighbor not in previous:
                    previous[neighbor] = node
                    q.append(neighbor)
        if target not in previous:
            return []
        return reconstruct_path(previous, target)

    def weighted_shortest_path(self, graph: Graph, source: str, target: str) -> Tuple[List[str], float]:
        if source not in graph.nodes or target not in graph.nodes:
            raise GraphNotFoundError("source or target node not found")
        distances = {source: 0.0}
        previous: Dict[str, Optional[str]] = {source: None}
        heap: List[Tuple[float, str]] = [(0.0, source)]
        while heap:
            distance, node = heapq.heappop(heap)
            if node == target:
                break
            if distance > distances.get(node, math.inf):
                continue
            for neighbor, weight in graph.adjacency.get(node, {}).items():
                nd = distance + max(0.0, float(weight))
                if nd < distances.get(neighbor, math.inf):
                    distances[neighbor] = nd
                    previous[neighbor] = node
                    heapq.heappush(heap, (nd, neighbor))
        if target not in distances:
            return [], math.inf
        return reconstruct_path(previous, target), distances[target]

    def connected_components(self, graph: Graph) -> List[List[str]]:
        visited: Set[str] = set()
        components: List[List[str]] = []
        for node in graph.nodes:
            if node in visited:
                continue
            component = []
            q = deque([node])
            while q:
                current = q.popleft()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                neighbors = set(graph.adjacency.get(current, {})) | set(graph.reverse_adjacency.get(current, {}))
                for neighbor in neighbors:
                    if neighbor not in visited:
                        q.append(neighbor)
            components.append(component)
        return components

    def strongly_connected_components(self, graph: Graph) -> List[List[str]]:
        index = 0
        stack: List[str] = []
        indices: Dict[str, int] = {}
        lowlinks: Dict[str, int] = {}
        on_stack: Set[str] = set()
        components: List[List[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)
            for neighbor in graph.adjacency.get(node, {}):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])
            if lowlinks[node] == indices[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.remove(item)
                    component.append(item)
                    if item == node:
                        break
                components.append(component)

        for node in graph.nodes:
            if node not in indices:
                strongconnect(node)
        return components

    def has_cycle(self, graph: Graph) -> bool:
        visiting: Set[str] = set()
        visited: Set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for neighbor in graph.adjacency.get(node, {}):
                if visit(neighbor):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph.nodes if node not in visited)

    def topological_sort(self, graph: Graph) -> List[str]:
        if graph.graph_type == GraphType.UNDIRECTED:
            raise GraphConfigError("topological sort requires directed graph")
        indegree = {node: 0 for node in graph.nodes}
        for targets in graph.adjacency.values():
            for target in targets:
                indegree[target] = indegree.get(target, 0) + 1
        q = deque([node for node, degree in indegree.items() if degree == 0])
        output = []
        while q:
            node = q.popleft()
            output.append(node)
            for neighbor in graph.adjacency.get(node, {}):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    q.append(neighbor)
        if len(output) != len(graph.nodes):
            raise GraphProcessingError("graph has at least one cycle")
        return output

    def degree_metrics(self, graph: Graph) -> Dict[str, Dict[str, float]]:
        result = {}
        for node in graph.nodes:
            out_degree = len(graph.adjacency.get(node, {}))
            in_degree = len(graph.reverse_adjacency.get(node, {}))
            weighted_out = sum(graph.adjacency.get(node, {}).values())
            weighted_in = sum(graph.reverse_adjacency.get(node, {}).values())
            result[node] = {
                "in_degree": in_degree,
                "out_degree": out_degree,
                "degree": in_degree + out_degree,
                "weighted_in_degree": round(weighted_in, 6),
                "weighted_out_degree": round(weighted_out, 6),
                "weighted_degree": round(weighted_in + weighted_out, 6),
            }
        return result

    def pagerank(self, graph: Graph, *, damping: float = 0.85, iterations: int = 50, tolerance: float = 1e-8) -> Dict[str, float]:
        nodes = list(graph.nodes)
        n = len(nodes)
        if n == 0:
            return {}
        ranks = {node: 1.0 / n for node in nodes}
        for _ in range(iterations):
            new_ranks = {node: (1.0 - damping) / n for node in nodes}
            sink_rank = sum(ranks[node] for node in nodes if not graph.adjacency.get(node))
            for node in nodes:
                new_ranks[node] += damping * sink_rank / n
            for node in nodes:
                neighbors = graph.adjacency.get(node, {})
                if not neighbors:
                    continue
                share = ranks[node] / len(neighbors)
                for neighbor in neighbors:
                    new_ranks[neighbor] += damping * share
            delta = sum(abs(new_ranks[node] - ranks[node]) for node in nodes)
            ranks = new_ranks
            if delta < tolerance:
                break
        return {node: round(score, 10) for node, score in sorted(ranks.items(), key=lambda item: item[1], reverse=True)}

    def neighborhood(self, graph: Graph, node_id: str, *, depth: int = 1, include_predecessors: bool = False) -> List[str]:
        if node_id not in graph.nodes:
            raise GraphNotFoundError(f"node not found: {node_id}")
        visited = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            next_frontier: Set[str] = set()
            for node in frontier:
                next_frontier.update(graph.adjacency.get(node, {}).keys())
                if include_predecessors:
                    next_frontier.update(graph.reverse_adjacency.get(node, {}).keys())
            next_frontier -= visited
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        return sorted(visited - {node_id})

    def subgraph(self, graph: Graph, node_ids: Sequence[str]) -> Graph:
        selected = set(node_ids)
        new_graph = self.create_graph(graph_type=graph.graph_type, metadata={"parent_graph_id": graph.id})
        for node_id in selected:
            if node_id in graph.nodes:
                node = graph.nodes[node_id]
                self.add_node(new_graph, node.id, label=node.label, attributes=node.attributes)
        for edge in graph.edges.values():
            if edge.source in selected and edge.target in selected:
                self.add_edge(new_graph, edge.source, edge.target, weight=edge.weight, label=edge.label, attributes=edge.attributes)
        return new_graph

    def save_graph(self, graph: Graph, path: str | os.PathLike[str]) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(graph.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)
        return target

    def load_graph(self, path: str | os.PathLike[str]) -> Graph:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        graph = Graph(id=str(payload.get("id") or uuid.uuid4()), graph_type=GraphType(payload.get("graph_type", GraphType.DIRECTED.value)), metadata=dict(payload.get("metadata", {})))
        for node in payload.get("nodes", []):
            self.add_node(graph, str(node["id"]), label=node.get("label"), attributes=node.get("attributes", {}))
        for edge in payload.get("edges", []):
            self.add_edge(graph, str(edge["source"]), str(edge["target"]), weight=float(edge.get("weight", 1.0)), label=edge.get("label"), attributes=edge.get("attributes", {}))
        return graph


def merge_node(existing: GraphNode, new: GraphNode, policy: DuplicatePolicy) -> GraphNode:
    if policy == DuplicatePolicy.ERROR:
        raise GraphConfigError(f"duplicate node: {new.id}")
    if policy == DuplicatePolicy.KEEP_FIRST:
        return existing
    if policy == DuplicatePolicy.KEEP_LAST:
        return new
    attrs = dict(existing.attributes)
    attrs.update(new.attributes)
    return GraphNode(id=existing.id, label=new.label or existing.label, attributes=attrs)


def merge_edge(existing: GraphEdge, new: GraphEdge, policy: DuplicatePolicy) -> GraphEdge:
    if policy == DuplicatePolicy.ERROR:
        raise GraphConfigError(f"duplicate edge: {new.id}")
    if policy == DuplicatePolicy.KEEP_FIRST:
        return existing
    if policy == DuplicatePolicy.KEEP_LAST:
        return new
    attrs = dict(existing.attributes)
    attrs.update(new.attributes)
    return GraphEdge(source=existing.source, target=existing.target, weight=new.weight, label=new.label or existing.label, attributes=attrs)


def reconstruct_path(previous: Mapping[str, Optional[str]], target: str) -> List[str]:
    path = []
    current: Optional[str] = target
    while current is not None:
        path.append(current)
        current = previous.get(current)
    return list(reversed(path))


def edge_id(source: str, target: str, label: Optional[str]) -> str:
    raw = json.dumps([source, target, label], ensure_ascii=False, sort_keys=True)
    import hashlib
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise GraphConfigError(f"Unsupported row type: {type(row)!r}")


def get_field(row: Mapping[str, Any], field_path: str) -> Any:
    current: Any = row
    for part in field_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    result: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            result[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set)):
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


@contextlib.contextmanager
def telemetry_operation(name: str, enabled: bool, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
    if not enabled:
        yield
        return
    try:
        from data.observability.telemetry import get_telemetry
        telemetry = get_telemetry()
        with telemetry.operation(name, attributes=attributes):
            yield
    except Exception:
        yield


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "DuplicatePolicy",
    "Graph",
    "GraphConfigError",
    "GraphEdge",
    "GraphLimitError",
    "GraphNode",
    "GraphNotFoundError",
    "GraphProcessingError",
    "GraphProcessingResult",
    "GraphProcessor",
    "GraphProcessorConfig",
    "GraphStatus",
    "GraphType",
    "GraphValidationResult",
    "TraversalStrategy",
    "edge_id",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    processor = GraphProcessor(GraphProcessorConfig(telemetry_enabled=False))
    g = processor.build_from_edges([("A", "B", 1.0), ("B", "C", 2.0), ("A", "C", 5.0)])
    print(g.to_json())
    print("bfs", processor.traverse(g, "A"))
    print("shortest", processor.shortest_path(g, "A", "C"))
    print("weighted", processor.weighted_shortest_path(g, "A", "C"))
    print("pagerank", processor.pagerank(g))
