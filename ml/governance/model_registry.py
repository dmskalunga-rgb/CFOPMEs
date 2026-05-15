# ml/governance/model_registry.py
"""
Enterprise ML Model Registry.

Recursos:
- Registro e versionamento de modelos
- Ciclo de vida: draft, staging, production, archived
- Aprovação governada
- Promoção entre ambientes
- Artefatos e checksums
- Métricas de avaliação
- Lineage de dataset/pipeline/features
- Tags, metadata e auditoria básica
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Sequence


class ModelStage(str, Enum):
    DRAFT = "draft"
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


class ArtifactType(str, Enum):
    MODEL_BINARY = "model_binary"
    PREPROCESSOR = "preprocessor"
    TOKENIZER = "tokenizer"
    CONFIG = "config"
    SIGNATURE = "signature"
    EXPLAINABILITY = "explainability"
    EVALUATION_REPORT = "evaluation_report"
    DATA_SCHEMA = "data_schema"
    CUSTOM = "custom"


class RegistryEventType(str, Enum):
    CREATED = "created"
    VERSION_REGISTERED = "version_registered"
    ARTIFACT_ADDED = "artifact_added"
    METRICS_UPDATED = "metrics_updated"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROMOTED = "promoted"
    ARCHIVED = "archived"
    TAGGED = "tagged"


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    artifact_type: ArtifactType
    uri: str
    checksum_sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelLineage:
    training_dataset_id: Optional[str] = None
    validation_dataset_id: Optional[str] = None
    pipeline_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    feature_set_id: Optional[str] = None
    code_commit: Optional[str] = None
    parent_model_version_id: Optional[str] = None


@dataclass(frozen=True)
class ModelSignature:
    inputs: Dict[str, str]
    outputs: Dict[str, str]
    parameters: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelApproval:
    status: ApprovalStatus
    approver_id: Optional[str] = None
    approved_at: Optional[str] = None
    reason: Optional[str] = None
    ticket_id: Optional[str] = None


@dataclass(frozen=True)
class RegistryEvent:
    event_id: str
    event_type: RegistryEventType
    timestamp: str
    actor_id: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelVersion:
    version_id: str
    model_id: str
    version: str
    stage: ModelStage
    approval: ModelApproval
    created_at: str
    created_by: str
    description: Optional[str] = None
    signature: Optional[ModelSignature] = None
    lineage: Optional[ModelLineage] = None
    artifacts: List[ModelArtifact] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    events: List[RegistryEvent] = field(default_factory=list)


@dataclass
class RegisteredModel:
    model_id: str
    name: str
    created_at: str
    created_by: str
    description: Optional[str] = None
    owner: Optional[str] = None
    task_type: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    versions: List[ModelVersion] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ModelRegistryError(RuntimeError):
    pass


class SemanticVersion:
    PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?$")

    @classmethod
    def validate(cls, version: str) -> None:
        if not cls.PATTERN.match(version):
            raise ModelRegistryError(
                f"Versão inválida '{version}'. Use formato semântico, exemplo: 1.0.0"
            )


class Checksum:
    @staticmethod
    def sha256_file(path: str | Path) -> str:
        file_path = Path(path)

        if not file_path.exists():
            raise ModelRegistryError(f"Arquivo não encontrado: {file_path}")

        hasher = hashlib.sha256()

        with file_path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                hasher.update(chunk)

        return hasher.hexdigest()


class RegistryStore:
    def load_all(self) -> Dict[str, RegisteredModel]:
        raise NotImplementedError

    def save_all(self, models: Mapping[str, RegisteredModel]) -> None:
        raise NotImplementedError


class JsonModelRegistryStore(RegistryStore):
    def __init__(self, path: str | Path = "artifacts/model_registry/registry.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> Dict[str, RegisteredModel]:
        if not self.path.exists():
            return {}

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            model_id: self._model_from_dict(model)
            for model_id, model in raw.items()
        }

    def save_all(self, models: Mapping[str, RegisteredModel]) -> None:
        payload = {
            model_id: asdict(model)
            for model_id, model in models.items()
        }

        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _model_from_dict(self, raw: Mapping[str, Any]) -> RegisteredModel:
        versions = [self._version_from_dict(v) for v in raw.get("versions", [])]

        return RegisteredModel(
            model_id=raw["model_id"],
            name=raw["name"],
            created_at=raw["created_at"],
            created_by=raw["created_by"],
            description=raw.get("description"),
            owner=raw.get("owner"),
            task_type=raw.get("task_type"),
            tags=dict(raw.get("tags", {})),
            versions=versions,
            metadata=dict(raw.get("metadata", {})),
        )

    def _version_from_dict(self, raw: Mapping[str, Any]) -> ModelVersion:
        return ModelVersion(
            version_id=raw["version_id"],
            model_id=raw["model_id"],
            version=raw["version"],
            stage=ModelStage(raw["stage"]),
            approval=ModelApproval(
                status=ApprovalStatus(raw["approval"]["status"]),
                approver_id=raw["approval"].get("approver_id"),
                approved_at=raw["approval"].get("approved_at"),
                reason=raw["approval"].get("reason"),
                ticket_id=raw["approval"].get("ticket_id"),
            ),
            created_at=raw["created_at"],
            created_by=raw["created_by"],
            description=raw.get("description"),
            signature=(
                ModelSignature(**raw["signature"])
                if raw.get("signature")
                else None
            ),
            lineage=(
                ModelLineage(**raw["lineage"])
                if raw.get("lineage")
                else None
            ),
            artifacts=[
                ModelArtifact(
                    artifact_id=a["artifact_id"],
                    artifact_type=ArtifactType(a["artifact_type"]),
                    uri=a["uri"],
                    checksum_sha256=a.get("checksum_sha256"),
                    size_bytes=a.get("size_bytes"),
                    created_at=a["created_at"],
                    metadata=dict(a.get("metadata", {})),
                )
                for a in raw.get("artifacts", [])
            ],
            metrics=dict(raw.get("metrics", {})),
            tags=dict(raw.get("tags", {})),
            metadata=dict(raw.get("metadata", {})),
            events=[
                RegistryEvent(
                    event_id=e["event_id"],
                    event_type=RegistryEventType(e["event_type"]),
                    timestamp=e["timestamp"],
                    actor_id=e["actor_id"],
                    message=e["message"],
                    metadata=dict(e.get("metadata", {})),
                )
                for e in raw.get("events", [])
            ],
        )


class EnterpriseModelRegistry:
    def __init__(
        self,
        store: Optional[RegistryStore] = None,
        require_approval_for_production: bool = True,
    ) -> None:
        self.store = store or JsonModelRegistryStore()
        self.require_approval_for_production = require_approval_for_production
        self._lock = Lock()
        self._models: Dict[str, RegisteredModel] = self.store.load_all()

    def create_model(
        self,
        *,
        name: str,
        created_by: str,
        description: Optional[str] = None,
        owner: Optional[str] = None,
        task_type: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RegisteredModel:
        with self._lock:
            existing = self.find_model_by_name(name)
            if existing:
                raise ModelRegistryError(f"Modelo já existe: {name}")

            model = RegisteredModel(
                model_id=str(uuid.uuid4()),
                name=name,
                created_at=self._now(),
                created_by=created_by,
                description=description,
                owner=owner,
                task_type=task_type,
                tags=tags or {},
                metadata=metadata or {},
            )

            self._models[model.model_id] = model
            self._save()
            return model

    def register_version(
        self,
        *,
        model_id: str,
        version: str,
        created_by: str,
        description: Optional[str] = None,
        signature: Optional[ModelSignature] = None,
        lineage: Optional[ModelLineage] = None,
        metrics: Optional[Dict[str, float]] = None,
        artifacts: Optional[List[ModelArtifact]] = None,
        tags: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        approval_required: bool = True,
    ) -> ModelVersion:
        SemanticVersion.validate(version)

        with self._lock:
            model = self._get_model(model_id)

            if any(v.version == version for v in model.versions):
                raise ModelRegistryError(
                    f"Versão {version} já existe para modelo {model.name}"
                )

            approval = ModelApproval(
                status=ApprovalStatus.PENDING if approval_required else ApprovalStatus.NOT_REQUIRED
            )

            model_version = ModelVersion(
                version_id=str(uuid.uuid4()),
                model_id=model_id,
                version=version,
                stage=ModelStage.DRAFT,
                approval=approval,
                created_at=self._now(),
                created_by=created_by,
                description=description,
                signature=signature,
                lineage=lineage,
                artifacts=artifacts or [],
                metrics=metrics or {},
                tags=tags or {},
                metadata=metadata or {},
                events=[
                    self._event(
                        RegistryEventType.VERSION_REGISTERED,
                        created_by,
                        f"Versão {version} registrada.",
                    )
                ],
            )

            model.versions.append(model_version)
            self._save()
            return model_version

    def add_artifact(
        self,
        *,
        version_id: str,
        artifact_type: ArtifactType,
        uri: str,
        actor_id: str,
        file_path_for_checksum: Optional[str | Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ModelArtifact:
        with self._lock:
            version = self._get_version(version_id)

            checksum = None
            size = None

            if file_path_for_checksum:
                path = Path(file_path_for_checksum)
                checksum = Checksum.sha256_file(path)
                size = path.stat().st_size

            artifact = ModelArtifact(
                artifact_id=str(uuid.uuid4()),
                artifact_type=artifact_type,
                uri=uri,
                checksum_sha256=checksum,
                size_bytes=size,
                metadata=metadata or {},
            )

            version.artifacts.append(artifact)
            version.events.append(
                self._event(
                    RegistryEventType.ARTIFACT_ADDED,
                    actor_id,
                    f"Artefato adicionado: {artifact_type.value}",
                    {"artifact_id": artifact.artifact_id},
                )
            )

            self._save()
            return artifact

    def update_metrics(
        self,
        *,
        version_id: str,
        metrics: Dict[str, float],
        actor_id: str,
        replace: bool = False,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)

            if replace:
                version.metrics = dict(metrics)
            else:
                version.metrics.update(metrics)

            version.events.append(
                self._event(
                    RegistryEventType.METRICS_UPDATED,
                    actor_id,
                    "Métricas atualizadas.",
                    {"metrics": metrics},
                )
            )

            self._save()
            return version

    def approve_version(
        self,
        *,
        version_id: str,
        approver_id: str,
        reason: Optional[str] = None,
        ticket_id: Optional[str] = None,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)

            version.approval = ModelApproval(
                status=ApprovalStatus.APPROVED,
                approver_id=approver_id,
                approved_at=self._now(),
                reason=reason,
                ticket_id=ticket_id,
            )

            version.events.append(
                self._event(
                    RegistryEventType.APPROVED,
                    approver_id,
                    "Versão aprovada.",
                    {"reason": reason, "ticket_id": ticket_id},
                )
            )

            self._save()
            return version

    def reject_version(
        self,
        *,
        version_id: str,
        approver_id: str,
        reason: str,
        ticket_id: Optional[str] = None,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)

            version.stage = ModelStage.REJECTED
            version.approval = ModelApproval(
                status=ApprovalStatus.REJECTED,
                approver_id=approver_id,
                approved_at=self._now(),
                reason=reason,
                ticket_id=ticket_id,
            )

            version.events.append(
                self._event(
                    RegistryEventType.REJECTED,
                    approver_id,
                    "Versão rejeitada.",
                    {"reason": reason, "ticket_id": ticket_id},
                )
            )

            self._save()
            return version

    def promote(
        self,
        *,
        version_id: str,
        target_stage: ModelStage,
        actor_id: str,
        reason: Optional[str] = None,
        archive_existing_production: bool = True,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)

            if version.stage in {ModelStage.ARCHIVED, ModelStage.REJECTED}:
                raise ModelRegistryError(
                    f"Não é possível promover versão em estado {version.stage.value}"
                )

            if target_stage == ModelStage.PRODUCTION:
                if (
                    self.require_approval_for_production
                    and version.approval.status not in {
                        ApprovalStatus.APPROVED,
                        ApprovalStatus.NOT_REQUIRED,
                    }
                ):
                    raise ModelRegistryError(
                        "Promoção para produção exige aprovação."
                    )

                if archive_existing_production:
                    for other in self._get_model(version.model_id).versions:
                        if other.version_id != version.version_id and other.stage == ModelStage.PRODUCTION:
                            other.stage = ModelStage.ARCHIVED
                            other.events.append(
                                self._event(
                                    RegistryEventType.ARCHIVED,
                                    actor_id,
                                    "Arquivado por nova promoção para produção.",
                                )
                            )

            previous_stage = version.stage
            version.stage = target_stage

            version.events.append(
                self._event(
                    RegistryEventType.PROMOTED,
                    actor_id,
                    f"Versão promovida de {previous_stage.value} para {target_stage.value}.",
                    {"reason": reason},
                )
            )

            self._save()
            return version

    def archive_version(
        self,
        *,
        version_id: str,
        actor_id: str,
        reason: Optional[str] = None,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)
            version.stage = ModelStage.ARCHIVED

            version.events.append(
                self._event(
                    RegistryEventType.ARCHIVED,
                    actor_id,
                    "Versão arquivada.",
                    {"reason": reason},
                )
            )

            self._save()
            return version

    def tag_version(
        self,
        *,
        version_id: str,
        tags: Dict[str, str],
        actor_id: str,
    ) -> ModelVersion:
        with self._lock:
            version = self._get_version(version_id)
            version.tags.update(tags)

            version.events.append(
                self._event(
                    RegistryEventType.TAGGED,
                    actor_id,
                    "Tags atualizadas.",
                    {"tags": tags},
                )
            )

            self._save()
            return version

    def get_model(self, model_id: str) -> RegisteredModel:
        return self._get_model(model_id)

    def get_version(self, version_id: str) -> ModelVersion:
        return self._get_version(version_id)

    def find_model_by_name(self, name: str) -> Optional[RegisteredModel]:
        normalized = name.strip().lower()

        for model in self._models.values():
            if model.name.strip().lower() == normalized:
                return model

        return None

    def latest_version(
        self,
        model_id: str,
        stage: Optional[ModelStage] = None,
    ) -> Optional[ModelVersion]:
        model = self._get_model(model_id)

        versions = model.versions
        if stage:
            versions = [v for v in versions if v.stage == stage]

        if not versions:
            return None

        return sorted(versions, key=lambda v: v.created_at, reverse=True)[0]

    def list_models(self) -> List[RegisteredModel]:
        return sorted(self._models.values(), key=lambda m: m.created_at, reverse=True)

    def list_versions(
        self,
        model_id: str,
        stage: Optional[ModelStage] = None,
    ) -> List[ModelVersion]:
        model = self._get_model(model_id)

        versions = model.versions
        if stage:
            versions = [v for v in versions if v.stage == stage]

        return sorted(versions, key=lambda v: v.created_at, reverse=True)

    def search(
        self,
        *,
        name_contains: Optional[str] = None,
        tag_filters: Optional[Dict[str, str]] = None,
        stage: Optional[ModelStage] = None,
        owner: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> List[RegisteredModel]:
        results: List[RegisteredModel] = []

        for model in self._models.values():
            if name_contains and name_contains.lower() not in model.name.lower():
                continue

            if owner and model.owner != owner:
                continue

            if task_type and model.task_type != task_type:
                continue

            if tag_filters:
                matched = all(model.tags.get(k) == v for k, v in tag_filters.items())
                if not matched:
                    continue

            if stage and not any(v.stage == stage for v in model.versions):
                continue

            results.append(model)

        return sorted(results, key=lambda m: m.created_at, reverse=True)

    def export_inventory(self) -> Dict[str, Any]:
        return {
            "generated_at": self._now(),
            "model_count": len(self._models),
            "models": [asdict(m) for m in self.list_models()],
        }

    def _get_model(self, model_id: str) -> RegisteredModel:
        if model_id not in self._models:
            raise ModelRegistryError(f"Modelo não encontrado: {model_id}")
        return self._models[model_id]

    def _get_version(self, version_id: str) -> ModelVersion:
        for model in self._models.values():
            for version in model.versions:
                if version.version_id == version_id:
                    return version

        raise ModelRegistryError(f"Versão não encontrada: {version_id}")

    def _save(self) -> None:
        self.store.save_all(self._models)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _event(
        event_type: RegistryEventType,
        actor_id: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RegistryEvent:
        return RegistryEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor_id=actor_id,
            message=message,
            metadata=metadata or {},
        )


if __name__ == "__main__":
    registry = EnterpriseModelRegistry(
        store=JsonModelRegistryStore("artifacts/model_registry/demo_registry.json")
    )

    model = registry.create_model(
        name="document-router",
        created_by="thiago",
        description="Modelo de roteamento inteligente de documentos.",
        owner="digital-meta",
        task_type="classification",
        tags={"domain": "documents", "criticality": "high"},
    )

    version = registry.register_version(
        model_id=model.model_id,
        version="1.0.0",
        created_by="thiago",
        description="Primeira versão estável.",
        signature=ModelSignature(
            inputs={"text": "string", "metadata": "json"},
            outputs={"class": "string", "confidence": "float"},
        ),
        lineage=ModelLineage(
            training_dataset_id="dataset-train-v1",
            validation_dataset_id="dataset-val-v1",
            pipeline_id="train-document-router",
            pipeline_run_id="run-001",
            feature_set_id="features-doc-v1",
            code_commit="abc123",
        ),
        metrics={
            "accuracy": 0.94,
            "f1_macro": 0.91,
            "latency_p95_ms": 120.0,
        },
    )

    registry.approve_version(
        version_id=version.version_id,
        approver_id="ml-governance",
        reason="Métricas aprovadas para produção.",
        ticket_id="GOV-001",
    )

    registry.promote(
        version_id=version.version_id,
        target_stage=ModelStage.PRODUCTION,
        actor_id="thiago",
    )

    print(json.dumps(registry.export_inventory(), indent=2, ensure_ascii=False, default=str))