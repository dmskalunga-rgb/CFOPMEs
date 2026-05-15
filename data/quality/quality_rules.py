"""
data/quality/quality_rules.py

Enterprise-grade Data Quality Rules module.

This module provides a centralized rule model, registry, catalog, builder,
serialization layer, and validation DSL for enterprise data quality systems.

Main capabilities:
- Unified rule model for multiple quality dimensions
- Versioned rule definitions and lifecycle status
- Threshold, severity, ownership, domain and SLA metadata
- Conditional rule expressions and row predicates
- Rule registry/catalog with query and grouping utilities
- JSON/YAML-like dict serialization support
- Factory helpers for common rules
- Rule execution context model
- Compatibility adapters for quality checkers
- Governance-ready metadata and audit fields

Designed for enterprise lakehouse quality gates, ETL/ELT validation, data
contracts, observability dashboards, governance workflows, and compliance-ready
quality rule management.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import operator
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union


# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# =============================================================================
# Exceptions
# =============================================================================


class QualityRuleError(Exception):
    """Base exception for quality rule failures."""


class QualityRuleConfigurationError(QualityRuleError):
    """Raised when a rule definition is invalid."""


class QualityRuleExecutionError(QualityRuleError):
    """Raised when a rule expression cannot be evaluated safely."""


class QualityRuleRegistryError(QualityRuleError):
    """Raised when rule registry operations fail."""


# =============================================================================
# Enums
# =============================================================================


class QualityDimension(str, Enum):
    """Standard data quality dimensions."""

    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    FRESHNESS = "freshness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    INTEGRITY = "integrity"
    TIMELINESS = "timeliness"
    NULL_ANALYSIS = "null_analysis"
    PROFILING = "profiling"
    GOVERNANCE = "governance"
    SECURITY = "security"
    CUSTOM = "custom"


class RuleType(str, Enum):
    """Generic rule types used across quality dimensions."""

    NOT_NULL = "not_null"
    REQUIRED_COLUMNS = "required_columns"
    DOMAIN = "domain"
    REGEX = "regex"
    RANGE = "range"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    PERCENT_TOLERANCE = "percent_tolerance"
    EXACT_MATCH = "exact_match"
    UNIQUE = "unique"
    DUPLICATE_RATE = "duplicate_rate"
    CROSS_FIELD = "cross_field"
    EXPRESSION = "expression"
    CONDITIONAL = "conditional"
    REFERENCE_LOOKUP = "reference_lookup"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    AGGREGATE_RECONCILIATION = "aggregate_reconciliation"
    ROW_COMPLETENESS = "row_completeness"
    TIME_WINDOW_COVERAGE = "time_window_coverage"
    MAX_DATA_AGE = "max_data_age"
    INGESTION_DELAY = "ingestion_delay"
    PARTITION_FRESHNESS = "partition_freshness"
    SCHEMA_MATCH = "schema_match"
    TYPE_CHECK = "type_check"
    CUSTOM = "custom"


class RuleStatus(str, Enum):
    """Lifecycle status for a quality rule."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class RuleSeverity(str, Enum):
    """Rule severity level."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RuleAction(str, Enum):
    """Recommended action when a rule fails."""

    OBSERVE = "observe"
    ALERT = "alert"
    QUARANTINE = "quarantine"
    BLOCK_PIPELINE = "block_pipeline"
    AUTO_REMEDIATE = "auto_remediate"
    CREATE_TICKET = "create_ticket"


class ComparisonOperator(str, Enum):
    """Comparison operators for rule expressions."""

    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    MATCHES = "matches"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class LogicalOperator(str, Enum):
    """Logical operators for composite conditions."""

    AND = "and"
    OR = "or"
    NOT = "not"


class NullHandling(str, Enum):
    """How null values should be handled during rule evaluation."""

    IGNORE = "ignore"
    FAIL = "fail"
    PASS = "pass"
    COMPARE = "compare"


class TimeUnit(str, Enum):
    """Time units for SLA/freshness rules."""

    SECONDS = "seconds"
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"


# =============================================================================
# Protocols
# =============================================================================


class RulePredicate(Protocol):
    """Callable row predicate protocol."""

    def __call__(self, row: Mapping[str, Any]) -> bool:
        ...


class RuleExpressionFunction(Protocol):
    """Callable row expression protocol."""

    def __call__(self, row: Mapping[str, Any]) -> Any:
        ...


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class QualityThreshold:
    """Thresholds used by quality rules."""

    min_score: float = 0.95
    warning_score: float = 0.98
    max_failed_records: Optional[int] = None
    max_failed_rate: Optional[float] = None
    max_error_rate: Optional[float] = None

    def validate(self) -> None:
        for name, value in {
            "min_score": self.min_score,
            "warning_score": self.warning_score,
        }.items():
            if value < 0 or value > 1:
                raise QualityRuleConfigurationError(f"{name} must be between 0 and 1.")
        if self.warning_score < self.min_score:
            raise QualityRuleConfigurationError("warning_score must be greater than or equal to min_score.")
        if self.max_failed_records is not None and self.max_failed_records < 0:
            raise QualityRuleConfigurationError("max_failed_records cannot be negative.")
        for name, value in {
            "max_failed_rate": self.max_failed_rate,
            "max_error_rate": self.max_error_rate,
        }.items():
            if value is not None and (value < 0 or value > 1):
                raise QualityRuleConfigurationError(f"{name} must be between 0 and 1.")

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class QualitySLA:
    """SLA metadata for quality rules."""

    enabled: bool = False
    max_age: Optional[int] = None
    unit: TimeUnit = TimeUnit.HOURS
    owner: Optional[str] = None
    escalation_policy: Optional[str] = None
    description: Optional[str] = None

    def validate(self) -> None:
        if self.enabled and self.max_age is None:
            raise QualityRuleConfigurationError("SLA max_age is required when SLA is enabled.")
        if self.max_age is not None and self.max_age < 0:
            raise QualityRuleConfigurationError("SLA max_age cannot be negative.")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["unit"] = self.unit.value
        return _json_safe(data)


@dataclass(frozen=True)
class RuleOwner:
    """Ownership metadata for a rule."""

    owner_id: Optional[str] = None
    owner_name: Optional[str] = None
    owner_email: Optional[str] = None
    team: Optional[str] = None
    domain: Optional[str] = None
    steward: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class RuleCondition:
    """Declarative condition expression for row-level rule logic."""

    field: Optional[str] = None
    operator: Optional[ComparisonOperator] = None
    value: Optional[Any] = None
    values: Optional[Sequence[Any]] = None
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    regex: Optional[str] = None
    logical_operator: Optional[LogicalOperator] = None
    children: Sequence["RuleCondition"] = field(default_factory=list)
    case_sensitive: bool = True
    trim_strings: bool = True

    def validate(self) -> None:
        if self.logical_operator:
            if not self.children:
                raise QualityRuleConfigurationError("Composite condition requires children.")
            for child in self.children:
                child.validate()
            return

        if not self.field:
            raise QualityRuleConfigurationError("Condition field is required.")
        if not self.operator:
            raise QualityRuleConfigurationError("Condition operator is required.")
        if self.operator == ComparisonOperator.BETWEEN and (self.min_value is None or self.max_value is None):
            raise QualityRuleConfigurationError("BETWEEN condition requires min_value and max_value.")
        if self.operator in {ComparisonOperator.IN, ComparisonOperator.NOT_IN} and self.values is None:
            raise QualityRuleConfigurationError("IN/NOT_IN condition requires values.")
        if self.operator == ComparisonOperator.MATCHES:
            if not self.regex:
                raise QualityRuleConfigurationError("MATCHES condition requires regex.")
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise QualityRuleConfigurationError(f"Invalid condition regex: {exc}") from exc

    def evaluate(self, row: Mapping[str, Any]) -> bool:
        self.validate()
        if self.logical_operator:
            if self.logical_operator == LogicalOperator.AND:
                return all(child.evaluate(row) for child in self.children)
            if self.logical_operator == LogicalOperator.OR:
                return any(child.evaluate(row) for child in self.children)
            if self.logical_operator == LogicalOperator.NOT:
                if len(self.children) != 1:
                    raise QualityRuleExecutionError("NOT condition requires exactly one child.")
                return not self.children[0].evaluate(row)
            raise QualityRuleExecutionError(f"Unsupported logical operator: {self.logical_operator}")

        actual = row.get(self.field or "")
        return _evaluate_comparison(
            actual=actual,
            operator=self.operator or ComparisonOperator.EQ,
            value=self.value,
            values=self.values,
            min_value=self.min_value,
            max_value=self.max_value,
            regex=self.regex,
            case_sensitive=self.case_sensitive,
            trim_strings=self.trim_strings,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "operator": self.operator.value if self.operator else None,
            "value": _json_safe(self.value),
            "values": _json_safe(list(self.values) if self.values is not None else None),
            "min_value": _json_safe(self.min_value),
            "max_value": _json_safe(self.max_value),
            "regex": self.regex,
            "logical_operator": self.logical_operator.value if self.logical_operator else None,
            "children": [child.to_dict() for child in self.children],
            "case_sensitive": self.case_sensitive,
            "trim_strings": self.trim_strings,
        }


@dataclass(frozen=True)
class RuleTarget:
    """Dataset/table/column target metadata."""

    dataset_name: Optional[str] = None
    table_name: Optional[str] = None
    schema_name: Optional[str] = None
    database_name: Optional[str] = None
    columns: Sequence[str] = field(default_factory=list)
    key_columns: Sequence[str] = field(default_factory=list)
    partition_columns: Sequence[str] = field(default_factory=list)
    group_by: Sequence[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class QualityRule:
    """Canonical enterprise data quality rule definition."""

    rule_id: str
    name: str
    dimension: QualityDimension
    rule_type: RuleType
    version: str = "1.0.0"
    status: RuleStatus = RuleStatus.ACTIVE
    severity: RuleSeverity = RuleSeverity.HIGH
    action: RuleAction = RuleAction.ALERT
    description: Optional[str] = None
    target: RuleTarget = field(default_factory=RuleTarget)
    threshold: QualityThreshold = field(default_factory=QualityThreshold)
    sla: QualitySLA = field(default_factory=QualitySLA)
    owner: RuleOwner = field(default_factory=RuleOwner)
    condition: Optional[RuleCondition] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    tags: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    def validate(self) -> None:
        if not self.rule_id.strip():
            raise QualityRuleConfigurationError("rule_id is required.")
        if not self.name.strip():
            raise QualityRuleConfigurationError("Rule name is required.")
        self.threshold.validate()
        self.sla.validate()
        if self.condition:
            self.condition.validate()
        self._validate_type_specific_parameters()

    def _validate_type_specific_parameters(self) -> None:
        params = self.parameters
        columns = list(self.target.columns)

        if self.rule_type == RuleType.NOT_NULL and not columns:
            raise QualityRuleConfigurationError("NOT_NULL rule requires target.columns.")
        if self.rule_type == RuleType.REQUIRED_COLUMNS and not params.get("required_columns"):
            raise QualityRuleConfigurationError("REQUIRED_COLUMNS rule requires parameters.required_columns.")
        if self.rule_type == RuleType.DOMAIN and not params.get("allowed_values"):
            raise QualityRuleConfigurationError("DOMAIN rule requires parameters.allowed_values.")
        if self.rule_type == RuleType.REGEX and not params.get("regex"):
            raise QualityRuleConfigurationError("REGEX rule requires parameters.regex.")
        if self.rule_type == RuleType.RANGE and not ("min_value" in params or "max_value" in params):
            raise QualityRuleConfigurationError("RANGE rule requires min_value and/or max_value.")
        if self.rule_type == RuleType.UNIQUE and not (columns or self.target.key_columns):
            raise QualityRuleConfigurationError("UNIQUE rule requires target.columns or target.key_columns.")
        if self.rule_type == RuleType.REFERENCE_LOOKUP:
            required = ["reference_dataset", "reference_column"]
            missing = [key for key in required if not params.get(key)]
            if missing:
                raise QualityRuleConfigurationError(f"REFERENCE_LOOKUP missing parameters: {missing}")
        if self.rule_type == RuleType.MAX_DATA_AGE:
            if not columns and not params.get("timestamp_column"):
                raise QualityRuleConfigurationError("MAX_DATA_AGE requires target.columns or timestamp_column.")
            if not params.get("max_age"):
                raise QualityRuleConfigurationError("MAX_DATA_AGE requires parameters.max_age.")

    @property
    def enabled(self) -> bool:
        return self.status == RuleStatus.ACTIVE

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict(include_fingerprint=False)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def clone_with_version(
        self,
        version: str,
        *,
        updated_by: Optional[str] = None,
        changes: Optional[Mapping[str, Any]] = None,
    ) -> "QualityRule":
        payload = self.to_dict(include_fingerprint=False)
        payload["version"] = version
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["updated_by"] = updated_by
        if changes:
            _deep_update(payload, dict(changes))
        return quality_rule_from_dict(payload)

    def to_dict(self, *, include_fingerprint: bool = True) -> Dict[str, Any]:
        data = {
            "rule_id": self.rule_id,
            "name": self.name,
            "dimension": self.dimension.value,
            "rule_type": self.rule_type.value,
            "version": self.version,
            "status": self.status.value,
            "severity": self.severity.value,
            "action": self.action.value,
            "description": self.description,
            "target": self.target.to_dict(),
            "threshold": self.threshold.to_dict(),
            "sla": self.sla.to_dict(),
            "owner": self.owner.to_dict(),
            "condition": self.condition.to_dict() if self.condition else None,
            "parameters": _json_safe(self.parameters),
            "tags": dict(self.tags),
            "metadata": _json_safe(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }
        if include_fingerprint:
            data["fingerprint"] = self.fingerprint
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class RuleEvaluationContext:
    """Runtime context used when evaluating or adapting a rule."""

    dataset_name: Optional[str] = None
    run_id: Optional[str] = None
    execution_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    variables: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class RuleCatalogSummary:
    """Aggregated rule catalog summary."""

    total_rules: int
    active_rules: int
    disabled_rules: int
    deprecated_rules: int
    archived_rules: int
    rules_by_dimension: Dict[str, int]
    rules_by_type: Dict[str, int]
    rules_by_severity: Dict[str, int]
    rules_by_action: Dict[str, int]
    datasets: List[str]
    owners: List[str]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# =============================================================================
# Helper Functions
# =============================================================================


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


def _deep_update(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    return False


def _normalize(value: Any, *, trim: bool, case_sensitive: bool) -> Any:
    if isinstance(value, str):
        normalized = value.strip() if trim else value
        return normalized if case_sensitive else normalized.casefold()
    return value


def _to_decimal(value: Any) -> Optional[Decimal]:
    if _is_null(value):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _evaluate_comparison(
    *,
    actual: Any,
    operator: ComparisonOperator,
    value: Any = None,
    values: Optional[Sequence[Any]] = None,
    min_value: Any = None,
    max_value: Any = None,
    regex: Optional[str] = None,
    case_sensitive: bool = True,
    trim_strings: bool = True,
) -> bool:
    if operator == ComparisonOperator.IS_NULL:
        return _is_null(actual)
    if operator == ComparisonOperator.IS_NOT_NULL:
        return not _is_null(actual)

    if _is_null(actual):
        return False

    actual_norm = _normalize(actual, trim=trim_strings, case_sensitive=case_sensitive)
    value_norm = _normalize(value, trim=trim_strings, case_sensitive=case_sensitive)

    numeric_ops = {
        ComparisonOperator.EQ,
        ComparisonOperator.NE,
        ComparisonOperator.GT,
        ComparisonOperator.GTE,
        ComparisonOperator.LT,
        ComparisonOperator.LTE,
    }
    if operator in numeric_ops:
        left_dec = _to_decimal(actual)
        right_dec = _to_decimal(value)
        if left_dec is not None and right_dec is not None:
            left: Any = left_dec
            right: Any = right_dec
        else:
            left = actual_norm
            right = value_norm

        op_map = {
            ComparisonOperator.EQ: operator_module.eq,
            ComparisonOperator.NE: operator_module.ne,
            ComparisonOperator.GT: operator_module.gt,
            ComparisonOperator.GTE: operator_module.ge,
            ComparisonOperator.LT: operator_module.lt,
            ComparisonOperator.LTE: operator_module.le,
        }
        return bool(op_map[operator](left, right))

    if operator == ComparisonOperator.IN:
        allowed = [_normalize(v, trim=trim_strings, case_sensitive=case_sensitive) for v in (values or [])]
        return actual_norm in allowed
    if operator == ComparisonOperator.NOT_IN:
        denied = [_normalize(v, trim=trim_strings, case_sensitive=case_sensitive) for v in (values or [])]
        return actual_norm not in denied
    if operator == ComparisonOperator.BETWEEN:
        actual_dec = _to_decimal(actual)
        min_dec = _to_decimal(min_value)
        max_dec = _to_decimal(max_value)
        if actual_dec is not None and min_dec is not None and max_dec is not None:
            return min_dec <= actual_dec <= max_dec
        return min_value <= actual <= max_value
    if operator == ComparisonOperator.MATCHES:
        flags = 0 if case_sensitive else re.IGNORECASE
        return bool(re.fullmatch(regex or "", str(actual).strip() if trim_strings else str(actual), flags=flags))
    if operator == ComparisonOperator.STARTS_WITH:
        return str(actual_norm).startswith(str(value_norm))
    if operator == ComparisonOperator.ENDS_WITH:
        return str(actual_norm).endswith(str(value_norm))
    if operator == ComparisonOperator.CONTAINS:
        return str(value_norm) in str(actual_norm)

    raise QualityRuleExecutionError(f"Unsupported comparison operator: {operator}")


operator_module = operator


def _enum_value(enum_cls: Any, value: Any, default: Any = None) -> Any:
    if isinstance(value, enum_cls):
        return value
    if value is None:
        if default is not None:
            return default
        raise QualityRuleConfigurationError(f"Missing enum value for {enum_cls}")
    return enum_cls(str(value))


# =============================================================================
# Serialization
# =============================================================================


def condition_from_dict(payload: Optional[Mapping[str, Any]]) -> Optional[RuleCondition]:
    if payload is None:
        return None
    children = [condition_from_dict(child) for child in payload.get("children") or []]
    return RuleCondition(
        field=payload.get("field"),
        operator=_enum_value(ComparisonOperator, payload.get("operator"), None) if payload.get("operator") else None,
        value=payload.get("value"),
        values=payload.get("values"),
        min_value=payload.get("min_value"),
        max_value=payload.get("max_value"),
        regex=payload.get("regex"),
        logical_operator=_enum_value(LogicalOperator, payload.get("logical_operator"), None) if payload.get("logical_operator") else None,
        children=[child for child in children if child is not None],
        case_sensitive=bool(payload.get("case_sensitive", True)),
        trim_strings=bool(payload.get("trim_strings", True)),
    )


def quality_rule_from_dict(payload: Mapping[str, Any]) -> QualityRule:
    target_payload = dict(payload.get("target") or {})
    threshold_payload = dict(payload.get("threshold") or {})
    sla_payload = dict(payload.get("sla") or {})
    owner_payload = dict(payload.get("owner") or {})

    return QualityRule(
        rule_id=str(payload.get("rule_id") or str(uuid.uuid4())),
        name=str(payload.get("name") or "quality_rule"),
        dimension=_enum_value(QualityDimension, payload.get("dimension"), QualityDimension.CUSTOM),
        rule_type=_enum_value(RuleType, payload.get("rule_type"), RuleType.CUSTOM),
        version=str(payload.get("version") or "1.0.0"),
        status=_enum_value(RuleStatus, payload.get("status"), RuleStatus.ACTIVE),
        severity=_enum_value(RuleSeverity, payload.get("severity"), RuleSeverity.HIGH),
        action=_enum_value(RuleAction, payload.get("action"), RuleAction.ALERT),
        description=payload.get("description"),
        target=RuleTarget(
            dataset_name=target_payload.get("dataset_name"),
            table_name=target_payload.get("table_name"),
            schema_name=target_payload.get("schema_name"),
            database_name=target_payload.get("database_name"),
            columns=list(target_payload.get("columns") or []),
            key_columns=list(target_payload.get("key_columns") or []),
            partition_columns=list(target_payload.get("partition_columns") or []),
            group_by=list(target_payload.get("group_by") or []),
        ),
        threshold=QualityThreshold(
            min_score=float(threshold_payload.get("min_score", 0.95)),
            warning_score=float(threshold_payload.get("warning_score", 0.98)),
            max_failed_records=threshold_payload.get("max_failed_records"),
            max_failed_rate=threshold_payload.get("max_failed_rate"),
            max_error_rate=threshold_payload.get("max_error_rate"),
        ),
        sla=QualitySLA(
            enabled=bool(sla_payload.get("enabled", False)),
            max_age=sla_payload.get("max_age"),
            unit=_enum_value(TimeUnit, sla_payload.get("unit"), TimeUnit.HOURS),
            owner=sla_payload.get("owner"),
            escalation_policy=sla_payload.get("escalation_policy"),
            description=sla_payload.get("description"),
        ),
        owner=RuleOwner(
            owner_id=owner_payload.get("owner_id"),
            owner_name=owner_payload.get("owner_name"),
            owner_email=owner_payload.get("owner_email"),
            team=owner_payload.get("team"),
            domain=owner_payload.get("domain"),
            steward=owner_payload.get("steward"),
        ),
        condition=condition_from_dict(payload.get("condition")),
        parameters=dict(payload.get("parameters") or {}),
        tags={str(k): str(v) for k, v in (payload.get("tags") or {}).items()},
        metadata=dict(payload.get("metadata") or {}),
        created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        updated_at=str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        created_by=payload.get("created_by"),
        updated_by=payload.get("updated_by"),
    )


# =============================================================================
# Registry / Catalog
# =============================================================================


@dataclass(frozen=True)
class RuleQuery:
    """Rule query filters."""

    dataset_name: Optional[str] = None
    dimensions: Optional[Sequence[QualityDimension]] = None
    rule_types: Optional[Sequence[RuleType]] = None
    statuses: Optional[Sequence[RuleStatus]] = None
    severities: Optional[Sequence[RuleSeverity]] = None
    owner_team: Optional[str] = None
    domain: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    text: Optional[str] = None
    enabled_only: bool = False


class QualityRuleRegistry:
    """In-memory enterprise rule catalog/registry."""

    def __init__(self, rules: Optional[Sequence[QualityRule | Mapping[str, Any]]] = None) -> None:
        self._rules: Dict[str, QualityRule] = {}
        self._versions: Dict[str, List[QualityRule]] = {}
        for rule in rules or []:
            self.add(rule)

    def add(self, rule: QualityRule | Mapping[str, Any], *, replace_existing: bool = False) -> QualityRule:
        parsed = quality_rule_from_dict(rule) if isinstance(rule, Mapping) else rule
        parsed.validate()
        if parsed.rule_id in self._rules and not replace_existing:
            raise QualityRuleRegistryError(f"Rule already exists: {parsed.rule_id}")
        self._rules[parsed.rule_id] = parsed
        self._versions.setdefault(parsed.rule_id, []).append(parsed)
        return parsed

    def upsert(self, rule: QualityRule | Mapping[str, Any]) -> QualityRule:
        return self.add(rule, replace_existing=True)

    def get(self, rule_id: str) -> QualityRule:
        if rule_id not in self._rules:
            raise QualityRuleRegistryError(f"Rule not found: {rule_id}")
        return self._rules[rule_id]

    def remove(self, rule_id: str) -> QualityRule:
        if rule_id not in self._rules:
            raise QualityRuleRegistryError(f"Rule not found: {rule_id}")
        return self._rules.pop(rule_id)

    def disable(self, rule_id: str, *, updated_by: Optional[str] = None) -> QualityRule:
        rule = self.get(rule_id)
        updated = replace(
            rule,
            status=RuleStatus.DISABLED,
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by,
        )
        return self.upsert(updated)

    def activate(self, rule_id: str, *, updated_by: Optional[str] = None) -> QualityRule:
        rule = self.get(rule_id)
        updated = replace(
            rule,
            status=RuleStatus.ACTIVE,
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by,
        )
        return self.upsert(updated)

    def list(self) -> List[QualityRule]:
        return sorted(self._rules.values(), key=lambda r: (r.target.dataset_name or "", r.dimension.value, r.name))

    def versions(self, rule_id: str) -> List[QualityRule]:
        return list(self._versions.get(rule_id, []))

    def query(self, query: Optional[RuleQuery] = None) -> List[QualityRule]:
        query = query or RuleQuery()
        dimensions = {d.value for d in query.dimensions} if query.dimensions else None
        rule_types = {t.value for t in query.rule_types} if query.rule_types else None
        statuses = {s.value for s in query.statuses} if query.statuses else None
        severities = {s.value for s in query.severities} if query.severities else None
        text = query.text.casefold() if query.text else None

        result: List[QualityRule] = []
        for rule in self._rules.values():
            if query.dataset_name and rule.target.dataset_name != query.dataset_name:
                continue
            if dimensions and rule.dimension.value not in dimensions:
                continue
            if rule_types and rule.rule_type.value not in rule_types:
                continue
            if statuses and rule.status.value not in statuses:
                continue
            if severities and rule.severity.value not in severities:
                continue
            if query.enabled_only and not rule.enabled:
                continue
            if query.owner_team and rule.owner.team != query.owner_team:
                continue
            if query.domain and not (rule.owner.domain == query.domain or rule.tags.get("domain") == query.domain):
                continue
            if query.tags and any(rule.tags.get(k) != v for k, v in query.tags.items()):
                continue
            if text:
                haystack = " ".join(
                    [rule.name, rule.description or "", rule.rule_id, rule.dimension.value, rule.rule_type.value]
                ).casefold()
                if text not in haystack:
                    continue
            result.append(rule)
        return sorted(result, key=lambda r: (r.target.dataset_name or "", r.name))

    def by_dataset(self) -> Dict[str, List[QualityRule]]:
        grouped: Dict[str, List[QualityRule]] = {}
        for rule in self.list():
            grouped.setdefault(rule.target.dataset_name or "unknown", []).append(rule)
        return grouped

    def by_dimension(self) -> Dict[str, List[QualityRule]]:
        grouped: Dict[str, List[QualityRule]] = {}
        for rule in self.list():
            grouped.setdefault(rule.dimension.value, []).append(rule)
        return grouped

    def summary(self) -> RuleCatalogSummary:
        rules = self.list()
        return RuleCatalogSummary(
            total_rules=len(rules),
            active_rules=sum(1 for r in rules if r.status == RuleStatus.ACTIVE),
            disabled_rules=sum(1 for r in rules if r.status == RuleStatus.DISABLED),
            deprecated_rules=sum(1 for r in rules if r.status == RuleStatus.DEPRECATED),
            archived_rules=sum(1 for r in rules if r.status == RuleStatus.ARCHIVED),
            rules_by_dimension=_count_by(r.dimension.value for r in rules),
            rules_by_type=_count_by(r.rule_type.value for r in rules),
            rules_by_severity=_count_by(r.severity.value for r in rules),
            rules_by_action=_count_by(r.action.value for r in rules),
            datasets=sorted(set(r.target.dataset_name or "unknown" for r in rules)),
            owners=sorted(set(filter(None, [r.owner.owner_email or r.owner.owner_name or r.owner.team for r in rules]))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"rules": [rule.to_dict() for rule in self.list()], "summary": self.summary().to_dict()}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @staticmethod
    def from_json(payload: str) -> "QualityRuleRegistry":
        data = json.loads(payload)
        return QualityRuleRegistry(data.get("rules") or [])



def _count_by(values: Iterable[str]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


# =============================================================================
# Rule Factories
# =============================================================================


class QualityRuleFactory:
    """Factory helpers for common enterprise data quality rules."""

    @staticmethod
    def not_null(
        name: str,
        dataset_name: str,
        columns: Sequence[str],
        *,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.CRITICAL,
        min_score: float = 1.0,
        owner: Optional[RuleOwner] = None,
        action: RuleAction = RuleAction.BLOCK_PIPELINE,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("not_null", dataset_name, name),
            name=name,
            dimension=QualityDimension.COMPLETENESS,
            rule_type=RuleType.NOT_NULL,
            severity=severity,
            action=action,
            target=RuleTarget(dataset_name=dataset_name, columns=list(columns)),
            threshold=QualityThreshold(min_score=min_score, warning_score=min_score),
            owner=owner or RuleOwner(),
            description=f"Require non-null values for columns: {', '.join(columns)}.",
        )

    @staticmethod
    def domain(
        name: str,
        dataset_name: str,
        column: str,
        allowed_values: Sequence[Any],
        *,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.HIGH,
        min_score: float = 0.99,
        case_sensitive: bool = False,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("domain", dataset_name, name),
            name=name,
            dimension=QualityDimension.VALIDITY,
            rule_type=RuleType.DOMAIN,
            severity=severity,
            action=RuleAction.ALERT,
            target=RuleTarget(dataset_name=dataset_name, columns=[column]),
            threshold=QualityThreshold(min_score=min_score),
            owner=owner or RuleOwner(),
            parameters={"allowed_values": list(allowed_values), "case_sensitive": case_sensitive},
            description=f"Validate that column '{column}' belongs to an accepted domain.",
        )

    @staticmethod
    def regex(
        name: str,
        dataset_name: str,
        column: str,
        pattern: str,
        *,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.MEDIUM,
        min_score: float = 0.98,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("regex", dataset_name, name),
            name=name,
            dimension=QualityDimension.VALIDITY,
            rule_type=RuleType.REGEX,
            severity=severity,
            target=RuleTarget(dataset_name=dataset_name, columns=[column]),
            threshold=QualityThreshold(min_score=min_score),
            owner=owner or RuleOwner(),
            parameters={"regex": pattern},
            description=f"Validate that column '{column}' matches regex pattern.",
        )

    @staticmethod
    def range(
        name: str,
        dataset_name: str,
        column: str,
        *,
        min_value: Optional[Any] = None,
        max_value: Optional[Any] = None,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.HIGH,
        min_score: float = 0.99,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("range", dataset_name, name),
            name=name,
            dimension=QualityDimension.VALIDITY,
            rule_type=RuleType.RANGE,
            severity=severity,
            target=RuleTarget(dataset_name=dataset_name, columns=[column]),
            threshold=QualityThreshold(min_score=min_score),
            owner=owner or RuleOwner(),
            parameters={"min_value": min_value, "max_value": max_value},
            description=f"Validate accepted range for column '{column}'.",
        )

    @staticmethod
    def unique(
        name: str,
        dataset_name: str,
        key_columns: Sequence[str],
        *,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.CRITICAL,
        min_score: float = 1.0,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("unique", dataset_name, name),
            name=name,
            dimension=QualityDimension.UNIQUENESS,
            rule_type=RuleType.UNIQUE,
            severity=severity,
            action=RuleAction.BLOCK_PIPELINE,
            target=RuleTarget(dataset_name=dataset_name, key_columns=list(key_columns)),
            threshold=QualityThreshold(min_score=min_score, warning_score=min_score),
            owner=owner or RuleOwner(),
            description=f"Require uniqueness for key columns: {', '.join(key_columns)}.",
        )

    @staticmethod
    def freshness(
        name: str,
        dataset_name: str,
        timestamp_column: str,
        *,
        max_age: int,
        unit: TimeUnit = TimeUnit.HOURS,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.CRITICAL,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("freshness", dataset_name, name),
            name=name,
            dimension=QualityDimension.FRESHNESS,
            rule_type=RuleType.MAX_DATA_AGE,
            severity=severity,
            action=RuleAction.ALERT,
            target=RuleTarget(dataset_name=dataset_name, columns=[timestamp_column]),
            threshold=QualityThreshold(min_score=1.0, warning_score=1.0),
            sla=QualitySLA(enabled=True, max_age=max_age, unit=unit, owner=owner.owner_email if owner else None),
            owner=owner or RuleOwner(),
            parameters={"timestamp_column": timestamp_column, "max_age": max_age, "unit": unit.value},
            description=f"Validate maximum data age for timestamp column '{timestamp_column}'.",
        )

    @staticmethod
    def reference_lookup(
        name: str,
        dataset_name: str,
        column: str,
        reference_dataset: str,
        reference_column: str,
        key_columns: Sequence[str],
        *,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.CRITICAL,
        min_score: float = 0.995,
        owner: Optional[RuleOwner] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("reference", dataset_name, name),
            name=name,
            dimension=QualityDimension.ACCURACY,
            rule_type=RuleType.REFERENCE_LOOKUP,
            severity=severity,
            target=RuleTarget(dataset_name=dataset_name, columns=[column], key_columns=list(key_columns)),
            threshold=QualityThreshold(min_score=min_score),
            owner=owner or RuleOwner(),
            parameters={"reference_dataset": reference_dataset, "reference_column": reference_column},
            description=f"Compare '{column}' against reference dataset '{reference_dataset}'.",
        )

    @staticmethod
    def custom(
        name: str,
        dataset_name: str,
        dimension: QualityDimension,
        *,
        parameters: Optional[Dict[str, Any]] = None,
        condition: Optional[RuleCondition] = None,
        rule_id: Optional[str] = None,
        severity: RuleSeverity = RuleSeverity.HIGH,
        min_score: float = 0.95,
        owner: Optional[RuleOwner] = None,
        description: Optional[str] = None,
    ) -> QualityRule:
        return QualityRule(
            rule_id=rule_id or _new_rule_id("custom", dataset_name, name),
            name=name,
            dimension=dimension,
            rule_type=RuleType.CUSTOM,
            severity=severity,
            target=RuleTarget(dataset_name=dataset_name),
            threshold=QualityThreshold(min_score=min_score),
            owner=owner or RuleOwner(),
            condition=condition,
            parameters=parameters or {},
            description=description,
        )



def _new_rule_id(prefix: str, dataset_name: str, name: str) -> str:
    raw = f"{prefix}|{dataset_name}|{name}|{uuid.uuid4()}"
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]
    safe_prefix = re.sub(r"[^a-zA-Z0-9_]+", "_", prefix).strip("_").lower() or "rule"
    return f"{safe_prefix}_{digest}"


# =============================================================================
# Rule Adapter Helpers
# =============================================================================


class QualityRuleAdapter:
    """
    Adapter helpers that convert canonical QualityRule objects into generic
    checker-friendly dictionaries.

    These dictionaries can be consumed by custom orchestration code or mapped
    into concrete checker classes such as AccuracyRule, CompletenessRule,
    ConsistencyRule, FreshnessRule, etc.
    """

    @staticmethod
    def to_checker_config(rule: QualityRule) -> Dict[str, Any]:
        rule.validate()
        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "dimension": rule.dimension.value,
            "rule_type": rule.rule_type.value,
            "enabled": rule.enabled,
            "severity": rule.severity.value,
            "action": rule.action.value,
            "dataset_name": rule.target.dataset_name,
            "columns": list(rule.target.columns),
            "key_columns": list(rule.target.key_columns),
            "group_by": list(rule.target.group_by),
            "threshold": rule.threshold.to_dict(),
            "parameters": _json_safe(rule.parameters),
            "condition": rule.condition.to_dict() if rule.condition else None,
            "tags": dict(rule.tags),
            "metadata": {**rule.metadata, "version": rule.version, "fingerprint": rule.fingerprint},
        }

    @staticmethod
    def to_contract_dict(rule: QualityRule) -> Dict[str, Any]:
        rule.validate()
        return {
            "id": rule.rule_id,
            "name": rule.name,
            "version": rule.version,
            "status": rule.status.value,
            "dimension": rule.dimension.value,
            "type": rule.rule_type.value,
            "severity": rule.severity.value,
            "action": rule.action.value,
            "target": rule.target.to_dict(),
            "threshold": rule.threshold.to_dict(),
            "sla": rule.sla.to_dict(),
            "owner": rule.owner.to_dict(),
            "params": _json_safe(rule.parameters),
            "condition": rule.condition.to_dict() if rule.condition else None,
            "fingerprint": rule.fingerprint,
        }


# =============================================================================
# Local Smoke Example
# =============================================================================


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    owner = RuleOwner(
        owner_name="Data Quality Team",
        owner_email="dq@example.com",
        team="data-platform",
        domain="customers",
        steward="customer-data-steward",
    )

    rules = [
        QualityRuleFactory.not_null(
            "customer_id_required",
            "customers",
            ["customer_id"],
            owner=owner,
        ),
        QualityRuleFactory.domain(
            "customer_status_domain",
            "customers",
            "status",
            ["active", "inactive", "blocked"],
            owner=owner,
        ),
        QualityRuleFactory.regex(
            "email_format",
            "customers",
            "email",
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
            owner=owner,
        ),
        QualityRuleFactory.unique(
            "customer_id_unique",
            "customers",
            ["customer_id"],
            owner=owner,
        ),
        QualityRuleFactory.freshness(
            "customers_updated_recently",
            "customers",
            "updated_at",
            max_age=24,
            unit=TimeUnit.HOURS,
            owner=owner,
        ),
    ]

    registry = QualityRuleRegistry(rules)
    print(registry.summary().to_json())
    print(registry.to_json())

    sample_condition = RuleCondition(
        logical_operator=LogicalOperator.AND,
        children=[
            RuleCondition(field="country", operator=ComparisonOperator.EQ, value="BR", case_sensitive=False),
            RuleCondition(field="document", operator=ComparisonOperator.IS_NOT_NULL),
        ],
    )
    print("Condition result:", sample_condition.evaluate({"country": "br", "document": "123"}))
