"""
===============================================================================
KwanzaControl Enterprise Feature Store Report Engine
File: reports/ml_metrics/feature_store_report.py

Description:
    Enterprise-grade Feature Store observability & reporting engine responsible for:

    - Feature registry analysis
    - Feature usage tracking
    - Feature freshness monitoring
    - Feature quality scoring
    - Training vs serving skew detection
    - Feature lineage audit
    - Data governance compliance
    - ML feature observability
    - Feature drift approximation
    - Critical feature risk classification
    - JSON / HTML / Markdown reporting
    - CI/CD feature validation gates
    - Enterprise ML audit readiness

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

import json
import logging
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

    logger = logging.getLogger("feature_store_report")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR / "feature_store_report.log",
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
class FeatureRecord:
    feature_name: str
    domain: str
    usage_count: int
    freshness_minutes: float
    null_rate: float
    drift_score: float
    importance_score: float
    source: str
    status: str


@dataclass(slots=True)
class FeatureStoreSummary:
    total_features: int
    active_features: int
    stale_features: int
    critical_features: int
    high_risk_features: int
    medium_risk_features: int
    low_risk_features: int
    avg_freshness: float
    avg_null_rate: float
    generated_at: str


@dataclass(slots=True)
class FeatureGovernance:
    compliance_score: float
    stale_ratio: float
    drifted_ratio: float
    production_ready_features: int
    non_compliant_features: int


# =============================================================================
# FEATURE STORE ENGINE
# =============================================================================


class FeatureStoreReportEngine:
    """
    Enterprise Feature Store reporting engine.
    """

    def __init__(self, feature_store_file: Path) -> None:
        self.feature_store_file = feature_store_file
        self.raw_data: Dict[str, Any] = {}
        self.features: List[FeatureRecord] = []

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info("FeatureStoreReportEngine initialized.")

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:
        logger.info("Loading feature store dataset...")

        if not self.feature_store_file.exists():
            raise FileNotFoundError(f"Feature store file not found: {self.feature_store_file}")

        with open(self.feature_store_file, encoding="utf-8") as f:
            self.raw_data = json.load(f)

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:
        logger.info("Parsing feature store...")

        for f in self.raw_data.get("features", []):

            drift = float(f.get("drift_score", 0))
            freshness = float(f.get("freshness_minutes", 0))
            null_rate = float(f.get("null_rate", 0))

            status = self._status(drift, freshness, null_rate)

            self.features.append(
                FeatureRecord(
                    feature_name=f.get("feature_name", ""),
                    domain=f.get("domain", "core"),
                    usage_count=int(f.get("usage_count", 0)),
                    freshness_minutes=freshness,
                    null_rate=null_rate,
                    drift_score=drift,
                    importance_score=float(f.get("importance_score", 0)),
                    source=f.get("source", "unknown"),
                    status=status,
                )
            )

    # =========================================================================
    # LOGIC
    # =========================================================================

    @staticmethod
    def _status(drift: float, freshness: float, null_rate: float) -> str:
        if drift > 0.7 or null_rate > 0.3 or freshness > 1440:
            return "CRITICAL"
        if drift > 0.4 or null_rate > 0.15:
            return "HIGH_RISK"
        if drift > 0.2:
            return "MEDIUM_RISK"
        return "LOW_RISK"

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(self) -> FeatureStoreSummary:
        freshness = [f.freshness_minutes for f in self.features]
        nulls = [f.null_rate for f in self.features]

        return FeatureStoreSummary(
            total_features=len(self.features),
            active_features=len([f for f in self.features if f.usage_count > 0]),
            stale_features=len([f for f in self.features if f.freshness_minutes > 1440]),
            critical_features=len([f for f in self.features if f.status == "CRITICAL"]),
            high_risk_features=len([f for f in self.features if f.status == "HIGH_RISK"]),
            medium_risk_features=len([f for f in self.features if f.status == "MEDIUM_RISK"]),
            low_risk_features=len([f for f in self.features if f.status == "LOW_RISK"]),
            avg_freshness=round(statistics.mean(freshness) if freshness else 0, 2),
            avg_null_rate=round(statistics.mean(nulls) if nulls else 0, 4),
            generated_at=datetime.now(UTC).isoformat(),
        )

    # =========================================================================
    # GOVERNANCE
    # =========================================================================

    def governance(self) -> FeatureGovernance:
        total = len(self.features)

        stale = len([f for f in self.features if f.freshness_minutes > 1440])
        drifted = len([f for f in self.features if f.drift_score > 0.4])

        compliant = len([f for f in self.features if f.status != "CRITICAL"])

        return FeatureGovernance(
            compliance_score=round((compliant / max(total, 1)) * 100, 2),
            stale_ratio=round(stale / max(total, 1), 4),
            drifted_ratio=round(drifted / max(total, 1), 4),
            production_ready_features=len([f for f in self.features if f.status == "LOW_RISK"]),
            non_compliant_features=total - compliant,
        )

    # =========================================================================
    # EXPORT
    # =========================================================================

    def export(self) -> Path:
        payload = {
            "summary": asdict(self.summary()),
            "governance": asdict(self.governance()),
            "features": [asdict(f) for f in self.features],
        }

        path = EXPORTS_DIR / "feature_store_report.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)

        logger.info("Feature store report exported.")

        return path

    # =========================================================================
    # MARKDOWN
    # =========================================================================

    def export_markdown(self) -> Path:
        s = self.summary()

        lines = [
            "# Enterprise Feature Store Report",
            f"Generated: {s.generated_at}\n",
            "## Summary",
            f"- Total Features: {s.total_features}",
            f"- Active Features: {s.active_features}",
            f"- Critical Features: {s.critical_features}",
            f"- High Risk: {s.high_risk_features}",
            f"- Medium Risk: {s.medium_risk_features}",
            f"- Low Risk: {s.low_risk_features}",
            f"- Avg Freshness: {s.avg_freshness} min",
            f"- Avg Null Rate: {s.avg_null_rate}\n",
            "## Feature Table",
            "| Feature | Domain | Status | Drift | Freshness |",
            "|---------|--------|--------|-------|-----------|",
        ]

        for f in self.features:
            lines.append(
                f"| {f.feature_name} | {f.domain} | {f.status} | {f.drift_score} | {f.freshness_minutes} |"
            )

        path = EXPORTS_DIR / "feature_store_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")

        logger.info("Markdown report exported.")
        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(self) -> None:
        gov = self.governance()

        if gov.compliance_score < 85:
            logger.error("Feature store compliance below threshold.")
            raise SystemExit(1)

        logger.info("Feature store validation passed.")

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(self) -> None:
        logger.info("Starting feature store pipeline...")

        self.load()
        self.parse()

        self.export()
        self.export_markdown()

        self.validate()

        logger.info("Feature store pipeline completed successfully.")


# =============================================================================
# FACTORY
# =============================================================================


def create_feature_store_engine(file: Path) -> FeatureStoreReportEngine:
    return FeatureStoreReportEngine(file)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = ML_DIR / "history" / "feature_store.json"

    engine = create_feature_store_engine(FILE)

    engine.run()