# ml/fraud_detection/trainer.py
"""
Enterprise Fraud Detection Trainer.

Responsável por:
- carregar datasets JSON/JSONL/CSV
- validar dados de treino
- construir perfis históricos
- treinar EnterpriseFraudDetectionModel
- avaliar métricas
- salvar artefatos
- gerar relatório de treino
- preparar integração com registry/governance
"""

from __future__ import annotations

import csv
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


try:
    from ml.fraud_detection.model import (
        EnterpriseFraudDetectionModel,
        FraudFeatureExtractor,
        FraudModelConfig,
        Transaction,
        UserProfile,
        build_profiles_from_history,
    )
except Exception:  # pragma: no cover
    from model import (  # type: ignore
        EnterpriseFraudDetectionModel,
        FraudFeatureExtractor,
        FraudModelConfig,
        Transaction,
        UserProfile,
        build_profiles_from_history,
    )


class TrainerError(RuntimeError):
    pass


class DatasetFormat(str, Enum):
    JSON = "json"
    JSONL = "jsonl"
    CSV = "csv"


class SplitStrategy(str, Enum):
    RANDOM_STRATIFIED = "random_stratified"
    TEMPORAL = "temporal"
    NONE = "none"


@dataclass(frozen=True)
class TrainerConfig:
    experiment_name: str = "fraud_detection_training"
    artifact_dir: str = "artifacts/fraud_detection"
    model_filename: str = "fraud_model.pkl"

    split_strategy: SplitStrategy = SplitStrategy.RANDOM_STRATIFIED
    validation_size: float = 0.2
    random_state: int = 42

    min_samples: int = 100
    min_positive_samples: int = 5
    fail_on_data_quality_error: bool = True

    risky_merchants: Dict[str, float] = field(default_factory=dict)
    risky_countries: Dict[str, float] = field(default_factory=dict)

    model_config: FraudModelConfig = field(default_factory=FraudModelConfig)


@dataclass(frozen=True)
class TrainingDataset:
    transactions: List[Transaction]
    labels: List[int]
    profiles: Dict[str, UserProfile]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataQualityIssue:
    field: str
    message: str
    severity: str
    row_index: Optional[int] = None


@dataclass(frozen=True)
class TrainingArtifact:
    name: str
    path: str
    artifact_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingReport:
    run_id: str
    experiment_name: str
    started_at: str
    finished_at: str
    status: str
    samples: int
    positive_samples: int
    negative_samples: int
    metrics: Dict[str, float]
    feature_names: List[str]
    artifacts: List[TrainingArtifact]
    data_quality_issues: List[DataQualityIssue]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class FraudDatasetLoader:
    def load(
        self,
        path: str | Path,
        dataset_format: Optional[DatasetFormat] = None,
    ) -> TrainingDataset:
        source = Path(path)

        if not source.exists():
            raise TrainerError(f"Dataset não encontrado: {source}")

        fmt = dataset_format or self._infer_format(source)

        if fmt == DatasetFormat.JSON:
            rows = json.loads(source.read_text(encoding="utf-8"))
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

        return self.from_rows(rows, metadata={"source_path": str(source), "format": fmt.value})

    def from_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrainingDataset:
        transactions: List[Transaction] = []
        labels: List[int] = []

        for i, row in enumerate(rows):
            tx_raw = row.get("transaction", row)
            label = row.get("label", row.get("is_fraud", row.get("fraud")))

            if label is None:
                raise TrainerError(f"Label ausente na linha {i}.")

            transaction = Transaction(
                transaction_id=str(tx_raw.get("transaction_id") or tx_raw.get("id") or f"tx-{i}"),
                user_id=str(tx_raw["user_id"]),
                amount=float(tx_raw["amount"]),
                currency=str(tx_raw.get("currency", "BRL")),
                merchant_id=tx_raw.get("merchant_id"),
                merchant_category=tx_raw.get("merchant_category"),
                country=tx_raw.get("country"),
                device_id=tx_raw.get("device_id"),
                ip_address=tx_raw.get("ip_address"),
                timestamp=str(tx_raw.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                metadata=dict(tx_raw.get("metadata", {})) if isinstance(tx_raw.get("metadata", {}), Mapping) else {},
            )

            transactions.append(transaction)
            labels.append(self._parse_label(label))

        profiles = build_profiles_from_history(transactions)

        return TrainingDataset(
            transactions=transactions,
            labels=labels,
            profiles=profiles,
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

        raise TrainerError(f"Não foi possível inferir formato do dataset: {path}")

    @staticmethod
    def _parse_label(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)

        if isinstance(value, (int, float)):
            return 1 if int(value) == 1 else 0

        normalized = str(value).strip().lower()

        if normalized in {"1", "true", "fraud", "fraude", "yes", "sim"}:
            return 1

        if normalized in {"0", "false", "legit", "normal", "no", "nao", "não"}:
            return 0

        raise TrainerError(f"Label inválido: {value}")


class FraudDatasetValidator:
    def validate(
        self,
        dataset: TrainingDataset,
        config: TrainerConfig,
    ) -> List[DataQualityIssue]:
        issues: List[DataQualityIssue] = []

        total = len(dataset.transactions)
        positives = sum(dataset.labels)

        if total < config.min_samples:
            issues.append(
                DataQualityIssue(
                    field="dataset",
                    message=f"Amostras insuficientes: {total} < {config.min_samples}",
                    severity="high",
                )
            )

        if positives < config.min_positive_samples:
            issues.append(
                DataQualityIssue(
                    field="label",
                    message=f"Poucos exemplos positivos: {positives} < {config.min_positive_samples}",
                    severity="high",
                )
            )

        if len(dataset.transactions) != len(dataset.labels):
            issues.append(
                DataQualityIssue(
                    field="dataset",
                    message="Quantidade de transações diferente da quantidade de labels.",
                    severity="critical",
                )
            )

        ids = set()

        for i, tx in enumerate(dataset.transactions):
            if not tx.transaction_id:
                issues.append(DataQualityIssue("transaction_id", "transaction_id vazio.", "critical", i))

            if tx.transaction_id in ids:
                issues.append(DataQualityIssue("transaction_id", "transaction_id duplicado.", "medium", i))

            ids.add(tx.transaction_id)

            if not tx.user_id:
                issues.append(DataQualityIssue("user_id", "user_id vazio.", "critical", i))

            if tx.amount < 0:
                issues.append(DataQualityIssue("amount", "amount negativo.", "critical", i))

            if not tx.currency:
                issues.append(DataQualityIssue("currency", "currency vazio.", "high", i))

        return issues


class FraudTrainingSplitter:
    def split(
        self,
        dataset: TrainingDataset,
        config: TrainerConfig,
    ) -> Tuple[TrainingDataset, Optional[TrainingDataset]]:
        if config.split_strategy == SplitStrategy.NONE:
            return dataset, None

        n = len(dataset.transactions)
        indices = np.arange(n)

        if config.split_strategy == SplitStrategy.TEMPORAL:
            indices = np.array(
                sorted(
                    range(n),
                    key=lambda i: dataset.transactions[i].timestamp,
                )
            )
        else:
            rng = np.random.default_rng(config.random_state)
            positives = indices[np.asarray(dataset.labels) == 1]
            negatives = indices[np.asarray(dataset.labels) == 0]

            rng.shuffle(positives)
            rng.shuffle(negatives)

            pos_val = max(1, int(len(positives) * config.validation_size))
            neg_val = max(1, int(len(negatives) * config.validation_size))

            val_indices = np.concatenate([positives[:pos_val], negatives[:neg_val]])
            train_indices = np.concatenate([positives[pos_val:], negatives[neg_val:]])

            rng.shuffle(train_indices)
            rng.shuffle(val_indices)

            return self._subset(dataset, train_indices), self._subset(dataset, val_indices)

        split_at = int(n * (1.0 - config.validation_size))
        train_idx = indices[:split_at]
        val_idx = indices[split_at:]

        return self._subset(dataset, train_idx), self._subset(dataset, val_idx)

    def _subset(
        self,
        dataset: TrainingDataset,
        indices: Sequence[int],
    ) -> TrainingDataset:
        transactions = [dataset.transactions[int(i)] for i in indices]
        labels = [dataset.labels[int(i)] for i in indices]
        profiles = build_profiles_from_history(transactions)

        return TrainingDataset(
            transactions=transactions,
            labels=labels,
            profiles=profiles,
            metadata=dataset.metadata,
        )


class FraudTrainingEvaluator:
    def evaluate(
        self,
        model: EnterpriseFraudDetectionModel,
        dataset: TrainingDataset,
    ) -> Dict[str, float]:
        predictions = model.predict_batch(dataset.transactions, dataset.profiles)

        y_true = np.asarray(dataset.labels, dtype=int)
        y_score = np.asarray([p.risk_score for p in predictions], dtype=float)
        y_pred = np.asarray([1 if p.decision.value in {"review", "block"} else 0 for p in predictions], dtype=int)

        tp = int(np.sum((y_true == 1) & (y_pred == 1)))
        fp = int(np.sum((y_true == 0) & (y_pred == 1)))
        tn = int(np.sum((y_true == 0) & (y_pred == 0)))
        fn = int(np.sum((y_true == 1) & (y_pred == 0)))

        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        accuracy = (tp + tn) / max(len(y_true), 1)

        return {
            "accuracy": float(accuracy),
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "tp": float(tp),
            "fp": float(fp),
            "tn": float(tn),
            "fn": float(fn),
            "avg_risk_score": float(np.mean(y_score)) if len(y_score) else 0.0,
            "p95_risk_score": float(np.percentile(y_score, 95)) if len(y_score) else 0.0,
        }


class FraudArtifactManager:
    def __init__(self, config: TrainerConfig) -> None:
        self.config = config

    def create_run_dir(self, run_id: str) -> Path:
        path = Path(self.config.artifact_dir) / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_dataset_snapshot(self, dataset: TrainingDataset, run_dir: Path) -> TrainingArtifact:
        path = run_dir / "dataset_snapshot.json"

        payload = {
            "transactions": [asdict(tx) for tx in dataset.transactions],
            "labels": dataset.labels,
            "profiles": {k: asdict(v) for k, v in dataset.profiles.items()},
            "metadata": dataset.metadata,
        }

        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        return TrainingArtifact(
            name="dataset_snapshot",
            path=str(path),
            artifact_type="dataset",
            metadata={"samples": len(dataset.transactions)},
        )

    def save_model(
        self,
        model: EnterpriseFraudDetectionModel,
        run_dir: Path,
    ) -> TrainingArtifact:
        path = run_dir / self.config.model_filename
        model.save(path)

        return TrainingArtifact(
            name="fraud_model",
            path=str(path),
            artifact_type="model_binary",
            metadata={
                "model_name": model.config.model_name,
                "model_version": model.config.model_version,
            },
        )

    def save_feature_importance(
        self,
        model: EnterpriseFraudDetectionModel,
        run_dir: Path,
    ) -> TrainingArtifact:
        path = run_dir / "feature_importance.json"
        importance = model.feature_importance()

        path.write_text(json.dumps(importance, ensure_ascii=False, indent=2), encoding="utf-8")

        return TrainingArtifact(
            name="feature_importance",
            path=str(path),
            artifact_type="explainability",
            metadata={"features": len(importance)},
        )

    def save_report(self, report: TrainingReport, run_dir: Path) -> TrainingArtifact:
        path = run_dir / "training_report.json"
        path.write_text(report.to_json(), encoding="utf-8")

        md_path = run_dir / "training_report.md"
        md_path.write_text(self._markdown_report(report), encoding="utf-8")

        return TrainingArtifact(
            name="training_report",
            path=str(path),
            artifact_type="report",
            metadata={"markdown_path": str(md_path)},
        )

    def _markdown_report(self, report: TrainingReport) -> str:
        lines = [
            f"# Fraud Detection Training Report",
            "",
            f"- Run ID: `{report.run_id}`",
            f"- Experiment: `{report.experiment_name}`",
            f"- Status: `{report.status}`",
            f"- Started at: `{report.started_at}`",
            f"- Finished at: `{report.finished_at}`",
            f"- Samples: `{report.samples}`",
            f"- Positive samples: `{report.positive_samples}`",
            f"- Negative samples: `{report.negative_samples}`",
            "",
            "## Metrics",
            "",
            "| Metric | Value |",
            "|---|---:|",
        ]

        for key, value in sorted(report.metrics.items()):
            lines.append(f"| {key} | {value:.6f} |")

        lines.extend(["", "## Artifacts", ""])

        for artifact in report.artifacts:
            lines.append(f"- **{artifact.name}** `{artifact.artifact_type}`: `{artifact.path}`")

        if report.data_quality_issues:
            lines.extend(["", "## Data Quality Issues", ""])
            for issue in report.data_quality_issues:
                lines.append(f"- `{issue.severity}` {issue.field}: {issue.message}")

        return "\n".join(lines)


class EnterpriseFraudTrainer:
    def __init__(
        self,
        config: Optional[TrainerConfig] = None,
        loader: Optional[FraudDatasetLoader] = None,
        validator: Optional[FraudDatasetValidator] = None,
        splitter: Optional[FraudTrainingSplitter] = None,
        evaluator: Optional[FraudTrainingEvaluator] = None,
    ) -> None:
        self.config = config or TrainerConfig()
        self.loader = loader or FraudDatasetLoader()
        self.validator = validator or FraudDatasetValidator()
        self.splitter = splitter or FraudTrainingSplitter()
        self.evaluator = evaluator or FraudTrainingEvaluator()
        self.artifacts = FraudArtifactManager(self.config)

    def train_from_file(
        self,
        path: str | Path,
        *,
        dataset_format: Optional[DatasetFormat] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TrainingReport:
        dataset = self.loader.load(path, dataset_format)

        dataset = TrainingDataset(
            transactions=dataset.transactions,
            labels=dataset.labels,
            profiles=dataset.profiles,
            metadata={**dataset.metadata, **(metadata or {})},
        )

        return self.train(dataset)

    def train(
        self,
        dataset: TrainingDataset,
    ) -> TrainingReport:
        run_id = f"fraud-train-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
        started_at = datetime.now(timezone.utc).isoformat()
        run_dir = self.artifacts.create_run_dir(run_id)

        issues = self.validator.validate(dataset, self.config)

        critical = [i for i in issues if i.severity in {"high", "critical"}]
        if critical and self.config.fail_on_data_quality_error:
            report = self._failed_report(run_id, started_at, dataset, issues)
            self.artifacts.save_report(report, run_dir)
            return report

        train_dataset, validation_dataset = self.splitter.split(dataset, self.config)

        extractor = FraudFeatureExtractor(
            risky_merchants=self.config.risky_merchants,
            risky_countries=self.config.risky_countries,
        )

        model = EnterpriseFraudDetectionModel(
            config=self.config.model_config,
            feature_extractor=extractor,
        )

        training_result = model.train(
            train_dataset.transactions,
            train_dataset.labels,
            profiles=train_dataset.profiles,
            validation_size=self.config.validation_size,
            metadata={
                "run_id": run_id,
                "experiment_name": self.config.experiment_name,
                **dataset.metadata,
            },
        )

        metrics = dict(training_result.metrics)

        if validation_dataset is not None and validation_dataset.transactions:
            validation_metrics = self.evaluator.evaluate(model, validation_dataset)
            metrics.update({f"validation_{k}": v for k, v in validation_metrics.items()})

        artifacts: List[TrainingArtifact] = []
        artifacts.append(self.artifacts.save_dataset_snapshot(dataset, run_dir))
        artifacts.append(self.artifacts.save_model(model, run_dir))
        artifacts.append(self.artifacts.save_feature_importance(model, run_dir))

        report = TrainingReport(
            run_id=run_id,
            experiment_name=self.config.experiment_name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="success",
            samples=len(dataset.transactions),
            positive_samples=sum(dataset.labels),
            negative_samples=len(dataset.labels) - sum(dataset.labels),
            metrics=metrics,
            feature_names=training_result.feature_names,
            artifacts=artifacts,
            data_quality_issues=issues,
            metadata={
                "model_name": self.config.model_config.model_name,
                "model_version": self.config.model_config.model_version,
                "artifact_dir": str(run_dir),
                **dataset.metadata,
            },
        )

        report_artifact = self.artifacts.save_report(report, run_dir)
        final_report = TrainingReport(
            **{
                **asdict(report),
                "artifacts": artifacts + [report_artifact],
            }
        )

        self.artifacts.save_report(final_report, run_dir)

        return final_report

    def _failed_report(
        self,
        run_id: str,
        started_at: str,
        dataset: TrainingDataset,
        issues: List[DataQualityIssue],
    ) -> TrainingReport:
        return TrainingReport(
            run_id=run_id,
            experiment_name=self.config.experiment_name,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            status="failed_data_quality",
            samples=len(dataset.transactions),
            positive_samples=sum(dataset.labels),
            negative_samples=len(dataset.labels) - sum(dataset.labels),
            metrics={},
            feature_names=[],
            artifacts=[],
            data_quality_issues=issues,
            metadata=dataset.metadata,
        )


def generate_synthetic_fraud_dataset(
    samples: int = 1000,
    fraud_rate: float = 0.12,
    seed: int = 42,
) -> TrainingDataset:
    rng = np.random.default_rng(seed)

    transactions: List[Transaction] = []
    labels: List[int] = []

    for i in range(samples):
        is_fraud = rng.random() < fraud_rate

        amount = (
            float(rng.normal(9000, 4500))
            if is_fraud
            else float(rng.normal(350, 180))
        )

        amount = max(amount, 5.0)

        transactions.append(
            Transaction(
                transaction_id=f"tx-{i}",
                user_id=f"user-{i % 120}",
                amount=amount,
                currency="BRL",
                merchant_id="risky-merchant" if is_fraud and rng.random() < 0.7 else "normal-merchant",
                merchant_category="digital_goods" if is_fraud else "grocery",
                country="XX" if is_fraud and rng.random() < 0.6 else "BR",
                device_id=f"device-{rng.integers(1, 300)}",
                ip_address=f"10.0.0.{rng.integers(1, 255)}",
                metadata={
                    "account_age_days": int(rng.integers(1, 1000)),
                    "device_risk": float(rng.uniform(0.1, 0.8) if is_fraud else rng.uniform(0.0, 0.2)),
                },
            )
        )

        labels.append(1 if is_fraud else 0)

    return TrainingDataset(
        transactions=transactions,
        labels=labels,
        profiles=build_profiles_from_history(transactions),
        metadata={
            "synthetic": True,
            "samples": samples,
            "fraud_rate": fraud_rate,
        },
    )


if __name__ == "__main__":
    config = TrainerConfig(
        experiment_name="fraud_detection_enterprise_demo",
        artifact_dir="artifacts/fraud_detection/demo",
        min_samples=100,
        min_positive_samples=5,
        risky_merchants={"risky-merchant": 0.25},
        risky_countries={"XX": 0.25},
        model_config=FraudModelConfig(
            model_name="enterprise_fraud_detector",
            model_version="1.0.0",
        ),
    )

    dataset = generate_synthetic_fraud_dataset(samples=1000, fraud_rate=0.12)

    trainer = EnterpriseFraudTrainer(config)
    report = trainer.train(dataset)

    print(report.to_json())