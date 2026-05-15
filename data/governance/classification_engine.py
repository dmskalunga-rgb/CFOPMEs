"""
classification_engine.py
========================

Enterprise-grade data classification engine for governance platforms.

Core capabilities
-----------------
- Data classification by column name, sample values, regex, dictionary and custom rules.
- PII, PCI, PHI, financial, credential and confidential-data detection patterns.
- Sensitivity scoring with confidence, evidence and explainability.
- Dataset, column and field-level classification results.
- Policy-driven sensitivity resolution and recommended controls.
- Batch classification for dictionaries, records and pandas DataFrames.
- Pluggable rule providers and audit sinks.
- Optional redaction/masking helpers for classified data.
- Dependency-light implementation with optional pandas support.

This module is vendor-neutral and can feed catalogs, privacy workflows, access
policies, data contracts, retention engines and compliance evidence pipelines.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import logging
import math
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Pattern, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Record = Mapping[str, Any]
RulePredicate = Callable[["ClassificationContext"], Optional["ClassificationFinding"]]


class ClassificationError(Exception):
    """Base exception for classification failures."""


class ClassificationRuleError(ClassificationError):
    """Raised when a classification rule fails."""


class ClassificationLevel(str, enum.Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    HIGHLY_RESTRICTED = "highly_restricted"


class ClassificationCategory(str, enum.Enum):
    PII = "pii"
    PCI = "pci"
    PHI = "phi"
    FINANCIAL = "financial"
    CREDENTIAL = "credential"
    SECURITY = "security"
    CONFIDENTIAL_BUSINESS = "confidential_business"
    PERSONAL = "personal"
    LOCATION = "location"
    BIOMETRIC = "biometric"
    EMPLOYEE = "employee"
    CUSTOMER = "customer"
    OPERATIONAL = "operational"
    PUBLIC = "public"
    UNKNOWN = "unknown"


class RuleType(str, enum.Enum):
    COLUMN_NAME = "column_name"
    VALUE_REGEX = "value_regex"
    VALUE_DICTIONARY = "value_dictionary"
    STATISTICAL = "statistical"
    CUSTOM = "custom"


class MatchStrategy(str, enum.Enum):
    ANY = "any"
    RATIO = "ratio"
    MAJORITY = "majority"


class MaskingStrategy(str, enum.Enum):
    NONE = "none"
    FULL = "full"
    PARTIAL = "partial"
    HASH = "hash"
    EMAIL = "email"
    TOKENIZE = "tokenize"


@dataclass(frozen=True)
class ClassificationContext:
    dataset_name: Optional[str]
    field_name: str
    values: Sequence[Any]
    metadata: JsonDict = field(default_factory=dict)

    @property
    def non_null_values(self) -> List[Any]:
        return [value for value in self.values if value is not None and str(value).strip() != ""]

    @property
    def sample_size(self) -> int:
        return len(self.values)

    @property
    def non_null_sample_size(self) -> int:
        return len(self.non_null_values)


@dataclass(frozen=True)
class ClassificationEvidence:
    rule_id: str
    rule_name: str
    rule_type: RuleType
    matched_count: int
    sample_size: int
    confidence: float
    examples: Tuple[str, ...] = field(default_factory=tuple)
    details: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_type": self.rule_type.value,
            "matched_count": self.matched_count,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "examples": list(self.examples),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ClassificationFinding:
    category: ClassificationCategory
    label: str
    level: ClassificationLevel
    confidence: float
    evidence: ClassificationEvidence
    recommended_controls: Tuple[str, ...] = field(default_factory=tuple)
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "category": self.category.value,
            "label": self.label,
            "level": self.level.value,
            "confidence": self.confidence,
            "evidence": self.evidence.to_dict(),
            "recommended_controls": list(self.recommended_controls),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ClassificationRule:
    rule_id: str
    name: str
    category: ClassificationCategory
    label: str
    level: ClassificationLevel
    rule_type: RuleType
    column_patterns: Tuple[str, ...] = field(default_factory=tuple)
    value_patterns: Tuple[str, ...] = field(default_factory=tuple)
    dictionary_values: Tuple[str, ...] = field(default_factory=tuple)
    min_confidence: float = 0.65
    match_threshold: float = 0.2
    match_strategy: MatchStrategy = MatchStrategy.RATIO
    enabled: bool = True
    recommended_controls: Tuple[str, ...] = field(default_factory=tuple)
    custom_predicate: Optional[RulePredicate] = None
    priority: int = 100
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError("rule_id is required")
        if not 0 <= self.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0 <= self.match_threshold <= 1:
            raise ValueError("match_threshold must be between 0 and 1")

    def evaluate(self, context: ClassificationContext) -> Optional[ClassificationFinding]:
        if not self.enabled:
            return None
        if self.rule_type == RuleType.CUSTOM:
            if not self.custom_predicate:
                return None
            return self.custom_predicate(context)
        if self.rule_type == RuleType.COLUMN_NAME:
            return self._evaluate_column_name(context)
        if self.rule_type == RuleType.VALUE_REGEX:
            return self._evaluate_value_regex(context)
        if self.rule_type == RuleType.VALUE_DICTIONARY:
            return self._evaluate_dictionary(context)
        if self.rule_type == RuleType.STATISTICAL:
            return self._evaluate_statistical(context)
        return None

    def _evaluate_column_name(self, context: ClassificationContext) -> Optional[ClassificationFinding]:
        field = normalize_text(context.field_name)
        matched_patterns = [pattern for pattern in self.column_patterns if re.search(pattern, field, re.IGNORECASE)]
        if not matched_patterns:
            return None
        confidence = max(self.min_confidence, 0.85 if len(matched_patterns) > 1 else 0.75)
        evidence = ClassificationEvidence(
            rule_id=self.rule_id,
            rule_name=self.name,
            rule_type=self.rule_type,
            matched_count=len(matched_patterns),
            sample_size=1,
            confidence=confidence,
            examples=tuple(matched_patterns[:5]),
        )
        return self._finding(confidence, evidence)

    def _evaluate_value_regex(self, context: ClassificationContext) -> Optional[ClassificationFinding]:
        values = [str(value) for value in context.non_null_values]
        if not values or not self.value_patterns:
            return None
        compiled = [re.compile(pattern, re.IGNORECASE) for pattern in self.value_patterns]
        matched_examples: List[str] = []
        matched = 0
        for value in values:
            if any(pattern.search(value) for pattern in compiled):
                matched += 1
                if len(matched_examples) < 5:
                    matched_examples.append(mask_example(value))
        confidence = compute_match_confidence(matched, len(values), self.match_strategy)
        if confidence < self.min_confidence or matched / max(len(values), 1) < self.match_threshold:
            return None
        evidence = ClassificationEvidence(
            rule_id=self.rule_id,
            rule_name=self.name,
            rule_type=self.rule_type,
            matched_count=matched,
            sample_size=len(values),
            confidence=confidence,
            examples=tuple(matched_examples),
            details={"match_ratio": matched / max(len(values), 1)},
        )
        return self._finding(confidence, evidence)

    def _evaluate_dictionary(self, context: ClassificationContext) -> Optional[ClassificationFinding]:
        values = [normalize_text(str(value)) for value in context.non_null_values]
        dictionary = {normalize_text(value) for value in self.dictionary_values}
        if not values or not dictionary:
            return None
        matched_values = [value for value in values if value in dictionary]
        confidence = compute_match_confidence(len(matched_values), len(values), self.match_strategy)
        if confidence < self.min_confidence or len(matched_values) / max(len(values), 1) < self.match_threshold:
            return None
        evidence = ClassificationEvidence(
            rule_id=self.rule_id,
            rule_name=self.name,
            rule_type=self.rule_type,
            matched_count=len(matched_values),
            sample_size=len(values),
            confidence=confidence,
            examples=tuple(sorted(set(matched_values))[:5]),
            details={"match_ratio": len(matched_values) / max(len(values), 1)},
        )
        return self._finding(confidence, evidence)

    def _evaluate_statistical(self, context: ClassificationContext) -> Optional[ClassificationFinding]:
        values = [str(value).strip() for value in context.non_null_values]
        if not values:
            return None
        lengths = [len(value) for value in values]
        unique_ratio = len(set(values)) / len(values)
        numeric_ratio = sum(value.replace(".", "", 1).isdigit() for value in values) / len(values)
        entropy = average_entropy(values)
        confidence = 0.0
        reasons: List[str] = []

        if self.label in {"identifier", "unique_identifier"} and unique_ratio >= 0.95 and statistics.mean(lengths) >= 6:
            confidence += 0.75
            reasons.append("high_unique_ratio")
        if self.label in {"token", "secret", "api_key"} and entropy >= 3.5 and statistics.mean(lengths) >= 20:
            confidence += 0.85
            reasons.append("high_entropy_long_values")
        if self.label in {"numeric_identifier"} and numeric_ratio >= 0.9 and unique_ratio >= 0.8:
            confidence += 0.70
            reasons.append("numeric_unique_values")

        confidence = min(confidence, 1.0)
        if confidence < self.min_confidence:
            return None
        evidence = ClassificationEvidence(
            rule_id=self.rule_id,
            rule_name=self.name,
            rule_type=self.rule_type,
            matched_count=len(values),
            sample_size=len(values),
            confidence=confidence,
            examples=tuple(mask_example(value) for value in values[:5]),
            details={"unique_ratio": unique_ratio, "numeric_ratio": numeric_ratio, "entropy": entropy, "reasons": reasons},
        )
        return self._finding(confidence, evidence)

    def _finding(self, confidence: float, evidence: ClassificationEvidence) -> ClassificationFinding:
        return ClassificationFinding(
            category=self.category,
            label=self.label,
            level=self.level,
            confidence=round(confidence, 6),
            evidence=evidence,
            recommended_controls=self.recommended_controls,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "category": self.category.value,
            "label": self.label,
            "level": self.level.value,
            "rule_type": self.rule_type.value,
            "column_patterns": list(self.column_patterns),
            "value_patterns": list(self.value_patterns),
            "dictionary_values": list(self.dictionary_values),
            "min_confidence": self.min_confidence,
            "match_threshold": self.match_threshold,
            "match_strategy": self.match_strategy.value,
            "enabled": self.enabled,
            "recommended_controls": list(self.recommended_controls),
            "priority": self.priority,
            "metadata": dict(self.metadata),
        }


@dataclass
class FieldClassificationResult:
    field_name: str
    dataset_name: Optional[str]
    level: ClassificationLevel
    categories: Set[ClassificationCategory]
    labels: Set[str]
    confidence: float
    findings: List[ClassificationFinding]
    sample_size: int
    non_null_count: int
    null_count: int
    unique_count: int
    recommended_controls: Set[str] = field(default_factory=set)
    classified_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def to_dict(self) -> JsonDict:
        return {
            "field_name": self.field_name,
            "dataset_name": self.dataset_name,
            "level": self.level.value,
            "categories": sorted(category.value for category in self.categories),
            "labels": sorted(self.labels),
            "confidence": self.confidence,
            "sample_size": self.sample_size,
            "non_null_count": self.non_null_count,
            "null_count": self.null_count,
            "unique_count": self.unique_count,
            "recommended_controls": sorted(self.recommended_controls),
            "classified_at": self.classified_at.isoformat(),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass
class DatasetClassificationResult:
    dataset_name: Optional[str]
    level: ClassificationLevel
    categories: Set[ClassificationCategory]
    fields: List[FieldClassificationResult]
    recommended_controls: Set[str]
    row_count: Optional[int] = None
    field_count: int = 0
    classified_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    audit_hash: Optional[str] = None

    def to_dict(self) -> JsonDict:
        return {
            "dataset_name": self.dataset_name,
            "level": self.level.value,
            "categories": sorted(category.value for category in self.categories),
            "recommended_controls": sorted(self.recommended_controls),
            "row_count": self.row_count,
            "field_count": self.field_count,
            "classified_at": self.classified_at.isoformat(),
            "audit_hash": self.audit_hash,
            "fields": [field_result.to_dict() for field_result in self.fields],
        }


@dataclass(frozen=True)
class ClassificationEngineConfig:
    sample_size: int = 1000
    min_field_confidence: float = 0.50
    include_unknown_fields: bool = True
    max_examples_per_finding: int = 5
    enable_column_name_rules: bool = True
    enable_value_rules: bool = True
    enable_statistical_rules: bool = True
    fail_on_rule_error: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sample_size <= 0:
            raise ValueError("sample_size must be > 0")
        if not 0 <= self.min_field_confidence <= 1:
            raise ValueError("min_field_confidence must be between 0 and 1")


@runtime_checkable
class ClassificationAuditSink(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        ...


class LoggingClassificationAuditSink:
    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self.log = log or logger

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        self.log.info("classification_audit", extra={"event_type": event_type, "payload": dict(payload)})


class ClassificationRuleRegistry:
    """Registry for classification rules."""

    def __init__(self, rules: Optional[Iterable[ClassificationRule]] = None) -> None:
        self._rules: Dict[str, ClassificationRule] = {}
        for rule in default_classification_rules():
            self.register(rule, replace=True)
        if rules:
            for rule in rules:
                self.register(rule, replace=True)

    def register(self, rule: ClassificationRule, *, replace: bool = False) -> None:
        if rule.rule_id in self._rules and not replace:
            raise ValueError(f"Rule already registered: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def remove(self, rule_id: str) -> bool:
        return self._rules.pop(rule_id, None) is not None

    def list_rules(self, *, enabled_only: bool = True) -> List[ClassificationRule]:
        rules = list(self._rules.values())
        if enabled_only:
            rules = [rule for rule in rules if rule.enabled]
        return sorted(rules, key=lambda rule: rule.priority)

    def to_dict(self) -> JsonDict:
        return {rule_id: rule.to_dict() for rule_id, rule in self._rules.items()}


class ClassificationEngine:
    """Main enterprise data classification engine."""

    def __init__(
        self,
        *,
        config: Optional[ClassificationEngineConfig] = None,
        registry: Optional[ClassificationRuleRegistry] = None,
        audit_sink: Optional[ClassificationAuditSink] = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or ClassificationEngineConfig()
        self.registry = registry or ClassificationRuleRegistry()
        self.audit = audit_sink or LoggingClassificationAuditSink()
        self.log = log or logger

    def classify_field(
        self,
        field_name: str,
        values: Sequence[Any],
        *,
        dataset_name: Optional[str] = None,
        metadata: Optional[JsonDict] = None,
    ) -> FieldClassificationResult:
        sampled_values = list(values[: self.config.sample_size]) if hasattr(values, "__getitem__") else list(values)[: self.config.sample_size]
        context = ClassificationContext(dataset_name=dataset_name, field_name=field_name, values=sampled_values, metadata=metadata or {})
        findings: List[ClassificationFinding] = []

        for rule in self.registry.list_rules(enabled_only=True):
            if not self._rule_type_enabled(rule.rule_type):
                continue
            try:
                finding = rule.evaluate(context)
                if finding and finding.confidence >= self.config.min_field_confidence:
                    findings.append(finding)
            except Exception as exc:
                self.audit.emit(
                    "classification_rule_error",
                    {"rule_id": rule.rule_id, "field_name": field_name, "dataset_name": dataset_name, "error": str(exc)},
                )
                if self.config.fail_on_rule_error:
                    raise ClassificationRuleError(f"Rule {rule.rule_id} failed: {exc}") from exc

        result = self._resolve_field_result(field_name, dataset_name, sampled_values, findings)
        self.audit.emit("field_classified", result.to_dict())
        return result

    def classify_records(self, records: Sequence[Record], *, dataset_name: Optional[str] = None, metadata: Optional[JsonDict] = None) -> DatasetClassificationResult:
        rows = list(records)
        field_names = sorted({key for row in rows for key in row.keys()})
        field_results: List[FieldClassificationResult] = []
        for field_name in field_names:
            values = [row.get(field_name) for row in rows]
            field_results.append(self.classify_field(field_name, values, dataset_name=dataset_name, metadata=metadata))
        return self._resolve_dataset_result(dataset_name, field_results, row_count=len(rows))

    def classify_dataframe(self, dataframe: Any, *, dataset_name: Optional[str] = None, metadata: Optional[JsonDict] = None) -> DatasetClassificationResult:
        if pd is None:
            raise ClassificationError("pandas is required for classify_dataframe")
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be a pandas DataFrame")
        field_results = []
        for column in dataframe.columns:
            values = dataframe[column].head(self.config.sample_size).tolist()
            field_results.append(self.classify_field(str(column), values, dataset_name=dataset_name, metadata=metadata))
        result = self._resolve_dataset_result(dataset_name, field_results, row_count=int(len(dataframe)))
        dataframe.attrs["classification_result"] = result.to_dict()
        return result

    def classify_mapping(self, mapping: Mapping[str, Any], *, dataset_name: Optional[str] = None, metadata: Optional[JsonDict] = None) -> DatasetClassificationResult:
        field_results = []
        for field_name, value in mapping.items():
            values = value if isinstance(value, list) else [value]
            field_results.append(self.classify_field(str(field_name), list(values), dataset_name=dataset_name, metadata=metadata))
        return self._resolve_dataset_result(dataset_name, field_results, row_count=1)

    def _rule_type_enabled(self, rule_type: RuleType) -> bool:
        if rule_type == RuleType.COLUMN_NAME:
            return self.config.enable_column_name_rules
        if rule_type in {RuleType.VALUE_REGEX, RuleType.VALUE_DICTIONARY}:
            return self.config.enable_value_rules
        if rule_type == RuleType.STATISTICAL:
            return self.config.enable_statistical_rules
        return True

    def _resolve_field_result(
        self,
        field_name: str,
        dataset_name: Optional[str],
        values: Sequence[Any],
        findings: List[ClassificationFinding],
    ) -> FieldClassificationResult:
        non_null = [value for value in values if value is not None and str(value).strip() != ""]
        if findings:
            level = max((finding.level for finding in findings), key=level_rank)
            confidence = round(max(finding.confidence for finding in findings), 6)
            categories = {finding.category for finding in findings}
            labels = {finding.label for finding in findings}
            controls = {control for finding in findings for control in finding.recommended_controls}
        else:
            level = ClassificationLevel.INTERNAL if self.config.include_unknown_fields else ClassificationLevel.PUBLIC
            confidence = 0.0
            categories = {ClassificationCategory.UNKNOWN} if self.config.include_unknown_fields else set()
            labels = {"unknown"} if self.config.include_unknown_fields else set()
            controls = set()

        return FieldClassificationResult(
            field_name=field_name,
            dataset_name=dataset_name,
            level=level,
            categories=categories,
            labels=labels,
            confidence=confidence,
            findings=sorted(findings, key=lambda finding: finding.confidence, reverse=True),
            sample_size=len(values),
            non_null_count=len(non_null),
            null_count=len(values) - len(non_null),
            unique_count=len(set(str(value) for value in non_null)),
            recommended_controls=controls,
        )

    def _resolve_dataset_result(
        self,
        dataset_name: Optional[str],
        field_results: List[FieldClassificationResult],
        row_count: Optional[int],
    ) -> DatasetClassificationResult:
        if field_results:
            level = max((field.level for field in field_results), key=level_rank)
        else:
            level = ClassificationLevel.PUBLIC
        categories = {category for field in field_results for category in field.categories}
        controls = {control for field in field_results for control in field.recommended_controls}
        result = DatasetClassificationResult(
            dataset_name=dataset_name,
            level=level,
            categories=categories,
            fields=field_results,
            recommended_controls=controls,
            row_count=row_count,
            field_count=len(field_results),
        )
        result.audit_hash = stable_hash(result.to_dict())
        self.audit.emit("dataset_classified", result.to_dict())
        return result

    def recommend_controls(self, result: Union[FieldClassificationResult, DatasetClassificationResult]) -> List[str]:
        return sorted(result.recommended_controls)

    def mask_value(self, value: Any, strategy: MaskingStrategy = MaskingStrategy.PARTIAL, *, salt: str = "") -> Any:
        if value is None or strategy == MaskingStrategy.NONE:
            return value
        text = str(value)
        if strategy == MaskingStrategy.FULL:
            return "***REDACTED***"
        if strategy == MaskingStrategy.PARTIAL:
            if len(text) <= 4:
                return "*" * len(text)
            return text[:2] + "*" * (len(text) - 4) + text[-2:]
        if strategy == MaskingStrategy.HASH:
            return hashlib.sha256((salt + text).encode("utf-8")).hexdigest()
        if strategy == MaskingStrategy.EMAIL:
            if "@" not in text:
                return self.mask_value(text, MaskingStrategy.PARTIAL)
            local, domain = text.split("@", 1)
            return self.mask_value(local, MaskingStrategy.PARTIAL) + "@" + domain
        if strategy == MaskingStrategy.TOKENIZE:
            return "tok_" + hashlib.sha256((salt + text).encode("utf-8")).hexdigest()[:24]
        return value

    def mask_dataframe(
        self,
        dataframe: Any,
        classification: DatasetClassificationResult,
        *,
        strategy_by_level: Optional[Mapping[ClassificationLevel, MaskingStrategy]] = None,
        salt: str = "",
    ) -> Any:
        if pd is None:
            raise ClassificationError("pandas is required for mask_dataframe")
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("dataframe must be a pandas DataFrame")
        strategies = dict(strategy_by_level or {
            ClassificationLevel.PUBLIC: MaskingStrategy.NONE,
            ClassificationLevel.INTERNAL: MaskingStrategy.NONE,
            ClassificationLevel.CONFIDENTIAL: MaskingStrategy.PARTIAL,
            ClassificationLevel.RESTRICTED: MaskingStrategy.HASH,
            ClassificationLevel.HIGHLY_RESTRICTED: MaskingStrategy.FULL,
        })
        output = dataframe.copy()
        for field_result in classification.fields:
            if field_result.field_name not in output.columns:
                continue
            strategy = strategies.get(field_result.level, MaskingStrategy.PARTIAL)
            output[field_result.field_name] = output[field_result.field_name].map(lambda value: self.mask_value(value, strategy, salt=salt))
        return output

    def describe_rules(self) -> JsonDict:
        return self.registry.to_dict()


# -----------------------------------------------------------------------------
# Default rules
# -----------------------------------------------------------------------------


def default_classification_rules() -> List[ClassificationRule]:
    controls_pii = ("encrypt_at_rest", "mask_in_nonprod", "access_review", "purpose_limitation")
    controls_restricted = ("encrypt_at_rest", "encrypt_in_transit", "tokenize", "mfa_required", "access_approval", "audit_access")
    return [
        ClassificationRule(
            rule_id="col_email",
            name="Email column name",
            category=ClassificationCategory.PII,
            label="email",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"(^|_)(email|e_mail|mail)(_|$)", r"email_address"),
            recommended_controls=controls_pii,
            priority=10,
        ),
        ClassificationRule(
            rule_id="val_email",
            name="Email value pattern",
            category=ClassificationCategory.PII,
            label="email",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.VALUE_REGEX,
            value_patterns=(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",),
            min_confidence=0.70,
            match_threshold=0.20,
            recommended_controls=controls_pii,
            priority=11,
        ),
        ClassificationRule(
            rule_id="col_phone",
            name="Phone column name",
            category=ClassificationCategory.PII,
            label="phone_number",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"phone", r"telefone", r"mobile", r"celular", r"whatsapp"),
            recommended_controls=controls_pii,
            priority=20,
        ),
        ClassificationRule(
            rule_id="val_phone",
            name="Phone value pattern",
            category=ClassificationCategory.PII,
            label="phone_number",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.VALUE_REGEX,
            value_patterns=(r"(\+?\d{1,3}[\s.-]?)?(\(?\d{2,3}\)?[\s.-]?)?\d{4,5}[\s.-]?\d{4}",),
            min_confidence=0.65,
            match_threshold=0.25,
            recommended_controls=controls_pii,
            priority=21,
        ),
        ClassificationRule(
            rule_id="col_name",
            name="Person name column",
            category=ClassificationCategory.PII,
            label="person_name",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"(^|_)(name|nome|full_name|first_name|last_name|customer_name|client_name)(_|$)",),
            recommended_controls=controls_pii,
            priority=30,
        ),
        ClassificationRule(
            rule_id="col_cpf",
            name="Brazil CPF column",
            category=ClassificationCategory.PII,
            label="cpf",
            level=ClassificationLevel.RESTRICTED,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"(^|_)(cpf|documento|tax_id)(_|$)",),
            recommended_controls=controls_restricted,
            priority=40,
        ),
        ClassificationRule(
            rule_id="val_cpf",
            name="Brazil CPF value pattern",
            category=ClassificationCategory.PII,
            label="cpf",
            level=ClassificationLevel.RESTRICTED,
            rule_type=RuleType.VALUE_REGEX,
            value_patterns=(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",),
            min_confidence=0.70,
            match_threshold=0.15,
            recommended_controls=controls_restricted,
            priority=41,
        ),
        ClassificationRule(
            rule_id="val_credit_card",
            name="Credit card value pattern",
            category=ClassificationCategory.PCI,
            label="credit_card_number",
            level=ClassificationLevel.HIGHLY_RESTRICTED,
            rule_type=RuleType.VALUE_REGEX,
            value_patterns=(r"\b(?:\d[ -]*?){13,19}\b",),
            min_confidence=0.75,
            match_threshold=0.10,
            recommended_controls=("pci_scope", "tokenize", "never_log", "strict_access_approval", "audit_access"),
            priority=50,
        ),
        ClassificationRule(
            rule_id="col_card",
            name="Payment card column",
            category=ClassificationCategory.PCI,
            label="payment_card",
            level=ClassificationLevel.HIGHLY_RESTRICTED,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"credit_card", r"card_number", r"pan", r"payment_card"),
            recommended_controls=("pci_scope", "tokenize", "never_log", "strict_access_approval", "audit_access"),
            priority=51,
        ),
        ClassificationRule(
            rule_id="col_password_secret",
            name="Credential column",
            category=ClassificationCategory.CREDENTIAL,
            label="credential",
            level=ClassificationLevel.HIGHLY_RESTRICTED,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"password", r"passwd", r"secret", r"token", r"api_key", r"apikey", r"private_key", r"credential"),
            recommended_controls=("never_log", "secret_manager", "rotate_secret", "restrict_admin_only"),
            priority=5,
        ),
        ClassificationRule(
            rule_id="stat_secret_entropy",
            name="High entropy secret-like values",
            category=ClassificationCategory.CREDENTIAL,
            label="secret",
            level=ClassificationLevel.HIGHLY_RESTRICTED,
            rule_type=RuleType.STATISTICAL,
            min_confidence=0.80,
            recommended_controls=("never_log", "secret_manager", "rotate_secret", "restrict_admin_only"),
            priority=6,
        ),
        ClassificationRule(
            rule_id="col_health",
            name="Health/PHI column",
            category=ClassificationCategory.PHI,
            label="health_information",
            level=ClassificationLevel.HIGHLY_RESTRICTED,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"diagnosis", r"medical", r"patient", r"health", r"cid", r"icd", r"prescription"),
            recommended_controls=("phi_policy", "strict_access_approval", "encrypt_at_rest", "audit_access"),
            priority=60,
        ),
        ClassificationRule(
            rule_id="col_financial",
            name="Financial sensitive column",
            category=ClassificationCategory.FINANCIAL,
            label="financial_sensitive",
            level=ClassificationLevel.RESTRICTED,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"salary", r"revenue", r"profit", r"bank_account", r"iban", r"routing", r"balance"),
            recommended_controls=("encrypt_at_rest", "financial_access_review", "audit_access"),
            priority=70,
        ),
        ClassificationRule(
            rule_id="col_location",
            name="Location column",
            category=ClassificationCategory.LOCATION,
            label="location",
            level=ClassificationLevel.CONFIDENTIAL,
            rule_type=RuleType.COLUMN_NAME,
            column_patterns=(r"address", r"endereco", r"latitude", r"longitude", r"geo", r"postal", r"zipcode", r"cep"),
            recommended_controls=controls_pii,
            priority=80,
        ),
        ClassificationRule(
            rule_id="stat_identifier",
            name="Identifier-like values",
            category=ClassificationCategory.PERSONAL,
            label="identifier",
            level=ClassificationLevel.INTERNAL,
            rule_type=RuleType.STATISTICAL,
            min_confidence=0.75,
            recommended_controls=("catalog_tag", "lineage_tracking"),
            priority=200,
        ),
    ]


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


_LEVEL_RANK = {
    ClassificationLevel.PUBLIC: 0,
    ClassificationLevel.INTERNAL: 1,
    ClassificationLevel.CONFIDENTIAL: 2,
    ClassificationLevel.RESTRICTED: 3,
    ClassificationLevel.HIGHLY_RESTRICTED: 4,
}


def level_rank(level: ClassificationLevel) -> int:
    return _LEVEL_RANK[level]


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")


def compute_match_confidence(matched: int, total: int, strategy: MatchStrategy) -> float:
    if total <= 0:
        return 0.0
    ratio = matched / total
    if strategy == MatchStrategy.ANY:
        return 1.0 if matched > 0 else 0.0
    if strategy == MatchStrategy.MAJORITY:
        return min(1.0, ratio * 1.25) if ratio >= 0.5 else ratio
    return min(1.0, 0.45 + ratio * 0.55) if matched else 0.0


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def average_entropy(values: Sequence[str]) -> float:
    if not values:
        return 0.0
    return sum(shannon_entropy(value) for value in values) / len(values)


def mask_example(value: Any) -> str:
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * min(8, max(1, len(text) - 4)) + text[-2:]


def stable_hash(value: Any) -> str:
    raw = json.dumps(to_json_safe(value), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def to_json_safe(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_json_safe(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return value


# -----------------------------------------------------------------------------
# Example factory
# -----------------------------------------------------------------------------


def build_default_classification_engine() -> ClassificationEngine:
    return ClassificationEngine()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    engine = build_default_classification_engine()
    sample_records = [
        {"customer_email": "ana@example.com", "cpf": "123.456.789-09", "sales": 100.50},
        {"customer_email": "bruno@example.com", "cpf": "987.654.321-00", "sales": 220.00},
        {"customer_email": "carla@example.com", "cpf": "111.222.333-44", "sales": 90.00},
    ]
    result = engine.classify_records(sample_records, dataset_name="customers_sales")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
