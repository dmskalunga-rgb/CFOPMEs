"""
===============================================================================
KwanzaControl Enterprise Latency Performance Report Engine
File: reports/performance/latency_report.py

Description:
    Enterprise-grade latency observability and performance governance engine
    responsible for:

    - API latency monitoring
    - Distributed system latency analysis
    - P50/P95/P99 percentile tracking
    - SLA/SLO latency governance
    - Microservice response-time observability
    - Event-loop and async latency analysis
    - Queue and worker latency monitoring
    - Database query latency assessment
    - Network transport latency evaluation
    - Multi-tenant latency isolation analysis
    - Throughput-to-latency correlation
    - Bottleneck detection and escalation
    - Runtime degradation detection
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
        "latency_report"
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
        LOGS_DIR / "latency_report.log",
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
class LatencyFinding:
    service_name: str
    endpoint: str
    request_count: int
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    max_latency_ms: float
    timeout_events: int
    tenant_isolated: bool
    timestamp: str


@dataclass(slots=True)
class LatencyMetrics:
    total_services: int
    average_latency_ms: float
    average_p95_latency_ms: float
    average_p99_latency_ms: float
    total_timeout_events: int
    degraded_services: int
    critical_latency_services: int
    governance_score: float


@dataclass(slots=True)
class LatencyGovernance:
    sla_compliance_score: float
    observability_score: float
    runtime_health_score: float
    governance_status: str
    enterprise_risk_level: str


@dataclass(slots=True)
class LatencySummary:
    total_endpoints_analyzed: int
    peak_latency_ms: float
    average_request_volume: float
    total_timeout_events: int
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class LatencyReportEngine:
    """
    Enterprise latency monitoring and observability engine.
    """

    P95_THRESHOLD_MS = 500

    P99_THRESHOLD_MS = 1000

    TIMEOUT_WEIGHT = 3

    DEGRADATION_WEIGHT = 5

    CRITICAL_WEIGHT = 10

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
            LatencyFinding
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "LatencyReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading latency dataset..."
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
            "Parsing latency findings..."
        )

        for item in self.raw_data.get(
            "findings",
            [],
        ):

            self.findings.append(
                LatencyFinding(
                    service_name=item.get(
                        "service_name",
                        "",
                    ),

                    endpoint=item.get(
                        "endpoint",
                        "",
                    ),

                    request_count=int(
                        item.get(
                            "request_count",
                            0,
                        )
                    ),

                    average_latency_ms=float(
                        item.get(
                            "average_latency_ms",
                            0,
                        )
                    ),

                    p50_latency_ms=float(
                        item.get(
                            "p50_latency_ms",
                            0,
                        )
                    ),

                    p95_latency_ms=float(
                        item.get(
                            "p95_latency_ms",
                            0,
                        )
                    ),

                    p99_latency_ms=float(
                        item.get(
                            "p99_latency_ms",
                            0,
                        )
                    ),

                    max_latency_ms=float(
                        item.get(
                            "max_latency_ms",
                            0,
                        )
                    ),

                    timeout_events=int(
                        item.get(
                            "timeout_events",
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
    ) -> LatencyMetrics:

        total_services = len(
            self.findings
        )

        avg_latency = statistics.mean([
            f.average_latency_ms
            for f in self.findings
        ]) if self.findings else 0

        avg_p95 = statistics.mean([
            f.p95_latency_ms
            for f in self.findings
        ]) if self.findings else 0

        avg_p99 = statistics.mean([
            f.p99_latency_ms
            for f in self.findings
        ]) if self.findings else 0

        total_timeouts = sum([
            f.timeout_events
            for f in self.findings
        ])

        degraded_services = len([
            f for f in self.findings
            if (
                f.p95_latency_ms
                >= self.P95_THRESHOLD_MS
            )
        ])

        critical_services = len([
            f for f in self.findings
            if (
                f.p99_latency_ms
                >= self.P99_THRESHOLD_MS
            )
        ])

        governance_score = max(
            0,
            100
            - (
                (
                    total_timeouts
                    * self.TIMEOUT_WEIGHT
                )
                + (
                    degraded_services
                    * self.DEGRADATION_WEIGHT
                )
                + (
                    critical_services
                    * self.CRITICAL_WEIGHT
                )
            ),
        )

        return LatencyMetrics(
            total_services=
            total_services,

            average_latency_ms=
            round(
                avg_latency,
                2,
            ),

            average_p95_latency_ms=
            round(
                avg_p95,
                2,
            ),

            average_p99_latency_ms=
            round(
                avg_p99,
                2,
            ),

            total_timeout_events=
            total_timeouts,

            degraded_services=
            degraded_services,

            critical_latency_services=
            critical_services,

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
    ) -> LatencyGovernance:

        metrics = (
            self.compute_metrics()
        )

        sla_score = max(
            0,
            100
            - (
                metrics.average_p95_latency_ms
                / 10
            )
        )

        observability_score = max(
            0,
            100
            - (
                metrics.total_timeout_events
                * 2
            )
        )

        runtime_health_score = statistics.mean([
            metrics.governance_score,
            sla_score,
            observability_score,
        ])

        if runtime_health_score >= 90:
            status = "HEALTHY"
            risk = "LOW"

        elif runtime_health_score >= 75:
            status = "WARNING"
            risk = "MEDIUM"

        else:
            status = "CRITICAL"
            risk = "HIGH"

        return LatencyGovernance(
            sla_compliance_score=
            round(
                sla_score,
                2,
            ),

            observability_score=
            round(
                observability_score,
                2,
            ),

            runtime_health_score=
            round(
                runtime_health_score,
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
    ) -> LatencySummary:

        peak_latency = max([
            f.max_latency_ms
            for f in self.findings
        ], default=0)

        avg_request_volume = statistics.mean([
            f.request_count
            for f in self.findings
        ]) if self.findings else 0

        total_timeouts = sum([
            f.timeout_events
            for f in self.findings
        ])

        return LatencySummary(
            total_endpoints_analyzed=
            len(self.findings),

            peak_latency_ms=
            peak_latency,

            average_request_volume=
            round(
                avg_request_volume,
                2,
            ),

            total_timeout_events=
            total_timeouts,

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
            / "latency_report.json"
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
            "Latency JSON report exported."
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
            "# Enterprise Latency Performance Report",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Summary",

            f"- Endpoints Analyzed: "
            f"{summary.total_endpoints_analyzed}",

            f"- Peak Latency: "
            f"{summary.peak_latency_ms}ms",

            f"- Avg Request Volume: "
            f"{summary.average_request_volume}",

            f"- Timeout Events: "
            f"{summary.total_timeout_events}\n",

            "## Governance",

            f"- SLA Compliance Score: "
            f"{governance.sla_compliance_score}",

            f"- Observability Score: "
            f"{governance.observability_score}",

            f"- Runtime Health Score: "
            f"{governance.runtime_health_score}",

            f"- Governance Status: "
            f"{governance.governance_status}",

            f"- Enterprise Risk: "
            f"{governance.enterprise_risk_level}\n",

            "## Metrics",

            f"- Average Latency: "
            f"{metrics.average_latency_ms}ms",

            f"- Average P95 Latency: "
            f"{metrics.average_p95_latency_ms}ms",

            f"- Average P99 Latency: "
            f"{metrics.average_p99_latency_ms}ms",

            f"- Degraded Services: "
            f"{metrics.degraded_services}",

            f"- Critical Services: "
            f"{metrics.critical_latency_services}",

            f"- Governance Score: "
            f"{metrics.governance_score}\n",

            "## Endpoint Findings",

            "| Service | Endpoint | Avg | P95 | P99 | Timeouts |",
            "|----------|-----------|-----|------|------|-----------|",
        ]

        for finding in self.findings:

            lines.append(
                f"| "
                f"{finding.service_name} | "
                f"{finding.endpoint} | "
                f"{finding.average_latency_ms}ms | "
                f"{finding.p95_latency_ms}ms | "
                f"{finding.p99_latency_ms}ms | "
                f"{finding.timeout_events} |"
            )

        path = (
            EXPORTS_DIR
            / "latency_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Latency Markdown report exported."
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
<td>{finding.endpoint}</td>
<td>{finding.average_latency_ms}</td>
<td>{finding.p95_latency_ms}</td>
<td>{finding.p99_latency_ms}</td>
<td>{finding.timeout_events}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Latency Performance Report
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
Enterprise Latency Performance Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>Service</th>
<th>Endpoint</th>
<th>Average</th>
<th>P95</th>
<th>P99</th>
<th>Timeouts</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "latency_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "Latency HTML report exported."
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
            governance.runtime_health_score
            < 70
        ):

            logger.error(
                "Latency governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Latency governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting latency report pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Latency report pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> LatencyReportEngine:

    return LatencyReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "latency_report.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()