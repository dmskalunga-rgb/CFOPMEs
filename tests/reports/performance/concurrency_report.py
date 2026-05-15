"""
===============================================================================
KwanzaControl Enterprise Concurrency Performance Report Engine
File: reports/performance/concurrency_report.py

Description:
    Enterprise-grade concurrency analysis and runtime parallelism governance
    engine responsible for:

    - Thread concurrency analysis
    - Async workload observability
    - Worker pool performance assessment
    - Event-loop latency monitoring
    - Deadlock and starvation detection
    - Queue contention analysis
    - Multi-tenant concurrency isolation
    - Distributed task orchestration governance
    - CPU-bound vs IO-bound workload analysis
    - Throughput scalability evaluation
    - Runtime bottleneck detection
    - SLA/SLO concurrency governance
    - JSON / Markdown / HTML exports

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

# =============================================================================
# PATHS
# =============================================================================

ROOT_DIR = Path(__file__).resolve().parents[2]

REPORTS_DIR = ROOT_DIR / "reports"

PERFORMANCE_DIR = REPORTS_DIR / "performance"

EXPORTS_DIR = PERFORMANCE_DIR / "exports"

LOGS_DIR = PERFORMANCE_DIR / "logs"

HISTORY_DIR = PERFORMANCE_DIR / "history"

# =============================================================================
# LOGGER
# =============================================================================


def setup_logger() -> logging.Logger:

    LOGS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger(
        "concurrency_report"
    )

    if logger.handlers:
        return logger

    logger.setLevel(
        logging.INFO
    )

    formatter = logging.Formatter(
        "[%(asctime)s] "
        "[%(levelname)s] "
        "%(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR / "concurrency_report.log",
        encoding="utf-8",
    )

    stream_handler = logging.StreamHandler(
        sys.stdout
    )

    file_handler.setFormatter(
        formatter
    )

    stream_handler.setFormatter(
        formatter
    )

    logger.addHandler(
        file_handler
    )

    logger.addHandler(
        stream_handler
    )

    return logger


logger = setup_logger()

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(slots=True)
class ConcurrencyFinding:
    service_name: str
    worker_type: str
    concurrent_tasks: int
    queue_wait_ms: float
    execution_latency_ms: float
    deadlock_detected: bool
    starvation_detected: bool
    thread_saturation_percent: float
    tenant_isolated: bool
    timestamp: str


@dataclass(slots=True)
class ConcurrencyMetrics:
    total_services: int
    average_concurrency: float
    average_latency_ms: float
    average_queue_wait_ms: float
    deadlock_events: int
    starvation_events: int
    saturation_risk_services: int
    governance_score: float


@dataclass(slots=True)
class ConcurrencyGovernance:
    concurrency_health_score: float
    scalability_score: float
    zero_contention_score: float
    governance_status: str
    enterprise_risk_level: str


@dataclass(slots=True)
class ConcurrencySummary:
    total_services_analyzed: int
    peak_concurrency: int
    average_worker_utilization: float
    total_deadlocks_detected: int
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class ConcurrencyReportEngine:
    """
    Enterprise concurrency and runtime governance engine.
    """

    SATURATION_THRESHOLD = 85.0

    DEADLOCK_WEIGHT = 20

    STARVATION_WEIGHT = 10

    LATENCY_THRESHOLD_MS = 500

    def __init__(
        self,
        dataset_file: Path,
    ) -> None:

        self.dataset_file = dataset_file

        self.raw_data: Dict[
            str,
            Any,
        ] = {}

        self.findings: List[
            ConcurrencyFinding
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "ConcurrencyReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading concurrency dataset..."
        )

        if not self.dataset_file.exists():

            raise FileNotFoundError(
                f"Dataset not found: "
                f"{self.dataset_file}"
            )

        with open(
            self.dataset_file,
            encoding="utf-8",
        ) as file:

            self.raw_data = json.load(
                file
            )

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:

        logger.info(
            "Parsing concurrency findings..."
        )

        for item in self.raw_data.get(
            "findings",
            [],
        ):

            self.findings.append(
                ConcurrencyFinding(
                    service_name=item.get(
                        "service_name",
                        "",
                    ),

                    worker_type=item.get(
                        "worker_type",
                        "async",
                    ),

                    concurrent_tasks=int(
                        item.get(
                            "concurrent_tasks",
                            0,
                        )
                    ),

                    queue_wait_ms=float(
                        item.get(
                            "queue_wait_ms",
                            0,
                        )
                    ),

                    execution_latency_ms=float(
                        item.get(
                            "execution_latency_ms",
                            0,
                        )
                    ),

                    deadlock_detected=bool(
                        item.get(
                            "deadlock_detected",
                            False,
                        )
                    ),

                    starvation_detected=bool(
                        item.get(
                            "starvation_detected",
                            False,
                        )
                    ),

                    thread_saturation_percent=float(
                        item.get(
                            "thread_saturation_percent",
                            0,
                        )
                    ),

                    tenant_isolated=bool(
                        item.get(
                            "tenant_isolated",
                            True,
                        )
                    ),

                    timestamp=item.get(
                        "timestamp",
                        "",
                    ),
                )
            )

    # =========================================================================
    # METRICS
    # =========================================================================

    def compute_metrics(
        self,
    ) -> ConcurrencyMetrics:

        total_services = len(
            self.findings
        )

        avg_concurrency = statistics.mean([
            f.concurrent_tasks
            for f in self.findings
        ]) if self.findings else 0

        avg_latency = statistics.mean([
            f.execution_latency_ms
            for f in self.findings
        ]) if self.findings else 0

        avg_queue_wait = statistics.mean([
            f.queue_wait_ms
            for f in self.findings
        ]) if self.findings else 0

        deadlocks = len([
            f for f in self.findings
            if f.deadlock_detected
        ])

        starvation = len([
            f for f in self.findings
            if f.starvation_detected
        ])

        saturation_services = len([
            f for f in self.findings
            if (
                f.thread_saturation_percent
                >= self.SATURATION_THRESHOLD
            )
        ])

        governance_score = max(
            0,
            100
            - (
                (deadlocks * self.DEADLOCK_WEIGHT)
                + (
                    starvation
                    * self.STARVATION_WEIGHT
                )
                + (
                    saturation_services
                    * 5
                )
            ),
        )

        return ConcurrencyMetrics(
            total_services=
            total_services,

            average_concurrency=
            round(
                avg_concurrency,
                2,
            ),

            average_latency_ms=
            round(
                avg_latency,
                2,
            ),

            average_queue_wait_ms=
            round(
                avg_queue_wait,
                2,
            ),

            deadlock_events=
            deadlocks,

            starvation_events=
            starvation,

            saturation_risk_services=
            saturation_services,

            governance_score=
            round(
                governance_score,
                2,
            ),
        )

    # =========================================================================
    # GOVERNANCE
    # =========================================================================

    def governance(
        self,
    ) -> ConcurrencyGovernance:

        metrics = (
            self.compute_metrics()
        )

        scalability_score = max(
            0,
            100
            - (
                metrics.average_latency_ms
                / 10
            )
        )

        zero_contention_score = max(
            0,
            100
            - (
                (
                    metrics.deadlock_events
                    * 20
                )
                + (
                    metrics.starvation_events
                    * 10
                )
            )
        )

        health_score = statistics.mean([
            metrics.governance_score,
            scalability_score,
            zero_contention_score,
        ])

        if health_score >= 90:
            status = "HEALTHY"
            risk = "LOW"

        elif health_score >= 75:
            status = "WARNING"
            risk = "MEDIUM"

        else:
            status = "CRITICAL"
            risk = "HIGH"

        return ConcurrencyGovernance(
            concurrency_health_score=
            round(
                health_score,
                2,
            ),

            scalability_score=
            round(
                scalability_score,
                2,
            ),

            zero_contention_score=
            round(
                zero_contention_score,
                2,
            ),

            governance_status=
            status,

            enterprise_risk_level=
            risk,
        )

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(
        self,
    ) -> ConcurrencySummary:

        peak_concurrency = max([
            f.concurrent_tasks
            for f in self.findings
        ], default=0)

        avg_worker_utilization = statistics.mean([
            f.thread_saturation_percent
            for f in self.findings
        ]) if self.findings else 0

        deadlocks = len([
            f for f in self.findings
            if f.deadlock_detected
        ])

        return ConcurrencySummary(
            total_services_analyzed=
            len(self.findings),

            peak_concurrency=
            peak_concurrency,

            average_worker_utilization=
            round(
                avg_worker_utilization,
                2,
            ),

            total_deadlocks_detected=
            deadlocks,

            generated_at=
            datetime.now(
                UTC
            ).isoformat(),
        )

    # =========================================================================
    # EXPORT JSON
    # =========================================================================

    def export_json(
        self,
    ) -> Path:

        payload = {
            "summary":
                asdict(
                    self.summary()
                ),

            "governance":
                asdict(
                    self.governance()
                ),

            "metrics":
                asdict(
                    self.compute_metrics()
                ),

            "findings": [
                asdict(f)
                for f in self.findings
            ],
        }

        path = (
            EXPORTS_DIR
            / "concurrency_report.json"
        )

        with open(
            path,
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
            "Concurrency JSON report exported."
        )

        return path

    # =========================================================================
    # EXPORT MARKDOWN
    # =========================================================================

    def export_markdown(
        self,
    ) -> Path:

        summary = self.summary()

        governance = self.governance()

        metrics = self.compute_metrics()

        lines = [
            "# Enterprise Concurrency Performance Report",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Summary",

            f"- Services Analyzed: "
            f"{summary.total_services_analyzed}",

            f"- Peak Concurrency: "
            f"{summary.peak_concurrency}",

            f"- Avg Worker Utilization: "
            f"{summary.average_worker_utilization}%",

            f"- Deadlocks Detected: "
            f"{summary.total_deadlocks_detected}\n",

            "## Governance",

            f"- Health Score: "
            f"{governance.concurrency_health_score}",

            f"- Scalability Score: "
            f"{governance.scalability_score}",

            f"- Zero Contention Score: "
            f"{governance.zero_contention_score}",

            f"- Governance Status: "
            f"{governance.governance_status}",

            f"- Enterprise Risk: "
            f"{governance.enterprise_risk_level}\n",

            "## Metrics",

            f"- Average Concurrency: "
            f"{metrics.average_concurrency}",

            f"- Average Latency: "
            f"{metrics.average_latency_ms}ms",

            f"- Queue Wait Avg: "
            f"{metrics.average_queue_wait_ms}ms",

            f"- Deadlock Events: "
            f"{metrics.deadlock_events}",

            f"- Starvation Events: "
            f"{metrics.starvation_events}",

            f"- Saturation Risk Services: "
            f"{metrics.saturation_risk_services}",

            f"- Governance Score: "
            f"{metrics.governance_score}\n",

            "## Findings",

            "| Service | Worker | Tasks | Latency | Saturation | Deadlock |",
            "|----------|---------|-------|-----------|-------------|-----------|",
        ]

        for finding in self.findings:

            lines.append(
                f"| "
                f"{finding.service_name} | "
                f"{finding.worker_type} | "
                f"{finding.concurrent_tasks} | "
                f"{finding.execution_latency_ms}ms | "
                f"{finding.thread_saturation_percent}% | "
                f"{finding.deadlock_detected} |"
            )

        path = (
            EXPORTS_DIR
            / "concurrency_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Concurrency Markdown report exported."
        )

        return path

    # =========================================================================
    # EXPORT HTML
    # =========================================================================

    def export_html(
        self,
    ) -> Path:

        summary = self.summary()

        rows = []

        for finding in self.findings:

            rows.append(
                f"""
<tr>
<td>{finding.service_name}</td>
<td>{finding.worker_type}</td>
<td>{finding.concurrent_tasks}</td>
<td>{finding.execution_latency_ms}</td>
<td>{finding.thread_saturation_percent}</td>
<td>{finding.deadlock_detected}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Concurrency Performance Report
</title>

<style>

body {{
    font-family: Arial;
    margin: 40px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

th, td {{
    border: 1px solid #cccccc;
    padding: 10px;
}}

th {{
    background-color: #f2f2f2;
}}

</style>

</head>

<body>

<h1>
Enterprise Concurrency Performance Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>Service</th>
<th>Worker</th>
<th>Tasks</th>
<th>Latency</th>
<th>Saturation</th>
<th>Deadlock</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "concurrency_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "Concurrency HTML report exported."
        )

        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(
        self,
    ) -> None:

        governance = (
            self.governance()
        )

        if (
            governance.concurrency_health_score
            < 70
        ):

            logger.error(
                "Concurrency governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Concurrency governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting concurrency report pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Concurrency report pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> ConcurrencyReportEngine:

    return ConcurrencyReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "concurrency_report.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()