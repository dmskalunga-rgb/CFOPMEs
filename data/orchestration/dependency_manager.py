"""
data/orchestration/dependency_manager.py

Enterprise Dependency Manager.

Recursos:
- Gerenciamento de dependências entre jobs, datasets, tarefas, modelos e serviços
- Grafo direcionado
- Validação de dependências
- Detecção de ciclos
- Ordenação topológica
- Readiness check
- Impact analysis upstream/downstream
- Bloqueios por status
- Versionamento lógico de dependências
- Auditoria e métricas
- Multi-tenant
- Exportação JSON
- Sem dependências externas obrigatórias
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Protocol, Set, Tuple


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# Enums
# =============================================================================

class DependencyEntityType(str, Enum):
    TASK = "task"
    JOB = "job"
    DAG = "dag"
    PIPELINE = "pipeline"
    DATASET = "dataset"
    MODEL = "model"
    SERVICE = "service"
    FEATURE = "feature"
    RESOURCE = "resource"
    CUSTOM = "custom"


class DependencyType(str, Enum):
    REQUIRES = "requires"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    BLOCKS = "blocks"
    TRIGGERS = "triggers"
    VALIDATES = "validates"
    DEPLOYS = "deploys"
    OBSERVES = "observes"


class DependencyStrength(str, Enum):
    HARD = "hard"
    SOFT = "soft"
    OPTIONAL = "optional"


class EntityStatus(str, Enum):
    UNKNOWN = "unknown"
    READY = "ready"
    NOT_READY = "not_ready"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    DEPRECATED = "deprecated"


class ReadinessStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ImpactDirection(str, Enum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class AuditSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# =============================================================================
# Exceptions
# =============================================================================

class DependencyManagerError(Exception):
    """Erro base do Dependency Manager."""


class DependencyValidationError(DependencyManagerError):
    """Erro de validação."""


class DependencyCycleError(DependencyManagerError):
    """Ciclo detectado no grafo de dependências."""


class DependencyEntityNotFound(DependencyManagerError):
    """Entidade não encontrada."""


class DependencyEdgeNotFound(DependencyManagerError):
    """Dependência não encontrada."""


# =============================================================================
# Protocols
# =============================================================================

class AuditBackend(Protocol):
    def write_event(self, event: Dict[str, Any]) -> None:
        ...


class MetricsBackend(Protocol):
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        ...


class EntityStatusProvider(Protocol):
    def get_status(self, entity_id: str) -> EntityStatus:
        ...


# =============================================================================
# Default Backends
# =============================================================================

class LoggingAuditBackend:
    def write_event(self, event: Dict[str, Any]) -> None:
        logger.info(
            "dependency_manager_audit=%s",
            json.dumps(event, ensure_ascii=False, default=str),
        )


class LoggingMetricsBackend:
    def increment(
        self,
        metric_name: str,
        value: int = 1,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("metric=%s value=%s tags=%s", metric_name, value, tags or {})

    def gauge(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        logger.info("gauge=%s value=%s tags=%s", metric_name, value, tags or {})


class InMemoryStatusProvider:
    def __init__(self) -> None:
        self._statuses: Dict[str, EntityStatus] = {}

    def set_status(self, entity_id: str, status: EntityStatus) -> None:
        self._statuses[entity_id] = status

    def get_status(self, entity_id: str) -> EntityStatus:
        return self._statuses.get(entity_id, EntityStatus.UNKNOWN)


# =============================================================================
# Models
# =============================================================================

@dataclass(frozen=True)
class DependencyContext:
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    environment: str = "production"
    user_id: Optional[str] = None
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DependencyEntity:
    entity_id: str
    name: str
    entity_type: DependencyEntityType
    tenant_id: Optional[str] = None
    domain: Optional[str] = None
    version: str = "1.0.0"
    owner: Optional[str] = None
    status: EntityStatus = EntityStatus.UNKNOWN
    enabled: bool = True
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: Optional[datetime] = None

    def validate(self) -> None:
        if not self.entity_id:
            raise DependencyValidationError("entity_id é obrigatório")

        if not self.name:
            raise DependencyValidationError("name é obrigatório")


@dataclass(frozen=True)
class DependencyEdge:
    edge_id: str
    source_id: str
    target_id: str
    dependency_type: DependencyType = DependencyType.REQUIRES
    strength: DependencyStrength = DependencyStrength.HARD
    enabled: bool = True
    version_constraint: Optional[str] = None
    condition: Optional[str] = None
    description: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def validate(self) -> None:
        if not self.edge_id:
            raise DependencyValidationError("edge_id é obrigatório")

        if not self.source_id:
            raise DependencyValidationError("source_id é obrigatório")

        if not self.target_id:
            raise DependencyValidationError("target_id é obrigatório")

        if self.source_id == self.target_id:
            raise DependencyValidationError("Dependência não pode apontar para si mesma")


@dataclass
class ReadinessCheck:
    entity_id: str
    status: ReadinessStatus
    ready_dependencies: List[str]
    not_ready_dependencies: List[str]
    blocked_dependencies: List[str]
    optional_not_ready: List[str]
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImpactAnalysisResult:
    entity_id: str
    direction: ImpactDirection
    affected_entities: List[DependencyEntity]
    affected_edges: List[DependencyEdge]
    depth: int
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DependencyValidationResult:
    valid: bool
    entity_count: int
    edge_count: int
    cycles: List[List[str]] = field(default_factory=list)
    missing_entities: List[str] = field(default_factory=list)
    disabled_entities: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class DependencyPath:
    source_id: str
    target_id: str
    path: List[str]
    edges: List[DependencyEdge]
    found: bool


# =============================================================================
# Repository
# =============================================================================

class DependencyRepository:
    def __init__(
        self,
        entities: Optional[List[DependencyEntity]] = None,
        edges: Optional[List[DependencyEdge]] = None,
    ) -> None:
        self._entities: Dict[str, DependencyEntity] = {}
        self._edges: Dict[str, DependencyEdge] = {}

        for entity in entities or []:
            self.save_entity(entity)

        for edge in edges or []:
            self.save_edge(edge)

    def save_entity(self, entity: DependencyEntity) -> None:
        entity.validate()
        self._entities[entity.entity_id] = entity

    def save_edge(self, edge: DependencyEdge) -> None:
        edge.validate()

        if edge.source_id not in self._entities:
            raise DependencyEntityNotFound(f"source_id não encontrado: {edge.source_id}")

        if edge.target_id not in self._entities:
            raise DependencyEntityNotFound(f"target_id não encontrado: {edge.target_id}")

        self._edges[edge.edge_id] = edge

    def get_entity(self, entity_id: str) -> DependencyEntity:
        entity = self._entities.get(entity_id)
        if not entity:
            raise DependencyEntityNotFound(entity_id)
        return entity

    def get_edge(self, edge_id: str) -> DependencyEdge:
        edge = self._edges.get(edge_id)
        if not edge:
            raise DependencyEdgeNotFound(edge_id)
        return edge

    def list_entities(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        entity_type: Optional[DependencyEntityType] = None,
        enabled_only: bool = False,
    ) -> List[DependencyEntity]:
        items = list(self._entities.values())

        if tenant_id is not None:
            items = [item for item in items if item.tenant_id == tenant_id]

        if domain is not None:
            items = [item for item in items if item.domain == domain]

        if entity_type is not None:
            items = [item for item in items if item.entity_type == entity_type]

        if enabled_only:
            items = [item for item in items if item.enabled]

        return items

    def list_edges(
        self,
        enabled_only: bool = False,
        dependency_type: Optional[DependencyType] = None,
        strength: Optional[DependencyStrength] = None,
    ) -> List[DependencyEdge]:
        items = list(self._edges.values())

        if enabled_only:
            items = [item for item in items if item.enabled]

        if dependency_type is not None:
            items = [item for item in items if item.dependency_type == dependency_type]

        if strength is not None:
            items = [item for item in items if item.strength == strength]

        return items

    def delete_entity(self, entity_id: str, cascade: bool = False) -> None:
        if entity_id not in self._entities:
            raise DependencyEntityNotFound(entity_id)

        related_edges = [
            edge_id for edge_id, edge in self._edges.items()
            if edge.source_id == entity_id or edge.target_id == entity_id
        ]

        if related_edges and not cascade:
            raise DependencyValidationError(
                f"Entidade {entity_id} possui dependências. Use cascade=True."
            )

        for edge_id in related_edges:
            del self._edges[edge_id]

        del self._entities[entity_id]

    def delete_edge(self, edge_id: str) -> None:
        if edge_id not in self._edges:
            raise DependencyEdgeNotFound(edge_id)
        del self._edges[edge_id]


# =============================================================================
# Graph
# =============================================================================

class DependencyGraph:
    def __init__(self, repository: DependencyRepository) -> None:
        self.repository = repository

    def adjacency(
        self,
        enabled_only: bool = True,
        hard_only: bool = False,
    ) -> Dict[str, List[str]]:
        graph: Dict[str, List[str]] = {
            entity.entity_id: []
            for entity in self.repository.list_entities(enabled_only=enabled_only)
        }

        for edge in self.repository.list_edges(enabled_only=enabled_only):
            if hard_only and edge.strength != DependencyStrength.HARD:
                continue

            if edge.source_id in graph and edge.target_id in graph:
                graph[edge.source_id].append(edge.target_id)

        return graph

    def reverse_adjacency(
        self,
        enabled_only: bool = True,
        hard_only: bool = False,
    ) -> Dict[str, List[str]]:
        reverse: Dict[str, List[str]] = {
            entity.entity_id: []
            for entity in self.repository.list_entities(enabled_only=enabled_only)
        }

        for edge in self.repository.list_edges(enabled_only=enabled_only):
            if hard_only and edge.strength != DependencyStrength.HARD:
                continue

            if edge.source_id in reverse and edge.target_id in reverse:
                reverse[edge.target_id].append(edge.source_id)

        return reverse

    def upstream(self, entity_id: str, max_depth: Optional[int] = None) -> Set[str]:
        reverse = self.reverse_adjacency()
        return self._walk(reverse, entity_id, max_depth=max_depth)

    def downstream(self, entity_id: str, max_depth: Optional[int] = None) -> Set[str]:
        graph = self.adjacency()
        return self._walk(graph, entity_id, max_depth=max_depth)

    def topological_sort(self) -> List[str]:
        graph = self.adjacency(enabled_only=True, hard_only=True)
        indegree: Dict[str, int] = {node_id: 0 for node_id in graph}

        for children in graph.values():
            for child in children:
                indegree[child] += 1

        queue = deque([node_id for node_id, degree in indegree.items() if degree == 0])
        order: List[str] = []

        while queue:
            node_id = queue.popleft()
            order.append(node_id)

            for child in graph[node_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        if len(order) != len(graph):
            raise DependencyCycleError("Ciclo detectado no grafo de dependências")

        return order

    def execution_levels(self) -> List[List[str]]:
        graph = self.adjacency(enabled_only=True, hard_only=True)
        reverse = self.reverse_adjacency(enabled_only=True, hard_only=True)

        remaining = set(graph.keys())
        completed: Set[str] = set()
        levels: List[List[str]] = []

        while remaining:
            current = sorted([
                node_id for node_id in remaining
                if set(reverse[node_id]).issubset(completed)
            ])

            if not current:
                raise DependencyCycleError("Não foi possível calcular níveis. Ciclo provável.")

            levels.append(current)
            completed.update(current)
            remaining.difference_update(current)

        return levels

    def find_cycles(self) -> List[List[str]]:
        graph = self.adjacency(enabled_only=True, hard_only=True)
        visited: Set[str] = set()
        stack: Set[str] = set()
        path: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node_id: str) -> None:
            visited.add(node_id)
            stack.add(node_id)
            path.append(node_id)

            for neighbor in graph[node_id]:
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in stack:
                    try:
                        index = path.index(neighbor)
                        cycles.append(path[index:] + [neighbor])
                    except ValueError:
                        pass

            stack.remove(node_id)
            path.pop()

        for node_id in graph:
            if node_id not in visited:
                dfs(node_id)

        return cycles

    def find_path(self, source_id: str, target_id: str) -> DependencyPath:
        graph = self.adjacency(enabled_only=True)
        edge_lookup = self._edge_lookup()

        queue = deque([(source_id, [source_id])])
        visited = {source_id}

        while queue:
            current, path = queue.popleft()

            if current == target_id:
                edges = [
                    edge_lookup[(path[index], path[index + 1])]
                    for index in range(len(path) - 1)
                    if (path[index], path[index + 1]) in edge_lookup
                ]

                return DependencyPath(
                    source_id=source_id,
                    target_id=target_id,
                    path=path,
                    edges=edges,
                    found=True,
                )

            for neighbor in graph.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return DependencyPath(
            source_id=source_id,
            target_id=target_id,
            path=[],
            edges=[],
            found=False,
        )

    def _edge_lookup(self) -> Dict[Tuple[str, str], DependencyEdge]:
        lookup: Dict[Tuple[str, str], DependencyEdge] = {}

        for edge in self.repository.list_edges(enabled_only=True):
            lookup[(edge.source_id, edge.target_id)] = edge

        return lookup

    @staticmethod
    def _walk(
        graph: Dict[str, List[str]],
        start_id: str,
        max_depth: Optional[int] = None,
    ) -> Set[str]:
        visited: Set[str] = set()
        queue = deque([(start_id, 0)])

        while queue:
            node_id, depth = queue.popleft()

            if max_depth is not None and depth >= max_depth:
                continue

            for neighbor in graph.get(node_id, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, depth + 1))

        return visited


# =============================================================================
# Manager
# =============================================================================

class DependencyManager:
    def __init__(
        self,
        repository: Optional[DependencyRepository] = None,
        status_provider: Optional[EntityStatusProvider] = None,
        audit_backend: Optional[AuditBackend] = None,
        metrics_backend: Optional[MetricsBackend] = None,
    ) -> None:
        self.repository = repository or DependencyRepository()
        self.status_provider = status_provider or InMemoryStatusProvider()
        self.audit_backend = audit_backend or LoggingAuditBackend()
        self.metrics_backend = metrics_backend or LoggingMetricsBackend()

    @property
    def graph(self) -> DependencyGraph:
        return DependencyGraph(self.repository)

    def register_entity(
        self,
        entity: DependencyEntity,
        context: Optional[DependencyContext] = None,
    ) -> None:
        self.repository.save_entity(entity)

        self._audit(
            "dependency.entity.registered",
            AuditSeverity.INFO,
            context,
            {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type.value,
                "name": entity.name,
                "tenant_id": entity.tenant_id,
                "domain": entity.domain,
            },
        )

        self.metrics_backend.increment(
            "dependency.entity.registered.total",
            tags={"entity_type": entity.entity_type.value},
        )

    def register_dependency(
        self,
        edge: DependencyEdge,
        context: Optional[DependencyContext] = None,
        validate_cycles: bool = True,
    ) -> None:
        self.repository.save_edge(edge)

        if validate_cycles:
            cycles = self.graph.find_cycles()
            if cycles:
                self.repository.delete_edge(edge.edge_id)
                raise DependencyCycleError(f"Dependência criaria ciclo: {cycles}")

        self._audit(
            "dependency.edge.registered",
            AuditSeverity.INFO,
            context,
            {
                "edge_id": edge.edge_id,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "dependency_type": edge.dependency_type.value,
                "strength": edge.strength.value,
            },
        )

        self.metrics_backend.increment(
            "dependency.edge.registered.total",
            tags={
                "dependency_type": edge.dependency_type.value,
                "strength": edge.strength.value,
            },
        )

    def remove_dependency(
        self,
        edge_id: str,
        context: Optional[DependencyContext] = None,
    ) -> None:
        edge = self.repository.get_edge(edge_id)
        self.repository.delete_edge(edge_id)

        self._audit(
            "dependency.edge.removed",
            AuditSeverity.WARNING,
            context,
            {
                "edge_id": edge.edge_id,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
            },
        )

    def validate_graph(self) -> DependencyValidationResult:
        entities = self.repository.list_entities()
        edges = self.repository.list_edges()
        entity_ids = {entity.entity_id for entity in entities}

        missing: List[str] = []

        for edge in edges:
            if edge.source_id not in entity_ids:
                missing.append(edge.source_id)
            if edge.target_id not in entity_ids:
                missing.append(edge.target_id)

        cycles = self.graph.find_cycles()

        disabled = [
            entity.entity_id
            for entity in entities
            if not entity.enabled
        ]

        warnings: List[str] = []

        if disabled:
            warnings.append(f"Existem {len(disabled)} entidades desabilitadas")

        return DependencyValidationResult(
            valid=not missing and not cycles,
            entity_count=len(entities),
            edge_count=len(edges),
            cycles=cycles,
            missing_entities=sorted(set(missing)),
            disabled_entities=disabled,
            warnings=warnings,
        )

    def readiness_check(
        self,
        entity_id: str,
        context: Optional[DependencyContext] = None,
        include_soft: bool = False,
    ) -> ReadinessCheck:
        self.repository.get_entity(entity_id)

        incoming_edges = [
            edge for edge in self.repository.list_edges(enabled_only=True)
            if edge.target_id == entity_id
            and (
                include_soft
                or edge.strength == DependencyStrength.HARD
            )
        ]

        ready: List[str] = []
        not_ready: List[str] = []
        blocked: List[str] = []
        optional_not_ready: List[str] = []

        for edge in incoming_edges:
            status = self.status_provider.get_status(edge.source_id)

            if status in {EntityStatus.READY, EntityStatus.SUCCESS}:
                ready.append(edge.source_id)

            elif status in {EntityStatus.FAILED, EntityStatus.BLOCKED}:
                if edge.strength == DependencyStrength.OPTIONAL:
                    optional_not_ready.append(edge.source_id)
                else:
                    blocked.append(edge.source_id)

            else:
                if edge.strength == DependencyStrength.OPTIONAL:
                    optional_not_ready.append(edge.source_id)
                else:
                    not_ready.append(edge.source_id)

        if blocked:
            status = ReadinessStatus.BLOCKED
            reason = "Dependências bloqueadas ou com falha."

        elif not_ready:
            status = ReadinessStatus.NOT_READY
            reason = "Ainda existem dependências obrigatórias não prontas."

        elif optional_not_ready:
            status = ReadinessStatus.PARTIAL
            reason = "Dependências obrigatórias prontas, mas opcionais não prontas."

        else:
            status = ReadinessStatus.READY
            reason = "Todas as dependências obrigatórias estão prontas."

        check = ReadinessCheck(
            entity_id=entity_id,
            status=status,
            ready_dependencies=ready,
            not_ready_dependencies=not_ready,
            blocked_dependencies=blocked,
            optional_not_ready=optional_not_ready,
            reason=reason,
            metadata={
                "include_soft": include_soft,
                "dependency_count": len(incoming_edges),
            },
        )

        self._audit(
            "dependency.readiness.checked",
            AuditSeverity.INFO,
            context,
            {
                "entity_id": entity_id,
                "status": status.value,
                "reason": reason,
                "ready": ready,
                "not_ready": not_ready,
                "blocked": blocked,
                "optional_not_ready": optional_not_ready,
            },
        )

        self.metrics_backend.increment(
            "dependency.readiness.checked.total",
            tags={"status": status.value},
        )

        return check

    def impact_analysis(
        self,
        entity_id: str,
        direction: ImpactDirection = ImpactDirection.DOWNSTREAM,
        max_depth: Optional[int] = None,
        context: Optional[DependencyContext] = None,
    ) -> ImpactAnalysisResult:
        self.repository.get_entity(entity_id)

        affected_ids: Set[str] = set()

        if direction in {ImpactDirection.DOWNSTREAM, ImpactDirection.BOTH}:
            affected_ids.update(self.graph.downstream(entity_id, max_depth=max_depth))

        if direction in {ImpactDirection.UPSTREAM, ImpactDirection.BOTH}:
            affected_ids.update(self.graph.upstream(entity_id, max_depth=max_depth))

        affected_entities = [
            self.repository.get_entity(item)
            for item in sorted(affected_ids)
        ]

        affected_edges = [
            edge for edge in self.repository.list_edges(enabled_only=True)
            if edge.source_id in affected_ids
            or edge.target_id in affected_ids
            or edge.source_id == entity_id
            or edge.target_id == entity_id
        ]

        result = ImpactAnalysisResult(
            entity_id=entity_id,
            direction=direction,
            affected_entities=affected_entities,
            affected_edges=affected_edges,
            depth=max_depth or -1,
            metadata={
                "affected_entity_count": len(affected_entities),
                "affected_edge_count": len(affected_edges),
            },
        )

        self._audit(
            "dependency.impact.generated",
            AuditSeverity.INFO,
            context,
            {
                "entity_id": entity_id,
                "direction": direction.value,
                "max_depth": max_depth,
                "affected_entities": len(affected_entities),
                "affected_edges": len(affected_edges),
            },
        )

        return result

    def topological_order(self) -> List[DependencyEntity]:
        order = self.graph.topological_sort()
        return [self.repository.get_entity(entity_id) for entity_id in order]

    def execution_levels(self) -> List[List[DependencyEntity]]:
        levels = self.graph.execution_levels()

        return [
            [self.repository.get_entity(entity_id) for entity_id in level]
            for level in levels
        ]

    def find_path(self, source_id: str, target_id: str) -> DependencyPath:
        self.repository.get_entity(source_id)
        self.repository.get_entity(target_id)
        return self.graph.find_path(source_id, target_id)

    def set_status(
        self,
        entity_id: str,
        status: EntityStatus,
        context: Optional[DependencyContext] = None,
    ) -> None:
        self.repository.get_entity(entity_id)

        if isinstance(self.status_provider, InMemoryStatusProvider):
            self.status_provider.set_status(entity_id, status)
        else:
            raise DependencyManagerError(
                "Status provider externo não suporta set_status direto"
            )

        self._audit(
            "dependency.entity.status_changed",
            AuditSeverity.INFO,
            context,
            {
                "entity_id": entity_id,
                "status": status.value,
            },
        )

    def export_graph_json(self) -> str:
        payload = {
            "entities": [
                self._entity_to_dict(entity)
                for entity in self.repository.list_entities()
            ],
            "edges": [
                self._edge_to_dict(edge)
                for edge in self.repository.list_edges()
            ],
            "validation": asdict(self.validate_graph()),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def export_impact_json(self, result: ImpactAnalysisResult) -> str:
        payload = asdict(result)
        payload["direction"] = result.direction.value
        payload["generated_at"] = result.generated_at.isoformat()

        for entity in payload["affected_entities"]:
            entity["entity_type"] = entity["entity_type"].value
            entity["status"] = entity["status"].value
            entity["created_at"] = entity["created_at"].isoformat()
            entity["updated_at"] = entity["updated_at"].isoformat() if entity["updated_at"] else None

        for edge in payload["affected_edges"]:
            edge["dependency_type"] = edge["dependency_type"].value
            edge["strength"] = edge["strength"].value
            edge["created_at"] = edge["created_at"].isoformat()

        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def _audit(
        self,
        event_type: str,
        severity: AuditSeverity,
        context: Optional[DependencyContext],
        details: Dict[str, Any],
    ) -> None:
        context = context or DependencyContext()

        self.audit_backend.write_event(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": event_type,
                "severity": severity.value,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "tenant_id": context.tenant_id,
                "domain": context.domain,
                "environment": context.environment,
                "user_id": context.user_id,
                "correlation_id": context.correlation_id,
                "details": details,
            }
        )

    @staticmethod
    def _entity_to_dict(entity: DependencyEntity) -> Dict[str, Any]:
        data = asdict(entity)
        data["entity_type"] = entity.entity_type.value
        data["status"] = entity.status.value
        data["created_at"] = entity.created_at.isoformat()
        data["updated_at"] = entity.updated_at.isoformat() if entity.updated_at else None
        return data

    @staticmethod
    def _edge_to_dict(edge: DependencyEdge) -> Dict[str, Any]:
        data = asdict(edge)
        data["dependency_type"] = edge.dependency_type.value
        data["strength"] = edge.strength.value
        data["created_at"] = edge.created_at.isoformat()
        return data


# =============================================================================
# Factory
# =============================================================================

def create_default_dependency_manager() -> DependencyManager:
    return DependencyManager()


def build_sample_dependency_manager() -> DependencyManager:
    manager = create_default_dependency_manager()

    context = DependencyContext(
        tenant_id="tenant-default",
        domain="sales",
        user_id="data-platform",
        correlation_id="corr-dependency-sample",
    )

    entities = [
        DependencyEntity(
            entity_id="raw_sales",
            name="Raw Sales Dataset",
            entity_type=DependencyEntityType.DATASET,
            tenant_id="tenant-default",
            domain="sales",
            status=EntityStatus.READY,
        ),
        DependencyEntity(
            entity_id="clean_sales",
            name="Clean Sales Dataset",
            entity_type=DependencyEntityType.DATASET,
            tenant_id="tenant-default",
            domain="sales",
        ),
        DependencyEntity(
            entity_id="sales_metrics",
            name="Sales Metrics Job",
            entity_type=DependencyEntityType.JOB,
            tenant_id="tenant-default",
            domain="sales",
        ),
        DependencyEntity(
            entity_id="sales_dashboard",
            name="Sales Dashboard",
            entity_type=DependencyEntityType.SERVICE,
            tenant_id="tenant-default",
            domain="sales",
        ),
    ]

    for entity in entities:
        manager.register_entity(entity, context=context)

    dependencies = [
        DependencyEdge(
            edge_id="edge_raw_to_clean",
            source_id="raw_sales",
            target_id="clean_sales",
            dependency_type=DependencyType.REQUIRES,
            strength=DependencyStrength.HARD,
        ),
        DependencyEdge(
            edge_id="edge_clean_to_metrics",
            source_id="clean_sales",
            target_id="sales_metrics",
            dependency_type=DependencyType.REQUIRES,
            strength=DependencyStrength.HARD,
        ),
        DependencyEdge(
            edge_id="edge_metrics_to_dashboard",
            source_id="sales_metrics",
            target_id="sales_dashboard",
            dependency_type=DependencyType.PRODUCES,
            strength=DependencyStrength.HARD,
        ),
    ]

    for dependency in dependencies:
        manager.register_dependency(dependency, context=context)

    manager.set_status("raw_sales", EntityStatus.READY, context=context)

    return manager


# =============================================================================
# Example
# =============================================================================

def example_usage() -> None:
    manager = build_sample_dependency_manager()

    validation = manager.validate_graph()
    print(json.dumps(asdict(validation), ensure_ascii=False, indent=2))

    readiness = manager.readiness_check("clean_sales")
    print(json.dumps(asdict(readiness), ensure_ascii=False, indent=2, default=str))

    impact = manager.impact_analysis(
        "raw_sales",
        direction=ImpactDirection.DOWNSTREAM,
    )
    print(manager.export_impact_json(impact))

    levels = manager.execution_levels()
    print([
        [entity.entity_id for entity in level]
        for level in levels
    ])

    print(manager.export_graph_json())


if __name__ == "__main__":
    example_usage()