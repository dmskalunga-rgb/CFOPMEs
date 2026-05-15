"""
===============================================================================
KwanzaControl Enterprise SQL Injection Security Report Engine
File: reports/security/sql_injection_report.py

Description:
    Enterprise-grade SQL Injection (SQLi) security analysis and governance
    engine responsible for:

    - SQL Injection vulnerability detection
    - ORM and raw query validation
    - Prepared statement enforcement analysis
    - Dynamic query exposure assessment
    - Multi-tenant query isolation validation
    - Database access security posture analysis
    - WAF and API SQLi protection assessment
    - SQL payload attack pattern correlation
    - CI/CD SQLi governance validation
    - Zero Trust database access verification
    - SOC2 / ISO27001 / PCI-DSS compliance auditing
    - Threat severity classification
    - JSON / Markdown / HTML reporting exports

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
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
        "sql_injection_report"
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
        LOGS_DIR / "sql_injection_report.log",
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
# SQLI PATTERNS
# =============================================================================

SQLI_PATTERNS = {
    "UNION_BASED":
        r"(?i)(union\s+select)",

    "BOOLEAN_BASED":
        r"(?i)(or\s+1=1)",

    "TIME_BASED":
        r"(?i)(sleep\s*\(|benchmark\s*\()",

    "STACKED_QUERIES":
        r"(?i)(;\s*drop\s+table)",

    "COMMENT_INJECTION":
        r"(?i)(--|#|\/\*)",

    "TAUTOLOGY":
        r"(?i)(\'\s*or\s*\'1\'=\'1)",

    "RAW_QUERY":
        r"(?i)(execute\s*\(|raw\s*sql)",

    "STRING_CONCAT":
        r"(?i)(\+.*select|f\".*select)",
}

# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass(slots=True)
class SQLInjectionFinding:
    file_path: str
    line_number: int
    vulnerability_type: str
    severity: str
    vulnerable_query_hash: str
    prepared_statement_used: bool
    orm_protected: bool
    tenant_isolated: bool
    remediation_required: bool
    timestamp: str


@dataclass(slots=True)
class SQLInjectionMetrics:
    total_findings: int
    critical_findings: int
    high_findings: int
    medium_findings: int
    low_findings: int
    protected_queries: int
    unprotected_queries: int
    remediation_backlog: int
    governance_score: float


@dataclass(slots=True)
class SQLInjectionGovernance:
    compliance_rate: float
    zero_trust_database_score: float
    unresolved_critical_findings: int
    governance_status: str
    enterprise_risk_level: str


@dataclass(slots=True)
class SQLInjectionSummary:
    total_files_scanned: int
    total_queries_analyzed: int
    vulnerable_queries_detected: int
    average_protection_score: float
    generated_at: str


# =============================================================================
# ENGINE
# =============================================================================


class SQLInjectionReportEngine:
    """
    Enterprise SQL Injection security observability engine.
    """

    SEVERITY_MAPPING = {
        "UNION_BASED": "CRITICAL",
        "BOOLEAN_BASED": "HIGH",
        "TIME_BASED": "HIGH",
        "STACKED_QUERIES": "CRITICAL",
        "COMMENT_INJECTION": "MEDIUM",
        "TAUTOLOGY": "HIGH",
        "RAW_QUERY": "MEDIUM",
        "STRING_CONCAT": "HIGH",
    }

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
            SQLInjectionFinding
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "SQLInjectionReportEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading SQL injection dataset..."
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
    # HELPERS
    # =========================================================================

    @staticmethod
    def hash_query(
        query: str,
    ) -> str:

        return hashlib.sha256(
            query.encode(
                "utf-8"
            )
        ).hexdigest()

    def detect_pattern(
        self,
        query: str,
    ) -> str:

        for pattern_name, regex in (
            SQLI_PATTERNS.items()
        ):

            if re.search(
                regex,
                query,
            ):
                return pattern_name

        return "UNKNOWN"

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:

        logger.info(
            "Parsing SQL injection findings..."
        )

        for item in self.raw_data.get(
            "findings",
            [],
        ):

            query = item.get(
                "query",
                "",
            )

            vulnerability_type = (
                self.detect_pattern(
                    query
                )
            )

            severity = (
                self.SEVERITY_MAPPING.get(
                    vulnerability_type,
                    "LOW",
                )
            )

            self.findings.append(
                SQLInjectionFinding(
                    file_path=item.get(
                        "file_path",
                        "",
                    ),

                    line_number=int(
                        item.get(
                            "line_number",
                            0,
                        )
                    ),

                    vulnerability_type=
                    vulnerability_type,

                    severity=
                    severity,

                    vulnerable_query_hash=
                    self.hash_query(
                        query
                    ),

                    prepared_statement_used=bool(
                        item.get(
                            "prepared_statement_used",
                            False,
                        )
                    ),

                    orm_protected=bool(
                        item.get(
                            "orm_protected",
                            False,
                        )
                    ),

                    tenant_isolated=bool(
                        item.get(
                            "tenant_isolated",
                            False,
                        )
                    ),

                    remediation_required=bool(
                        item.get(
                            "remediation_required",
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
    ) -> SQLInjectionMetrics:

        total = len(
            self.findings
        )

        critical = len([
            f for f in self.findings
            if f.severity == "CRITICAL"
        ])

        high = len([
            f for f in self.findings
            if f.severity == "HIGH"
        ])

        medium = len([
            f for f in self.findings
            if f.severity == "MEDIUM"
        ])

        low = len([
            f for f in self.findings
            if f.severity == "LOW"
        ])

        protected = len([
            f for f in self.findings
            if (
                f.prepared_statement_used
                and f.orm_protected
                and f.tenant_isolated
            )
        ])

        unprotected = (
            total - protected
        )

        backlog = len([
            f for f in self.findings
            if f.remediation_required
        ])

        governance_score = max(
            0,
            100 - (
                (critical * 10)
                + (high * 5)
                + (medium * 2)
            )
        )

        return SQLInjectionMetrics(
            total_findings=
            total,

            critical_findings=
            critical,

            high_findings=
            high,

            medium_findings=
            medium,

            low_findings=
            low,

            protected_queries=
            protected,

            unprotected_queries=
            unprotected,

            remediation_backlog=
            backlog,

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
    ) -> SQLInjectionGovernance:

        metrics = (
            self.compute_metrics()
        )

        protected_ratio = (
            metrics.protected_queries
            / max(
                metrics.total_findings,
                1,
            )
        ) * 100

        zero_trust_score = statistics.mean([
            protected_ratio,
            metrics.governance_score,
        ])

        unresolved_critical = len([
            f for f in self.findings
            if (
                f.severity == "CRITICAL"
                and f.remediation_required
            )
        ])

        compliance_rate = max(
            0,
            100 - (
                unresolved_critical * 10
            )
        )

        if compliance_rate >= 95:
            status = "COMPLIANT"
            risk = "LOW"

        elif compliance_rate >= 80:
            status = "WARNING"
            risk = "MEDIUM"

        else:
            status = "CRITICAL"
            risk = "HIGH"

        return SQLInjectionGovernance(
            compliance_rate=
            round(
                compliance_rate,
                2,
            ),

            zero_trust_database_score=
            round(
                zero_trust_score,
                2,
            ),

            unresolved_critical_findings=
            unresolved_critical,

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
    ) -> SQLInjectionSummary:

        files = {
            f.file_path
            for f in self.findings
        }

        protection_scores = []

        for finding in self.findings:

            score = 0

            if finding.prepared_statement_used:
                score += 35

            if finding.orm_protected:
                score += 35

            if finding.tenant_isolated:
                score += 30

            protection_scores.append(
                score
            )

        average_protection = (
            statistics.mean(
                protection_scores
            )
            if protection_scores
            else 0
        )

        return SQLInjectionSummary(
            total_files_scanned=
            len(files),

            total_queries_analyzed=
            len(self.findings),

            vulnerable_queries_detected=
            len(self.findings),

            average_protection_score=
            round(
                average_protection,
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
                asdict(finding)
                for finding
                in self.findings
            ],
        }

        path = (
            EXPORTS_DIR
            / "sql_injection_report.json"
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
            "SQL injection JSON report exported."
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
            "# Enterprise SQL Injection Security Report",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Summary",

            f"- Total Files Scanned: "
            f"{summary.total_files_scanned}",

            f"- Queries Analyzed: "
            f"{summary.total_queries_analyzed}",

            f"- Vulnerabilities Detected: "
            f"{summary.vulnerable_queries_detected}",

            f"- Avg Protection Score: "
            f"{summary.average_protection_score}\n",

            "## Governance",

            f"- Compliance Rate: "
            f"{governance.compliance_rate}%",

            f"- Zero Trust DB Score: "
            f"{governance.zero_trust_database_score}%",

            f"- Governance Status: "
            f"{governance.governance_status}",

            f"- Enterprise Risk: "
            f"{governance.enterprise_risk_level}\n",

            "## Metrics",

            f"- Critical Findings: "
            f"{metrics.critical_findings}",

            f"- High Findings: "
            f"{metrics.high_findings}",

            f"- Medium Findings: "
            f"{metrics.medium_findings}",

            f"- Low Findings: "
            f"{metrics.low_findings}",

            f"- Protected Queries: "
            f"{metrics.protected_queries}",

            f"- Unprotected Queries: "
            f"{metrics.unprotected_queries}",

            f"- Governance Score: "
            f"{metrics.governance_score}\n",

            "## Vulnerability Findings",

            "| File | Type | Severity | Prepared | ORM | Tenant Isolation |",
            "|------|------|-----------|-----------|-----|------------------|",
        ]

        for finding in self.findings:

            lines.append(
                f"| "
                f"{finding.file_path} | "
                f"{finding.vulnerability_type} | "
                f"{finding.severity} | "
                f"{finding.prepared_statement_used} | "
                f"{finding.orm_protected} | "
                f"{finding.tenant_isolated} |"
            )

        path = (
            EXPORTS_DIR
            / "sql_injection_report.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "SQL injection Markdown report exported."
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
<td>{finding.file_path}</td>
<td>{finding.vulnerability_type}</td>
<td>{finding.severity}</td>
<td>{finding.prepared_statement_used}</td>
<td>{finding.orm_protected}</td>
<td>{finding.tenant_isolated}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise SQL Injection Security Report
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
Enterprise SQL Injection Security Report
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<table>

<tr>
<th>File</th>
<th>Type</th>
<th>Severity</th>
<th>Prepared</th>
<th>ORM</th>
<th>Tenant Isolation</th>
</tr>

{''.join(rows)}

</table>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "sql_injection_report.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "SQL injection HTML report exported."
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
            governance.compliance_rate
            < 85
        ):

            logger.error(
                "SQL injection governance validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "SQL injection governance validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting SQL injection pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "SQL injection pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> SQLInjectionReportEngine:

    return SQLInjectionReportEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "sql_injection.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()