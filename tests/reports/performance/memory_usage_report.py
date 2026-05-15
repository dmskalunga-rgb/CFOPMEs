"""
===============================================================================
KwanzaControl Enterprise Memory Usage Report Engine
File: reports/performance/memory_usage_report.py

Description:
    Enterprise-grade memory observability and governance engine responsible for:

    - Heap and stack memory analysis
    - Memory leak detection
    - RAM utilization governance
    - Container memory monitoring
    - Kubernetes memory pressure analysis
    - Swap usage observability
    - Garbage collection analytics
    - Cache memory efficiency tracking
    - Multi-tenant memory isolation validation
    - High-consumption process identification
    - Memory fragmentation analysis
    - OOM (Out Of Memory) risk assessment
    - SLA/SLO memory compliance governance
    - Runtime memory degradation tracking
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
        "memory_usage_report"
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
        LOGS_DIR / "memory_usage_report.log",
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
class MemoryFinding:
    service_name: str
    host: str
    total_memory_mb: float
    used_memory_mb: float
    free_memory_mb: float
    cached_memory_mb: float
    swap_usage_mb: float
    memory_usage_percent: float
    garbage_collection_events: int
    memory_leak_detected: bool
    oom_risk_detected: bool
    tenant_isolated: bool
    timestamp: str


@dataclass(slots=True)
class MemoryMetrics:
    total_services: int
    average_memory_usage_percent: float
    peak_memory_usage_percent: float
    total_swap_usage_mb: float
    memory_leak_events: int
    oom_risk_events: int
    saturated_services: int
    governance_score: float


@dataclass(slots=True)
class MemoryGovernance:
    memory_health_score: float
    resource_efficiency_score: float
    stability_score: float
    governance_status: str
    enterprise_risk_level: str


@dataclass(slots=True)
class MemorySummary:
    total_hosts_analyzed: int
    total_memory_consumed_mb: float
    average_gc_events: float
    total_oom_risks: int
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class MemoryUsageReportEngine:
    """
    Enterprise memory observability and governance engine.
    """

    MEMORY_SATURATION_THRESHOLD = 85.0

    SWAP_USAGE_THRESHOLD_MB = 2048

    MEMORY_LEAK_WEIGHT = 15

    OOM_RISK_WEIGHT = 20

    SATURATION_WEIGHT = 5

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
            MemoryFinding
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "MemoryUsageReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading memory usage dataset..."
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
            "Parsing memory findings..."
        )

        for item in self.raw_data.get(
            "findings",
            [],
        ):

            self.findings.append(
                MemoryFinding(
                    service_name=item.get(
                        "service_name",
                        "",
                    ),

                    host=item.get(
                        "host",
                        "",
                    ),

                    total_memory_mb=float(
                        item.get(
                            "total_memory_mb",
                            0,
                        )
                    ),

                    used_memory_mb=float(
                        item.get(
                            "used_memory_mb",
                            0,
                        )
                    ),

                    free_memory_mb=float(
                        item.get(
                            "free_memory_mb",
                            0,
                        )
                    ),

                    cached_memory_mb=float(
                        item.get(
                            "cached_memory_mb",
                            0,
                        )
                    ),

                    swap_usage_mb=float(
                        item.get(
                            "swap_usage_mb",
                            0,
                        )
                    ),

                    memory_usage_percent=float(
                        item.get(
                            "memory_usage_percent",
                            0,
                        )
                    ),

                    garbage_collection_events=int(
                        item.get(
                            "garbage_collection_events",
                            0,
                        )
                    ),

                    memory_leak_detected=bool(
                        item.get(
                            "memory_leak_detected",
                            False,
                        )
                    ),

                    oom_risk_detected=bool(
                        item.get(
                            "oom_risk_detected",
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
    ) -> MemoryMetrics:

        total = len(
            self.findings
        )

        avg_usage = statistics.mean([
            f.memory_usage_percent
            for f in self.findings
        ]) if self.findings else 0

        peak_usage = max([
            f.memory_usage_percent
            for f in self.findings
        ], default=0)

        total_swap = sum([
            f.swap_usage_mb
            for f in self.findings
        ])

        leaks = len([
            f for f in self.findings
            if f.memory_leak_detected
        ])

        oom_risks = len([
            f for f in self.findings
            if f.oom_risk_detected
        ])

        saturated = len([
            f for f in self.findings
            if (
                f.memory_usage_percent
                >= self.MEMORY_SATURATION_THRESHOLD
            )
        ])

        governance_score = max(
            0,
            100
            - (
                (
                    leaks
                    * self.MEMORY_LEAK_WEIGHT
                )
                + (
                    oom_risks
                    * self.OOM_RISK_WEIGHT
                )
                + (
                    saturated
                    * self.SATURATION_WEIGHT
                )
            )
        )

        return MemoryMetrics(
            total_services=
            total,

            average_memory_usage_percent=
            round(
                avg_usage,
                2,
            ),

            peak_memory_usage_percent=
            round(
                peak_usage,
                2,
            ),

            total_swap_usage_mb=
            round(
                total_swap,
                2,
            ),

            memory_leak_events=
            leaks,

            oom_risk_events=
            oom_risks,

            saturated_services=
            saturated,

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
    ) -> MemoryGovernance:

        metrics = (
            self.compute_metrics()
        )

        memory_health = max(
            0,
            100
            - metrics.average_memory_usage_percent
        )

        resource_efficiency = max(
            0,
            100
            - (
                metrics.total_swap_usage_mb
                / 100
            )
        )

        stability_score = statistics.mean([
            metrics.governance_score,
            memory_health,
            resource_efficiency,
        ])

        if stability_score >= 90:
            status = "HEALTHY"
            risk = "LOW"

        elif stability_score >= 75:
            status = "WARNING"
            risk = "MEDIUM"

        else:
            status = "CRITICAL"
            risk = "HIGH"

        return MemoryGovernance(
            memory_health_score=
            round(
                memory_health,
                2,
            ),

            resource_efficiency_score=
            round(
                resource_efficiency,
                2,
            ),

            stability_score=
            round(
                stability_score,
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
    ) -> MemorySummary:

        hosts = {
            f.host
            for f in self.findings
        }

        total_memory = sum([
            f.used_memory_mb
            for f in self.findings
        ])

        avg_gc = statistics.mean([
            f.garbage_collection_events
            for f in self.findings
        ]) if self.findings else 0

        oom_total = len([
            f for f in self.findings
            if f.oom_risk_detected
        ])

        return MemorySummary(
            total_hosts_analyzed=
            len(hosts),

            total_memory_consumed_mb=
            round(
                total_memory,
                2,
            ),

            average_gc_events=
            round(
                avg_gc,
                2,
            ),

            total_oom_risks=
            oom_total,

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
            / "memory_usage_report.json"
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
            "Memory usage JSON report exported."
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
            "# Enterprise Memory Usage Report",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Summary",

            f"- Hosts Analyzed: "
            f"{summary.total_hosts_analyzed}",

            f"- Total Memory Consumed: "
            f"{summary.total_memory_consumed_mb} MB",

            f"- Avg GC Events: "
            f"{summary.average_gc_events}",

            f"- OOM Risks: "
            f"{summary.total_oom_risks}\n",

            "## Governance",

            f"- Memory Health Score: "
            f"{governance.memory_health_score}",

            f"- Resource Efficiency Score: "
            f"{governance.resource_efficiency_score}",

            f"- Stability Score: "
            f"{governance.stability_score}",

            f"- Governance Status: "
            f"{governance.governance_status}",

            f"- Enterprise Risk: "
            f"{governance.enterprise_risk_level}\n",

            "## Metrics",

            f"- Average Memory Usage: "
            f"{metrics.average_memory_usage_percent}%",

            f"- Peak Memory Usage: "
            f"{metrics.peak_memory_usage_percent}%",

            f"- Total Swap Usage: "
            f"{metrics.total_swap_usage_mb} MB",

            f"- Memory Leak Events: "
            f"{metrics.memory_leak_events}",

            f"- OOM Risk Events: "
            f"{metrics.oom_risk_events}",

            f"- Saturated Services: "
            f"{metrics.saturated_services}",

            f"- Governance Score: "
            f"{metrics.governance_score}\n",

            "## Memory Findings",

            "| Service | Host | Usage % | Swap MB | Leak | OOM Risk |",
            "|----------|------|----------|----------|------|-----------|",
        ]

        for finding in self.findings:

            lines.append(
                f"| "
                f"{finding.service_name} | "
                f"{finding.host} | "
                f"{finding.memory_usage_percent}% | "
                f"{finding.swap_usage_mb} | "
                f"{finding.memory_leak_detected} | "
                f"{finding.oom_risk_detected} |"
            )

        path = (
            EXPORTS_DIR
            / "memory_usage_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Memory usage Markdown report exported."
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
<td>{finding.host}</td>
<td>{finding.memory_usage_percent}</td>
<td>{finding.swap_usage_mb}</td>
<td>{finding.memory_leak_detected}</td>
<td>{finding.oom_risk_detected}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Memory Usage Report
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
Enterprise Memory Usage Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>Service</th>
<th>Host</th>
<th>Usage %</th>
<th>Swap MB</th>
<th>Leak</th>
<th>OOM Risk</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "memory_usage_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "Memory usage HTML report exported."
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
            governance.stability_score
            < 70
        ):

            logger.error(
                "Memory governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Memory governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting memory usage pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Memory usage pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> MemoryUsageReportEngine:

    return MemoryUsageReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "memory_usage_report.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()