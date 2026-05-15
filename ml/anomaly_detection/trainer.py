# ml/anomaly_detection/trainer.py
"""
Enterprise Anomaly Detection Trainer.

Recursos:
- carregamento de datasets JSON/JSONL/CSV
- inferência ou configuração explícita de features
- feature engineering integrado
- treino de detector de anomalias
- avaliação supervisionada opcional quando há labels
- comparação simples de configurações
- persistência de modelo, feature engineer e relatórios
- artefatos prontos para serving e governance
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


try:
    from ml.anomaly_detection.evaluator import (
        AnomalyEvaluationReport,
        EnterpriseAnomalyEvaluator,
        ThresholdConfig,
        ThresholdStrategy,
    )
    from ml.anomaly_detection.features import (
        AnomalyFeatureEngineer,
        FeatureBuildResult,
        FeatureConfig,
        infer_feature_config,
    )
    from ml.anomaly_detection.model import (
        AnomalyModelConfig,
        AnomalyTrainingResult,
        DetectorType,
        EnterpriseAnomalyDetector,
    )
except Exception:  # pragma: no cover
    from evaluator import (  # type: ignore
        AnomalyEvaluationReport,
        EnterpriseAnomalyEvaluator,
        ThresholdConfig,
        ThresholdStrategy,
    )
    from features import (  # type: ignore
        AnomalyFeatureEngineer,
        FeatureBuildResult,
        FeatureConfig,
        infer_feature_config,
    )
    from model import (  # type: ignore
        AnomalyModelConfig,
        AnomalyTrainingResult,
        DetectorType,
        EnterpriseAnomalyDetector,
    )


class TrainerError(RuntimeError):
    pass


class DatasetFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


class TrainerStatus(str, Enum):
    SUCCESS = "success"
    FAILED_DATA_QUALITY = "failed_data_quality"
    FAILED_TRAINING = "failed_training"


@dataclass(frozen=True)
class AnomalyTrainerConfig:
    experiment_name: str = "anomaly_detection_training"
    artifact_dir: str = "artifacts/anomaly_detection/training"

    dataset_format: Optional[DatasetFormat] = None
    label_field: Optional[str] = "label"
    record_id_field: str = "record_id"

    min_samples: int = 100
    max_missing_ratio: float = 0.80

    auto_infer_features: bool = True
    feature_config: Optional[FeatureConfig] = None
    model_config: AnomalyModelConfig = field(default_factory=AnomalyModelConfig)

    evaluate_if_labels_available: bool = True
    threshold_config: ThresholdConfig = field(
        default_factory=lambda: ThresholdConfig(strategy=ThresholdStrategy.MAX_F1)
    )

    compare_detectors: bool = False
    detector_candidates: Sequence[DetectorType] = (
        DetectorType.ISOLATION_FOREST,
        DetectorType.ROBUST_ZSCORE,
        DetectorType.ENSEMBLE,
    )

    fail_on_data_quality_error: bool = True


@dataclass(frozen=True)
class DataQualityIssue:
    field: str
    message: str
    severity: str
    row_index: Optional[int] = None


@dataclass(frozen=True)
class TrainingDataset:
    records: List[Dict[str, Any]]
    labels: Optional[List[int]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingArtifact:
    name: str
    path: str
    artifact_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectorCandidateResult:
    detector_type: DetectorType
    training_result: AnomalyTrainingResult
    evaluation_report: Optional[AnomalyEvaluationReport]
    model_path: str
    score: float


@dataclass(frozen=True)
class AnomalyTrainingReport:
    run_id: str
    experiment_name: str
    started_at: str
    finished_at: str
    status: TrainerStatus
    samples: int
    labels_available: bool
    positive_labels: Optional[int]
    feature_count: int
    selected_detector: DetectorType
    training_result: Optional[AnomalyTrainingResult]
    evaluation_report: Optional[AnomalyEvaluationReport]
    candidate_results: List[DetectorCandidateResult]
    artifacts: List[TrainingArtifact]
    data_quality_issues: List[DataQualityIssue]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class AnomalyDatasetLoader:
    def load(
        self,
        path: str | Path,
        *,
        dataset_format: Optional[DatasetFormat] = None,
        label_field: Optional[str] = "label",
    ) -> TrainingDataset:
        source = Path(path)

        if not source.exists():
            raise TrainerError(f"Dataset não encontrado: {source}")

        fmt = dataset_format or self._infer_format(source)

        if fmt == DatasetFormat.JSON:
            raw = json.loads(source.read_text(encoding="utf-8"))
            rows = raw["records"] if isinstance(raw, Mapping) and "records" in raw else raw

        elif fmt == DatasetFormat.JSONL:
            rows = [
                json.loads(line)
                for line in source.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        elif fmt == DatasetFormat.CSV:
            with source.open("r", encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))

        else:
            raise TrainerError(f"Formato não suportado: {fmt}")

        if not isinstance(rows, list):
            raise TrainerError("Dataset precisa ser uma lista de registros.")

        return self.from_records(
            rows,
            label_field=label_field,
            metadata={"source_path": str(source), "format": fmt.value},
        )

    def from_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        label_field: Optional[str] = "label",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrainingDataset:
        clean_records: List[Dict[str, Any]] = []
        labels: Optional[List[int]] = [] if label_field else None

        for row in records:
            item = dict(row)

            if label_field and label_field in item:
                assert labels is not None
                labels.append(self._parse_label(item[label_field]))

            clean_records.append(item)

        if label_field and labels is not None and len(labels) != len(clean_records):
            labels = None

        return TrainingDataset(
            records=clean_records,
            labels=labels,
            metadata=metadata or {},
        )

    def _infer_format(self, path: Path) -> DatasetFormat:
        suffix = path.suffix.lower()

        if suffix == ".json":
            return DatasetFormat.JSON
        if suffix == ".jsonl":
            return DatasetFormat.JSONL
        if suffix == ".csv":
            return DatasetFormat.CSV

        raise TrainerError(f"Formato não inferido para: {path}")

    @staticmethod
    def _parse_label(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)

        if isinstance(value, (int, float)):
            return 1 if int(value) == 1 else 0

        normalized = str(value).strip().lower()

        if normalized in {"1", "true", "yes", "sim", "anomaly", "anomalia", "fraud"}:
            return 1

        if normalized in {"0", "false", "no", "nao", "não", "normal"}:
            return 0

        raise TrainerError(f"Label inválido: {value}")


class AnomalyDatasetValidator:
    def validate(
        self,
        dataset: TrainingDataset,
        config: AnomalyTrainerConfig,
    ) -> List[DataQualityIssue]:
        issues: List[DataQualityIssue] = []

        if len(dataset.records) < config.min_samples:
            issues.append(
                DataQualityIssue(
                    field="dataset",
                    message=f"Amostras insuficientes: {len(dataset.records)} < {config.min_samples}",
                    severity="high",
                )
            )

        if not dataset.records:
            issues.append(
                DataQualityIssue(
                    field="dataset",
                    message="Dataset vazio.",
                    severity="critical",
                )
            )
            return issues

        all_fields = sorted(set().union(*(r.keys() for r in dataset.records)))

        for field_name in all_fields:
            missing = sum(1 for r in dataset.records if r.get(field_name) in (None, ""))
            ratio = missing / max(len(dataset.records), 1)

            if ratio >= config.max_missing_ratio:
                issues.append(
                    DataQualityIssue(
                        field=field_name,
                        message=f"Campo com missing ratio alto: {ratio:.2%}",
                        severity="medium",
                    )
                )

        if dataset.labels is not None:
            if len(dataset.labels) != len(dataset.records):
                issues.append(
                    DataQualityIssue(
                        field=config.label_field or "label",
                        message="Quantidade de labels diferente da quantidade de registros.",
                        severity="critical",
                    )
                )

            positives = sum(dataset.labels)
            if positives == 0:
                issues.append(
                    DataQualityIssue(
                        field=config.label_field or "label",
                        message="Nenhum exemplo positivo/anômalo nos labels.",
                        severity="low",
                    )
                )

        return issues


class AnomalyArtifactManager:
    def __init__(self, config: AnomalyTrainerConfig) -> None:
        self.config = config

    def create_run_dir(self, run_id: str) -> Path:
        path = Path(self.config.artifact_dir) / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_dataset_snapshot(self, dataset: TrainingDataset, run_dir: Path) -> TrainingArtifact:
        path = run_dir / "dataset_snapshot.json"

        payload = {
            "records": dataset.records,
            "labels": dataset.labels,
            "metadata": dataset.metadata,
        }

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        return TrainingArtifact(
            name="dataset_snapshot",
            path=str(path),
            artifact_type="dataset",
            metadata={"samples": len(dataset.records)},
        )

    def save_feature_engineer(
        self,
        feature_engineer: AnomalyFeatureEngineer,
        run_dir: Path,
    ) -> TrainingArtifact:
        path = run_dir / "feature_engineer.pkl"
        feature_engineer.save(path)

        return TrainingArtifact(
            name="feature_engineer",
            path=str(path),
            artifact_type="preprocessor",
        )

    def save_features(
        self,
        feature_result: FeatureBuildResult,
        run_dir: Path,
    ) -> TrainingArtifact:
        path = run_dir / "features.json"

        path.write_text(feature_result.to_json(), encoding="utf-8")

        matrix_path = run_dir / "features.npy"
        np.save(matrix_path, feature_result.to_matrix())

        return TrainingArtifact(
            name="features",
            path=str(path),
            artifact_type="features",
            metadata={
                "matrix_path": str(matrix_path),
                "rows": len(feature_result.vectors),
                "columns": len(feature_result.feature_names),
            },
        )

    def save_model(
        self,
        model: EnterpriseAnomalyDetector,
        run_dir: Path,
        *,
        filename: str = "anomaly_model.pkl",
    ) -> TrainingArtifact:
        path = run_dir / filename
        model.save(path)

        return TrainingArtifact(
            name=filename.replace(".pkl", ""),
            path=str(path),
            artifact_type="model_binary",
            metadata={
                "model_name": model.config.model_name,
                "model_version": model.config.model_version,
                "detector_type": model.config.detector_type.value,
            },
        )

    def save_report(self, report: AnomalyTrainingReport, run_dir: Path) -> TrainingArtifact:
        json_path = run_dir / "training_report.json"
        md_path = run_dir / "training_report.md"

        json_path.write_text(report.to_json(), encoding="utf-8")
        md_path.write_text(self._markdown(report), encoding="utf-8")

        return TrainingArtifact(
            name="training_report",
            path=str(json_path),
            artifact_type="report",
            metadata={"markdown_path": str(md_path)},
        )

    def _markdown(self, report: AnomalyTrainingReport) -> str:
        lines = [
            "# Anomaly Detection Training Report",
            "",
            f"- Run ID: `{report.run_id}`",
            f"- Experiment: `{report.experiment_name}`",
            f"- Status: `{report.status.value}`",
            f"- Selected detector: `{report.selected_detector.value}`",
            f"- Samples: `{report.samples}`",
            f"- Feature count: `{report.feature_count}`",
            f"- Labels available: `{report.labels_available}`",
            "",
        ]

        if report.training_result:
            lines.extend(
                [
                    "## Training Metrics",
                    "",
                    "| Metric | Value |",
                    "|---|---:|",
                ]
            )

            for key, value in sorted(report.training_result.metrics.items()):
                lines.append(f"| {key} | {value:.6f} |")

        if report.evaluation_report:
            lines.extend(
                [
                    "",
                    "## Evaluation Metrics",
                    "",
                    "| Metric | Value |",
                    "|---|---:|",
                ]
            )

            for key, value in sorted(report.evaluation_report.metrics.items()):
                lines.append(f"| {key} | {value:.6f} |")

        if report.candidate_results:
            lines.extend(
                [
                    "",
                    "## Candidate Models",
                    "",
                    "| Detector | Score | Model Path |",
                    "|---|---:|---|",
                ]
            )

            for candidate in report.candidate_results:
                lines.append(
                    f"| {candidate.detector_type.value} | {candidate.score:.6f} | `{candidate.model_path}` |"
                )

        if report.data_quality_issues:
            lines.extend(["", "## Data Quality Issues", ""])
            for issue in report.data_quality_issues:
                lines.append(f"- `{issue.severity}` **{issue.field}**: {issue.message}")

        lines.extend(["", "## Artifacts", ""])
        for artifact in report.artifacts:
            lines.append(f"- **{artifact.name}** `{artifact.artifact_type}`: `{artifact.path}`")

        return "\n".join(lines)


class EnterpriseAnomalyTrainer:
    def __init__(
        self,
        config: Optional[AnomalyTrainerConfig] = None,
        loader: Optional[AnomalyDatasetLoader] = None,
        validator: Optional[AnomalyDatasetValidator] = None,
    ) -> None:
        self.config = config or AnomalyTrainerConfig()
        self.loader = loader or AnomalyDatasetLoader()
        self.validator = validator or AnomalyDatasetValidator()
        self.artifacts = AnomalyArtifactManager(self.config)

    def train_from_file(
        self,
        path: str | Path,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AnomalyTrainingReport:
        dataset = self.loader.load(
            path,
            dataset_format=self.config.dataset_format,
            label_field=self.config.label_field,
        )

        dataset = TrainingDataset(
            records=dataset.records,
            labels=dataset.labels,
            metadata={**dataset.metadata, **(metadata or {})},
        )

        return self.train(dataset)

    def train(self, dataset: TrainingDataset) -> AnomalyTrainingReport:
        run_id = f"anomaly-train-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc).isoformat()
        run_dir = self.artifacts.create_run_dir(run_id)

        artifacts: List[TrainingArtifact] = []
        candidate_results: List[DetectorCandidateResult] = []

        try:
            issues = self.validator.validate(dataset, self.config)
            critical = [i for i in issues if i.severity in {"high", "critical"}]

            artifacts.append(self.artifacts.save_dataset_snapshot(dataset, run_dir))

            if critical and self.config.fail_on_data_quality_error:
                report = self._build_report(
                    run_id=run_id,
                    started_at=started_at,
                    status=TrainerStatus.FAILED_DATA_QUALITY,
                    dataset=dataset,
                    feature_count=0,
                    selected_detector=self.config.model_config.detector_type,
                    training_result=None,
                    evaluation_report=None,
                    candidate_results=[],
                    artifacts=artifacts,
                    issues=issues,
                    metadata={"reason": "data_quality_failed"},
                )
                artifacts.append(self.artifacts.save_report(report, run_dir))
                return report

            feature_config = self._resolve_feature_config(dataset)
            feature_engineer = AnomalyFeatureEngineer(feature_config)
            feature_result = feature_engineer.fit_transform(dataset.records)

            artifacts.append(self.artifacts.save_feature_engineer(feature_engineer, run_dir))
            artifacts.append(self.artifacts.save_features(feature_result, run_dir))

            if self.config.compare_detectors:
                selected_model, selected_training, selected_eval, candidate_results = self._train_candidates(
                    feature_result,
                    dataset,
                    run_dir,
                )
            else:
                selected_model, selected_training, selected_eval = self._train_single(
                    feature_result,
                    dataset,
                    self.config.model_config,
                )
                model_artifact = self.artifacts.save_model(selected_model, run_dir)
                artifacts.append(model_artifact)

            report = self._build_report(
                run_id=run_id,
                started_at=started_at,
                status=TrainerStatus.SUCCESS,
                dataset=dataset,
                feature_count=len(feature_result.feature_names),
                selected_detector=selected_training.detector_type,
                training_result=selected_training,
                evaluation_report=selected_eval,
                candidate_results=candidate_results,
                artifacts=artifacts,
                issues=issues,
                metadata={
                    "artifact_dir": str(run_dir),
                    **dataset.metadata,
                },
            )

            artifacts.append(self.artifacts.save_report(report, run_dir))

            final_report = self._build_report(
                run_id=run_id,
                started_at=started_at,
                status=TrainerStatus.SUCCESS,
                dataset=dataset,
                feature_count=len(feature_result.feature_names),
                selected_detector=selected_training.detector_type,
                training_result=selected_training,
                evaluation_report=selected_eval,
                candidate_results=candidate_results,
                artifacts=artifacts,
                issues=issues,
                metadata={
                    "artifact_dir": str(run_dir),
                    **dataset.metadata,
                },
            )

            self.artifacts.save_report(final_report, run_dir)
            return final_report

        except Exception as exc:
            report = self._build_report(
                run_id=run_id,
                started_at=started_at,
                status=TrainerStatus.FAILED_TRAINING,
                dataset=dataset,
                feature_count=0,
                selected_detector=self.config.model_config.detector_type,
                training_result=None,
                evaluation_report=None,
                candidate_results=[],
                artifacts=artifacts,
                issues=[
                    DataQualityIssue(
                        field="trainer",
                        message=str(exc),
                        severity="critical",
                    )
                ],
                metadata={"error_type": exc.__class__.__name__},
            )
            artifacts.append(self.artifacts.save_report(report, run_dir))
            return report

    def _resolve_feature_config(self, dataset: TrainingDataset) -> FeatureConfig:
        if self.config.feature_config is not None:
            return self.config.feature_config

        if self.config.auto_infer_features:
            return infer_feature_config(dataset.records)

        raise TrainerError("feature_config obrigatório quando auto_infer_features=False.")

    def _train_single(
        self,
        feature_result: FeatureBuildResult,
        dataset: TrainingDataset,
        model_config: AnomalyModelConfig,
    ) -> Tuple[EnterpriseAnomalyDetector, AnomalyTrainingResult, Optional[AnomalyEvaluationReport]]:
        matrix = feature_result.to_matrix()

        model = EnterpriseAnomalyDetector(model_config)
        training_result = model.train(
            matrix,
            feature_names=feature_result.feature_names,
            metadata={
                "feature_run_id": feature_result.run_id,
                **dataset.metadata,
            },
        )

        evaluation = None

        if (
            self.config.evaluate_if_labels_available
            and dataset.labels is not None
            and len(dataset.labels) == len(matrix)
        ):
            scores = model.score_batch(matrix)
            evaluator = EnterpriseAnomalyEvaluator(self.config.threshold_config)
            evaluation = evaluator.evaluate(
                scores.tolist(),
                y_true=dataset.labels,
                model_name=model_config.model_name,
                model_version=model_config.model_version,
                metadata={"feature_run_id": feature_result.run_id},
            )

        return model, training_result, evaluation

    def _train_candidates(
        self,
        feature_result: FeatureBuildResult,
        dataset: TrainingDataset,
        run_dir: Path,
    ) -> Tuple[
        EnterpriseAnomalyDetector,
        AnomalyTrainingResult,
        Optional[AnomalyEvaluationReport],
        List[DetectorCandidateResult],
    ]:
        best_model: Optional[EnterpriseAnomalyDetector] = None
        best_training: Optional[AnomalyTrainingResult] = None
        best_eval: Optional[AnomalyEvaluationReport] = None
        best_score = -float("inf")

        candidates: List[DetectorCandidateResult] = []

        for detector_type in self.config.detector_candidates:
            cfg = AnomalyModelConfig(
                **{
                    **asdict(self.config.model_config),
                    "detector_type": detector_type,
                }
            )

            model, training, evaluation = self._train_single(feature_result, dataset, cfg)

            model_artifact = self.artifacts.save_model(
                model,
                run_dir,
                filename=f"anomaly_model_{detector_type.value}.pkl",
            )

            score = self._candidate_score(training, evaluation)

            candidate = DetectorCandidateResult(
                detector_type=detector_type,
                training_result=training,
                evaluation_report=evaluation,
                model_path=model_artifact.path,
                score=score,
            )
            candidates.append(candidate)

            if score > best_score:
                best_score = score
                best_model = model
                best_training = training
                best_eval = evaluation

        if best_model is None or best_training is None:
            raise TrainerError("Nenhum candidato treinado com sucesso.")

        self.artifacts.save_model(best_model, run_dir, filename="anomaly_model.pkl")

        return best_model, best_training, best_eval, candidates

    @staticmethod
    def _candidate_score(
        training: AnomalyTrainingResult,
        evaluation: Optional[AnomalyEvaluationReport],
    ) -> float:
        if evaluation:
            if "average_precision" in evaluation.metrics:
                return float(evaluation.metrics["average_precision"])
            if "f1" in evaluation.metrics:
                return float(evaluation.metrics["f1"])
            if "roc_auc" in evaluation.metrics:
                return float(evaluation.metrics["roc_auc"])

        return float(training.metrics.get("training_score_p95", 0.0))

    def _build_report(
        self,
        *,
        run_id: str,
        started_at: str,
        status: TrainerStatus,
        dataset: TrainingDataset,
        feature_count: int,
        selected_detector: DetectorType,
        training_result: Optional[AnomalyTrainingResult],
        evaluation_report: Optional[AnomalyEvaluationReport],
        candidate_results: List[DetectorCandidateResult],
        artifacts: List[TrainingArtifact],
        issues: List[DataQualityIssue],
        metadata: Dict[str, Any],
    ) -> AnomalyTrainingReport:
        labels_available = dataset.labels is not None
        positive_labels = sum(dataset.labels) if dataset.labels is not None else None

        return AnomalyTrainingReport(
            run_id=run_id,
            experiment_name=self.config.experiment_name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status=status,
            samples=len(dataset.records),
            labels_available=labels_available,
            positive_labels=positive_labels,
            feature_count=feature_count,
            selected_detector=selected_detector,
            training_result=training_result,
            evaluation_report=evaluation_report,
            candidate_results=candidate_results,
            artifacts=artifacts,
            data_quality_issues=issues,
            metadata=metadata,
        )


def generate_synthetic_training_dataset(
    samples: int = 1000,
    anomaly_rate: float = 0.05,
    seed: int = 42,
) -> TrainingDataset:
    rng = np.random.default_rng(seed)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    records: List[Dict[str, Any]] = []
    labels: List[int] = []

    for i in range(samples):
        is_anomaly = rng.random() < anomaly_rate

        amount = float(rng.normal(500, 120))
        latency = float(rng.normal(120, 30))
        error_count = float(rng.poisson(1))

        if is_anomaly:
            amount *= float(rng.uniform(4, 10))
            latency *= float(rng.uniform(2, 5))
            error_count += float(rng.integers(5, 20))

        records.append(
            {
                "record_id": f"rec-{i}",
                "entity_id": f"entity-{int(rng.integers(1, 80))}",
                "timestamp": (start + __import__("datetime").timedelta(minutes=i * 5)).isoformat(),
                "amount": max(amount, 0.0),
                "latency_ms": max(latency, 0.0),
                "error_count": error_count,
                "channel": str(rng.choice(["web", "mobile", "api"])),
                "country": str(rng.choice(["BR", "AR", "US", "XX"] if is_anomaly else ["BR", "AR", "US"])),
                "is_new_device": bool(is_anomaly and rng.random() < 0.5),
                "label": 1 if is_anomaly else 0,
            }
        )
        labels.append(1 if is_anomaly else 0)

    return TrainingDataset(
        records=records,
        labels=labels,
        metadata={
            "synthetic": True,
            "samples": samples,
            "anomaly_rate": anomaly_rate,
        },
    )


if __name__ == "__main__":
    dataset = generate_synthetic_training_dataset(samples=1000, anomaly_rate=0.06)

    config = AnomalyTrainerConfig(
        experiment_name="enterprise_anomaly_detection_demo",
        artifact_dir="artifacts/anomaly_detection/demo_training",
        compare_detectors=True,
        model_config=AnomalyModelConfig(
            model_name="enterprise_anomaly_detector",
            model_version="1.0.0",
            detector_type=DetectorType.ENSEMBLE,
            contamination=0.06,
        ),
    )

    trainer = EnterpriseAnomalyTrainer(config)
    report = trainer.train(dataset)

    print(report.to_json())