"""
===============================================================================
KwanzaControl Enterprise Security Scorecard Engine
File: reports/security/security_scorecard.py

Description:
    Enterprise-grade unified security scorecard and cyber governance engine
    responsible for:

    - Enterprise-wide security posture scoring
    - IAM/RBAC maturity assessment
    - Zero Trust security evaluation
    - Vulnerability risk aggregation
    - Secret exposure governance scoring
    - Multi-tenant isolation maturity analysis
    - JWT/API security validation
    - Compliance posture assessment
    - Security KPI and SLA governance
    - SOC2 / ISO27001 / PCI-DSS readiness analysis
    - Security trend scoring
    - Executive CISO scorecards
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
        "security_scorecard"
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
        LOGS_DIR / "security_scorecard.log",
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
class SecurityDomainScore:
    domain: str
    score: float
    maturity: str
    risk_level: str
    compliant: bool
    weighted_impact: float


@dataclass(slots=True)
class SecurityKPI:
    metric_name: str
    value: float
    threshold: float
    status: str


@dataclass(slots=True)
class SecurityScorecardMetrics:
    total_domains: int
    average_score: float
    compliance_rate: float
    high_risk_domains: int
    critical_risk_domains: int
    overall_security_score: float
    zero_trust_maturity_score: float


@dataclass(slots=True)
class SecurityScorecardSummary:
    organization: str
    environment: str
    report_version: str
    generated_at: str
    executive_rating: str
    ciso_recommendation: str


# =============================================================================
# ENGINE
# =============================================================================


class SecurityScorecardEngine:
    """
    Enterprise unified cyber security scorecard engine.
    """

    DOMAIN_WEIGHTS = {
        "IAM": 1.2,
        "RBAC": 1.1,
        "SECRETS": 1.3,
        "ZERO_TRUST": 1.5,
        "API_SECURITY": 1.2,
        "TENANT_ISOLATION": 1.4,
        "COMPLIANCE": 1.3,
        "VULNERABILITY_MANAGEMENT": 1.4,
        "THREAT_DETECTION": 1.5,
        "AUDIT_GOVERNANCE": 1.2,
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

        self.domains: List[
            SecurityDomainScore
        ] = []

        self.kpis: List[
            SecurityKPI
        ] = []

        EXPORTS_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        logger.info(
            "SecurityScorecardEngine initialized."
        )

    # =========================================================================
    # LOAD
    # =========================================================================

    def load(self) -> None:

        logger.info(
            "Loading security scorecard dataset..."
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
    def classify_maturity(
        score: float,
    ) -> str:

        if score >= 95:
            return "OPTIMIZED"

        if score >= 85:
            return "ADVANCED"

        if score >= 70:
            return "MANAGED"

        if score >= 50:
            return "DEFINED"

        return "INITIAL"

    @staticmethod
    def classify_risk(
        score: float,
    ) -> str:

        if score >= 90:
            return "LOW"

        if score >= 75:
            return "MEDIUM"

        if score >= 60:
            return "HIGH"

        return "CRITICAL"

    @staticmethod
    def executive_rating(
        score: float,
    ) -> str:

        if score >= 95:
            return "A+"

        if score >= 90:
            return "A"

        if score >= 80:
            return "B"

        if score >= 70:
            return "C"

        return "D"

    # =========================================================================
    # PARSE
    # =========================================================================

    def parse(self) -> None:

        logger.info(
            "Parsing scorecard domains..."
        )

        for item in self.raw_data.get(
            "domains",
            [],
        ):

            domain = item.get(
                "domain",
                "UNKNOWN",
            )

            score = float(
                item.get(
                    "score",
                    0,
                )
            )

            weight = (
                self.DOMAIN_WEIGHTS.get(
                    domain,
                    1.0,
                )
            )

            self.domains.append(
                SecurityDomainScore(
                    domain=domain,

                    score=score,

                    maturity=
                    self.classify_maturity(
                        score
                    ),

                    risk_level=
                    self.classify_risk(
                        score
                    ),

                    compliant=bool(
                        item.get(
                            "compliant",
                            False,
                        )
                    ),

                    weighted_impact=
                    round(
                        score * weight,
                        2,
                    ),
                )
            )

        logger.info(
            "Parsing KPIs..."
        )

        for item in self.raw_data.get(
            "kpis",
            [],
        ):

            value = float(
                item.get(
                    "value",
                    0,
                )
            )

            threshold = float(
                item.get(
                    "threshold",
                    0,
                )
            )

            status = (
                "PASS"
                if value >= threshold
                else "FAIL"
            )

            self.kpis.append(
                SecurityKPI(
                    metric_name=item.get(
                        "metric_name",
                        "",
                    ),
                    value=value,
                    threshold=threshold,
                    status=status,
                )
            )

    # =========================================================================
    # METRICS
    # =========================================================================

    def compute_metrics(
        self,
    ) -> SecurityScorecardMetrics:

        scores = [
            d.score
            for d in self.domains
        ]

        average_score = (
            statistics.mean(scores)
            if scores
            else 0
        )

        compliant = len([
            d for d in self.domains
            if d.compliant
        ])

        compliance_rate = (
            compliant
            / max(
                len(self.domains),
                1,
            )
        ) * 100

        high_risk = len([
            d for d in self.domains
            if d.risk_level == "HIGH"
        ])

        critical_risk = len([
            d for d in self.domains
            if d.risk_level == "CRITICAL"
        ])

        weighted_scores = [
            d.weighted_impact
            for d in self.domains
        ]

        overall_score = (
            statistics.mean(
                weighted_scores
            )
            if weighted_scores
            else 0
        )

        zero_trust_domains = [
            d.score
            for d in self.domains
            if d.domain in {
                "ZERO_TRUST",
                "IAM",
                "RBAC",
                "TENANT_ISOLATION",
            }
        ]

        zero_trust_score = (
            statistics.mean(
                zero_trust_domains
            )
            if zero_trust_domains
            else 0
        )

        return SecurityScorecardMetrics(
            total_domains=
            len(self.domains),

            average_score=
            round(
                average_score,
                2,
            ),

            compliance_rate=
            round(
                compliance_rate,
                2,
            ),

            high_risk_domains=
            high_risk,

            critical_risk_domains=
            critical_risk,

            overall_security_score=
            round(
                overall_score,
                2,
            ),

            zero_trust_maturity_score=
            round(
                zero_trust_score,
                2,
            ),
        )

    # =========================================================================
    # SUMMARY
    # =========================================================================

    def summary(
        self,
    ) -> SecurityScorecardSummary:

        metrics = (
            self.compute_metrics()
        )

        score = (
            metrics.overall_security_score
        )

        if score >= 90:
            recommendation = (
                "Maintain continuous governance "
                "and proactive cyber resilience."
            )

        elif score >= 75:
            recommendation = (
                "Improve medium-risk domains "
                "and increase Zero Trust maturity."
            )

        else:
            recommendation = (
                "Immediate remediation required "
                "for critical cyber governance gaps."
            )

        return SecurityScorecardSummary(
            organization=
            self.raw_data.get(
                "organization",
                "KwanzaControl",
            ),

            environment=
            self.raw_data.get(
                "environment",
                "production",
            ),

            report_version=
            self.raw_data.get(
                "report_version",
                "1.0.0",
            ),

            generated_at=
            datetime.now(
                UTC
            ).isoformat(),

            executive_rating=
            self.executive_rating(
                score
            ),

            ciso_recommendation=
            recommendation,
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

            "metrics":
                asdict(
                    self.compute_metrics()
                ),

            "domains": [
                asdict(domain)
                for domain
                in self.domains
            ],

            "kpis": [
                asdict(kpi)
                for kpi
                in self.kpis
            ],
        }

        path = (
            EXPORTS_DIR
            / "security_scorecard.json"
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
            "Security scorecard JSON exported."
        )

        return path

    # =========================================================================
    # EXPORT MARKDOWN
    # =========================================================================

    def export_markdown(
        self,
    ) -> Path:

        summary = self.summary()

        metrics = self.compute_metrics()

        lines = [
            "# Enterprise Security Scorecard",

            f"Generated: "
            f"{summary.generated_at}\n",

            "## Executive Overview",

            f"- Organization: "
            f"{summary.organization}",

            f"- Environment: "
            f"{summary.environment}",

            f"- Executive Rating: "
            f"{summary.executive_rating}",

            f"- Overall Security Score: "
            f"{metrics.overall_security_score}",

            f"- Zero Trust Score: "
            f"{metrics.zero_trust_maturity_score}\n",

            "## Governance Metrics",

            f"- Compliance Rate: "
            f"{metrics.compliance_rate}%",

            f"- High Risk Domains: "
            f"{metrics.high_risk_domains}",

            f"- Critical Risk Domains: "
            f"{metrics.critical_risk_domains}\n",

            "## Domain Scores",

            "| Domain | Score | Maturity | Risk | Compliant |",
            "|--------|-------|-----------|------|------------|",
        ]

        for domain in self.domains:

            lines.append(
                f"| "
                f"{domain.domain} | "
                f"{domain.score} | "
                f"{domain.maturity} | "
                f"{domain.risk_level} | "
                f"{domain.compliant} |"
            )

        lines.extend([
            "\n## KPI Status",

            "| KPI | Value | Threshold | Status |",
            "|-----|--------|------------|--------|",
        ])

        for kpi in self.kpis:

            lines.append(
                f"| "
                f"{kpi.metric_name} | "
                f"{kpi.value} | "
                f"{kpi.threshold} | "
                f"{kpi.status} |"
            )

        lines.extend([
            "\n## CISO Recommendation",

            summary.ciso_recommendation,
        ])

        path = (
            EXPORTS_DIR
            / "security_scorecard.md"
        )

        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )

        logger.info(
            "Security scorecard Markdown exported."
        )

        return path

    # =========================================================================
    # EXPORT HTML
    # =========================================================================

    def export_html(
        self,
    ) -> Path:

        summary = self.summary()

        metrics = self.compute_metrics()

        domain_rows = []

        for domain in self.domains:

            domain_rows.append(
                f"""
<tr>
<td>{domain.domain}</td>
<td>{domain.score}</td>
<td>{domain.maturity}</td>
<td>{domain.risk_level}</td>
<td>{domain.compliant}</td>
</tr>
"""
            )

        kpi_rows = []

        for kpi in self.kpis:

            kpi_rows.append(
                f"""
<tr>
<td>{kpi.metric_name}</td>
<td>{kpi.value}</td>
<td>{kpi.threshold}</td>
<td>{kpi.status}</td>
</tr>
"""
            )

        html = f"""
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<title>
Enterprise Security Scorecard
</title>

<style>

body {{
    font-family: Arial;
    margin: 40px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 40px;
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
Enterprise Security Scorecard
</h1>

<p>
Generated:
{summary.generated_at}
</p>

<h2>
Executive Overview
</h2>

<ul>
<li>
Executive Rating:
{summary.executive_rating}
</li>

<li>
Overall Security Score:
{metrics.overall_security_score}
</li>

<li>
Zero Trust Maturity:
{metrics.zero_trust_maturity_score}
</li>
</ul>

<h2>
Domain Scores
</h2>

<table>

<tr>
<th>Domain</th>
<th>Score</th>
<th>Maturity</th>
<th>Risk</th>
<th>Compliant</th>
</tr>

{''.join(domain_rows)}

</table>

<h2>
KPI Status
</h2>

<table>

<tr>
<th>KPI</th>
<th>Value</th>
<th>Threshold</th>
<th>Status</th>
</tr>

{''.join(kpi_rows)}

</table>

<h2>
CISO Recommendation
</h2>

<p>
{summary.ciso_recommendation}
</p>

</body>

</html>
"""

        path = (
            EXPORTS_DIR
            / "security_scorecard.html"
        )

        path.write_text(
            html,
            encoding="utf-8",
        )

        logger.info(
            "Security scorecard HTML exported."
        )

        return path

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate(
        self,
    ) -> None:

        metrics = (
            self.compute_metrics()
        )

        if (
            metrics.overall_security_score
            < 70
        ):

            logger.error(
                "Enterprise security posture validation failed."
            )

            raise SystemExit(
                1
            )

        logger.info(
            "Enterprise security posture validation passed."
        )

    # =========================================================================
    # PIPELINE
    # =========================================================================

    def run(
        self,
    ) -> None:

        logger.info(
            "Starting enterprise security scorecard pipeline..."
        )

        self.load()

        self.parse()

        self.export_json()

        self.export_markdown()

        self.export_html()

        self.validate()

        logger.info(
            "Enterprise security scorecard pipeline completed successfully."
        )


# =============================================================================
# FACTORY
# =============================================================================


def create_engine(
    file: Path,
) -> SecurityScorecardEngine:

    return SecurityScorecardEngine(
        dataset_file=file
    )


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":

    FILE = (
        HISTORY_DIR
        / "security_scorecard.json"
    )

    engine = create_engine(
        FILE
    )

    engine.run()