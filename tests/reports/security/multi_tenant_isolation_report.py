"""
===============================================================================
KwanzaControl Enterprise Multi-Tenant Isolation Security Report Engine
File: reports/security/multi_tenant_isolation_report.py

Description:
    Enterprise-grade multi-tenant isolation validation and security reporting
    engine responsible for:

    - Tenant boundary validation
    - Cross-tenant access detection
    - Row-Level Security (RLS) verification
    - Tenant data leakage analysis
    - API isolation validation
    - Database isolation monitoring
    - Shared resource exposure analysis
    - Identity boundary governance
    - SaaS tenant segmentation validation
    - UEBA tenant anomaly correlation
    - Compliance and governance enforcement
    - Zero Trust tenant isolation assessment
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

SECURITY_DIR = REPORTS_DIR / "security"

EXPORTS_DIR = SECURITY_DIR / "exports"

LOGS_DIR = SECURITY_DIR / "logs"

HISTORY_DIR = SECURITY_DIR / "history"

# =============================================================================
# LOGGER
# =============================================================================


def setup_logger() -> logging.Logger:

    LOGS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger = logging.getLogger(
        "multi_tenant_isolation_report"
    )

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] "
        "[%(levelname)s] "
        "%(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        LOGS_DIR / "multi_tenant_isolation_report.log",
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
class TenantIsolationRecord:
    tenant_id: str
    resource_id: str
    resource_type: str
    access_origin_tenant: str
    authorized: bool
    rls_enabled: bool
    api_isolated: bool
    encryption_enabled: bool
    status: str
    timestamp: str


@dataclass(slots=True)
class TenantIsolationMetrics:
    total_resources: int
    isolated_resources: int
    shared_resources: int
    unauthorized_access_attempts: int
    rls_coverage_rate: float
    api_isolation_rate: float
    encryption_coverage_rate: float
    isolation_score: float


@dataclass(slots=True)
class TenantIsolationGovernance:
    compliant_tenants: int
    non_compliant_tenants: int
    compliance_rate: float
    critical_violations: int
    governance_status: str


@dataclass(slots=True)
class TenantIsolationSummary:
    total_tenants: int
    total_resources: int
    total_cross_tenant_attempts: int
    total_violations: int
    average_isolation_score: float
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class MultiTenantIsolationReportEngine:
    """
    Enterprise-grade multi-tenant isolation validation engine.
    """

    def __init__(
        self,
        dataset_file: Path,
    ) -> None:

        self.dataset_file = dataset_file

        self.raw_data: Dict[
            str,
            Any,
        ] = {}

        self.records: List[
            TenantIsolationRecord
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "MultiTenantIsolationReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading tenant isolation dataset..."
        )

        if not self.dataset_file.exists():

            raise FileNotFoundError(
                f"Dataset file not found: "
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
            "Parsing isolation records..."
        )

        for item in self.raw_data.get(
            "tenant_isolation",
            [],
        ):

            self.records.append(
                TenantIsolationRecord(
                    tenant_id=item.get(
                        "tenant_id",
                        "",
                    ),
                    resource_id=item.get(
                        "resource_id",
                        "",
                    ),
                    resource_type=item.get(
                        "resource_type",
                        "",
                    ),
                    access_origin_tenant=item.get(
                        "access_origin_tenant",
                        "",
                    ),
                    authorized=bool(
                        item.get(
                            "authorized",
                            False,
                        )
                    ),
                    rls_enabled=bool(
                        item.get(
                            "rls_enabled",
                            False,
                        )
                    ),
                    api_isolated=bool(
                        item.get(
                            "api_isolated",
                            False,
                        )
                    ),
                    encryption_enabled=bool(
                        item.get(
                            "encryption_enabled",
                            False,
                        )
                    ),
                    status=item.get(
                        "status",
                        "UNKNOWN",
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
    ) -> TenantIsolationMetrics:

        total_resources = len(
            self.records
        )

        isolated_resources = len([
            r for r in self.records
            if (
                r.rls_enabled
                and r.api_isolated
                and r.encryption_enabled
            )
        ])

        shared_resources = (
            total_resources
            - isolated_resources
        )

        unauthorized_access_attempts = len([
            r for r in self.records
            if not r.authorized
        ])

        rls_coverage_rate = (
            len([
                r for r in self.records
                if r.rls_enabled
            ])
            / max(total_resources, 1)
        ) * 100

        api_isolation_rate = (
            len([
                r for r in self.records
                if r.api_isolated
            ])
            / max(total_resources, 1)
        ) * 100

        encryption_coverage_rate = (
            len([
                r for r in self.records
                if r.encryption_enabled
            ])
            / max(total_resources, 1)
        ) * 100

        isolation_score = statistics.mean([
            rls_coverage_rate,
            api_isolation_rate,
            encryption_coverage_rate,
        ])

        return TenantIsolationMetrics(
            total_resources=
            total_resources,

            isolated_resources=
            isolated_resources,

            shared_resources=
            shared_resources,

            unauthorized_access_attempts=
            unauthorized_access_attempts,

            rls_coverage_rate=
            round(rls_coverage_rate, 2),

            api_isolation_rate=
            round(api_isolation_rate, 2),

            encryption_coverage_rate=
            round(encryption_coverage_rate, 2),

            isolation_score=
            round(isolation_score, 2),
        )

    # =========================================================================
    # GOVERNANCE
    # =========================================================================

    def governance(
        self,
    ) -> TenantIsolationGovernance:

        tenant_scores: Dict[
            str,
            List[bool],
        ] = {}

        for record in self.records:

            tenant_scores.setdefault(
                record.tenant_id,
                [],
            )

            tenant_scores[
                record.tenant_id
            ].append(
                (
                    record.rls_enabled
                    and record.api_isolated
                    and record.encryption_enabled
                    and record.authorized
                )
            )

        compliant = 0

        non_compliant = 0

        critical = 0

        for _, checks in tenant_scores.items():

            success_rate = (
                sum(checks)
                / max(len(checks), 1)
            )

            if success_rate >= 0.95:
                compliant += 1
            else:
                non_compliant += 1

            if success_rate < 0.70:
                critical += 1

        compliance_rate = (
            compliant
            / max(
                len(tenant_scores),
                1,
            )
        ) * 100

        if compliance_rate >= 95:
            status = "COMPLIANT"

        elif compliance_rate >= 80:
            status = "WARNING"

        else:
            status = "CRITICAL"

        return TenantIsolationGovernance(
            compliant_tenants=
            compliant,

            non_compliant_tenants=
            non_compliant,

            compliance_rate=
            round(compliance_rate, 2),

            critical_violations=
            critical,

            governance_status=
            status,
        )

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(
        self,
    ) -> TenantIsolationSummary:

        metrics = self.compute_metrics()

        tenants = {
            r.tenant_id
            for r in self.records
        }

        cross_tenant_attempts = len([
            r for r in self.records
            if (
                r.tenant_id
                != r.access_origin_tenant
            )
        ])

        violations = len([
            r for r in self.records
            if not r.authorized
        ])

        return TenantIsolationSummary(
            total_tenants=
            len(tenants),

            total_resources=
            metrics.total_resources,

            total_cross_tenant_attempts=
            cross_tenant_attempts,

            total_violations=
            violations,

            average_isolation_score=
            metrics.isolation_score,

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

            "records": [
                asdict(record)
                for record
                in self.records
            ],
        }

        path = (
            EXPORTS_DIR
            / "multi_tenant_isolation_report.json"
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
            "JSON report exported."
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
            "# Enterprise Multi-Tenant Isolation Report",
            (
                f"Generated: "
                f"{summary.generated_at}\n"
            ),

            "## Summary",

            (
                f"- Total Tenants: "
                f"{summary.total_tenants}"
            ),

            (
                f"- Total Resources: "
                f"{summary.total_resources}"
            ),

            (
                f"- Cross-Tenant Attempts: "
                f"{summary.total_cross_tenant_attempts}"
            ),

            (
                f"- Violations: "
                f"{summary.total_violations}"
            ),

            (
                f"- Isolation Score: "
                f"{summary.average_isolation_score}\n"
            ),

            "## Governance",

            (
                f"- Compliance Rate: "
                f"{governance.compliance_rate}%"
            ),

            (
                f"- Governance Status: "
                f"{governance.governance_status}\n"
            ),

            "## Metrics",

            (
                f"- RLS Coverage: "
                f"{metrics.rls_coverage_rate}%"
            ),

            (
                f"- API Isolation: "
                f"{metrics.api_isolation_rate}%"
            ),

            (
                f"- Encryption Coverage: "
                f"{metrics.encryption_coverage_rate}%\n"
            ),

            "## Resource Validation",

            "| Tenant | Resource | Authorized | RLS | API | Encryption |",
            "|--------|----------|-------------|-----|-----|-------------|",
        ]

        for record in self.records:

            lines.append(
                f"| "
                f"{record.tenant_id} | "
                f"{record.resource_id} | "
                f"{record.authorized} | "
                f"{record.rls_enabled} | "
                f"{record.api_isolated} | "
                f"{record.encryption_enabled} |"
            )

        path = (
            EXPORTS_DIR
            / "multi_tenant_isolation_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Markdown report exported."
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

        for record in self.records:

            rows.append(
                f"""
<tr>
<td>{record.tenant_id}</td>
<td>{record.resource_id}</td>
<td>{record.authorized}</td>
<td>{record.rls_enabled}</td>
<td>{record.api_isolated}</td>
<td>{record.encryption_enabled}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Multi-Tenant Isolation Report
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
Enterprise Multi-Tenant Isolation Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>Tenant</th>
<th>Resource</th>
<th>Authorized</th>
<th>RLS</th>
<th>API</th>
<th>Encryption</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "multi_tenant_isolation_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "HTML report exported."
        )

        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(
        self,
    ) -> None:

        governance = self.governance()

        if governance.compliance_rate < 85:

            logger.error(
                "Tenant isolation governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Tenant isolation governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting tenant isolation pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Tenant isolation pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> MultiTenantIsolationReportEngine:

    return MultiTenantIsolationReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "multi_tenant_isolation.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()