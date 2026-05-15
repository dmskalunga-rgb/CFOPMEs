"""
data/validation/__init__.py

Enterprise-grade validation package public API.

This package centralizes reusable validation primitives for data platforms,
including schema validation, data quality checks, business-rule validation,
contract validation, anomaly validation and pipeline guardrails.

The package is designed to be imported by ingestion, transformation, analytics,
AI and governance layers without forcing heavy dependencies at import time.

Recommended package layout:

    data/validation/
        __init__.py
        base.py
        schema_validator.py
        data_quality.py
        rule_engine.py
        contract_validator.py
        anomaly_validator.py
        validation_report.py
        validation_metrics.py
        validation_audit.py
        validators.py

Python:
    3.10+
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from importlib import import_module
from typing import Any, Dict, Mapping, Optional, Sequence


__title__ = "data.validation"
__description__ = "Enterprise validation primitives for data and AI platforms."
__version__ = "1.0.0"
__author__ = "Digital Meta"
__license__ = "Proprietary"


class ValidationSeverity(str, Enum):
    """Severity level for validation findings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ValidationStatus(str, Enum):
    """Normalized validation status."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class ValidationScope(str, Enum):
    """Scope where validation is applied."""

    RECORD = "record"
    BATCH = "batch"
    DATASET = "dataset"
    SCHEMA = "schema"
    CONTRACT = "contract"
    PIPELINE = "pipeline"
    MODEL = "model"
    SYSTEM = "system"


class ValidationMode(str, Enum):
    """Execution mode for validators."""

    STRICT = "strict"
    LENIENT = "lenient"
    AUDIT_ONLY = "audit_only"
    FAIL_FAST = "fail_fast"


@dataclass(frozen=True)
class ValidationIssue:
    """Package-level lightweight validation issue model.

    Deeper modules may expose richer issue classes, but this minimal model is
    intentionally safe to import from package initialization.
    """

    code: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    scope: ValidationScope = ValidationScope.RECORD
    field: Optional[str] = None
    value: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationSummary:
    """Lightweight validation summary for package-level interoperability."""

    status: ValidationStatus
    total_checks: int = 0
    passed_checks: int = 0
    warning_checks: int = 0
    failed_checks: int = 0
    skipped_checks: int = 0
    issues: Sequence[ValidationIssue] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return True when validation can safely continue."""

        return self.status in {ValidationStatus.PASSED, ValidationStatus.WARNING}

    @property
    def has_errors(self) -> bool:
        """Return True when at least one error or critical issue exists."""

        return any(issue.severity in {ValidationSeverity.ERROR, ValidationSeverity.CRITICAL} for issue in self.issues)


class ValidationPackageError(Exception):
    """Base exception for the validation package."""


class ValidationConfigurationError(ValidationPackageError):
    """Raised when validation configuration is invalid."""


class ValidationExecutionError(ValidationPackageError):
    """Raised when validation execution fails unexpectedly."""


class ValidationContractError(ValidationPackageError):
    """Raised when a data contract is invalid or violated."""


class ValidationSchemaError(ValidationPackageError):
    """Raised when schema validation fails."""


class DataQualityError(ValidationPackageError):
    """Raised when data quality checks fail."""


_LAZY_EXPORTS: Dict[str, str] = {
    # base.py
    "BaseValidator": "data.validation.base",
    "ValidatorContext": "data.validation.base",
    "ValidatorConfig": "data.validation.base",
    "ValidationResult": "data.validation.base",
    "ValidationRule": "data.validation.base",
    # schema_validator.py
    "SchemaValidator": "data.validation.schema_validator",
    "SchemaField": "data.validation.schema_validator",
    "SchemaDefinition": "data.validation.schema_validator",
    # data_quality.py
    "DataQualityValidator": "data.validation.data_quality",
    "QualityCheck": "data.validation.data_quality",
    "QualityProfile": "data.validation.data_quality",
    # rule_engine.py
    "ValidationRuleEngine": "data.validation.rule_engine",
    "RuleExpression": "data.validation.rule_engine",
    "RuleSet": "data.validation.rule_engine",
    # contract_validator.py
    "DataContractValidator": "data.validation.contract_validator",
    "DataContract": "data.validation.contract_validator",
    "ContractVersion": "data.validation.contract_validator",
    # anomaly_validator.py
    "AnomalyValidator": "data.validation.anomaly_validator",
    "AnomalyCheck": "data.validation.anomaly_validator",
    # validation_report.py
    "ValidationReport": "data.validation.validation_report",
    "ValidationReportBuilder": "data.validation.validation_report",
    # validation_metrics.py
    "ValidationMetricsCollector": "data.validation.validation_metrics",
    "ValidationMetric": "data.validation.validation_metrics",
    # validation_audit.py
    "ValidationAuditLogger": "data.validation.validation_audit",
    "ValidationAuditEvent": "data.validation.validation_audit",
}


__all__ = [
    "__title__",
    "__description__",
    "__version__",
    "__author__",
    "__license__",
    "ValidationSeverity",
    "ValidationStatus",
    "ValidationScope",
    "ValidationMode",
    "ValidationIssue",
    "ValidationSummary",
    "ValidationPackageError",
    "ValidationConfigurationError",
    "ValidationExecutionError",
    "ValidationContractError",
    "ValidationSchemaError",
    "DataQualityError",
    "get_version",
    "get_public_api",
    "is_validation_successful",
    "build_summary",
    *_LAZY_EXPORTS.keys(),
]


def __getattr__(name: str) -> Any:
    """Lazy-load optional validation modules on first access.

    This keeps package import fast and avoids importing optional dependencies
    unless the specific validator is used.
    """

    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'data.validation' has no attribute {name!r}")

    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> Sequence[str]:
    """Return public symbols for developer tooling and autocomplete."""

    return sorted(__all__)


def get_version() -> str:
    """Return package version."""

    return __version__


def get_public_api() -> Mapping[str, str]:
    """Return lazy public API symbol-to-module mapping."""

    return dict(_LAZY_EXPORTS)


def is_validation_successful(summary: ValidationSummary) -> bool:
    """Return whether a validation summary represents a successful outcome."""

    return summary.ok and not summary.has_errors


def build_summary(
    *,
    issues: Sequence[ValidationIssue] = (),
    total_checks: int = 0,
    passed_checks: int = 0,
    skipped_checks: int = 0,
    metadata: Optional[Mapping[str, Any]] = None,
) -> ValidationSummary:
    """Build a normalized ValidationSummary from lightweight issues.

    Args:
        issues: Validation issues collected by a validator.
        total_checks: Number of checks executed.
        passed_checks: Number of checks that passed.
        skipped_checks: Number of checks skipped.
        metadata: Optional additional metadata.

    Returns:
        ValidationSummary with derived status counters.
    """

    issues_tuple = tuple(issues)
    warning_checks = sum(1 for issue in issues_tuple if issue.severity == ValidationSeverity.WARNING)
    failed_checks = sum(1 for issue in issues_tuple if issue.severity in {ValidationSeverity.ERROR, ValidationSeverity.CRITICAL})

    if any(issue.severity == ValidationSeverity.CRITICAL for issue in issues_tuple):
        status = ValidationStatus.FAILED
    elif failed_checks:
        status = ValidationStatus.FAILED
    elif warning_checks:
        status = ValidationStatus.WARNING
    else:
        status = ValidationStatus.PASSED

    effective_total = total_checks or (passed_checks + warning_checks + failed_checks + skipped_checks)

    return ValidationSummary(
        status=status,
        total_checks=effective_total,
        passed_checks=passed_checks,
        warning_checks=warning_checks,
        failed_checks=failed_checks,
        skipped_checks=skipped_checks,
        issues=issues_tuple,
        metadata=metadata or {},
    )
