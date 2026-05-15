"""
===============================================================================
KwanzaControl Enterprise ML Metrics Package
File: reports/ml_metrics/__init__.py

Description:
    Enterprise-grade ML Metrics package initializer responsible for:

    - Centralized ML metrics exports
    - Enterprise observability integration
    - AI/ML governance registration
    - Metrics orchestration exposure
    - Public API normalization
    - Version management
    - Runtime metadata exposure
    - Compliance-ready initialization
    - Distributed analytics compatibility
    - Model evaluation interoperability

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
    "kwanzacontrol.reports.ml_metrics"
)

PACKAGE_DESCRIPTION: Final[str] = (
    "Enterprise ML Metrics Intelligence Package"
)

PACKAGE_VERSION: Final[str] = (
    "1.0.0"
)

PACKAGE_STATUS: Final[str] = (
    "production"
)

PACKAGE_AUTHOR: Final[str] = (
    "KwanzaControl Engineering"
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

MODELS_DIR: Final[Path] = (
    PACKAGE_ROOT / "models"
)

CONFIG_DIR: Final[Path] = (
    PACKAGE_ROOT / "config"
)

# =============================================================================
# DIRECTORY INITIALIZATION
# =============================================================================

DIRECTORIES = [
    EXPORTS_DIR,
    LOGS_DIR,
    HISTORY_DIR,
    MODELS_DIR,
    CONFIG_DIR,
]

for directory in DIRECTORIES:

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

# =============================================================================
# ENTERPRISE EXPORTS
# =============================================================================

try:

    from .classification_metrics import (
        ClassificationMetricsEngine,
    )

except Exception:

    ClassificationMetricsEngine = None

try:

    from .regression_metrics import (
        RegressionMetricsEngine,
    )

except Exception:

    RegressionMetricsEngine = None

try:

    from .drift_detection import (
        DriftDetectionEngine,
    )

except Exception:

    DriftDetectionEngine = None

try:

    from .model_performance_tracker import (
        ModelPerformanceTracker,
    )

except Exception:

    ModelPerformanceTracker = None

try:

    from .ml_metrics_summary import (
        MLMetricsSummaryEngine,
    )

except Exception:

    MLMetricsSummaryEngine = None

try:

    from .metrics_governance import (
        MetricsGovernanceEngine,
    )

except Exception:

    MetricsGovernanceEngine = None

# =============================================================================
# PUBLIC EXPORTS
# =============================================================================

__all__ = [

    # Metadata
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
    "MODELS_DIR",
    "CONFIG_DIR",

    # Engines
    "ClassificationMetricsEngine",
    "RegressionMetricsEngine",
    "DriftDetectionEngine",
    "ModelPerformanceTracker",
    "MLMetricsSummaryEngine",
    "MetricsGovernanceEngine",
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
        "classification_metrics":
            ClassificationMetricsEngine
            is not None,

        "regression_metrics":
            RegressionMetricsEngine
            is not None,

        "drift_detection":
            DriftDetectionEngine
            is not None,

        "model_performance_tracker":
            ModelPerformanceTracker
            is not None,

        "ml_metrics_summary":
            MLMetricsSummaryEngine
            is not None,

        "metrics_governance":
            MetricsGovernanceEngine
            is not None,
    },
}

# =============================================================================
# HEALTH CHECK
# =============================================================================


def package_healthcheck() -> dict:
    """
    Enterprise ML metrics package healthcheck.
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
# PACKAGE INITIALIZATION COMPLETE
# =============================================================================