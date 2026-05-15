"""
===============================================================================
KwanzaControl Enterprise Anomaly Detection Report Engine
File: reports/ml_metrics/anomaly_detection_report.py

Description:
    Enterprise-grade anomaly detection reporting engine for:

    - AI/ML anomaly analysis
    - Fraud detection observability
    - UEBA anomaly intelligence
    - Financial anomaly reporting
    - Drift anomaly monitoring
    - Risk-based anomaly scoring
    - Security anomaly governance
    - Model performance auditing
    - Enterprise ML compliance
    - Real-time anomaly summaries
    - Historical anomaly tracking
    - Executive anomaly dashboards
    - JSON/HTML/Markdown exports
    - CI/CD ML quality gates

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

import json
import logging
import statistics
import sys
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

# =============================================================================
# BASE PATHS
# =============================================================================

ROOT_DIR = Path(__file__).resolve().parents[2]

REPORTS_DIR = ROOT_DIR / "reports"

ML_METRICS_DIR = REPORTS_DIR / "ml_metrics"

EXPORTS_DIR = ML_METRICS_DIR / "exports"
LOGS_DIR = ML_METRICS_DIR / "logs"
HISTORY_DIR = ML_METRICS_DIR / "history"

# =============================================================================
# LOGGER
# =============================================================================


def setup_logger() -> logging.Logger:
    """
    Configure enterprise logger.
    """

    LOGS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger(
        "anomaly_detection_report"
    )

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt=(
            "[%(asctime)s] "
            "[%(levelname)s] "
            "[%(name)s] "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR
        / "anomaly_detection_report.log",
        encoding="utf-8",
    )

    stream_handler = logging.StreamHandler(
        sys.stdout
    )

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
class AnomalyRecord:
    """
    Individual anomaly record.
    """

    anomaly_id: str
    model_name: str
    anomaly_type: str
    risk_level: str
    anomaly_score: float
    threshold: float
    entity_id: str
    detected_at: str
    source: str
    status: str


@dataclass(slots=True)
class AnomalySummary:
    """
    Global anomaly summary.
    """

    total_anomalies: int
    critical_anomalies: int
    high_anomalies: int
    medium_anomalies: int
    low_anomalies: int
    average_score: float
    maximum_score: float
    minimum_score: float
    generated_at: str


@dataclass(slots=True)
class GovernanceMetrics:
    """
    Enterprise governance metrics.
    """

    compliant_models: int
    non_compliant_models: int
    monitored_entities: int
    active_models: int
    governance_score: float


# =============================================================================
# ANOMALY DETECTION REPORT ENGINE
# =============================================================================


class AnomalyDetectionReportEngine:
    """
    Enterprise anomaly reporting engine.
    """

    def __init__(
        self,
        anomalies_file: Path,
    ) -> None:

        self.anomalies_file = anomalies_file

        self.raw_data: Dict[str, Any] = {}

        self.anomalies: List[
            AnomalyRecord
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "AnomalyDetectionReportEngine initialized."
        )

    # =========================================================================
    # LOADERS
    # =========================================================================

    def load_data(
        self,
    ) -> None:
        """
        Load anomaly dataset.
        """

        logger.info(
            "Loading anomaly dataset..."
        )

        if not self.anomalies_file.exists():

            raise FileNotFoundError(
                f"Anomaly dataset not found: "
                f"{self.anomalies_file}"
            )

        with open(
            self.anomalies_file,
            encoding="utf-8",
        ) as file:

            self.raw_data = json.load(
                file
            )

        logger.info(
            "Anomaly dataset loaded successfully."
        )

    # =========================================================================
    # PARSERS
    # =========================================================================

    def parse_anomalies(
        self,
    ) -> None:
        """
        Parse anomaly records.
        """

        logger.info(
            "Parsing anomaly records..."
        )

        entries = self.raw_data.get(
            "anomalies",
            [],
        )

        for entry in entries:

            score = float(
                entry.get(
                    "anomaly_score",
                    0,
                )
            )

            threshold = float(
                entry.get(
                    "threshold",
                    0.5,
                )
            )

            risk_level = (
                self._determine_risk_level(
                    score
                )
            )

            anomaly = AnomalyRecord(
                anomaly_id=str(
                    entry.get(
                        "anomaly_id",
                        "",
                    )
                ),
                model_name=str(
                    entry.get(
                        "model_name",
                        "",
                    )
                ),
                anomaly_type=str(
                    entry.get(
                        "anomaly_type",
                        "",
                    )
                ),
                risk_level=risk_level,
                anomaly_score=score,
                threshold=threshold,
                entity_id=str(
                    entry.get(
                        "entity_id",
                        "",
                    )
                ),
                detected_at=str(
                    entry.get(
                        "detected_at",
                        "",
                    )
                ),
                source=str(
                    entry.get(
                        "source",
                        "",
                    )
                ),
                status=str(
                    entry.get(
                        "status",
                        "OPEN",
                    )
                ),
            )

            self.anomalies.append(
                anomaly
            )

        logger.info(
            "Anomaly records parsed successfully."
        )

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _determine_risk_level(
        score: float,
    ) -> str:
        """
        Determine enterprise anomaly risk level.
        """

        if score >= 0.95:
            return "CRITICAL"

        if score >= 0.85:
            return "HIGH"

        if score >= 0.70:
            return "MEDIUM"

        return "LOW"

    # =========================================================================
    # SUMMARIES
    # =========================================================================

    def build_summary(
        self,
    ) -> AnomalySummary:
        """
        Build anomaly summary.
        """

        scores = [
            anomaly.anomaly_score
            for anomaly in self.anomalies
        ]

        return AnomalySummary(
            total_anomalies=len(
                self.anomalies
            ),
            critical_anomalies=len([
                anomaly
                for anomaly in
                self.anomalies
                if anomaly.risk_level
                == "CRITICAL"
            ]),
            high_anomalies=len([
                anomaly
                for anomaly in
                self.anomalies
                if anomaly.risk_level
                == "HIGH"
            ]),
            medium_anomalies=len([
                anomaly
                for anomaly in
                self.anomalies
                if anomaly.risk_level
                == "MEDIUM"
            ]),
            low_anomalies=len([
                anomaly
                for anomaly in
                self.anomalies
                if anomaly.risk_level
                == "LOW"
            ]),
            average_score=round(
                statistics.mean(
                    scores
                )
                if scores
                else 0,
                4,
            ),
            maximum_score=max(
                scores,
                default=0,
            ),
            minimum_score=min(
                scores,
                default=0,
            ),
            generated_at=datetime.now(
                UTC
            ).isoformat(),
        )

    def build_governance_metrics(
        self,
    ) -> GovernanceMetrics:
        """
        Build governance metrics.
        """

        unique_models = set(
            anomaly.model_name
            for anomaly in
            self.anomalies
        )

        monitored_entities = set(
            anomaly.entity_id
            for anomaly in
            self.anomalies
        )

        compliant_models = len([
            anomaly
            for anomaly in
            self.anomalies
            if anomaly.risk_level
            in ["LOW", "MEDIUM"]
        ])

        governance_score = round(
            (
                compliant_models
                / max(
                    len(
                        self.anomalies
                    ),
                    1,
                )
            )
            * 100,
            2,
        )

        return GovernanceMetrics(
            compliant_models=
            compliant_models,
            non_compliant_models=
            len(self.anomalies)
            - compliant_models,
            monitored_entities=
            len(monitored_entities),
            active_models=
            len(unique_models),
            governance_score=
            governance_score,
        )

    # =========================================================================
    # EXPORTS
    # =========================================================================

    def export_json_report(
        self,
    ) -> Path:
        """
        Export enterprise JSON report.
        """

        payload = {
            "summary": asdict(
                self.build_summary()
            ),
            "governance": asdict(
                self.build_governance_metrics()
            ),
            "anomalies": [
                asdict(anomaly)
                for anomaly in
                self.anomalies
            ],
        }

        output_path = (
            EXPORTS_DIR
            / "anomaly_detection_report.json"
        )

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                payload,
                file,
                indent=4,
                ensure_ascii=False,
            )

        logger.info(
            "Anomaly JSON report exported."
        )

        return output_path

    def export_markdown_report(
        self,
    ) -> Path:
        """
        Export Markdown anomaly report.
        """

        summary = self.build_summary()

        lines: List[str] = []

        lines.append(
            "# Enterprise Anomaly Detection Report\n"
        )

        lines.append(
            f"Generated: "
            f"{summary.generated_at}\n"
        )

        lines.append(
            "## Summary\n"
        )

        lines.append(
            f"- Total Anomalies: "
            f"{summary.total_anomalies}"
        )

        lines.append(
            f"- Critical: "
            f"{summary.critical_anomalies}"
        )

        lines.append(
            f"- High: "
            f"{summary.high_anomalies}"
        )

        lines.append(
            f"- Medium: "
            f"{summary.medium_anomalies}"
        )

        lines.append(
            f"- Low: "
            f"{summary.low_anomalies}\n"
        )

        lines.append(
            "## Anomalies\n"
        )

        lines.append(
            "| ID | Model | Type | Risk | Score | Status |"
        )

        lines.append(
            "|------|------|------|------|------|------|"
        )

        for anomaly in (
            self.anomalies
        ):

            lines.append(
                "| "
                f"{anomaly.anomaly_id} | "
                f"{anomaly.model_name} | "
                f"{anomaly.anomaly_type} | "
                f"{anomaly.risk_level} | "
                f"{anomaly.anomaly_score} | "
                f"{anomaly.status} |"
            )

        output_path = (
            EXPORTS_DIR
            / "anomaly_detection_report.md"
        )

        output_path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Anomaly Markdown report exported."
        )

        return output_path

    def export_html_report(
        self,
    ) -> Path:
        """
        Export enterprise HTML report.
        """

        summary = self.build_summary()

        rows = []

        for anomaly in (
            self.anomalies
        ):

            rows.append(
                f"""
<tr>
    <td>{anomaly.anomaly_id}</td>
    <td>{anomaly.model_name}</td>
    <td>{anomaly.anomaly_type}</td>
    <td>{anomaly.risk_level}</td>
    <td>{anomaly.anomaly_score}</td>
    <td>{anomaly.status}</td>
</tr>
"""
            )

        html_content = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
    Enterprise Anomaly Detection Report
</title>

<style>

body {{
    font-family: Arial, sans-serif;
    margin: 40px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

th,
td {{
    border: 1px solid #dddddd;
    padding: 10px;
}}

th {{
    background-color: #efefef;
}}

</style>

</head>

<body>

<h1>
    Enterprise Anomaly Detection Report
</h1>

<p>
    Generated:
    {summary.generated_at}
</p>

<h2>
    Summary
</h2>

<ul>
    <li>
        Total Anomalies:
        {summary.total_anomalies}
    </li>

    <li>
        Critical:
        {summary.critical_anomalies}
    </li>

    <li>
        High:
        {summary.high_anomalies}
    </li>
</ul>

<table>

<tr>
    <th>ID</th>
    <th>Model</th>
    <th>Type</th>
    <th>Risk</th>
    <th>Score</th>
    <th>Status</th>
</tr>

{''.join(rows)}

</table>

</body>
</html>
"""

        output_path = (
            EXPORTS_DIR
            / "anomaly_detection_report.html"
        )

        output_path.write_text(
            html_content,
            encoding="utf-8",
        )

        logger.info(
            "Anomaly HTML report exported."
        )

        return output_path

    # =========================================================================
    # QUALITY GATES
    # =========================================================================

    def validate_quality_gates(
        self,
        critical_limit: int = 0,
    ) -> None:
        """
        Validate anomaly governance gates.
        """

        summary = self.build_summary()

        if (
            summary.critical_anomalies
            > critical_limit
        ):

            logger.error(
                "Critical anomaly threshold exceeded."
            )

            raise SystemExit(
                (
                    "Critical anomaly count exceeded: "
                    f"{summary.critical_anomalies}"
                )
            )

        logger.info(
            "Anomaly quality gates validated."
        )

    # =========================================================================
    # EXECUTION
    # =========================================================================

    def generate(
        self,
    ) -> None:
        """
        Execute anomaly reporting pipeline.
        """

        logger.info(
            "Starting anomaly reporting pipeline..."
        )

        self.load_data()

        self.parse_anomalies()

        self.export_json_report()

        self.export_markdown_report()

        self.export_html_report()

        logger.info(
            "Anomaly reporting pipeline completed."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_anomaly_engine(
    anomalies_file: Path,
) -> AnomalyDetectionReportEngine:
    """
    Factory builder.
    """

    return (
        AnomalyDetectionReportEngine(
            anomalies_file=
            anomalies_file
        )
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    ANOMALY_FILE = (
        HISTORY_DIR
        / "anomalies.json"
    )

    engine = create_anomaly_engine(
        anomalies_file=ANOMALY_FILE
    )

    engine.generate()

    engine.validate_quality_gates(
        critical_limit=0
    )

    logger.info(
        "Anomaly detection reporting completed successfully."
    )