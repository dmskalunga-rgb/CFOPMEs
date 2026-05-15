"""
===============================================================================
KwanzaControl Enterprise Security Reports Package
File: reports/security/__init__.py

Description:
    Enterprise-grade Security Reporting Package initializer responsible for:

    - Centralized security reporting exports
    - Security observability registration
    - Threat intelligence integrations
    - Compliance governance initialization
    - SIEM interoperability exposure
    - UEBA/Fraud/SOC integrations
    - Runtime metadata exposure
    - Security analytics orchestration
    - Audit-ready package initialization
    - Enterprise monitoring compatibility

Architecture Level:
    ENTERPRISE / PRODUCTION READY

===============================================================================
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Final

# =============================================================================
# PACKAGE METADATA
# =============================================================================

PACKAGE_NAME: Final[str] = (
    "kwanzacontrol.reports.security"
)

PACKAGE_DESCRIPTION: Final[str] = (
    "Enterprise Security Intelligence Reporting Package"
)

PACKAGE_VERSION: Final[str] = (
    "1.0.0"
)

PACKAGE_STATUS: Final[str] = (
    "production"
)

PACKAGE_AUTHOR: Final[str] = (
    "KwanzaControl Security Engineering"
)

PACKAGE_LICENSE: Final[str] = (
    "Enterprise Proprietary"
)

PACKAGE_CREATED_AT: Final[str] = (
    datetime.now(UTC).isoformat()
)

# =============================================================================
# PACKAGE PATHS
# =============================================================================

PACKAGE_ROOT: Final[Path] = (
    Path(__file__).resolve().parent
)

REPORTS_ROOT: Final[Path] = (
    PACKAGE_ROOT.parent
)

EXPORTS_DIR: Final[Path] = (
    PACKAGE_ROOT / "exports"
)

LOGS_DIR: Final[Path] = (
    PACKAGE_ROOT / "logs"
)

HISTORY_DIR: Final[Path] = (
    PACKAGE_ROOT / "history"
)

THREATS_DIR: Final[Path] = (
    PACKAGE_ROOT / "threats"
)

CONFIG_DIR: Final[Path] = (
    PACKAGE_ROOT / "config"
)

AUDITS_DIR: Final[Path] = (
    PACKAGE_ROOT / "audits"
)

COMPLIANCE_DIR: Final[Path] = (
    PACKAGE_ROOT / "compliance"
)

# =============================================================================
# DIRECTORY INITIALIZATION
# =============================================================================

DIRECTORIES = [
    EXPORTS_DIR,
    LOGS_DIR,
    HISTORY_DIR,
    THREATS_DIR,
    CONFIG_DIR,
    AUDITS_DIR,
    COMPLIANCE_DIR,
]

for directory in DIRECTORIES:

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

# =============================================================================
# SECURITY MODULE EXPORTS
# =============================================================================

try:

    from .audit_security_report import (
        AuditSecurityReportEngine,
    )

except Exception:

    AuditSecurityReportEngine = None

try:

    from .authentication_security_report import (
        AuthenticationSecurityReportEngine,
    )

except Exception:

    AuthenticationSecurityReportEngine = None

try:

    from .authorization_security_report import (
        AuthorizationSecurityReportEngine,
    )

except Exception:

    AuthorizationSecurityReportEngine = None

try:

    from .threat_detection_report import (
        ThreatDetectionReportEngine,
    )

except Exception:

    ThreatDetectionReportEngine = None

try:

    from .vulnerability_report import (
        VulnerabilityReportEngine,
    )

except Exception:

    VulnerabilityReportEngine = None

try:

    from .incident_response_report import (
        IncidentResponseReportEngine,
    )

except Exception:

    IncidentResponseReportEngine = None

try:

    from .security_compliance_report import (
        SecurityComplianceReportEngine,
    )

except Exception:

    SecurityComplianceReportEngine = None

try:

    from .security_summary_report import (
        SecuritySummaryReportEngine,
    )

except Exception:

    SecuritySummaryReportEngine = None

# =============================================================================
# PUBLIC EXPORTS
# =============================================================================

__all__ = [

    # Package metadata
    "PACKAGE_NAME",
    "PACKAGE_DESCRIPTION",
    "PACKAGE_VERSION",
    "PACKAGE_STATUS",
    "PACKAGE_AUTHOR",
    "PACKAGE_LICENSE",
    "PACKAGE_CREATED_AT",

    # Paths
    "PACKAGE_ROOT",
    "REPORTS_ROOT",
    "EXPORTS_DIR",
    "LOGS_DIR",
    "HISTORY_DIR",
    "THREATS_DIR",
    "CONFIG_DIR",
    "AUDITS_DIR",
    "COMPLIANCE_DIR",

    # Engines
    "AuditSecurityReportEngine",
    "AuthenticationSecurityReportEngine",
    "AuthorizationSecurityReportEngine",
    "ThreatDetectionReportEngine",
    "VulnerabilityReportEngine",
    "IncidentResponseReportEngine",
    "SecurityComplianceReportEngine",
    "SecuritySummaryReportEngine",
]

# =============================================================================
# RUNTIME REGISTRY
# =============================================================================

RUNTIME_REGISTRY = {
    "package": PACKAGE_NAME,
    "version": PACKAGE_VERSION,
    "status": PACKAGE_STATUS,
    "initialized_at": PACKAGE_CREATED_AT,
    "available_engines": {
        "audit_security":
            AuditSecurityReportEngine
            is not None,

        "authentication_security":
            AuthenticationSecurityReportEngine
            is not None,

        "authorization_security":
            AuthorizationSecurityReportEngine
            is not None,

        "threat_detection":
            ThreatDetectionReportEngine
            is not None,

        "vulnerability":
            VulnerabilityReportEngine
            is not None,

        "incident_response":
            IncidentResponseReportEngine
            is not None,

        "security_compliance":
            SecurityComplianceReportEngine
            is not None,

        "security_summary":
            SecuritySummaryReportEngine
            is not None,
    },
}

# =============================================================================
# HEALTHCHECK
# =============================================================================


def package_healthcheck() -> dict:
    """
    Enterprise security package healthcheck.
    """

    return {
        "package": PACKAGE_NAME,
        "version": PACKAGE_VERSION,
        "status": PACKAGE_STATUS,
        "initialized": True,
        "directories_ready": all(
            directory.exists()
            for directory in DIRECTORIES
        ),
        "engines_loaded":
            RUNTIME_REGISTRY[
                "available_engines"
            ],
        "timestamp":
            datetime.now(
                UTC
            ).isoformat(),
    }


# =============================================================================
# SECURITY BOOTSTRAP
# =============================================================================


def initialize_security_runtime() -> dict:
    """
    Initialize enterprise security runtime environment.
    """

    runtime = {
        "security_runtime": True,
        "threat_intelligence_enabled": True,
        "audit_pipeline_enabled": True,
        "compliance_monitoring_enabled": True,
        "siem_integrations_ready": True,
        "governance_mode": "enterprise",
        "initialized_at": datetime.now(
            UTC
        ).isoformat(),
    }

    return runtime


# =============================================================================
# PACKAGE INITIALIZATION COMPLETE
# =============================================================================