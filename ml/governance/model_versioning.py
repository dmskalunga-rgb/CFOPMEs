# ml/governance/model_versioning.py
"""
Enterprise ML Model Versioning.

Recursos:
- versionamento semântico
- snapshots imutáveis
- comparação entre versões
- rollback
- changelog
- compatibilidade de schema
- política de promoção
- diff de métricas, artefatos, assinatura e metadata
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
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class VersionBump(str, Enum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    PRERELEASE = "prerelease"


class VersionStatus(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class CompatibilityLevel(str, Enum):
    COMPATIBLE = "compatible"
    BACKWARD_COMPATIBLE = "backward_compatible"
    BREAKING = "breaking"
    UNKNOWN = "unknown"


class ChangeType(str, Enum):
    METRIC_CHANGED = "metric_changed"
    ARTIFACT_CHANGED = "artifact_changed"
    SIGNATURE_CHANGED = "signature_changed"
    METADATA_CHANGED = "metadata_changed"
    TAG_CHANGED = "tag_changed"
    STAGE_CHANGED = "stage_changed"
    SCHEMA_CHANGED = "schema_changed"
    CUSTOM = "custom"


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None
    build: Optional[str] = None

    VERSION_RE = re.compile(
        r"^(?P<major>0|[1-9]\d*)\."
        r"(?P<minor>0|[1-9]\d*)\."
        r"(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
        r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
    )

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = cls.VERSION_RE.match(value.strip())
        if not match:
            raise ModelVersioningError(
                f"Versão inválida: {value}. Use formato semântico, exemplo: 1.2.3"
            )

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=match.group("prerelease"),
            build=match.group("build"),
        )

    def bump(self, bump_type: VersionBump, prerelease: Optional[str] = None) -> "SemanticVersion":
        if bump_type == VersionBump.MAJOR:
            return SemanticVersion(self.major + 1, 0, 0, prerelease=prerelease)

        if bump_type == VersionBump.MINOR:
            return SemanticVersion(self.major, self.minor + 1, 0, prerelease=prerelease)

        if bump_type == VersionBump.PATCH:
            return SemanticVersion(self.major, self.minor, self.patch + 1, prerelease=prerelease)

        if bump_type == VersionBump.PRERELEASE:
            return SemanticVersion(self.major, self.minor, self.patch, prerelease=prerelease or "rc.1")

        raise ModelVersioningError(f"Tipo de bump inválido: {bump_type}")

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += f"-{self.prerelease}"
        if self.build:
            value += f"+{self.build}"
        return value

    def sort_key(self) -> Tuple[int, int, int, int, str]:
        prerelease_rank = 0 if self.prerelease else 1
        return self.major, self.minor, self.patch, prerelease_rank, self.prerelease or ""


@dataclass(frozen=True)
class ModelArtifactSnapshot:
    name: str
    uri: str
    artifact_type: str
    checksum_sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelSchemaField:
    name: str
    dtype: str
    required: bool = True
    shape: Optional[Sequence[int]] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class ModelSignatureSnapshot:
    inputs: List[ModelSchemaField]
    outputs: List[ModelSchemaField]
    parameters: List[ModelSchemaField] = field(default_factory=list)


@dataclass(frozen=True)
class ModelVersionSnapshot:
    snapshot_id: str
    model_id: str
    model_name: str
    version: str
    status: VersionStatus
    created_at: str
    created_by: str
    artifacts: List[ModelArtifactSnapshot] = field(default_factory=list)
    signature: Optional[ModelSignatureSnapshot] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    parent_snapshot_id: Optional[str] = None
    source_commit: Optional[str] = None
    training_run_id: Optional[str] = None
    checksum_sha256: Optional[str] = None

    def canonical_payload(self, include_checksum: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_checksum:
            data["checksum_sha256"] = None
        return data

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VersionChange:
    change_type: ChangeType
    field: str
    old_value: Any
    new_value: Any
    severity: str = "info"
    description: Optional[str] = None


@dataclass(frozen=True)
class VersionComparison:
    base_version: str
    candidate_version: str
    compatibility: CompatibilityLevel
    changes: List[VersionChange]
    metric_deltas: Dict[str, float]
    breaking_changes: List[VersionChange]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class VersionLogEntry:
    log_id: str
    timestamp: str
    snapshot_id: str
    version: str
    actor_id: str
    status: VersionStatus
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class ModelVersioningError(RuntimeError):
    pass


class SnapshotIntegrity:
    @staticmethod
    def compute_checksum(snapshot: ModelVersionSnapshot) -> str:
        payload = snapshot.canonical_payload(include_checksum=False)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def verify(snapshot: ModelVersionSnapshot) -> bool:
        if not snapshot.checksum_sha256:
            return False
        return SnapshotIntegrity.compute_checksum(snapshot) == snapshot.checksum_sha256

    @staticmethod
    def file_checksum(path: str | Path) -> str:
        file_path = Path(path)
        if not file_path.exists():
            raise ModelVersioningError(f"Arquivo não encontrado: {file_path}")

        h = hashlib.sha256()
        with file_path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


class VersionStore:
    def load_snapshots(self) -> Dict[str, ModelVersionSnapshot]:
        raise NotImplementedError

    def save_snapshots(self, snapshots: Mapping[str, ModelVersionSnapshot]) -> None:
        raise NotImplementedError

    def load_logs(self) -> List[VersionLogEntry]:
        raise NotImplementedError

    def save_logs(self, logs: Sequence[VersionLogEntry]) -> None:
        raise NotImplementedError


class JsonVersionStore(VersionStore):
    def __init__(self, base_dir: str | Path = "artifacts/model_versioning") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_path = self.base_dir / "snapshots.json"
        self.logs_path = self.base_dir / "version_logs.json"

    def load_snapshots(self) -> Dict[str, ModelVersionSnapshot]:
        if not self.snapshots_path.exists():
            return {}

        raw = json.loads(self.snapshots_path.read_text(encoding="utf-8"))
        return {sid: self._snapshot_from_dict(data) for sid, data in raw.items()}

    def save_snapshots(self, snapshots: Mapping[str, ModelVersionSnapshot]) -> None:
        payload = {sid: snapshot.to_dict() for sid, snapshot in snapshots.items()}
        self._atomic_write(self.snapshots_path, payload)

    def load_logs(self) -> List[VersionLogEntry]:
        if not self.logs_path.exists():
            return []

        raw = json.loads(self.logs_path.read_text(encoding="utf-8"))
        return [VersionLogEntry(**item) for item in raw]

    def save_logs(self, logs: Sequence[VersionLogEntry]) -> None:
        self._atomic_write(self.logs_path, [asdict(log) for log in logs])

    def _atomic_write(self, path: Path, payload: Any) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    def _snapshot_from_dict(self, raw: Mapping[str, Any]) -> ModelVersionSnapshot:
        signature = None

        if raw.get("signature"):
            sig = raw["signature"]
            signature = ModelSignatureSnapshot(
                inputs=[ModelSchemaField(**x) for x in sig.get("inputs", [])],
                outputs=[ModelSchemaField(**x) for x in sig.get("outputs", [])],
                parameters=[ModelSchemaField(**x) for x in sig.get("parameters", [])],
            )

        return ModelVersionSnapshot(
            snapshot_id=raw["snapshot_id"],
            model_id=raw["model_id"],
            model_name=raw["model_name"],
            version=raw["version"],
            status=VersionStatus(raw["status"]),
            created_at=raw["created_at"],
            created_by=raw["created_by"],
            artifacts=[ModelArtifactSnapshot(**a) for a in raw.get("artifacts", [])],
            signature=signature,
            metrics=dict(raw.get("metrics", {})),
            tags=dict(raw.get("tags", {})),
            metadata=dict(raw.get("metadata", {})),
            parent_snapshot_id=raw.get("parent_snapshot_id"),
            source_commit=raw.get("source_commit"),
            training_run_id=raw.get("training_run_id"),
            checksum_sha256=raw.get("checksum_sha256"),
        )


class VersionComparator:
    def compare(
        self,
        base: ModelVersionSnapshot,
        candidate: ModelVersionSnapshot,
    ) -> VersionComparison:
        changes: List[VersionChange] = []
        metric_deltas: Dict[str, float] = {}

        changes.extend(self._compare_signature(base.signature, candidate.signature))
        changes.extend(self._compare_artifacts(base.artifacts, candidate.artifacts))
        changes.extend(self._compare_dict("metadata", base.metadata, candidate.metadata, ChangeType.METADATA_CHANGED))
        changes.extend(self._compare_dict("tags", base.tags, candidate.tags, ChangeType.TAG_CHANGED))

        metric_keys = sorted(set(base.metrics) | set(candidate.metrics))
        for key in metric_keys:
            old = base.metrics.get(key)
            new = candidate.metrics.get(key)

            if old != new:
                if old is not None and new is not None:
                    metric_deltas[key] = float(new) - float(old)

                changes.append(
                    VersionChange(
                        change_type=ChangeType.METRIC_CHANGED,
                        field=f"metrics.{key}",
                        old_value=old,
                        new_value=new,
                        severity="info",
                    )
                )

        breaking = [c for c in changes if c.severity == "breaking"]

        compatibility = (
            CompatibilityLevel.BREAKING
            if breaking
            else CompatibilityLevel.BACKWARD_COMPATIBLE
            if any(c.change_type == ChangeType.SIGNATURE_CHANGED for c in changes)
            else CompatibilityLevel.COMPATIBLE
        )

        return VersionComparison(
            base_version=base.version,
            candidate_version=candidate.version,
            compatibility=compatibility,
            changes=changes,
            metric_deltas=metric_deltas,
            breaking_changes=breaking,
        )

    def _compare_signature(
        self,
        base: Optional[ModelSignatureSnapshot],
        candidate: Optional[ModelSignatureSnapshot],
    ) -> List[VersionChange]:
        if base == candidate:
            return []

        if base is None or candidate is None:
            return [
                VersionChange(
                    change_type=ChangeType.SIGNATURE_CHANGED,
                    field="signature",
                    old_value=asdict(base) if base else None,
                    new_value=asdict(candidate) if candidate else None,
                    severity="breaking",
                    description="Assinatura ausente em uma das versões.",
                )
            ]

        changes: List[VersionChange] = []

        changes.extend(self._compare_schema_fields("inputs", base.inputs, candidate.inputs, input_schema=True))
        changes.extend(self._compare_schema_fields("outputs", base.outputs, candidate.outputs, input_schema=False))
        changes.extend(self._compare_schema_fields("parameters", base.parameters, candidate.parameters, input_schema=True))

        return changes

    def _compare_schema_fields(
        self,
        prefix: str,
        base_fields: Sequence[ModelSchemaField],
        candidate_fields: Sequence[ModelSchemaField],
        *,
        input_schema: bool,
    ) -> List[VersionChange]:
        changes: List[VersionChange] = []

        base_map = {f.name: f for f in base_fields}
        cand_map = {f.name: f for f in candidate_fields}

        removed = sorted(set(base_map) - set(cand_map))
        added = sorted(set(cand_map) - set(base_map))

        for name in removed:
            changes.append(
                VersionChange(
                    change_type=ChangeType.SCHEMA_CHANGED,
                    field=f"{prefix}.{name}",
                    old_value=asdict(base_map[name]),
                    new_value=None,
                    severity="breaking",
                    description="Campo removido.",
                )
            )

        for name in added:
            field = cand_map[name]
            severity = "breaking" if input_schema and field.required else "info"

            changes.append(
                VersionChange(
                    change_type=ChangeType.SCHEMA_CHANGED,
                    field=f"{prefix}.{name}",
                    old_value=None,
                    new_value=asdict(field),
                    severity=severity,
                    description="Campo obrigatório adicionado." if severity == "breaking" else "Campo opcional adicionado.",
                )
            )

        for name in sorted(set(base_map) & set(cand_map)):
            old = base_map[name]
            new = cand_map[name]

            if old.dtype != new.dtype or old.shape != new.shape:
                changes.append(
                    VersionChange(
                        change_type=ChangeType.SCHEMA_CHANGED,
                        field=f"{prefix}.{name}",
                        old_value=asdict(old),
                        new_value=asdict(new),
                        severity="breaking",
                        description="Tipo ou shape alterado.",
                    )
                )
            elif old.required != new.required:
                severity = "breaking" if old.required is False and new.required is True else "info"
                changes.append(
                    VersionChange(
                        change_type=ChangeType.SCHEMA_CHANGED,
                        field=f"{prefix}.{name}.required",
                        old_value=old.required,
                        new_value=new.required,
                        severity=severity,
                    )
                )

        return changes

    def _compare_artifacts(
        self,
        base: Sequence[ModelArtifactSnapshot],
        candidate: Sequence[ModelArtifactSnapshot],
    ) -> List[VersionChange]:
        changes: List[VersionChange] = []

        base_map = {a.name: a for a in base}
        cand_map = {a.name: a for a in candidate}

        for name in sorted(set(base_map) | set(cand_map)):
            old = base_map.get(name)
            new = cand_map.get(name)

            if old != new:
                changes.append(
                    VersionChange(
                        change_type=ChangeType.ARTIFACT_CHANGED,
                        field=f"artifacts.{name}",
                        old_value=asdict(old) if old else None,
                        new_value=asdict(new) if new else None,
                        severity="info",
                    )
                )

        return changes

    def _compare_dict(
        self,
        prefix: str,
        old: Mapping[str, Any],
        new: Mapping[str, Any],
        change_type: ChangeType,
    ) -> List[VersionChange]:
        changes: List[VersionChange] = []

        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                changes.append(
                    VersionChange(
                        change_type=change_type,
                        field=f"{prefix}.{key}",
                        old_value=old.get(key),
                        new_value=new.get(key),
                        severity="info",
                    )
                )

        return changes


class PromotionPolicy:
    def __init__(
        self,
        required_metrics: Optional[Mapping[str, float]] = None,
        max_allowed_regression: Optional[Mapping[str, float]] = None,
        require_compatible_schema: bool = True,
        require_integrity: bool = True,
    ) -> None:
        self.required_metrics = dict(required_metrics or {})
        self.max_allowed_regression = dict(max_allowed_regression or {})
        self.require_compatible_schema = require_compatible_schema
        self.require_integrity = require_integrity

    def evaluate(
        self,
        *,
        base: Optional[ModelVersionSnapshot],
        candidate: ModelVersionSnapshot,
        comparison: Optional[VersionComparison] = None,
    ) -> Dict[str, Any]:
        violations: List[str] = []

        if self.require_integrity and not SnapshotIntegrity.verify(candidate):
            violations.append("Checksum do snapshot inválido.")

        for metric, minimum in self.required_metrics.items():
            value = candidate.metrics.get(metric)
            if value is None:
                violations.append(f"Métrica obrigatória ausente: {metric}")
            elif value < minimum:
                violations.append(f"Métrica {metric} abaixo do mínimo: {value} < {minimum}")

        if base and comparison:
            if self.require_compatible_schema and comparison.compatibility == CompatibilityLevel.BREAKING:
                violations.append("Mudança incompatível de schema/assinatura.")

            for metric, max_drop in self.max_allowed_regression.items():
                delta = comparison.metric_deltas.get(metric)
                if delta is not None and delta < -abs(max_drop):
                    violations.append(f"Regressão excessiva em {metric}: {delta}")

        return {
            "approved": len(violations) == 0,
            "violations": violations,
        }


class EnterpriseModelVersioning:
    def __init__(
        self,
        store: Optional[VersionStore] = None,
        promotion_policy: Optional[PromotionPolicy] = None,
    ) -> None:
        self.store = store or JsonVersionStore()
        self.promotion_policy = promotion_policy or PromotionPolicy()
        self.snapshots = self.store.load_snapshots()
        self.logs = self.store.load_logs()
        self.comparator = VersionComparator()

    def create_snapshot(
        self,
        *,
        model_id: str,
        model_name: str,
        version: str,
        created_by: str,
        artifacts: Optional[List[ModelArtifactSnapshot]] = None,
        signature: Optional[ModelSignatureSnapshot] = None,
        metrics: Optional[Dict[str, float]] = None,
        tags: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_snapshot_id: Optional[str] = None,
        source_commit: Optional[str] = None,
        training_run_id: Optional[str] = None,
    ) -> ModelVersionSnapshot:
        SemanticVersion.parse(version)

        if self.find_snapshot(model_id=model_id, version=version):
            raise ModelVersioningError(f"Snapshot já existe para {model_name}:{version}")

        snapshot = ModelVersionSnapshot(
            snapshot_id=str(uuid.uuid4()),
            model_id=model_id,
            model_name=model_name,
            version=version,
            status=VersionStatus.CREATED,
            created_at=self._now(),
            created_by=created_by,
            artifacts=artifacts or [],
            signature=signature,
            metrics=metrics or {},
            tags=tags or {},
            metadata=metadata or {},
            parent_snapshot_id=parent_snapshot_id,
            source_commit=source_commit,
            training_run_id=training_run_id,
            checksum_sha256=None,
        )

        checksum = SnapshotIntegrity.compute_checksum(snapshot)
        signed = self._replace(snapshot, checksum_sha256=checksum)

        self.snapshots[signed.snapshot_id] = signed
        self._log(signed, created_by, VersionStatus.CREATED, f"Snapshot criado: {model_name}:{version}")
        self._save()

        return signed

    def next_version(
        self,
        *,
        model_id: str,
        bump: VersionBump,
        prerelease: Optional[str] = None,
    ) -> str:
        latest = self.latest_snapshot(model_id)

        if latest is None:
            return str(SemanticVersion(0, 1, 0, prerelease=prerelease))

        parsed = SemanticVersion.parse(latest.version)
        return str(parsed.bump(bump, prerelease=prerelease))

    def compare_versions(
        self,
        *,
        base_snapshot_id: str,
        candidate_snapshot_id: str,
    ) -> VersionComparison:
        base = self.get_snapshot(base_snapshot_id)
        candidate = self.get_snapshot(candidate_snapshot_id)
        return self.comparator.compare(base, candidate)

    def validate_snapshot(self, snapshot_id: str, actor_id: str) -> Dict[str, Any]:
        snapshot = self.get_snapshot(snapshot_id)

        result = {
            "snapshot_id": snapshot_id,
            "valid_checksum": SnapshotIntegrity.verify(snapshot),
            "has_artifact": len(snapshot.artifacts) > 0,
            "has_signature": snapshot.signature is not None,
            "has_metrics": len(snapshot.metrics) > 0,
        }

        approved = all(result.values()) if result else False

        updated = self._replace(
            snapshot,
            status=VersionStatus.VALIDATED if approved else snapshot.status,
        )

        self.snapshots[snapshot_id] = updated

        self._log(
            updated,
            actor_id,
            updated.status,
            "Snapshot validado." if approved else "Validação do snapshot falhou.",
            result,
        )

        self._save()
        return result

    def approve_snapshot(
        self,
        *,
        snapshot_id: str,
        actor_id: str,
        base_snapshot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        candidate = self.get_snapshot(snapshot_id)
        base = self.get_snapshot(base_snapshot_id) if base_snapshot_id else self.latest_promoted(candidate.model_id)

        comparison = self.comparator.compare(base, candidate) if base else None

        policy = self.promotion_policy.evaluate(
            base=base,
            candidate=candidate,
            comparison=comparison,
        )

        if not policy["approved"]:
            self._log(candidate, actor_id, candidate.status, "Aprovação negada.", policy)
            self._save()
            return policy

        updated = self._replace(candidate, status=VersionStatus.APPROVED)
        self.snapshots[snapshot_id] = updated

        self._log(updated, actor_id, VersionStatus.APPROVED, "Snapshot aprovado.", policy)
        self._save()

        return policy

    def promote_snapshot(
        self,
        *,
        snapshot_id: str,
        actor_id: str,
        archive_previous: bool = False,
    ) -> ModelVersionSnapshot:
        snapshot = self.get_snapshot(snapshot_id)

        if snapshot.status not in {VersionStatus.APPROVED, VersionStatus.VALIDATED}:
            raise ModelVersioningError("Snapshot precisa estar aprovado ou validado para promoção.")

        if archive_previous:
            for other in list(self.snapshots.values()):
                if (
                    other.model_id == snapshot.model_id
                    and other.snapshot_id != snapshot.snapshot_id
                    and other.status == VersionStatus.PROMOTED
                ):
                    archived = self._replace(other, status=VersionStatus.ARCHIVED)
                    self.snapshots[archived.snapshot_id] = archived
                    self._log(archived, actor_id, VersionStatus.ARCHIVED, "Snapshot anterior arquivado.")

        promoted = self._replace(snapshot, status=VersionStatus.PROMOTED)
        self.snapshots[snapshot_id] = promoted

        self._log(promoted, actor_id, VersionStatus.PROMOTED, "Snapshot promovido.")
        self._save()

        return promoted

    def rollback(
        self,
        *,
        model_id: str,
        target_snapshot_id: str,
        actor_id: str,
        reason: str,
    ) -> ModelVersionSnapshot:
        target = self.get_snapshot(target_snapshot_id)

        if target.model_id != model_id:
            raise ModelVersioningError("Snapshot alvo não pertence ao modelo informado.")

        for snapshot in list(self.snapshots.values()):
            if snapshot.model_id == model_id and snapshot.status == VersionStatus.PROMOTED:
                deprecated = self._replace(snapshot, status=VersionStatus.DEPRECATED)
                self.snapshots[deprecated.snapshot_id] = deprecated
                self._log(
                    deprecated,
                    actor_id,
                    VersionStatus.DEPRECATED,
                    "Snapshot depreciado por rollback.",
                    {"rollback_target": target_snapshot_id},
                )

        rolled = self._replace(target, status=VersionStatus.ROLLED_BACK)
        self.snapshots[target_snapshot_id] = rolled

        self._log(
            rolled,
            actor_id,
            VersionStatus.ROLLED_BACK,
            "Rollback executado.",
            {"reason": reason},
        )

        self._save()
        return rolled

    def changelog(
        self,
        *,
        model_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[VersionLogEntry]:
        logs = self.logs

        if model_id:
            snapshot_ids = {
                s.snapshot_id
                for s in self.snapshots.values()
                if s.model_id == model_id
            }
            logs = [log for log in logs if log.snapshot_id in snapshot_ids]

        logs = sorted(logs, key=lambda x: x.timestamp, reverse=True)

        if limit:
            logs = logs[:limit]

        return logs

    def get_snapshot(self, snapshot_id: str) -> ModelVersionSnapshot:
        if snapshot_id not in self.snapshots:
            raise ModelVersioningError(f"Snapshot não encontrado: {snapshot_id}")
        return self.snapshots[snapshot_id]

    def find_snapshot(
        self,
        *,
        model_id: str,
        version: str,
    ) -> Optional[ModelVersionSnapshot]:
        for snapshot in self.snapshots.values():
            if snapshot.model_id == model_id and snapshot.version == version:
                return snapshot
        return None

    def latest_snapshot(self, model_id: str) -> Optional[ModelVersionSnapshot]:
        items = [s for s in self.snapshots.values() if s.model_id == model_id]
        if not items:
            return None

        return sorted(
            items,
            key=lambda s: SemanticVersion.parse(s.version).sort_key(),
            reverse=True,
        )[0]

    def latest_promoted(self, model_id: str) -> Optional[ModelVersionSnapshot]:
        items = [
            s for s in self.snapshots.values()
            if s.model_id == model_id and s.status == VersionStatus.PROMOTED
        ]

        if not items:
            return None

        return sorted(items, key=lambda s: s.created_at, reverse=True)[0]

    def export_inventory(self) -> Dict[str, Any]:
        return {
            "generated_at": self._now(),
            "snapshot_count": len(self.snapshots),
            "snapshots": [s.to_dict() for s in self.snapshots.values()],
            "logs": [asdict(log) for log in self.logs],
        }

    def _log(
        self,
        snapshot: ModelVersionSnapshot,
        actor_id: str,
        status: VersionStatus,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logs.append(
            VersionLogEntry(
                log_id=str(uuid.uuid4()),
                timestamp=self._now(),
                snapshot_id=snapshot.snapshot_id,
                version=snapshot.version,
                actor_id=actor_id,
                status=status,
                message=message,
                metadata=metadata or {},
            )
        )

    def _save(self) -> None:
        self.store.save_snapshots(self.snapshots)
        self.store.save_logs(self.logs)

    @staticmethod
    def _replace(snapshot: ModelVersionSnapshot, **changes: Any) -> ModelVersionSnapshot:
        data = asdict(snapshot)
        data.update(changes)

        signature = data.get("signature")
        if isinstance(signature, dict):
            signature = ModelSignatureSnapshot(
                inputs=[ModelSchemaField(**x) for x in signature.get("inputs", [])],
                outputs=[ModelSchemaField(**x) for x in signature.get("outputs", [])],
                parameters=[ModelSchemaField(**x) for x in signature.get("parameters", [])],
            )

        return ModelVersionSnapshot(
            snapshot_id=data["snapshot_id"],
            model_id=data["model_id"],
            model_name=data["model_name"],
            version=data["version"],
            status=VersionStatus(data["status"]),
            created_at=data["created_at"],
            created_by=data["created_by"],
            artifacts=[
                a if isinstance(a, ModelArtifactSnapshot) else ModelArtifactSnapshot(**a)
                for a in data.get("artifacts", [])
            ],
            signature=signature,
            metrics=dict(data.get("metrics", {})),
            tags=dict(data.get("tags", {})),
            metadata=dict(data.get("metadata", {})),
            parent_snapshot_id=data.get("parent_snapshot_id"),
            source_commit=data.get("source_commit"),
            training_run_id=data.get("training_run_id"),
            checksum_sha256=data.get("checksum_sha256"),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    versioning = EnterpriseModelVersioning(
        store=JsonVersionStore("artifacts/model_versioning/demo"),
        promotion_policy=PromotionPolicy(
            required_metrics={"accuracy": 0.90, "f1_macro": 0.85},
            max_allowed_regression={"accuracy": 0.03},
        ),
    )

    signature = ModelSignatureSnapshot(
        inputs=[
            ModelSchemaField("text", "string", required=True),
            ModelSchemaField("metadata", "json", required=False),
        ],
        outputs=[
            ModelSchemaField("class", "string", required=True),
            ModelSchemaField("confidence", "float", required=True),
        ],
    )

    snapshot = versioning.create_snapshot(
        model_id="document-router",
        model_name="Document Router",
        version="1.0.0",
        created_by="thiago",
        artifacts=[
            ModelArtifactSnapshot(
                name="model.pkl",
                uri="s3://models/document-router/1.0.0/model.pkl",
                artifact_type="model_binary",
                checksum_sha256="demo-checksum",
            )
        ],
        signature=signature,
        metrics={"accuracy": 0.94, "f1_macro": 0.91},
        tags={"env": "staging"},
        source_commit="abc123",
        training_run_id="train-run-001",
    )

    print(versioning.validate_snapshot(snapshot.snapshot_id, actor_id="thiago"))
    print(versioning.approve_snapshot(snapshot_id=snapshot.snapshot_id, actor_id="governance"))
    promoted = versioning.promote_snapshot(snapshot_id=snapshot.snapshot_id, actor_id="thiago")

    print(json.dumps(promoted.to_dict(), indent=2, ensure_ascii=False, default=str))