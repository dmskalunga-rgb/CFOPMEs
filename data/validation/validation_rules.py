"""
data/validation/validation_rules.py

Enterprise-grade validation rules catalog and registry.

Este módulo centraliza definições reutilizáveis de regras de validação para
schema, qualidade, integridade, PII, compliance, consistência, contratos e drift.

Capacidades principais:
- Modelo declarativo genérico para regras de validação.
- Registry thread-safe para cadastro, busca e versionamento lógico de regras.
- Builders/factories para regras comuns enterprise.
- Presets de políticas por domínio: bronze/silver/gold, analytics, compliance e PII.
- Serialização/deserialização JSON para configuração externa.
- Validação de configuração das regras antes da execução.
- Tags, severidade, dimensão, escopo, owner, versão e metadados.
- Compatível com orquestradores como validation_pipeline.py.
"""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Pattern, Sequence, Set, Tuple, Union


JsonDict = Dict[str, Any]


class RuleSeverity(str, Enum):
    """Severidade operacional de uma regra."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RuleDomain(str, Enum):
    """Domínio funcional da regra."""

    SCHEMA = "SCHEMA"
    QUALITY = "QUALITY"
    INTEGRITY = "INTEGRITY"
    PII = "PII"
    COMPLIANCE = "COMPLIANCE"
    CONSISTENCY = "CONSISTENCY"
    CONTRACT = "CONTRACT"
    DRIFT = "DRIFT"
    OBSERVABILITY = "OBSERVABILITY"
    CUSTOM = "CUSTOM"


class RuleDimension(str, Enum):
    """Dimensão conceitual da validação."""

    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    VALIDITY = "VALIDITY"
    CONSISTENCY = "CONSISTENCY"
    ACCURACY = "ACCURACY"
    TIMELINESS = "TIMELINESS"
    FRESHNESS = "FRESHNESS"
    CONFORMITY = "CONFORMITY"
    INTEGRITY = "INTEGRITY"
    PRIVACY = "PRIVACY"
    SECURITY = "SECURITY"
    COMPLIANCE = "COMPLIANCE"
    OBSERVABILITY = "OBSERVABILITY"
    CUSTOM = "CUSTOM"


class RuleType(str, Enum):
    """Tipos padronizados de regras."""

    REQUIRED_COLUMNS = "REQUIRED_COLUMNS"
    FORBIDDEN_COLUMNS = "FORBIDDEN_COLUMNS"
    COLUMN_ORDER = "COLUMN_ORDER"
    TYPE_CONFORMANCE = "TYPE_CONFORMANCE"
    NOT_NULL = "NOT_NULL"
    COMPLETENESS = "COMPLETENESS"
    UNIQUE = "UNIQUE"
    PRIMARY_KEY = "PRIMARY_KEY"
    REFERENTIAL_INTEGRITY = "REFERENTIAL_INTEGRITY"
    ALLOWED_VALUES = "ALLOWED_VALUES"
    RANGE = "RANGE"
    REGEX = "REGEX"
    STRING_LENGTH = "STRING_LENGTH"
    NUMERIC_PRECISION = "NUMERIC_PRECISION"
    ROW_COUNT = "ROW_COUNT"
    FRESHNESS = "FRESHNESS"
    TIMESTAMP_NOT_FUTURE = "TIMESTAMP_NOT_FUTURE"
    OUTLIER_ZSCORE = "OUTLIER_ZSCORE"
    OUTLIER_IQR = "OUTLIER_IQR"
    CARDINALITY = "CARDINALITY"
    CROSS_FIELD_CONSISTENCY = "CROSS_FIELD_CONSISTENCY"
    CHECKSUM = "CHECKSUM"
    HASH_MATCH = "HASH_MATCH"
    MONOTONIC = "MONOTONIC"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    PII_DETECTION = "PII_DETECTION"
    PII_FORBIDDEN = "PII_FORBIDDEN"
    POLICY_REQUIRED = "POLICY_REQUIRED"
    DRIFT_THRESHOLD = "DRIFT_THRESHOLD"
    CUSTOM = "CUSTOM"


class RuleScope(str, Enum):
    """Escopo de aplicação da regra."""

    DATASET = "DATASET"
    COLUMN = "COLUMN"
    MULTI_COLUMN = "MULTI_COLUMN"
    ROW = "ROW"
    CELL = "CELL"
    RELATIONSHIP = "RELATIONSHIP"
    PIPELINE = "PIPELINE"


class RuleAction(str, Enum):
    """Ação recomendada quando a regra falha."""

    LOG = "LOG"
    WARN = "WARN"
    FAIL = "FAIL"
    QUARANTINE = "QUARANTINE"
    MASK = "MASK"
    HASH = "HASH"
    TOKENIZE = "TOKENIZE"
    SKIP_RECORD = "SKIP_RECORD"
    CUSTOM = "CUSTOM"


class RuleConfigurationError(Exception):
    """Erro de configuração inválida de regra."""


class RuleRegistryError(Exception):
    """Erro relacionado ao registry de regras."""


@dataclass(frozen=True)
class RuleThreshold:
    """Thresholds de aprovação/alerta para uma regra."""

    min_score: float = 1.0
    warning_score: Optional[float] = None
    max_errors: Optional[int] = None
    max_error_ratio: Optional[float] = None
    max_evidence: int = 50

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_score <= 1.0:
            raise RuleConfigurationError("min_score must be between 0 and 1")
        if self.warning_score is not None and not 0.0 <= self.warning_score <= 1.0:
            raise RuleConfigurationError("warning_score must be between 0 and 1")
        if self.max_error_ratio is not None and not 0.0 <= self.max_error_ratio <= 1.0:
            raise RuleConfigurationError("max_error_ratio must be between 0 and 1")
        if self.max_evidence < 0:
            raise RuleConfigurationError("max_evidence must be greater than or equal to zero")

    def to_dict(self) -> JsonDict:
        return {
            "min_score": self.min_score,
            "warning_score": self.warning_score,
            "max_errors": self.max_errors,
            "max_error_ratio": self.max_error_ratio,
            "max_evidence": self.max_evidence,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "RuleThreshold":
        return RuleThreshold(
            min_score=float(payload.get("min_score", 1.0)),
            warning_score=float(payload["warning_score"]) if payload.get("warning_score") is not None else None,
            max_errors=int(payload["max_errors"]) if payload.get("max_errors") is not None else None,
            max_error_ratio=float(payload["max_error_ratio"]) if payload.get("max_error_ratio") is not None else None,
            max_evidence=int(payload.get("max_evidence", 50)),
        )


@dataclass(frozen=True)
class ValidationRule:
    """Regra genérica e serializável de validação enterprise."""

    rule_type: RuleType
    domain: RuleDomain
    dimension: RuleDimension
    scope: RuleScope
    name: str
    rule_id: str = field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:12]}")
    version: str = "1.0.0"
    description: Optional[str] = None
    columns: Tuple[str, ...] = field(default_factory=tuple)
    params: Mapping[str, Any] = field(default_factory=dict)
    severity: RuleSeverity = RuleSeverity.ERROR
    action: RuleAction = RuleAction.FAIL
    threshold: RuleThreshold = field(default_factory=RuleThreshold)
    enabled: bool = True
    required: bool = True
    owner: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise RuleConfigurationError("ValidationRule.name is required")
        if not self.rule_id:
            raise RuleConfigurationError("ValidationRule.rule_id is required")
        if self.rule_type in {
            RuleType.NOT_NULL,
            RuleType.COMPLETENESS,
            RuleType.UNIQUE,
            RuleType.PRIMARY_KEY,
            RuleType.ALLOWED_VALUES,
            RuleType.RANGE,
            RuleType.REGEX,
            RuleType.STRING_LENGTH,
            RuleType.TYPE_CONFORMANCE,
            RuleType.MONOTONIC,
            RuleType.SEQUENCE_GAP,
        } and not self.columns:
            raise RuleConfigurationError(f"Rule {self.rule_type.value} requires at least one column")

    @property
    def key(self) -> str:
        return f"{self.domain.value}:{self.rule_id}:{self.version}"

    def with_params(self, **params: Any) -> "ValidationRule":
        merged = {**dict(self.params), **params}
        return self.replace(params=merged)

    def replace(self, **changes: Any) -> "ValidationRule":
        data = self.to_dict()
        data.update(changes)
        return ValidationRule.from_dict(data)

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "version": self.version,
            "rule_type": self.rule_type.value,
            "domain": self.domain.value,
            "dimension": self.dimension.value,
            "scope": self.scope.value,
            "description": self.description,
            "columns": list(self.columns),
            "params": safe_json_value(dict(self.params)),
            "severity": self.severity.value,
            "action": self.action.value,
            "threshold": self.threshold.to_dict(),
            "enabled": self.enabled,
            "required": self.required,
            "owner": self.owner,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
            "metadata": safe_json_value(dict(self.metadata)),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "ValidationRule":
        created_raw = payload.get("created_at")
        created_at = parse_datetime(created_raw) if created_raw else datetime.now(timezone.utc)
        return ValidationRule(
            rule_id=str(payload.get("rule_id") or f"rule_{uuid.uuid4().hex[:12]}"),
            name=str(payload["name"]),
            version=str(payload.get("version", "1.0.0")),
            rule_type=RuleType(payload["rule_type"]),
            domain=RuleDomain(payload["domain"]),
            dimension=RuleDimension(payload["dimension"]),
            scope=RuleScope(payload["scope"]),
            description=payload.get("description"),
            columns=tuple(payload.get("columns", ())),
            params=payload.get("params", {}),
            severity=RuleSeverity(payload.get("severity", RuleSeverity.ERROR.value)),
            action=RuleAction(payload.get("action", RuleAction.FAIL.value)),
            threshold=RuleThreshold.from_dict(payload.get("threshold", {})),
            enabled=bool(payload.get("enabled", True)),
            required=bool(payload.get("required", True)),
            owner=payload.get("owner"),
            tags=tuple(payload.get("tags", ())),
            created_at=created_at or datetime.now(timezone.utc),
            metadata=payload.get("metadata", {}),
        )


class ValidationRuleBuilder:
    """Factory fluente para criação de regras."""

    @staticmethod
    def required_columns(columns: Sequence[str], **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "required_columns"),
            rule_type=RuleType.REQUIRED_COLUMNS,
            domain=RuleDomain.SCHEMA,
            dimension=RuleDimension.CONFORMITY,
            scope=RuleScope.DATASET,
            columns=tuple(columns),
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            action=kwargs.pop("action", RuleAction.FAIL),
            params={"required_columns": tuple(columns), **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def forbidden_columns(columns: Sequence[str], **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "forbidden_columns"),
            rule_type=RuleType.FORBIDDEN_COLUMNS,
            domain=RuleDomain.SCHEMA,
            dimension=RuleDimension.SECURITY,
            scope=RuleScope.DATASET,
            columns=tuple(columns),
            severity=kwargs.pop("severity", RuleSeverity.CRITICAL),
            action=kwargs.pop("action", RuleAction.FAIL),
            params={"forbidden_columns": tuple(columns), **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def not_null(columns: Sequence[str], min_score: float = 1.0, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "not_null"),
            rule_type=RuleType.NOT_NULL,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.COMPLETENESS,
            scope=RuleScope.MULTI_COLUMN,
            columns=tuple(columns),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_score)),
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            params={"min_ratio": min_score, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def completeness(columns: Sequence[str], min_ratio: float = 0.99, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "completeness"),
            rule_type=RuleType.COMPLETENESS,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.COMPLETENESS,
            scope=RuleScope.MULTI_COLUMN,
            columns=tuple(columns),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_ratio)),
            params={"min_ratio": min_ratio, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def unique(columns: Sequence[str], min_ratio: float = 1.0, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "unique"),
            rule_type=RuleType.UNIQUE,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.UNIQUENESS,
            scope=RuleScope.MULTI_COLUMN,
            columns=tuple(columns),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_ratio)),
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            params={"min_ratio": min_ratio, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def primary_key(columns: Sequence[str], **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "primary_key"),
            rule_type=RuleType.PRIMARY_KEY,
            domain=RuleDomain.INTEGRITY,
            dimension=RuleDimension.INTEGRITY,
            scope=RuleScope.MULTI_COLUMN,
            columns=tuple(columns),
            severity=kwargs.pop("severity", RuleSeverity.CRITICAL),
            action=kwargs.pop("action", RuleAction.FAIL),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=1.0)),
            params={"primary_key": tuple(columns), **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def allowed_values(column: str, values: Iterable[Any], min_ratio: float = 1.0, **kwargs: Any) -> ValidationRule:
        values_tuple = tuple(values)
        return ValidationRule(
            name=kwargs.pop("name", f"allowed_values_{column}"),
            rule_type=RuleType.ALLOWED_VALUES,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.VALIDITY,
            scope=RuleScope.COLUMN,
            columns=(column,),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_ratio)),
            params={"values": values_tuple, "min_ratio": min_ratio, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def range(column: str, min_value: Optional[float] = None, max_value: Optional[float] = None, min_ratio: float = 1.0, **kwargs: Any) -> ValidationRule:
        if min_value is None and max_value is None:
            raise RuleConfigurationError("range rule requires min_value or max_value")
        return ValidationRule(
            name=kwargs.pop("name", f"range_{column}"),
            rule_type=RuleType.RANGE,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.VALIDITY,
            scope=RuleScope.COLUMN,
            columns=(column,),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_ratio)),
            params={"min_value": min_value, "max_value": max_value, "min_ratio": min_ratio, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def regex(column: str, pattern: Union[str, Pattern[str]], min_ratio: float = 1.0, **kwargs: Any) -> ValidationRule:
        pattern_text = pattern.pattern if hasattr(pattern, "pattern") else str(pattern)
        return ValidationRule(
            name=kwargs.pop("name", f"regex_{column}"),
            rule_type=RuleType.REGEX,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.CONFORMITY,
            scope=RuleScope.COLUMN,
            columns=(column,),
            threshold=kwargs.pop("threshold", RuleThreshold(min_score=min_ratio)),
            params={"pattern": pattern_text, "min_ratio": min_ratio, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def row_count(min_rows: Optional[int] = None, max_rows: Optional[int] = None, expected_rows: Optional[int] = None, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "row_count"),
            rule_type=RuleType.ROW_COUNT,
            domain=RuleDomain.OBSERVABILITY,
            dimension=RuleDimension.OBSERVABILITY,
            scope=RuleScope.DATASET,
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            params={"min_rows": min_rows, "max_rows": max_rows, "expected_rows": expected_rows, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def freshness(timestamp_column: str, max_age_seconds: int, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", f"freshness_{timestamp_column}"),
            rule_type=RuleType.FRESHNESS,
            domain=RuleDomain.QUALITY,
            dimension=RuleDimension.FRESHNESS,
            scope=RuleScope.COLUMN,
            columns=(timestamp_column,),
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            params={"max_age_seconds": max_age_seconds, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def referential_integrity(columns: Sequence[str], reference: str, reference_columns: Sequence[str], **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "referential_integrity"),
            rule_type=RuleType.REFERENTIAL_INTEGRITY,
            domain=RuleDomain.INTEGRITY,
            dimension=RuleDimension.INTEGRITY,
            scope=RuleScope.RELATIONSHIP,
            columns=tuple(columns),
            severity=kwargs.pop("severity", RuleSeverity.ERROR),
            params={"reference": reference, "reference_columns": tuple(reference_columns), **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def pii_forbidden(columns: Optional[Sequence[str]] = None, pii_types: Optional[Sequence[str]] = None, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", "pii_forbidden"),
            rule_type=RuleType.PII_FORBIDDEN,
            domain=RuleDomain.PII,
            dimension=RuleDimension.PRIVACY,
            scope=RuleScope.DATASET if not columns else RuleScope.MULTI_COLUMN,
            columns=tuple(columns or ()),
            severity=kwargs.pop("severity", RuleSeverity.CRITICAL),
            action=kwargs.pop("action", RuleAction.FAIL),
            params={"pii_types": tuple(pii_types or ()), **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def drift_threshold(metric: str, max_drift_score: float, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=kwargs.pop("name", f"drift_{metric}"),
            rule_type=RuleType.DRIFT_THRESHOLD,
            domain=RuleDomain.DRIFT,
            dimension=RuleDimension.CONSISTENCY,
            scope=RuleScope.DATASET,
            severity=kwargs.pop("severity", RuleSeverity.WARNING),
            params={"metric": metric, "max_drift_score": max_drift_score, **kwargs.pop("params", {})},
            **kwargs,
        )

    @staticmethod
    def custom(name: str, params: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> ValidationRule:
        return ValidationRule(
            name=name,
            rule_type=kwargs.pop("rule_type", RuleType.CUSTOM),
            domain=kwargs.pop("domain", RuleDomain.CUSTOM),
            dimension=kwargs.pop("dimension", RuleDimension.CUSTOM),
            scope=kwargs.pop("scope", RuleScope.DATASET),
            params=params or {},
            **kwargs,
        )


class ValidationRuleRegistry:
    """Registry thread-safe para regras de validação."""

    def __init__(self) -> None:
        self._rules: MutableMapping[str, ValidationRule] = {}
        self._aliases: MutableMapping[str, str] = {}
        self._lock = threading.RLock()

    def register(self, rule: ValidationRule, *, alias: Optional[str] = None, replace: bool = False) -> None:
        with self._lock:
            if rule.key in self._rules and not replace:
                raise RuleRegistryError(f"Rule already registered: {rule.key}")
            self._rules[rule.key] = rule
            self._aliases[rule.rule_id] = rule.key
            self._aliases[rule.name] = rule.key
            if alias:
                self._aliases[alias] = rule.key

    def unregister(self, key_or_alias: str) -> None:
        with self._lock:
            key = self._aliases.get(key_or_alias, key_or_alias)
            if key not in self._rules:
                raise RuleRegistryError(f"Rule not found: {key_or_alias}")
            rule = self._rules.pop(key)
            for alias_key in [rule.rule_id, rule.name, key_or_alias]:
                self._aliases.pop(alias_key, None)

    def get(self, key_or_alias: str) -> ValidationRule:
        with self._lock:
            key = self._aliases.get(key_or_alias, key_or_alias)
            if key not in self._rules:
                raise RuleRegistryError(f"Rule not found: {key_or_alias}")
            return self._rules[key]

    def list(
        self,
        *,
        domain: Optional[RuleDomain] = None,
        dimension: Optional[RuleDimension] = None,
        rule_type: Optional[RuleType] = None,
        enabled_only: bool = False,
        tags: Optional[Iterable[str]] = None,
    ) -> List[ValidationRule]:
        wanted_tags = set(tags or ())
        with self._lock:
            values = list(self._rules.values())
        if domain is not None:
            values = [rule for rule in values if rule.domain == domain]
        if dimension is not None:
            values = [rule for rule in values if rule.dimension == dimension]
        if rule_type is not None:
            values = [rule for rule in values if rule.rule_type == rule_type]
        if enabled_only:
            values = [rule for rule in values if rule.enabled]
        if wanted_tags:
            values = [rule for rule in values if wanted_tags.issubset(set(rule.tags))]
        return sorted(values, key=lambda rule: (rule.domain.value, rule.name, rule.version))

    def to_dict(self) -> JsonDict:
        with self._lock:
            return {"rules": [rule.to_dict() for rule in self._rules.values()]}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def save_json(self, path: Union[str, Path]) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.to_json(), encoding="utf-8")
        return output

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "ValidationRuleRegistry":
        registry = ValidationRuleRegistry()
        for item in payload.get("rules", []):
            registry.register(ValidationRule.from_dict(item), replace=True)
        return registry

    @staticmethod
    def load_json(path: Union[str, Path]) -> "ValidationRuleRegistry":
        input_path = Path(path)
        if not input_path.exists():
            raise RuleRegistryError(f"Rule registry file not found: {input_path}")
        return ValidationRuleRegistry.from_dict(json.loads(input_path.read_text(encoding="utf-8")))


class RulePresetFactory:
    """Presets enterprise de regras para camadas e domínios comuns."""

    @staticmethod
    def bronze_minimum(required_columns: Sequence[str], *, min_rows: int = 1) -> List[ValidationRule]:
        return [
            ValidationRuleBuilder.required_columns(required_columns, name="bronze_required_columns", tags=("bronze", "schema")),
            ValidationRuleBuilder.row_count(min_rows=min_rows, name="bronze_minimum_rows", tags=("bronze", "observability")),
        ]

    @staticmethod
    def silver_standard(primary_key: Sequence[str], required_columns: Sequence[str], *, min_rows: int = 1) -> List[ValidationRule]:
        return [
            *RulePresetFactory.bronze_minimum(required_columns, min_rows=min_rows),
            ValidationRuleBuilder.primary_key(primary_key, name="silver_primary_key", tags=("silver", "integrity")),
            ValidationRuleBuilder.not_null(required_columns, name="silver_required_not_null", tags=("silver", "quality")),
        ]

    @staticmethod
    def gold_analytics(
        primary_key: Sequence[str],
        required_columns: Sequence[str],
        timestamp_column: Optional[str] = None,
        freshness_seconds: Optional[int] = None,
    ) -> List[ValidationRule]:
        rules = RulePresetFactory.silver_standard(primary_key, required_columns)
        rules.append(
            ValidationRuleBuilder.completeness(
                required_columns,
                min_ratio=0.995,
                name="gold_required_completeness",
                tags=("gold", "quality"),
                severity=RuleSeverity.ERROR,
            )
        )
        if timestamp_column and freshness_seconds:
            rules.append(
                ValidationRuleBuilder.freshness(
                    timestamp_column,
                    freshness_seconds,
                    name="gold_freshness_sla",
                    tags=("gold", "freshness"),
                )
            )
        return rules

    @staticmethod
    def pii_guardrails(forbidden_columns: Optional[Sequence[str]] = None, pii_types: Optional[Sequence[str]] = None) -> List[ValidationRule]:
        rules = [ValidationRuleBuilder.pii_forbidden(pii_types=pii_types, name="pii_detection_guardrail", tags=("pii", "privacy", "guardrail"))]
        if forbidden_columns:
            rules.append(
                ValidationRuleBuilder.forbidden_columns(
                    forbidden_columns,
                    name="forbidden_sensitive_columns",
                    tags=("pii", "security", "schema"),
                )
            )
        return rules

    @staticmethod
    def compliance_required(*, legal_basis_required: bool = True, retention_required: bool = True) -> List[ValidationRule]:
        rules: List[ValidationRule] = []
        if legal_basis_required:
            rules.append(
                ValidationRuleBuilder.custom(
                    "legal_basis_required",
                    rule_type=RuleType.POLICY_REQUIRED,
                    domain=RuleDomain.COMPLIANCE,
                    dimension=RuleDimension.COMPLIANCE,
                    scope=RuleScope.DATASET,
                    severity=RuleSeverity.CRITICAL,
                    action=RuleAction.FAIL,
                    params={"policy": "legal_basis_required"},
                    tags=("compliance", "legal_basis"),
                )
            )
        if retention_required:
            rules.append(
                ValidationRuleBuilder.custom(
                    "retention_policy_required",
                    rule_type=RuleType.POLICY_REQUIRED,
                    domain=RuleDomain.COMPLIANCE,
                    dimension=RuleDimension.COMPLIANCE,
                    scope=RuleScope.DATASET,
                    severity=RuleSeverity.ERROR,
                    action=RuleAction.FAIL,
                    params={"policy": "retention_required"},
                    tags=("compliance", "retention"),
                )
            )
        return rules


class RuleConfigValidator:
    """Validador de configuração das regras antes da execução."""

    REQUIRED_PARAMS_BY_TYPE: Mapping[RuleType, Tuple[str, ...]] = {
        RuleType.ALLOWED_VALUES: ("values",),
        RuleType.REGEX: ("pattern",),
        RuleType.FRESHNESS: ("max_age_seconds",),
        RuleType.REFERENTIAL_INTEGRITY: ("reference", "reference_columns"),
        RuleType.CHECKSUM: ("expected_checksum",),
        RuleType.HASH_MATCH: ("hash_column",),
        RuleType.DRIFT_THRESHOLD: ("metric", "max_drift_score"),
    }

    @classmethod
    def validate(cls, rule: ValidationRule) -> None:
        missing = [param for param in cls.REQUIRED_PARAMS_BY_TYPE.get(rule.rule_type, ()) if param not in rule.params]
        if missing:
            raise RuleConfigurationError(f"Rule {rule.rule_id} missing required params: {missing}")
        if rule.rule_type == RuleType.RANGE and rule.params.get("min_value") is None and rule.params.get("max_value") is None:
            raise RuleConfigurationError(f"Rule {rule.rule_id} requires min_value or max_value")
        if rule.rule_type == RuleType.REGEX:
            try:
                re.compile(str(rule.params["pattern"]))
            except re.error as exc:
                raise RuleConfigurationError(f"Rule {rule.rule_id} has invalid regex: {exc}") from exc
        if rule.weight if hasattr(rule, "weight") else False:  # defensive compatibility guard
            pass

    @classmethod
    def validate_all(cls, rules: Sequence[ValidationRule]) -> None:
        seen: Set[str] = set()
        for rule in rules:
            if rule.key in seen:
                raise RuleConfigurationError(f"Duplicated rule key: {rule.key}")
            seen.add(rule.key)
            cls.validate(rule)


def rules_to_json(rules: Sequence[ValidationRule], indent: int = 2) -> str:
    return json.dumps([rule.to_dict() for rule in rules], ensure_ascii=False, indent=indent, default=str)


def rules_from_json(payload: Union[str, Sequence[Mapping[str, Any]]]) -> List[ValidationRule]:
    raw = json.loads(payload) if isinstance(payload, str) else payload
    return [ValidationRule.from_dict(item) for item in raw]


def load_rules_json(path: Union[str, Path]) -> List[ValidationRule]:
    input_path = Path(path)
    if not input_path.exists():
        raise RuleRegistryError(f"Rules file not found: {input_path}")
    return rules_from_json(input_path.read_text(encoding="utf-8"))


def save_rules_json(rules: Sequence[ValidationRule], path: Union[str, Path], indent: int = 2) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rules_to_json(rules, indent=indent), encoding="utf-8")
    return output_path


def group_rules_by_domain(rules: Sequence[ValidationRule]) -> Dict[RuleDomain, List[ValidationRule]]:
    grouped: Dict[RuleDomain, List[ValidationRule]] = defaultdict(list)
    for rule in rules:
        grouped[rule.domain].append(rule)
    return dict(grouped)


def filter_rules(
    rules: Sequence[ValidationRule],
    *,
    domain: Optional[RuleDomain] = None,
    dimension: Optional[RuleDimension] = None,
    rule_type: Optional[RuleType] = None,
    enabled_only: bool = True,
    tags: Optional[Iterable[str]] = None,
) -> List[ValidationRule]:
    wanted_tags = set(tags or ())
    result = list(rules)
    if domain is not None:
        result = [rule for rule in result if rule.domain == domain]
    if dimension is not None:
        result = [rule for rule in result if rule.dimension == dimension]
    if rule_type is not None:
        result = [rule for rule in result if rule.rule_type == rule_type]
    if enabled_only:
        result = [rule for rule in result if rule.enabled]
    if wanted_tags:
        result = [rule for rule in result if wanted_tags.issubset(set(rule.tags))]
    return result


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


__all__ = [
    "RuleAction",
    "RuleConfigValidator",
    "RuleConfigurationError",
    "RuleDimension",
    "RuleDomain",
    "RulePresetFactory",
    "RuleRegistryError",
    "RuleScope",
    "RuleSeverity",
    "RuleThreshold",
    "RuleType",
    "ValidationRule",
    "ValidationRuleBuilder",
    "ValidationRuleRegistry",
    "filter_rules",
    "group_rules_by_domain",
    "load_rules_json",
    "parse_datetime",
    "rules_from_json",
    "rules_to_json",
    "safe_json_value",
    "save_rules_json",
]
