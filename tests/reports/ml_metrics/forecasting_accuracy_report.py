"""
===============================================================================
KwanzaControl Enterprise Forecasting Accuracy Report Engine
File: reports/ml_metrics/forecasting_accuracy_report.py

Description:
    Enterprise-grade forecasting evaluation and accuracy reporting engine for:

    - Time series forecast validation
    - Model accuracy benchmarking (MAPE, MAE, RMSE, WAPE)
    - Bias detection in predictions
    - Forecast drift monitoring
    - Multi-model comparison (champion vs challenger)
    - SLA compliance for forecasting systems
    - Business KPI alignment tracking
    - Financial forecasting governance
    - Demand forecasting validation
    - Inventory & revenue forecast monitoring
    - CI/CD ML quality gates
    - JSON / HTML / Markdown enterprise reporting
    - Historical forecasting performance tracking

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

import json
import logging
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List

# =============================================================================
# BASE PATHS
# =============================================================================

ROOT_DIR = Path(__file__).resolve().parents[2]

REPORTS_DIR = ROOT_DIR / "reports"
ML_DIR = REPORTS_DIR / "ml_metrics"

EXPORTS_DIR = ML_DIR / "exports"
LOGS_DIR = ML_DIR / "logs"
HISTORY_DIR = ML_DIR / "history"

# =============================================================================
# LOGGER
# =============================================================================


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("forecasting_accuracy_report")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR / "forecasting_accuracy_report.log",
        encoding="utf-8",
    )

    stream_handler = logging.StreamHandler(sys.stdout)

    file_handler.setFormatter(formatter)
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger


logger = setup_logger()

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(slots=True)
class ForecastRecord:
    model_name: str
    horizon: int
    actual: float
    predicted: float
    timestamp: str
    series: str


@dataclass(slots=True)
class ForecastMetrics:
    model_name: str
    mape: float
    mae: float
    rmse: float
    wape: float
    bias: float
    accuracy_score: float


@dataclass(slots=True)
class ForecastSummary:
    total_predictions: int
    models_evaluated: int
    best_model: str
    worst_model: str
    avg_mape: float
    avg_rmse: float
    generated_at: str


@dataclass(slots=True)
class ForecastGovernance:
    compliant_models: int
    non_compliant_models: int
    sla_compliance_rate: float
    forecast_risk_level: str


# =============================================================================
# ENGINE
# =============================================================================


class ForecastingAccuracyReportEngine:
    """
    Enterprise forecasting evaluation engine.
    """

    def __init__(self, forecast_file: Path) -> None:
        self.forecast_file = forecast_file
        self.raw_data: Dict[str, Any] = {}
        self.records: List[ForecastRecord] = []
        self.metrics: Dict[str, ForecastMetrics] = {}

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info("ForecastingAccuracyReportEngine initialized.")

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:
        logger.info("Loading forecasting dataset...")

        if not self.forecast_file.exists():
            raise FileNotFoundError(f"Forecast file not found: {self.forecast_file}")

        with open(self.forecast_file, encoding="utf-8") as f:
            self.raw_data = json.load(f)

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:
        logger.info("Parsing forecasting records...")

        for r in self.raw_data.get("forecasts", []):

            self.records.append(
                ForecastRecord(
                    model_name=r.get("model_name", ""),
                    horizon=int(r.get("horizon", 1)),
                    actual=float(r.get("actual", 0)),
                    predicted=float(r.get("predicted", 0)),
                    timestamp=r.get("timestamp", ""),
                    series=r.get("series", "default"),
                )
            )

    # =========================================================================
    # METRICS
    # =========================================================================

    def compute_metrics(self) -> None:
        logger.info("Computing forecasting metrics...")

        grouped: Dict[str, List[ForecastRecord]] = {}

        for r in self.records:
            grouped.setdefault(r.model_name, []).append(r)

        for model, records in grouped.items():

            errors = []
            abs_errors = []
            pct_errors = []
            squared_errors = []
            actuals = []

            for r in records:

                error = r.predicted - r.actual
                abs_error = abs(error)

                errors.append(error)
                abs_errors.append(abs_error)
                actuals.append(r.actual)

                if r.actual != 0:
                    pct_errors.append(abs_error / abs(r.actual))

                squared_errors.append(error ** 2)

            mae = statistics.mean(abs_errors)
            rmse = math.sqrt(statistics.mean(squared_errors))
            mape = statistics.mean(pct_errors) * 100 if pct_errors else 0
            wape = (sum(abs_errors) / max(sum(actuals), 1)) * 100

            bias = statistics.mean(errors)

            accuracy = max(0, 100 - mape)

            self.metrics[model] = ForecastMetrics(
                model_name=model,
                mape=round(mape, 4),
                mae=round(mae, 4),
                rmse=round(rmse, 4),
                wape=round(wape, 4),
                bias=round(bias, 4),
                accuracy_score=round(accuracy, 2),
            )

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(self) -> ForecastSummary:

        models = list(self.metrics.values())

        best = max(models, key=lambda x: x.accuracy_score, default=None)
        worst = min(models, key=lambda x: x.accuracy_score, default=None)

        return ForecastSummary(
            total_predictions=len(self.records),
            models_evaluated=len(models),
            best_model=best.model_name if best else "",
            worst_model=worst.model_name if worst else "",
            avg_mape=round(statistics.mean([m.mape for m in models]) if models else 0, 4),
            avg_rmse=round(statistics.mean([m.rmse for m in models]) if models else 0, 4),
            generated_at=datetime.now(UTC).isoformat(),
        )

    # =========================================================================
    # GOVERNANCE
    # =========================================================================

    def governance(self) -> ForecastGovernance:

        compliant = [
            m for m in self.metrics.values()
            if m.mape < 15 and m.bias < 5
        ]

        non_compliant = [
            m for m in self.metrics.values()
            if m not in compliant
        ]

        sla_rate = len(compliant) / max(len(self.metrics), 1)

        if sla_rate > 0.9:
            risk = "LOW_RISK"
        elif sla_rate > 0.7:
            risk = "MEDIUM_RISK"
        else:
            risk = "HIGH_RISK"

        return ForecastGovernance(
            compliant_models=len(compliant),
            non_compliant_models=len(non_compliant),
            sla_compliance_rate=round(sla_rate * 100, 2),
            forecast_risk_level=risk,
        )

    # =========================================================================
    # EXPORT
    # =========================================================================

    def export(self) -> Path:

        payload = {
            "summary": asdict(self.summary()),
            "governance": asdict(self.governance()),
            "metrics": {k: asdict(v) for k, v in self.metrics.items()},
        }

        path = EXPORTS_DIR / "forecasting_accuracy_report.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)

        logger.info("Forecasting report exported.")

        return path

    # =========================================================================
    # MARKDOWN
    # =========================================================================

    def export_markdown(self) -> Path:

        s = self.summary()

        lines = [
            "# Enterprise Forecasting Accuracy Report",
            f"Generated: {s.generated_at}\n",
            "## Summary",
            f"- Total Predictions: {s.total_predictions}",
            f"- Models Evaluated: {s.models_evaluated}",
            f"- Best Model: {s.best_model}",
            f"- Worst Model: {s.worst_model}",
            f"- Avg MAPE: {s.avg_mape}",
            f"- Avg RMSE: {s.avg_rmse}\n",
            "## Model Metrics",
            "| Model | MAPE | RMSE | Bias | Accuracy |",
            "|------|------|------|------|------|",
        ]

        for m in self.metrics.values():
            lines.append(
                f"| {m.model_name} | {m.mape} | {m.rmse} | {m.bias} | {m.accuracy_score}% |"
            )

        path = EXPORTS_DIR / "forecasting_accuracy_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")

        logger.info("Markdown report exported.")
        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(self) -> None:

        gov = self.governance()

        if gov.sla_compliance_rate < 80:
            logger.error("Forecasting SLA violation detected.")
            raise SystemExit(1)

        logger.info("Forecast validation passed.")

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(self) -> None:

        logger.info("Starting forecasting pipeline...")

        self.load()
        self.parse()
        self.compute_metrics()

        self.export()
        self.export_markdown()

        self.validate()

        logger.info("Forecasting pipeline completed successfully.")


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(file: Path) -> ForecastingAccuracyReportEngine:
    return ForecastingAccuracyReportEngine(file)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = ML_DIR / "history" / "forecasting.json"

    engine = create_engine(FILE)

    engine.run()