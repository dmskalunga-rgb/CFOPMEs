"""
data/quality/__init__.py

Enterprise Data Quality Package.
"""

from __future__ import annotations

from datetime import datetime

__version__ = "1.0.0"
__author__ = "Digital Meta Enterprise Architecture"
__license__ = "Enterprise"


try:
    from .quality_engine import QualityEngine, create_default_quality_engine
except Exception:  # pragma: no cover
    QualityEngine = None
    create_default_quality_engine = None

try:
    from .quality_rules import QualityRule, RuleSeverity, RuleType
except Exception:  # pragma: no cover
    QualityRule = None
    RuleSeverity = None
    RuleType = None

try:
    from .quality_validator import QualityValidator, ValidationResult
except Exception:  # pragma: no cover
    QualityValidator = None
    ValidationResult = None

try:
    from .quality_metrics import QualityMetricsEngine, QualityMetric
except Exception:  # pragma: no cover
    QualityMetricsEngine = None
    QualityMetric = None

try:
    from .quality_audit import QualityAuditEngine, QualityAuditEvent
except Exception:  # pragma: no cover
    QualityAuditEngine = None
    QualityAuditEvent = None

try:
    from .data_profiler import DataProfiler, ProfileResult
except Exception:  # pragma: no cover
    DataProfiler = None
    ProfileResult = None

try:
    from .anomaly_detector import QualityAnomalyDetector, QualityAnomaly
except Exception:  # pragma: no cover
    QualityAnomalyDetector = None
    QualityAnomaly = None

try:
    from .schema_validator import SchemaValidator, SchemaValidationResult
except Exception:  # pragma: no cover
    SchemaValidator = None
    SchemaValidationResult = None

try:
    from .freshness_validator import FreshnessValidator, FreshnessResult
except Exception:  # pragma: no cover
    FreshnessValidator = None
    FreshnessResult = None

try:
    from .completeness_validator import CompletenessValidator, CompletenessResult
except Exception:  # pragma: no cover
    CompletenessValidator = None
    CompletenessResult = None

try:
    from .uniqueness_validator import UniquenessValidator, UniquenessResult
except Exception:  # pragma: no cover
    UniquenessValidator = None
    UniquenessResult = None

try:
    from .consistency_validator import ConsistencyValidator, ConsistencyResult
except Exception:  # pragma: no cover
    ConsistencyValidator = None
    ConsistencyResult = None

try:
    from .exceptions import (
        DataQualityError,
        QualityValidationError,
        QualityRuleError,
        QualityExecutionError,
        QualityConfigurationError,
    )
except Exception:  # pragma: no cover
    DataQualityError = Exception
    QualityValidationError = Exception
    QualityRuleError = Exception
    QualityExecutionError = Exception
    QualityConfigurationError = Exception


__all__ = [
    "__version__",
    "__author__",
    "__license__",

    "QualityEngine",
    "create_default_quality_engine",

    "QualityRule",
    "RuleSeverity",
    "RuleType",

    "QualityValidator",
    "ValidationResult",

    "QualityMetricsEngine",
    "QualityMetric",

    "QualityAuditEngine",
    "QualityAuditEvent",

    "DataProfiler",
    "ProfileResult",

    "QualityAnomalyDetector",
    "QualityAnomaly",

    "SchemaValidator",
    "SchemaValidationResult",

    "FreshnessValidator",
    "FreshnessResult",

    "CompletenessValidator",
    "CompletenessResult",

    "UniquenessValidator",
    "UniquenessResult",

    "ConsistencyValidator",
    "ConsistencyResult",

    "DataQualityError",
    "QualityValidationError",
    "QualityRuleError",
    "QualityExecutionError",
    "QualityConfigurationError",

    "PACKAGE_INFO",
    "quality_package_healthcheck",
    "bootstrap_quality_platform",
]


PACKAGE_INFO = {
    "name": "data.quality",
    "version": __version__,
    "architecture": "enterprise",
    "runtime": "data_quality_platform",
    "supports": [
        "data_quality_validation",
        "schema_validation",
        "freshness_validation",
        "completeness_validation",
        "uniqueness_validation",
        "consistency_validation",
        "data_profiling",
        "quality_metrics",
        "quality_audit",
        "anomaly_detection",
        "multi_tenant_quality",
        "observability",
        "governance_integration",
    ],
}


def quality_package_healthcheck() -> dict:
    return {
        "package": PACKAGE_INFO["name"],
        "version": PACKAGE_INFO["version"],
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "components": {
            "quality_engine": QualityEngine is not None,
            "quality_rules": QualityRule is not None,
            "quality_validator": QualityValidator is not None,
            "quality_metrics": QualityMetricsEngine is not None,
            "quality_audit": QualityAuditEngine is not None,
            "data_profiler": DataProfiler is not None,
            "anomaly_detector": QualityAnomalyDetector is not None,
            "schema_validator": SchemaValidator is not None,
            "freshness_validator": FreshnessValidator is not None,
            "completeness_validator": CompletenessValidator is not None,
            "uniqueness_validator": UniquenessValidator is not None,
            "consistency_validator": ConsistencyValidator is not None,
        },
    }


def bootstrap_quality_platform() -> dict:
    return {
        "quality_engine": (
            create_default_quality_engine()
            if create_default_quality_engine
            else None
        ),
        "health": quality_package_healthcheck(),
        "package_info": PACKAGE_INFO,
    }