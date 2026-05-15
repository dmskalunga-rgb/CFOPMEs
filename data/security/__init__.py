"""
data/validation/integrity_validator.py

Enterprise-grade data integrity validation engine.

This module validates structural and relational integrity for records, batches,
datasets and pipeline outputs before persistence, analytics, ML/AI processing or
publication.

Core capabilities:

- Primary key presence and uniqueness
- Composite unique constraints
- Foreign-key and referential integrity checks
- Required field and non-null constraints
- Domain/accepted-values constraints
- Numeric range constraints
- Parent-child relationship validation
- Checksum/hash validation
- Record fingerprinting
- Duplicate detection
- Orphan detection
- Dataset completeness checks
- Custom integrity rules
- Batch validation
- Audit and metrics hooks
- Risk scoring and allow/review/block decisions

Python:
    3.10+
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class IntegrityValidationError(Exception):
    """Base exception for integrity validation."""


class IntegrityConfigurationError(IntegrityValidationError):
    """Raised when integrity validator configuration is invalid."""


class IntegrityInputError(IntegrityValidationError):
    """Raised when integrity input is invalid."""


class IntegrityRuleExecutionError(IntegrityValidationError):
    """Raised when a custom integrity rule fails."""


# =============================================================================
# Enums
# =============================================================================


class IntegrityStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class IntegrityDecision(str, Enum):
    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class IntegritySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IntegrityScope(str, Enum):
    RECORD = "record"
    BATCH = "batch"
    DATASET = "dataset"
    CROSS_DATASET = "cross_dataset"
    PIPELINE = "pipeline"


class IntegrityRuleType(str, Enum):
    REQUIRED_FIELDS = "required_fields"
    PRIMARY_KEY = "primary_key"
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    CHECKSUM = "checksum"
    HASH_SIGNATURE = "hash_signature"
    ACCEPTED_VALUES = "accepted_values"
    RANGE = "range"
    PARENT_CHILD = "parent_child"
    COMPLETENESS = "completeness"
    DUPLICATE_RECORD = "duplicate_record"
    CUSTOM = "custom"


class HashAlgorithm(str, Enum):
    SHA256 = "sha256"
    SHA1 = "sha1"
    MD5 = "md5"


# =============================================================================
# Data Models
# =============================================================================


@dataclass(frozen=True)
class IntegrityValidatorConfig:
    """Integrity validator configuration."""

    fail_fast: bool = False
    audit_enabled: bool = True
    metrics_enabled: bool = True
    include_passed_checks: bool = False
    block_on_critical: bool = True
    review_on_error: bool = True
    max_evidence_chars: int = 2_000
    max_duplicate_examples: int = 50
    default_hash_algorithm: HashAlgorithm = HashAlgorithm.SHA256
    canonical_json_sort_keys: bool = True
    version: str = "1.0.0"

    def validate(self) -> None:
        if self.max_evidence_chars < 0:
            raise IntegrityConfigurationError("max_evidence_chars must be >= 0")
        if self.max_duplicate_examples < 0:
            raise IntegrityConfigurationError("max_duplicate_examples must be >= 0")


@dataclass(frozen=True)
class IntegrityContext:
    """Execution context for integrity validation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    application: Optional[str] = None
    pipeline_id: Optional[str] = None
    dataset_id: Optional[str] = None
    batch_id: Optional[str] = None
    environment: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntegrityEvidence:
    """Evidence attached to an integrity finding."""

    key: str
    value: Any
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntegrityFinding:
    """One integrity validation finding."""

    finding_id: str
    rule_id: str
    rule_type: IntegrityRuleType
    scope: IntegrityScope
    status: IntegrityStatus
    severity: IntegritySeverity
    message: str
    field: Optional[str] = None
    record_index: Optional[int] = None
    record_id: Optional[str] = None
    evidence: Sequence[IntegrityEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntegrityRuleDefinition:
    """Integrity rule definition."""

    rule_id: str
    name: str
    rule_type: IntegrityRuleType
    scope: IntegrityScope = IntegrityScope.DATASET
    severity: IntegritySeverity = IntegritySeverity.ERROR
    enabled: bool = True
    fields: Sequence[str] = field(default_factory=tuple)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    description: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.rule_id:
            raise IntegrityConfigurationError("rule_id is required")
        if not self.name:
            raise IntegrityConfigurationError("rule name is required")


@dataclass(frozen=True)
class IntegrityRuleResult:
    """Result returned by an integrity rule implementation."""

    passed: bool
    message: str
    severity: Optional[IntegritySeverity] = None
    field: Optional[str] = None
    record_index: Optional[int] = None
    record_id: Optional[str] = None
    evidence: Sequence[IntegrityEvidence] = field(default_factory=tuple)
    remediation: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntegrityReport:
    """Final integrity validation report."""

    report_id: str
    request_id: str
    created_at: str
    status: IntegrityStatus
    decision: IntegrityDecision
    risk_score: float
    rules_evaluated: int
    passed_rules: int
    warning_rules: int
    failed_rules: int
    skipped_rules: int
    record_count: int
    findings: Sequence[IntegrityFinding]
    recommendations: Sequence[str]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == IntegrityDecision.ALLOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class IntegrityRule(Protocol):
    """Protocol for integrity rule handlers."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        """Evaluate integrity rule."""


class AuditSink(Protocol):
    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        """Emit audit event."""


class MetricsSink(Protocol):
    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Increment metric."""

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Observe metric value."""


# =============================================================================
# Utility Functions
# =============================================================================


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(value: Any, *, sort_keys: bool = True) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=sort_keys, default=str, separators=(",", ":"))


def stable_hash(value: str, algorithm: HashAlgorithm = HashAlgorithm.SHA256) -> str:
    encoded = value.encode("utf-8")
    if algorithm == HashAlgorithm.SHA1:
        return hashlib.sha1(encoded).hexdigest()  # noqa: S324
    if algorithm == HashAlgorithm.MD5:
        return hashlib.md5(encoded).hexdigest()  # noqa: S324
    return hashlib.sha256(encoded).hexdigest()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def truncate(value: Any, max_chars: int) -> str:
    text = str(value)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 15] + "...[TRUNCATED]"
    return text


def normalize_records(data: Any) -> Sequence[Mapping[str, Any]]:
    if isinstance(data, Mapping):
        return (data,)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        records: List[Mapping[str, Any]] = []
        for item in data:
            if not isinstance(item, Mapping):
                raise IntegrityInputError("all records must be mappings")
            records.append(item)
        return tuple(records)
    raise IntegrityInputError("data must be a mapping or a sequence of mappings")


def get_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
        if current is default:
            return default
    return current


def is_nullish(value: Any) -> bool:
    return value is None or value == ""


def composite_key(record: Mapping[str, Any], fields: Sequence[str]) -> Tuple[Any, ...]:
    return tuple(get_path(record, field) for field in fields)


def record_identifier(record: Mapping[str, Any], fields: Sequence[str] = ("id", "uuid", "key")) -> Optional[str]:
    for field in fields:
        value = get_path(record, field)
        if not is_nullish(value):
            return str(value)
    return None


def canonical_record_hash(
    record: Mapping[str, Any],
    *,
    fields: Optional[Sequence[str]] = None,
    exclude_fields: Sequence[str] = (),
    algorithm: HashAlgorithm = HashAlgorithm.SHA256,
    sort_keys: bool = True,
) -> str:
    if fields:
        payload = {field: get_path(record, field) for field in fields}
    else:
        excluded = set(exclude_fields)
        payload = {key: value for key, value in record.items() if key not in excluded}
    return stable_hash(safe_json(payload, sort_keys=sort_keys), algorithm=algorithm)


def values_set(records: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> set[Tuple[Any, ...]]:
    return {composite_key(record, fields) for record in records}


# =============================================================================
# Default sinks
# =============================================================================


class LoggingAuditSink:
    """Logging-based audit sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def emit(self, event_name: str, payload: Mapping[str, Any]) -> None:
        self.logger.info("integrity_audit=%s payload=%s", event_name, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


class LoggingMetricsSink:
    """Logging-based metrics sink."""

    def __init__(self, logger_: Optional[logging.Logger] = None) -> None:
        self.logger = logger_ or logger

    async def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("integrity_metric_counter=%s value=%s tags=%s", name, value, dict(tags or {}))

    async def observe(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.logger.debug("integrity_metric_observe=%s value=%s tags=%s", name, value, dict(tags or {}))


# =============================================================================
# Rule Implementations
# =============================================================================


class RequiredFieldsRule:
    """Validates required fields are present and non-null."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        if not fields:
            raise IntegrityConfigurationError("RequiredFieldsRule requires fields")
        missing: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            for field in fields:
                value = get_path(record, field)
                if is_nullish(value):
                    missing.append(
                        IntegrityEvidence(
                            key=field,
                            value=value,
                            expected="non-null",
                            actual=value,
                            metadata={"record_index": idx, "record_id": record_identifier(record)},
                        )
                    )
        passed = not missing
        return IntegrityRuleResult(
            passed=passed,
            message="Required fields are present." if passed else f"Missing required field values detected for {len(missing)} occurrence(s).",
            evidence=tuple(missing[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else f"Populate required fields: {', '.join(fields)}.",
            metadata={"missing_count": len(missing), "fields": fields},
        )


class PrimaryKeyRule:
    """Validates primary key completeness and uniqueness."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        if not fields:
            raise IntegrityConfigurationError("PrimaryKeyRule requires fields")
        seen: Dict[Tuple[Any, ...], int] = {}
        duplicate_examples: List[IntegrityEvidence] = []
        null_examples: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            key = composite_key(record, fields)
            if any(is_nullish(part) for part in key):
                null_examples.append(IntegrityEvidence(key="primary_key", value=key, expected="complete primary key", actual=key, metadata={"record_index": idx}))
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                duplicate_examples.append(IntegrityEvidence(key="primary_key", value=key, expected="unique", actual="duplicate", metadata={"record_index": idx}))
        passed = not duplicate_examples and not null_examples
        evidence = tuple((null_examples + duplicate_examples)[: (config.max_duplicate_examples if config else 50)])
        return IntegrityRuleResult(
            passed=passed,
            message="Primary key integrity passed." if passed else "Primary key integrity failed.",
            evidence=evidence,
            remediation=None if passed else f"Ensure primary key fields are complete and unique: {', '.join(fields)}.",
            metadata={"duplicate_count": len(duplicate_examples), "null_key_count": len(null_examples), "fields": fields},
        )


class UniqueConstraintRule:
    """Validates one or more fields are unique as a composite key."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        if not fields:
            raise IntegrityConfigurationError("UniqueConstraintRule requires fields")
        ignore_nulls = bool(definition.parameters.get("ignore_nulls", True))
        seen: Dict[Tuple[Any, ...], int] = {}
        duplicates: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            key = composite_key(record, fields)
            if ignore_nulls and any(is_nullish(part) for part in key):
                continue
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                duplicates.append(IntegrityEvidence(key="unique_key", value=key, expected="unique", actual="duplicate", metadata={"record_index": idx, "fields": fields}))
        passed = not duplicates
        return IntegrityRuleResult(
            passed=passed,
            message="Unique constraint passed." if passed else f"Unique constraint failed with {len(duplicates)} duplicate key(s).",
            evidence=tuple(duplicates[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else f"Deduplicate records by fields: {', '.join(fields)}.",
            metadata={"duplicate_count": len(duplicates), "fields": fields},
        )


class ForeignKeyRule:
    """Validates foreign-key values exist in reference data."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        reference_name = str(definition.parameters.get("reference"))
        reference_fields = tuple(definition.parameters.get("reference_fields") or fields)
        allow_null = bool(definition.parameters.get("allow_null", False))
        if not fields or not reference_name:
            raise IntegrityConfigurationError("ForeignKeyRule requires fields and reference")
        references = (reference_data or {}).get(reference_name)
        if references is None:
            raise IntegrityConfigurationError(f"reference dataset not provided: {reference_name}")
        if isinstance(references, set):
            ref_values = references
        else:
            ref_records = normalize_records(references)
            ref_values = values_set(ref_records, reference_fields)
        missing: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            key = composite_key(record, fields)
            if any(is_nullish(part) for part in key):
                if allow_null:
                    continue
                missing.append(IntegrityEvidence(key="foreign_key", value=key, expected=f"exists in {reference_name}", actual="null", metadata={"record_index": idx}))
                continue
            if key not in ref_values:
                missing.append(IntegrityEvidence(key="foreign_key", value=key, expected=f"exists in {reference_name}", actual="missing", metadata={"record_index": idx}))
        passed = not missing
        return IntegrityRuleResult(
            passed=passed,
            message="Foreign key integrity passed." if passed else f"Foreign key integrity failed with {len(missing)} missing reference(s).",
            evidence=tuple(missing[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else f"Load missing references in '{reference_name}' or fix FK fields: {', '.join(fields)}.",
            metadata={"missing_reference_count": len(missing), "reference": reference_name, "fields": fields},
        )


class AcceptedValuesRule:
    """Validates values are members of an accepted domain."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        field = str(definition.parameters.get("field") or (definition.fields[0] if definition.fields else ""))
        accepted = set(definition.parameters.get("values", ()))
        allow_null = bool(definition.parameters.get("allow_null", True))
        if not field:
            raise IntegrityConfigurationError("AcceptedValuesRule requires field")
        invalid: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            value = get_path(record, field)
            if is_nullish(value) and allow_null:
                continue
            if value not in accepted:
                invalid.append(IntegrityEvidence(key=field, value=value, expected=sorted(map(str, accepted)), actual=value, metadata={"record_index": idx}))
        passed = not invalid
        return IntegrityRuleResult(
            passed=passed,
            message="Accepted values integrity passed." if passed else f"Accepted values integrity failed for field '{field}'.",
            field=field,
            evidence=tuple(invalid[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else f"Normalize '{field}' to accepted domain values.",
            metadata={"invalid_count": len(invalid), "field": field},
        )


class RangeRule:
    """Validates numeric values fall inside configured bounds."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        field = str(definition.parameters.get("field") or (definition.fields[0] if definition.fields else ""))
        min_value = definition.parameters.get("min")
        max_value = definition.parameters.get("max")
        allow_null = bool(definition.parameters.get("allow_null", True))
        if not field:
            raise IntegrityConfigurationError("RangeRule requires field")
        invalid: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            raw = get_path(record, field)
            if is_nullish(raw) and allow_null:
                continue
            try:
                value = float(raw)
                valid = math.isfinite(value)
                if min_value is not None:
                    valid = valid and value >= float(min_value)
                if max_value is not None:
                    valid = valid and value <= float(max_value)
            except (TypeError, ValueError):
                value = raw
                valid = False
            if not valid:
                invalid.append(IntegrityEvidence(key=field, value=raw, expected={"min": min_value, "max": max_value}, actual=value, metadata={"record_index": idx}))
        passed = not invalid
        return IntegrityRuleResult(
            passed=passed,
            message="Range integrity passed." if passed else f"Range integrity failed for field '{field}'.",
            field=field,
            evidence=tuple(invalid[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else f"Ensure '{field}' is numeric and within configured range.",
            metadata={"invalid_count": len(invalid), "field": field},
        )


class ChecksumRule:
    """Validates record checksum/hash field against canonical record content."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        checksum_field = str(definition.parameters.get("checksum_field", "checksum"))
        fields = tuple(definition.parameters.get("fields") or ())
        exclude_fields = tuple(definition.parameters.get("exclude_fields") or (checksum_field,))
        algorithm = HashAlgorithm(str(definition.parameters.get("algorithm", (config.default_hash_algorithm.value if config else "sha256"))))
        mismatches: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            expected = get_path(record, checksum_field)
            if is_nullish(expected):
                mismatches.append(IntegrityEvidence(key=checksum_field, value=expected, expected="checksum present", actual=expected, metadata={"record_index": idx}))
                continue
            actual = canonical_record_hash(record, fields=fields or None, exclude_fields=exclude_fields, algorithm=algorithm, sort_keys=(config.canonical_json_sort_keys if config else True))
            if str(expected).lower() != actual.lower():
                mismatches.append(IntegrityEvidence(key=checksum_field, value=expected, expected=expected, actual=actual, metadata={"record_index": idx, "algorithm": algorithm.value}))
        passed = not mismatches
        return IntegrityRuleResult(
            passed=passed,
            message="Checksum integrity passed." if passed else f"Checksum integrity failed with {len(mismatches)} mismatch(es).",
            field=checksum_field,
            evidence=tuple(mismatches[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else "Recompute checksums or investigate record tampering/transformation mismatch.",
            metadata={"mismatch_count": len(mismatches), "checksum_field": checksum_field},
        )


class DuplicateRecordRule:
    """Detects duplicate records by canonical hash."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or ())
        exclude_fields = tuple(definition.parameters.get("exclude_fields") or ())
        algorithm = config.default_hash_algorithm if config else HashAlgorithm.SHA256
        seen: Dict[str, int] = {}
        duplicates: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            digest = canonical_record_hash(record, fields=fields or None, exclude_fields=exclude_fields, algorithm=algorithm, sort_keys=(config.canonical_json_sort_keys if config else True))
            seen[digest] = seen.get(digest, 0) + 1
            if seen[digest] == 2:
                duplicates.append(IntegrityEvidence(key="record_hash", value=digest, expected="unique record", actual="duplicate", metadata={"record_index": idx}))
        passed = not duplicates
        return IntegrityRuleResult(
            passed=passed,
            message="Duplicate record integrity passed." if passed else f"Duplicate records detected: {len(duplicates)} duplicate hash(es).",
            evidence=tuple(duplicates[: (config.max_duplicate_examples if config else 50)]),
            remediation=None if passed else "Deduplicate identical records or investigate repeated ingestion.",
            metadata={"duplicate_hash_count": len(duplicates)},
        )


class ParentChildRule:
    """Validates parent-child relationship inside a dataset."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        id_field = str(definition.parameters.get("id_field", "id"))
        parent_field = str(definition.parameters.get("parent_field", "parent_id"))
        allow_root = bool(definition.parameters.get("allow_root", True))
        ids = {get_path(record, id_field) for record in data if not is_nullish(get_path(record, id_field))}
        orphans: List[IntegrityEvidence] = []
        self_parent: List[IntegrityEvidence] = []
        for idx, record in enumerate(data):
            record_id = get_path(record, id_field)
            parent_id = get_path(record, parent_field)
            if is_nullish(parent_id):
                if allow_root:
                    continue
                orphans.append(IntegrityEvidence(key=parent_field, value=parent_id, expected="parent id", actual=parent_id, metadata={"record_index": idx, "record_id": record_id}))
                continue
            if record_id == parent_id:
                self_parent.append(IntegrityEvidence(key=parent_field, value=parent_id, expected="different from id", actual=parent_id, metadata={"record_index": idx, "record_id": record_id}))
            elif parent_id not in ids:
                orphans.append(IntegrityEvidence(key=parent_field, value=parent_id, expected="existing parent id", actual="missing", metadata={"record_index": idx, "record_id": record_id}))
        passed = not orphans and not self_parent
        evidence = tuple((orphans + self_parent)[: (config.max_duplicate_examples if config else 50)])
        return IntegrityRuleResult(
            passed=passed,
            message="Parent-child integrity passed." if passed else "Parent-child integrity failed.",
            evidence=evidence,
            remediation=None if passed else "Fix orphan parent references and self-parent relationships.",
            metadata={"orphan_count": len(orphans), "self_parent_count": len(self_parent)},
        )


class CompletenessRule:
    """Validates completeness rates for fields."""

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        await asyncio.sleep(0)
        fields = tuple(definition.parameters.get("fields") or definition.fields)
        min_rate = float(definition.parameters.get("min_rate", 1.0))
        if not fields:
            raise IntegrityConfigurationError("CompletenessRule requires fields")
        failed: List[IntegrityEvidence] = []
        total = max(len(data), 1)
        for field in fields:
            complete = sum(1 for record in data if not is_nullish(get_path(record, field)))
            rate = complete / total
            if rate < min_rate:
                failed.append(IntegrityEvidence(key=field, value=rate, expected=f">= {min_rate}", actual=rate, metadata={"complete": complete, "total": total}))
        passed = not failed
        return IntegrityRuleResult(
            passed=passed,
            message="Completeness integrity passed." if passed else f"Completeness integrity failed for {len(failed)} field(s).",
            evidence=tuple(failed),
            remediation=None if passed else "Investigate missing values and correct upstream extraction or mapping.",
            metadata={"failed_field_count": len(failed), "min_rate": min_rate},
        )


class CallableIntegrityRule:
    """Adapter for custom sync/async integrity rule callables."""

    def __init__(self, func: Callable[..., IntegrityRuleResult]) -> None:
        self.func = func

    async def evaluate(
        self,
        data: Sequence[Mapping[str, Any]],
        *,
        definition: IntegrityRuleDefinition,
        context: IntegrityContext,
        reference_data: Optional[Mapping[str, Any]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
    ) -> IntegrityRuleResult:
        result = self.func(data, definition=definition, context=context, reference_data=reference_data, config=config)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, IntegrityRuleResult):
            raise IntegrityRuleExecutionError("custom integrity rule must return IntegrityRuleResult")
        return result


# =============================================================================
# Validator
# =============================================================================


class IntegrityValidator:
    """Enterprise integrity validator."""

    DEFAULT_HANDLERS: Mapping[IntegrityRuleType, IntegrityRule] = {
        IntegrityRuleType.REQUIRED_FIELDS: RequiredFieldsRule(),
        IntegrityRuleType.PRIMARY_KEY: PrimaryKeyRule(),
        IntegrityRuleType.UNIQUE: UniqueConstraintRule(),
        IntegrityRuleType.FOREIGN_KEY: ForeignKeyRule(),
        IntegrityRuleType.ACCEPTED_VALUES: AcceptedValuesRule(),
        IntegrityRuleType.RANGE: RangeRule(),
        IntegrityRuleType.CHECKSUM: ChecksumRule(),
        IntegrityRuleType.HASH_SIGNATURE: ChecksumRule(),
        IntegrityRuleType.DUPLICATE_RECORD: DuplicateRecordRule(),
        IntegrityRuleType.PARENT_CHILD: ParentChildRule(),
        IntegrityRuleType.COMPLETENESS: CompletenessRule(),
    }

    def __init__(
        self,
        *,
        rules: Sequence[IntegrityRuleDefinition],
        rule_handlers: Optional[Mapping[str, IntegrityRule]] = None,
        config: Optional[IntegrityValidatorConfig] = None,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
    ) -> None:
        self.config = config or IntegrityValidatorConfig()
        self.config.validate()
        self.rules = tuple(rules)
        for rule in self.rules:
            rule.validate()
        self.rule_handlers = dict(rule_handlers or {})
        self.audit_sink = audit_sink or LoggingAuditSink()
        self.metrics_sink = metrics_sink or LoggingMetricsSink()

    async def validate(
        self,
        data: Any,
        *,
        context: Optional[IntegrityContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        scope: Optional[IntegrityScope] = None,
    ) -> IntegrityReport:
        """Validate dataset integrity."""

        context = context or IntegrityContext()
        started = time.perf_counter()
        findings: List[IntegrityFinding] = []
        rules_evaluated = passed_rules = warning_rules = failed_rules = skipped_rules = 0

        try:
            records = normalize_records(data)
            for definition in self.rules:
                if not definition.enabled:
                    skipped_rules += 1
                    continue
                if scope and definition.scope != scope:
                    skipped_rules += 1
                    continue
                rules_evaluated += 1
                try:
                    handler = self._handler_for(definition)
                    result = await handler.evaluate(
                        records,
                        definition=definition,
                        context=context,
                        reference_data=reference_data,
                        config=self.config,
                    )
                    finding = self._finding_from_result(definition, result)
                    if result.passed:
                        passed_rules += 1
                        if self.config.include_passed_checks:
                            findings.append(finding)
                    else:
                        findings.append(finding)
                        if finding.severity == IntegritySeverity.WARNING:
                            warning_rules += 1
                        else:
                            failed_rules += 1
                        if self.config.fail_fast:
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Integrity rule failed: %s", definition.rule_id)
                    finding = self._finding_from_exception(definition, exc)
                    findings.append(finding)
                    failed_rules += 1
                    if self.config.fail_fast:
                        break

            report = self._build_report(
                context=context,
                records=records,
                findings=findings,
                rules_evaluated=rules_evaluated,
                passed_rules=passed_rules,
                warning_rules=warning_rules,
                failed_rules=failed_rules,
                skipped_rules=skipped_rules,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            await self._record_success(context, report)
            await self._audit_completed(context, report)
            return report

        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            await self._record_failure(context, exc, latency_ms)
            await self._audit_failure(context, exc, latency_ms)
            raise

    def validate_sync(
        self,
        data: Any,
        *,
        context: Optional[IntegrityContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        scope: Optional[IntegrityScope] = None,
    ) -> IntegrityReport:
        return asyncio.run(self.validate(data, context=context, reference_data=reference_data, scope=scope))

    async def validate_many(
        self,
        datasets: Sequence[Any],
        *,
        context: Optional[IntegrityContext] = None,
        reference_data: Optional[Mapping[str, Any]] = None,
        concurrency: int = 5,
    ) -> Sequence[IntegrityReport]:
        if concurrency <= 0:
            raise IntegrityConfigurationError("concurrency must be positive")
        semaphore = asyncio.Semaphore(concurrency)

        async def run_one(dataset: Any) -> IntegrityReport:
            async with semaphore:
                return await self.validate(dataset, context=context, reference_data=reference_data)

        return tuple(await asyncio.gather(*(run_one(dataset) for dataset in datasets)))

    def _handler_for(self, definition: IntegrityRuleDefinition) -> IntegrityRule:
        if definition.rule_id in self.rule_handlers:
            return self.rule_handlers[definition.rule_id]
        if definition.rule_type == IntegrityRuleType.CUSTOM:
            raise IntegrityConfigurationError(f"No custom handler registered for rule {definition.rule_id}")
        handler = self.DEFAULT_HANDLERS.get(definition.rule_type)
        if handler is None:
            raise IntegrityConfigurationError(f"No handler registered for rule type {definition.rule_type.value}")
        return handler

    def _finding_from_result(self, definition: IntegrityRuleDefinition, result: IntegrityRuleResult) -> IntegrityFinding:
        status = IntegrityStatus.PASSED if result.passed else IntegrityStatus.FAILED
        severity = result.severity or (IntegritySeverity.INFO if result.passed else definition.severity)
        evidence = tuple(
            IntegrityEvidence(
                key=item.key,
                value=truncate(item.value, self.config.max_evidence_chars),
                expected=truncate(item.expected, self.config.max_evidence_chars) if item.expected is not None else None,
                actual=truncate(item.actual, self.config.max_evidence_chars) if item.actual is not None else None,
                metadata=item.metadata,
            )
            for item in result.evidence
        )
        return IntegrityFinding(
            finding_id=str(uuid.uuid4()),
            rule_id=definition.rule_id,
            rule_type=definition.rule_type,
            scope=definition.scope,
            status=status,
            severity=severity,
            message=result.message,
            field=result.field,
            record_index=result.record_index,
            record_id=result.record_id,
            evidence=evidence,
            remediation=result.remediation,
            metadata={**dict(definition.metadata), **dict(result.metadata)},
        )

    def _finding_from_exception(self, definition: IntegrityRuleDefinition, exc: BaseException) -> IntegrityFinding:
        return IntegrityFinding(
            finding_id=str(uuid.uuid4()),
            rule_id=definition.rule_id,
            rule_type=definition.rule_type,
            scope=definition.scope,
            status=IntegrityStatus.ERROR,
            severity=IntegritySeverity.ERROR,
            message=f"Rule execution failed: {type(exc).__name__}: {exc}",
            remediation="Inspect rule configuration, handler implementation and input data.",
            metadata={"error_type": type(exc).__name__},
        )

    def _build_report(
        self,
        *,
        context: IntegrityContext,
        records: Sequence[Mapping[str, Any]],
        findings: Sequence[IntegrityFinding],
        rules_evaluated: int,
        passed_rules: int,
        warning_rules: int,
        failed_rules: int,
        skipped_rules: int,
        latency_ms: float,
    ) -> IntegrityReport:
        risk_score = self._risk_score(findings)
        decision = self._decision(findings, risk_score)
        status = self._status(decision, findings)
        return IntegrityReport(
            report_id=str(uuid.uuid4()),
            request_id=context.request_id,
            created_at=utc_now_iso(),
            status=status,
            decision=decision,
            risk_score=risk_score,
            rules_evaluated=rules_evaluated,
            passed_rules=passed_rules,
            warning_rules=warning_rules,
            failed_rules=failed_rules,
            skipped_rules=skipped_rules,
            record_count=len(records),
            findings=tuple(findings),
            recommendations=tuple(self._recommendations(findings, decision)),
            metadata={
                "tenant_id": context.tenant_id,
                "application": context.application,
                "pipeline_id": context.pipeline_id,
                "dataset_id": context.dataset_id,
                "batch_id": context.batch_id,
                "environment": context.environment,
                "latency_ms": round(latency_ms, 3),
                "validator_version": self.config.version,
            },
        )

    def _risk_score(self, findings: Sequence[IntegrityFinding]) -> float:
        weights = {
            IntegritySeverity.INFO: 0.0,
            IntegritySeverity.WARNING: 0.25,
            IntegritySeverity.ERROR: 0.70,
            IntegritySeverity.CRITICAL: 1.0,
        }
        active = [finding for finding in findings if finding.status not in {IntegrityStatus.PASSED, IntegrityStatus.SKIPPED}]
        if not active:
            return 0.0
        max_weight = max(weights[finding.severity] for finding in active)
        avg_weight = sum(weights[finding.severity] for finding in active) / len(active)
        return clamp((max_weight * 0.75) + (avg_weight * 0.25))

    def _decision(self, findings: Sequence[IntegrityFinding], risk_score: float) -> IntegrityDecision:
        severities = {finding.severity for finding in findings if finding.status not in {IntegrityStatus.PASSED, IntegrityStatus.SKIPPED}}
        if self.config.block_on_critical and IntegritySeverity.CRITICAL in severities:
            return IntegrityDecision.BLOCK
        if risk_score >= 0.85:
            return IntegrityDecision.BLOCK
        if self.config.review_on_error and IntegritySeverity.ERROR in severities:
            return IntegrityDecision.REVIEW
        if risk_score >= 0.25:
            return IntegrityDecision.REVIEW
        return IntegrityDecision.ALLOW

    def _status(self, decision: IntegrityDecision, findings: Sequence[IntegrityFinding]) -> IntegrityStatus:
        if any(finding.status == IntegrityStatus.ERROR for finding in findings):
            return IntegrityStatus.ERROR
        if decision == IntegrityDecision.BLOCK:
            return IntegrityStatus.FAILED
        if decision == IntegrityDecision.REVIEW:
            return IntegrityStatus.WARNING
        return IntegrityStatus.PASSED

    def _recommendations(self, findings: Sequence[IntegrityFinding], decision: IntegrityDecision) -> List[str]:
        recommendations: List[str] = []
        if decision == IntegrityDecision.BLOCK:
            recommendations.append("Block downstream processing until critical integrity failures are corrected.")
        elif decision == IntegrityDecision.REVIEW:
            recommendations.append("Route the dataset to data owner or governance review before downstream use.")
        else:
            recommendations.append("Integrity validation passed within configured thresholds.")
        for finding in findings:
            if finding.status != IntegrityStatus.PASSED and finding.remediation:
                recommendations.append(finding.remediation)
        return list(dict.fromkeys(recommendations))

    async def _record_success(self, context: IntegrityContext, report: IntegrityReport) -> None:
        if not self.config.metrics_enabled:
            return
        tags = self._metric_tags(context, report.decision)
        await self.metrics_sink.increment("data.validation.integrity.success", 1, tags)
        await self.metrics_sink.observe("data.validation.integrity.risk_score", report.risk_score, tags)
        await self.metrics_sink.observe("data.validation.integrity.findings", len(report.findings), tags)
        await self.metrics_sink.observe("data.validation.integrity.records", report.record_count, tags)

    async def _record_failure(self, context: IntegrityContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.metrics_enabled:
            return
        tags = {**self._metric_tags(context, IntegrityDecision.BLOCK), "error_type": type(exc).__name__}
        await self.metrics_sink.increment("data.validation.integrity.failure", 1, tags)
        await self.metrics_sink.observe("data.validation.integrity.failure_latency_ms", latency_ms, tags)

    def _metric_tags(self, context: IntegrityContext, decision: IntegrityDecision) -> Mapping[str, str]:
        return {
            "tenant_id": context.tenant_id or "unknown",
            "application": context.application or "unknown",
            "environment": context.environment or "unknown",
            "dataset_id": context.dataset_id or "unknown",
            "decision": decision.value,
        }

    async def _audit_completed(self, context: IntegrityContext, report: IntegrityReport) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("integrity_validation_completed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "application": context.application,
            "pipeline_id": context.pipeline_id,
            "dataset_id": context.dataset_id,
            "batch_id": context.batch_id,
            "environment": context.environment,
            "trace_id": context.trace_id,
            "report_id": report.report_id,
            "status": report.status.value,
            "decision": report.decision.value,
            "risk_score": report.risk_score,
            "record_count": report.record_count,
            "rules_evaluated": report.rules_evaluated,
            "findings": [asdict(finding) for finding in report.findings],
        })

    async def _audit_failure(self, context: IntegrityContext, exc: BaseException, latency_ms: float) -> None:
        if not self.config.audit_enabled:
            return
        await self.audit_sink.emit("integrity_validation_failed", {
            "event_id": str(uuid.uuid4()),
            "created_at": utc_now_iso(),
            "request_id": context.request_id,
            "tenant_id": context.tenant_id,
            "application": context.application,
            "dataset_id": context.dataset_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "latency_ms": round(latency_ms, 3),
        })


# =============================================================================
# Factory Helpers
# =============================================================================


def build_default_integrity_rules() -> Sequence[IntegrityRuleDefinition]:
    """Build practical default integrity rules."""

    return (
        IntegrityRuleDefinition(
            rule_id="integrity.primary_key.id",
            name="Primary key id integrity",
            rule_type=IntegrityRuleType.PRIMARY_KEY,
            scope=IntegrityScope.DATASET,
            severity=IntegritySeverity.CRITICAL,
            fields=("id",),
            parameters={"fields": ("id",)},
        ),
        IntegrityRuleDefinition(
            rule_id="integrity.required.timestamps",
            name="Required timestamp fields",
            rule_type=IntegrityRuleType.REQUIRED_FIELDS,
            scope=IntegrityScope.DATASET,
            severity=IntegritySeverity.ERROR,
            fields=("created_at",),
            parameters={"fields": ("created_at",)},
        ),
        IntegrityRuleDefinition(
            rule_id="integrity.no_duplicate_records",
            name="No duplicate records",
            rule_type=IntegrityRuleType.DUPLICATE_RECORD,
            scope=IntegrityScope.DATASET,
            severity=IntegritySeverity.WARNING,
            parameters={"exclude_fields": ("updated_at", "ingested_at")},
        ),
    )


def build_default_integrity_validator(
    *,
    config: Optional[IntegrityValidatorConfig] = None,
    extra_rules: Sequence[IntegrityRuleDefinition] = (),
    rule_handlers: Optional[Mapping[str, IntegrityRule]] = None,
) -> IntegrityValidator:
    return IntegrityValidator(
        rules=tuple(build_default_integrity_rules()) + tuple(extra_rules),
        rule_handlers=rule_handlers,
        config=config,
    )


async def _demo_async() -> None:
    logging.basicConfig(level=logging.INFO)
    data = (
        {"id": "1", "name": "A", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "2", "name": "B", "created_at": "2026-01-01T00:00:00Z"},
    )
    validator = build_default_integrity_validator()
    report = await validator.validate(
        data,
        context=IntegrityContext(
            tenant_id="demo",
            application="data-platform",
            dataset_id="customers",
            environment="dev",
        ),
    )
    print(report.to_json(indent=2))


if __name__ == "__main__":
    asyncio.run(_demo_async())
