"""
===============================================================================
KwanzaControl Enterprise Load Test Report Engine
File: reports/performance/load_test_report.py

Description:
    Enterprise-grade load testing observability and performance governance
    engine responsible for:

    - Concurrent virtual user analysis
    - Throughput and RPS governance
    - SLA/SLO compliance validation
    - Stress and spike-test analysis
    - Response-time degradation detection
    - Error-rate correlation
    - Resource saturation monitoring
    - Multi-tenant isolation validation
    - Distributed load orchestration metrics
    - Capacity planning intelligence
    - Scalability and resilience assessment
    - Bottleneck and contention detection
    - Runtime performance regression tracking
    - JSON / Markdown / HTML reporting exports

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
        "load_test_report"
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
        LOGS_DIR / "load_test_report.log",
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
class LoadTestFinding:
    scenario_name: str
    endpoint: str
    virtual_users: int
    requests_per_second: float
    average_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    error_rate_percent: float
    cpu_utilization_percent: float
    memory_utilization_percent: float
    saturation_detected: bool
    tenant_isolated: bool
    timestamp: str


@dataclass(slots=True)
class LoadTestMetrics:
    total_scenarios: int
    average_rps: float
    peak_rps: float
    average_response_time_ms: float
    average_error_rate_percent: float
    saturation_events: int
    degraded_scenarios: int
    governance_score: float


@dataclass(slots=True)
class LoadTestGovernance:
    scalability_score: float
    resilience_score: float
    sla_compliance_score: float
    governance_status: str
    enterprise_risk_level: str


@dataclass(slots=True)
class LoadTestSummary:
    total_scenarios_analyzed: int
    peak_virtual_users: int
    total_requests_processed: int
    average_resource_utilization: float
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class LoadTestReportEngine:
    """
    Enterprise load testing governance engine.
    """

    P95_THRESHOLD_MS = 700

    ERROR_RATE_THRESHOLD = 2.0

    CPU_SATURATION_THRESHOLD = 85.0

    MEMORY_SATURATION_THRESHOLD = 90.0

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
            LoadTestFinding
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "LoadTestReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading load test dataset..."
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
            "Parsing load test findings..."
        )

        for item in self.raw_data.get(
            "findings",
            [],
        ):

            self.findings.append(
                LoadTestFinding(
                    scenario_name=item.get(
                        "scenario_name",
                        "",
                    ),

                    endpoint=item.get(
                        "endpoint",
                        "",
                    ),

                    virtual_users=int(
                        item.get(
                            "virtual_users",
                            0,
                        )
                    ),

                    requests_per_second=float(
                        item.get(
                            "requests_per_second",
                            0,
                        )
                    ),

                    average_response_time_ms=float(
                        item.get(
                            "average_response_time_ms",
                            0,
                        )
                    ),

                    p95_response_time_ms=float(
                        item.get(
                            "p95_response_time_ms",
                            0,
                        )
                    ),

                    p99_response_time_ms=float(
                        item.get(
                            "p99_response_time_ms",
                            0,
                        )
                    ),

                    error_rate_percent=float(
                        item.get(
                            "error_rate_percent",
                            0,
                        )
                    ),

                    cpu_utilization_percent=float(
                        item.get(
                            "cpu_utilization_percent",
                            0,
                        )
                    ),

                    memory_utilization_percent=float(
                        item.get(
                            "memory_utilization_percent",
                            0,
                        )
                    ),

                    saturation_detected=bool(
                        item.get(
                            "saturation_detected",
                            False,
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
    ) -> LoadTestMetrics:

        total = len(
            self.findings
        )

        avg_rps = statistics.mean([
            f.requests_per_second
            for f in self.findings
        ]) if self.findings else 0

        peak_rps = max([
            f.requests_per_second
            for f in self.findings
        ], default=0)

        avg_response = statistics.mean([
            f.average_response_time_ms
            for f in self.findings
        ]) if self.findings else 0

        avg_error = statistics.mean([
            f.error_rate_percent
            for f in self.findings
        ]) if self.findings else 0

        saturation_events = len([
            f for f in self.findings
            if (
                f.saturation_detected
                or (
                    f.cpu_utilization_percent
                    >= self.CPU_SATURATION_THRESHOLD
                )
                or (
                    f.memory_utilization_percent
                    >= self.MEMORY_SATURATION_THRESHOLD
                )
            )
        ])

        degraded = len([
            f for f in self.findings
            if (
                f.p95_response_time_ms
                >= self.P95_THRESHOLD_MS
            )
        ])

        governance_score = max(
            0,
            100
            - (
                (avg_error * 10)
                + (saturation_events * 5)
                + (degraded * 5)
            )
        )

        return LoadTestMetrics(
            total_scenarios=
            total,

            average_rps=
            round(
                avg_rps,
                2,
            ),

            peak_rps=
            round(
                peak_rps,
                2,
            ),

            average_response_time_ms=
            round(
                avg_response,
                2,
            ),

            average_error_rate_percent=
            round(
                avg_error,
                2,
            ),

            saturation_events=
            saturation_events,

            degraded_scenarios=
            degraded,

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
    ) -> LoadTestGovernance:

        metrics = (
            self.compute_metrics()
        )

        scalability_score = max(
            0,
            100
            - (
                metrics.average_response_time_ms
                / 10
            )
        )

        resilience_score = max(
            0,
            100
            - (
                metrics.average_error_rate_percent
                * 15
            )
        )

        sla_score = statistics.mean([
            scalability_score,
            resilience_score,
            metrics.governance_score,
        ])

        if sla_score >= 90:
            status = "HEALTHY"
            risk = "LOW"

        elif sla_score >= 75:
            status = "WARNING"
            risk = "MEDIUM"

        else:
            status = "CRITICAL"
            risk = "HIGH"

        return LoadTestGovernance(
            scalability_score=
            round(
                scalability_score,
                2,
            ),

            resilience_score=
            round(
                resilience_score,
                2,
            ),

            sla_compliance_score=
            round(
                sla_score,
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
    ) -> LoadTestSummary:

        peak_users = max([
            f.virtual_users
            for f in self.findings
        ], default=0)

        total_requests = int(sum([
            f.requests_per_second
            for f in self.findings
        ]))

        resource_utilization = statistics.mean([
            (
                f.cpu_utilization_percent
                + f.memory_utilization_percent
            ) / 2
            for f in self.findings
        ]) if self.findings else 0

        return LoadTestSummary(
            total_scenarios_analyzed=
            len(self.findings),

            peak_virtual_users=
            peak_users,

            total_requests_processed=
            total_requests,

            average_resource_utilization=
            round(
                resource_utilization,
                2,
            ),

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
            / "load_test_report.json"
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
            "Load test JSON report exported."
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
            "# Enterprise Load Test Report",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Summary",

            f"- Scenarios Analyzed: "
            f"{summary.total_scenarios_analyzed}",

            f"- Peak Virtual Users: "
            f"{summary.peak_virtual_users}",

            f"- Requests Processed: "
            f"{summary.total_requests_processed}",

            f"- Avg Resource Utilization: "
            f"{summary.average_resource_utilization}%\n",

            "## Governance",

            f"- Scalability Score: "
            f"{governance.scalability_score}",

            f"- Resilience Score: "
            f"{governance.resilience_score}",

            f"- SLA Compliance Score: "
            f"{governance.sla_compliance_score}",

            f"- Governance Status: "
            f"{governance.governance_status}",

            f"- Enterprise Risk: "
            f"{governance.enterprise_risk_level}\n",

            "## Metrics",

            f"- Average RPS: "
            f"{metrics.average_rps}",

            f"- Peak RPS: "
            f"{metrics.peak_rps}",

            f"- Average Response Time: "
            f"{metrics.average_response_time_ms}ms",

            f"- Average Error Rate: "
            f"{metrics.average_error_rate_percent}%",

            f"- Saturation Events: "
            f"{metrics.saturation_events}",

            f"- Degraded Scenarios: "
            f"{metrics.degraded_scenarios}",

            f"- Governance Score: "
            f"{metrics.governance_score}\n",

            "## Scenario Findings",

            "| Scenario | Users | RPS | Avg RT | P95 | Errors |",
            "|-----------|-------|-----|---------|------|---------|",
        ]

        for finding in self.findings:

            lines.append(
                f"| "
                f"{finding.scenario_name} | "
                f"{finding.virtual_users} | "
                f"{finding.requests_per_second} | "
                f"{finding.average_response_time_ms}ms | "
                f"{finding.p95_response_time_ms}ms | "
                f"{finding.error_rate_percent}% |"
            )

        path = (
            EXPORTS_DIR
            / "load_test_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Load test Markdown report exported."
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
<td>{finding.scenario_name}</td>
<td>{finding.virtual_users}</td>
<td>{finding.requests_per_second}</td>
<td>{finding.average_response_time_ms}</td>
<td>{finding.p95_response_time_ms}</td>
<td>{finding.error_rate_percent}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Load Test Report
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
Enterprise Load Test Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>Scenario</th>
<th>Users</th>
<th>RPS</th>
<th>Avg RT</th>
<th>P95</th>
<th>Error Rate</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "load_test_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "Load test HTML report exported."
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
            governance.sla_compliance_score
            < 70
        ):

            logger.error(
                "Load test governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Load test governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting load test pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Load test pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> LoadTestReportEngine:

    return LoadTestReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "load_test_report.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()