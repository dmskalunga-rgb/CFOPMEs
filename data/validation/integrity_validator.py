"""
data/validation/integrity_validator.py

Enterprise-grade data integrity validator.

Este módulo fornece uma camada robusta para validação de integridade de dados em
pipelines batch, streaming, lakehouse, data warehouse, APIs internas e processos
de governança de dados.

Principais capacidades:
- Validação de chaves primárias, unicidade, nulos e duplicidades.
- Validação de integridade referencial entre datasets.
- Validação de checksums, hashes e assinaturas de payload.
- Validação de ranges, domínios permitidos e consistência estrutural.
- Validação de monotonicidade, sequência e gaps em séries temporais.
- Motor de regras plugável e extensível.
- Relatório estruturado com severidade, estatísticas e evidências.
- Suporte a pandas DataFrame sem dependência obrigatória em bancos externos.
- Hooks para auditoria, observabilidade e integração com métricas.
- Design defensivo, tipado e preparado para arquitetura enterprise.

Exemplo básico:
    validator = IntegrityValidator()
    result = validator.validate(
        dataset=df,
        rules=[
            IntegrityRule.primary_key(columns=["id"]),
            IntegrityRule.not_null(columns=["id", "created_at"]),
            IntegrityRule.allowed_values(column="status", values={"ACTIVE", "INACTIVE"}),
        ],
        context=ValidationContext(dataset_name="customers")
    )

    if not result.is_valid:
        raise DataIntegrityError(result.summary())
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
)

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover - pandas pode não estar instalado em alguns workers
    pd = None  # type: ignore


logger = logging.getLogger(__name__)


JsonDict = Dict[str, Any]
DataLike = Union["pd.DataFrame", Sequence[Mapping[str, Any]]]
RuleCallable = Callable[[DataLike, "IntegrityRule", "ValidationContext"], "RuleValidationResult"]


class Severity(str, Enum):
    """Nível de impacto de uma violação de integridade."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RuleType(str, Enum):
    """Tipos nativos de regras de integridade."""

    PRIMARY_KEY = "PRIMARY_KEY"
    UNIQUE = "UNIQUE"
    NOT_NULL = "NOT_NULL"
    REFERENTIAL_INTEGRITY = "REFERENTIAL_INTEGRITY"
    CHECKSUM = "CHECKSUM"
    HASH_MATCH = "HASH_MATCH"
    ALLOWED_VALUES = "ALLOWED_VALUES"
    RANGE = "RANGE"
    REGEX = "REGEX"
    TYPE_CONFORMANCE = "TYPE_CONFORMANCE"
    ROW_COUNT = "ROW_COUNT"
    SCHEMA_COLUMNS = "SCHEMA_COLUMNS"
    MONOTONIC = "MONOTONIC"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    CUSTOM = "CUSTOM"


class IntegrityStatus(str, Enum):
    """Status de execução de uma regra."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class DataIntegrityError(Exception):
    """Erro de alto nível para falhas de integridade bloqueantes."""


class IntegrityConfigurationError(Exception):
    """Erro de configuração inválida de regras ou contexto."""


class AuditSink(Protocol):
    """Contrato para persistência/encaminhamento de auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Envia um evento de auditoria."""


class MetricsSink(Protocol):
    """Contrato para publicação de métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica valor numérico instantâneo."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica métrica de tempo."""


@dataclass(frozen=True)
class ValidationContext:
    """Contexto operacional de uma execução de validação."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    correlation_id: Optional[str] = None
    execution_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def tags(self) -> Dict[str, str]:
        return {
            "dataset": self.dataset_name,
            "pipeline": self.pipeline_name or "unknown",
            "environment": self.environment,
            "tenant": self.tenant_id or "default",
            "source": self.source_system or "unknown",
        }


@dataclass(frozen=True)
class IntegrityViolation:
    """Representa uma violação individual detectada por uma regra."""

    rule_id: str
    rule_type: RuleType
    severity: Severity
    message: str
    column: Optional[str] = None
    row_index: Optional[Any] = None
    offending_value: Optional[Any] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "column": self.column,
            "row_index": self.row_index,
            "offending_value": _safe_json_value(self.offending_value),
            "evidence": _safe_json_value(dict(self.evidence)),
        }


@dataclass(frozen=True)
class RuleValidationResult:
    """Resultado da execução de uma regra."""

    rule_id: str
    rule_type: RuleType
    status: IntegrityStatus
    severity: Severity
    checked_rows: int
    violations: Tuple[IntegrityViolation, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_valid(self) -> bool:
        return self.status == IntegrityStatus.PASSED

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "checked_rows": self.checked_rows,
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
            "metrics": _safe_json_value(dict(self.metrics)),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class IntegrityValidationResult:
    """Resultado consolidado da validação de integridade."""

    context: ValidationContext
    status: IntegrityStatus
    rule_results: Tuple[RuleValidationResult, ...]
    started_at: datetime
    finished_at: datetime
    dataset_rows: int
    dataset_columns: Tuple[str, ...]

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_valid(self) -> bool:
        return self.status == IntegrityStatus.PASSED

    @property
    def violations(self) -> Tuple[IntegrityViolation, ...]:
        values: List[IntegrityViolation] = []
        for result in self.rule_results:
            values.extend(result.violations)
        return tuple(values)

    def summary(self) -> str:
        counts = Counter(r.status.value for r in self.rule_results)
        return (
            f"IntegrityValidationResult(dataset={self.context.dataset_name}, "
            f"status={self.status.value}, rows={self.dataset_rows}, "
            f"rules={len(self.rule_results)}, passed={counts.get('PASSED', 0)}, "
            f"failed={counts.get('FAILED', 0)}, errors={counts.get('ERROR', 0)}, "
            f"violations={len(self.violations)}, duration_ms={self.duration_ms:.2f})"
        )

    def to_dict(self) -> JsonDict:
        return {
            "context": {
                "dataset_name": self.context.dataset_name,
                "pipeline_name": self.context.pipeline_name,
                "environment": self.context.environment,
                "run_id": self.context.run_id,
                "tenant_id": self.context.tenant_id,
                "source_system": self.context.source_system,
                "correlation_id": self.context.correlation_id,
                "execution_ts": self.context.execution_ts.isoformat(),
                "metadata": _safe_json_value(dict(self.context.metadata)),
            },
            "status": self.status.value,
            "dataset_rows": self.dataset_rows,
            "dataset_columns": list(self.dataset_columns),
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "rule_results": [r.to_dict() for r in self.rule_results],
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def raise_for_critical(self) -> None:
        blocking = [
            violation
            for violation in self.violations
            if violation.severity in {Severity.ERROR, Severity.CRITICAL}
        ]
        if blocking:
            raise DataIntegrityError(self.summary())


@dataclass(frozen=True)
class IntegrityRule:
    """Definição declarativa de uma regra de integridade."""

    rule_type: RuleType
    rule_id: str = field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:12]}")
    columns: Tuple[str, ...] = field(default_factory=tuple)
    severity: Severity = Severity.ERROR
    enabled: bool = True
    description: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)
    max_evidence: int = 50

    @staticmethod
    def primary_key(columns: Sequence[str], **kwargs: Any) -> "IntegrityRule":
        return IntegrityRule(RuleType.PRIMARY_KEY, columns=tuple(columns), **kwargs)

    @staticmethod
    def unique(columns: Sequence[str], **kwargs: Any) -> "IntegrityRule":
        return IntegrityRule(RuleType.UNIQUE, columns=tuple(columns), **kwargs)

    @staticmethod
    def not_null(columns: Sequence[str], **kwargs: Any) -> "IntegrityRule":
        return IntegrityRule(RuleType.NOT_NULL, columns=tuple(columns), **kwargs)

    @staticmethod
    def allowed_values(column: str, values: Iterable[Any], **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params["values"] = set(values)
        return IntegrityRule(RuleType.ALLOWED_VALUES, columns=(column,), params=params, **kwargs)

    @staticmethod
    def range(column: str, min_value: Optional[float] = None, max_value: Optional[float] = None, **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_value": min_value, "max_value": max_value})
        return IntegrityRule(RuleType.RANGE, columns=(column,), params=params, **kwargs)

    @staticmethod
    def schema_columns(required_columns: Sequence[str], allow_extra: bool = True, **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"required_columns": tuple(required_columns), "allow_extra": allow_extra})
        return IntegrityRule(RuleType.SCHEMA_COLUMNS, params=params, **kwargs)

    @staticmethod
    def row_count(min_rows: Optional[int] = None, max_rows: Optional[int] = None, expected_rows: Optional[int] = None, **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_rows": min_rows, "max_rows": max_rows, "expected_rows": expected_rows})
        return IntegrityRule(RuleType.ROW_COUNT, params=params, **kwargs)

    @staticmethod
    def referential_integrity(
        columns: Sequence[str],
        reference_dataset: DataLike,
        reference_columns: Sequence[str],
        **kwargs: Any,
    ) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"reference_dataset": reference_dataset, "reference_columns": tuple(reference_columns)})
        return IntegrityRule(RuleType.REFERENTIAL_INTEGRITY, columns=tuple(columns), params=params, **kwargs)

    @staticmethod
    def checksum(columns: Sequence[str], expected_checksum: str, algorithm: str = "sha256", **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"expected_checksum": expected_checksum, "algorithm": algorithm})
        return IntegrityRule(RuleType.CHECKSUM, columns=tuple(columns), params=params, **kwargs)

    @staticmethod
    def monotonic(column: str, increasing: bool = True, strict: bool = False, **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"increasing": increasing, "strict": strict})
        return IntegrityRule(RuleType.MONOTONIC, columns=(column,), params=params, **kwargs)

    @staticmethod
    def sequence_gap(column: str, step: Union[int, float] = 1, **kwargs: Any) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"step": step})
        return IntegrityRule(RuleType.SEQUENCE_GAP, columns=(column,), params=params, **kwargs)

    @staticmethod
    def custom(
        validator: RuleCallable,
        columns: Sequence[str] = (),
        rule_id: Optional[str] = None,
        **kwargs: Any,
    ) -> "IntegrityRule":
        params = dict(kwargs.pop("params", {}))
        params["validator"] = validator
        return IntegrityRule(
            RuleType.CUSTOM,
            rule_id=rule_id or f"custom_{uuid.uuid4().hex[:12]}",
            columns=tuple(columns),
            params=params,
            **kwargs,
        )


class IntegrityValidator:
    """Motor enterprise para validação de integridade de dados."""

    def __init__(
        self,
        *,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        fail_fast: bool = False,
        max_global_evidence: int = 500,
        strict_columns: bool = True,
    ) -> None:
        self.audit_sink = audit_sink
        self.metrics_sink = metrics_sink
        self.fail_fast = fail_fast
        self.max_global_evidence = max_global_evidence
        self.strict_columns = strict_columns
        self._handlers: Dict[RuleType, RuleCallable] = {
            RuleType.PRIMARY_KEY: self._validate_primary_key,
            RuleType.UNIQUE: self._validate_unique,
            RuleType.NOT_NULL: self._validate_not_null,
            RuleType.REFERENTIAL_INTEGRITY: self._validate_referential_integrity,
            RuleType.CHECKSUM: self._validate_checksum,
            RuleType.HASH_MATCH: self._validate_hash_match,
            RuleType.ALLOWED_VALUES: self._validate_allowed_values,
            RuleType.RANGE: self._validate_range,
            RuleType.ROW_COUNT: self._validate_row_count,
            RuleType.SCHEMA_COLUMNS: self._validate_schema_columns,
            RuleType.MONOTONIC: self._validate_monotonic,
            RuleType.SEQUENCE_GAP: self._validate_sequence_gap,
            RuleType.CUSTOM: self._validate_custom,
        }

    def register_handler(self, rule_type: RuleType, handler: RuleCallable) -> None:
        """Registra ou sobrescreve um handler para um tipo de regra."""
        self._handlers[rule_type] = handler

    def validate(
        self,
        dataset: DataLike,
        rules: Sequence[IntegrityRule],
        context: ValidationContext,
    ) -> IntegrityValidationResult:
        """Executa uma lista de regras de integridade contra um dataset."""
        started = datetime.now(timezone.utc)
        start_monotonic = time.perf_counter()

        df = self._to_dataframe(dataset)
        self._validate_context_and_rules(df, rules, context)

        results: List[RuleValidationResult] = []
        global_violations = 0

        self._emit_audit(
            "integrity_validation_started",
            context,
            {
                "rule_count": len(rules),
                "row_count": len(df),
                "columns": list(df.columns),
            },
        )

        for rule in rules:
            if not rule.enabled:
                results.append(
                    RuleValidationResult(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        status=IntegrityStatus.SKIPPED,
                        severity=rule.severity,
                        checked_rows=len(df),
                        metrics={"reason": "disabled"},
                    )
                )
                continue

            handler = self._handlers.get(rule.rule_type)
            if handler is None:
                results.append(self._error_result(rule, len(df), f"No handler registered for rule type {rule.rule_type.value}"))
                if self.fail_fast:
                    break
                continue

            try:
                result = handler(df, rule, context)
                if global_violations + len(result.violations) > self.max_global_evidence:
                    result = self._trim_result_evidence(result, self.max_global_evidence - global_violations)
                global_violations += len(result.violations)
                results.append(result)

                self._publish_rule_metrics(result, context)

                if self.fail_fast and result.status in {IntegrityStatus.FAILED, IntegrityStatus.ERROR}:
                    break
            except Exception as exc:  # pragma: no cover - proteção enterprise
                logger.exception("Integrity rule failed unexpectedly: %s", rule.rule_id)
                result = self._error_result(rule, len(df), str(exc))
                results.append(result)
                self._publish_rule_metrics(result, context)
                if self.fail_fast:
                    break

        final_status = self._compute_final_status(results)
        finished = datetime.now(timezone.utc)
        consolidated = IntegrityValidationResult(
            context=context,
            status=final_status,
            rule_results=tuple(results),
            started_at=started,
            finished_at=finished,
            dataset_rows=len(df),
            dataset_columns=tuple(str(c) for c in df.columns),
        )

        elapsed_ms = (time.perf_counter() - start_monotonic) * 1000.0
        self._publish_global_metrics(consolidated, elapsed_ms)
        self._emit_audit(
            "integrity_validation_finished",
            context,
            {
                "status": consolidated.status.value,
                "duration_ms": consolidated.duration_ms,
                "violation_count": len(consolidated.violations),
                "summary": consolidated.summary(),
            },
        )

        return consolidated

    def calculate_checksum(
        self,
        dataset: DataLike,
        columns: Optional[Sequence[str]] = None,
        algorithm: str = "sha256",
    ) -> str:
        """Calcula checksum determinístico de um dataset ou subconjunto de colunas."""
        df = self._to_dataframe(dataset)
        selected = list(columns) if columns else list(df.columns)
        self._ensure_columns(df, selected)
        payload = self._canonical_dataset_payload(df[selected])
        digest = hashlib.new(algorithm)
        digest.update(payload.encode("utf-8"))
        return digest.hexdigest()

    def _validate_primary_key(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)

        violations: List[IntegrityViolation] = []
        violations.extend(self._null_violations(df, rule, message_prefix="Primary key contains null"))
        duplicate_violations = self._duplicate_violations(df, rule, message_prefix="Primary key duplicate")
        violations.extend(duplicate_violations)

        metrics = {
            "null_violations": len(violations) - len(duplicate_violations),
            "duplicate_violations": len(duplicate_violations),
            "key_columns": list(rule.columns),
        }
        return self._build_result(rule, started, len(df), violations, metrics)

    def _validate_unique(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        violations = self._duplicate_violations(df, rule, message_prefix="Unique constraint duplicate")
        return self._build_result(rule, started, len(df), violations, {"unique_columns": list(rule.columns)})

    def _validate_not_null(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        violations = self._null_violations(df, rule, message_prefix="Null value detected")
        return self._build_result(rule, started, len(df), violations, {"columns": list(rule.columns)})

    def _validate_referential_integrity(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        ref_df = self._to_dataframe(rule.params.get("reference_dataset"))
        ref_cols = tuple(rule.params.get("reference_columns", ()))

        if not ref_cols:
            raise IntegrityConfigurationError("reference_columns is required for REFERENTIAL_INTEGRITY")
        if len(ref_cols) != len(rule.columns):
            raise IntegrityConfigurationError("columns and reference_columns must have the same length")

        self._ensure_columns(df, rule.columns)
        self._ensure_columns(ref_df, ref_cols)

        left_keys = self._key_tuples(df, rule.columns, drop_null=True)
        right_keys = set(self._key_tuples(ref_df, ref_cols, drop_null=True))
        violations: List[IntegrityViolation] = []

        for idx, key in left_keys:
            if key not in right_keys:
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message="Referential integrity violation: key not found in reference dataset",
                        row_index=idx,
                        evidence={"key": key, "columns": list(rule.columns), "reference_columns": list(ref_cols)},
                    )
                )
                if len(violations) >= rule.max_evidence:
                    break

        metrics = {
            "reference_rows": len(ref_df),
            "checked_non_null_keys": len(left_keys),
            "missing_keys": len(violations),
        }
        return self._build_result(rule, started, len(df), violations, metrics)

    def _validate_checksum(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        columns = rule.columns or tuple(df.columns)
        expected = rule.params.get("expected_checksum")
        algorithm = str(rule.params.get("algorithm", "sha256"))

        if not expected:
            raise IntegrityConfigurationError("expected_checksum is required for CHECKSUM")

        actual = self.calculate_checksum(df, columns=columns, algorithm=algorithm)
        violations: List[IntegrityViolation] = []
        if actual != expected:
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Dataset checksum does not match expected checksum",
                    evidence={"expected": expected, "actual": actual, "algorithm": algorithm, "columns": list(columns)},
                )
            )
        return self._build_result(rule, started, len(df), violations, {"actual_checksum": actual, "algorithm": algorithm})

    def _validate_hash_match(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        source_columns = tuple(rule.params.get("source_columns", rule.columns))
        hash_column = rule.params.get("hash_column")
        algorithm = str(rule.params.get("algorithm", "sha256"))

        if not hash_column:
            raise IntegrityConfigurationError("hash_column is required for HASH_MATCH")

        self._ensure_columns(df, source_columns)
        self._ensure_columns(df, [hash_column])

        violations: List[IntegrityViolation] = []
        for idx, row in df.iterrows():
            expected = row[hash_column]
            payload = "|".join(_canonical_value(row[col]) for col in source_columns)
            actual = hashlib.new(algorithm, payload.encode("utf-8")).hexdigest()
            if str(expected) != actual:
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message="Row hash does not match expected hash column",
                        row_index=idx,
                        column=str(hash_column),
                        offending_value=expected,
                        evidence={"actual": actual, "source_columns": list(source_columns)},
                    )
                )
                if len(violations) >= rule.max_evidence:
                    break

        return self._build_result(rule, started, len(df), violations, {"algorithm": algorithm, "hash_column": hash_column})

    def _validate_allowed_values(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        allowed = set(rule.params.get("values", set()))
        if not allowed:
            raise IntegrityConfigurationError("values is required for ALLOWED_VALUES")

        column = rule.columns[0]
        violations: List[IntegrityViolation] = []
        invalid_counter: Counter[Any] = Counter()

        for idx, value in df[column].items():
            if _is_null(value):
                continue
            if value not in allowed:
                invalid_counter[value] += 1
                if len(violations) < rule.max_evidence:
                    violations.append(
                        IntegrityViolation(
                            rule_id=rule.rule_id,
                            rule_type=rule.rule_type,
                            severity=rule.severity,
                            message="Value outside allowed domain",
                            column=str(column),
                            row_index=idx,
                            offending_value=value,
                            evidence={"allowed_values": sorted(map(str, allowed))},
                        )
                    )

        return self._build_result(
            rule,
            started,
            len(df),
            violations,
            {"invalid_distinct_values": len(invalid_counter), "invalid_total_values": sum(invalid_counter.values())},
        )

    def _validate_range(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        min_value = rule.params.get("min_value")
        max_value = rule.params.get("max_value")

        if min_value is None and max_value is None:
            raise IntegrityConfigurationError("min_value or max_value is required for RANGE")

        violations: List[IntegrityViolation] = []
        invalid_total = 0
        for idx, value in df[column].items():
            if _is_null(value):
                continue
            try:
                numeric = float(value)
            except Exception:
                invalid_total += 1
                if len(violations) < rule.max_evidence:
                    violations.append(
                        IntegrityViolation(
                            rule_id=rule.rule_id,
                            rule_type=rule.rule_type,
                            severity=rule.severity,
                            message="Value is not numeric for range validation",
                            column=str(column),
                            row_index=idx,
                            offending_value=value,
                        )
                    )
                continue

            below = min_value is not None and numeric < float(min_value)
            above = max_value is not None and numeric > float(max_value)
            if below or above:
                invalid_total += 1
                if len(violations) < rule.max_evidence:
                    violations.append(
                        IntegrityViolation(
                            rule_id=rule.rule_id,
                            rule_type=rule.rule_type,
                            severity=rule.severity,
                            message="Value outside accepted range",
                            column=str(column),
                            row_index=idx,
                            offending_value=value,
                            evidence={"min_value": min_value, "max_value": max_value},
                        )
                    )

        return self._build_result(rule, started, len(df), violations, {"invalid_total_values": invalid_total})

    def _validate_row_count(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        row_count = len(df)
        min_rows = rule.params.get("min_rows")
        max_rows = rule.params.get("max_rows")
        expected_rows = rule.params.get("expected_rows")

        violations: List[IntegrityViolation] = []
        if expected_rows is not None and row_count != int(expected_rows):
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Dataset row count does not match expected value",
                    evidence={"row_count": row_count, "expected_rows": expected_rows},
                )
            )
        if min_rows is not None and row_count < int(min_rows):
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Dataset row count below minimum",
                    evidence={"row_count": row_count, "min_rows": min_rows},
                )
            )
        if max_rows is not None and row_count > int(max_rows):
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Dataset row count above maximum",
                    evidence={"row_count": row_count, "max_rows": max_rows},
                )
            )

        return self._build_result(rule, started, row_count, violations, {"row_count": row_count})

    def _validate_schema_columns(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        required = tuple(rule.params.get("required_columns", ()))
        allow_extra = bool(rule.params.get("allow_extra", True))
        actual = set(map(str, df.columns))
        required_set = set(map(str, required))

        missing = sorted(required_set - actual)
        extra = sorted(actual - required_set) if not allow_extra else []
        violations: List[IntegrityViolation] = []

        for column in missing:
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Required column is missing",
                    column=column,
                    evidence={"required_columns": list(required), "actual_columns": list(map(str, df.columns))},
                )
            )
        for column in extra:
            violations.append(
                IntegrityViolation(
                    rule_id=rule.rule_id,
                    rule_type=rule.rule_type,
                    severity=rule.severity,
                    message="Unexpected extra column detected",
                    column=column,
                    evidence={"required_columns": list(required), "actual_columns": list(map(str, df.columns))},
                )
            )

        return self._build_result(rule, started, len(df), violations, {"missing_columns": missing, "extra_columns": extra})

    def _validate_monotonic(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        increasing = bool(rule.params.get("increasing", True))
        strict = bool(rule.params.get("strict", False))

        values = [(idx, value) for idx, value in df[column].items() if not _is_null(value)]
        violations: List[IntegrityViolation] = []
        for pos in range(1, len(values)):
            prev_idx, prev_value = values[pos - 1]
            idx, value = values[pos]
            if increasing:
                invalid = value <= prev_value if strict else value < prev_value
            else:
                invalid = value >= prev_value if strict else value > prev_value
            if invalid:
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message="Column monotonicity violation",
                        column=str(column),
                        row_index=idx,
                        offending_value=value,
                        evidence={"previous_row_index": prev_idx, "previous_value": prev_value, "increasing": increasing, "strict": strict},
                    )
                )
                if len(violations) >= rule.max_evidence:
                    break

        return self._build_result(rule, started, len(df), violations, {"checked_non_null_values": len(values)})

    def _validate_sequence_gap(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        started = datetime.now(timezone.utc)
        df = self._to_dataframe(dataset)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        step = rule.params.get("step", 1)

        values = [(idx, value) for idx, value in df[column].items() if not _is_null(value)]
        violations: List[IntegrityViolation] = []
        for pos in range(1, len(values)):
            prev_idx, prev_value = values[pos - 1]
            idx, value = values[pos]
            try:
                expected = prev_value + step
            except Exception as exc:
                raise IntegrityConfigurationError(f"Cannot apply sequence step to values: {exc}") from exc
            if value != expected:
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message="Sequence gap detected",
                        column=str(column),
                        row_index=idx,
                        offending_value=value,
                        evidence={"previous_row_index": prev_idx, "previous_value": prev_value, "expected_value": expected, "step": step},
                    )
                )
                if len(violations) >= rule.max_evidence:
                    break

        return self._build_result(rule, started, len(df), violations, {"checked_non_null_values": len(values), "step": step})

    def _validate_custom(self, dataset: DataLike, rule: IntegrityRule, context: ValidationContext) -> RuleValidationResult:
        validator = rule.params.get("validator")
        if not callable(validator):
            raise IntegrityConfigurationError("validator callable is required for CUSTOM rule")
        return validator(dataset, rule, context)

    def _to_dataframe(self, dataset: DataLike) -> "pd.DataFrame":
        if pd is None:
            raise ImportError("pandas is required for IntegrityValidator. Install with: pip install pandas")
        if dataset is None:
            raise IntegrityConfigurationError("dataset cannot be None")
        if isinstance(dataset, pd.DataFrame):
            return dataset.copy(deep=False)
        if isinstance(dataset, Sequence):
            return pd.DataFrame(list(dataset))
        raise IntegrityConfigurationError(f"Unsupported dataset type: {type(dataset)!r}")

    def _validate_context_and_rules(self, df: "pd.DataFrame", rules: Sequence[IntegrityRule], context: ValidationContext) -> None:
        if not context.dataset_name:
            raise IntegrityConfigurationError("context.dataset_name is required")
        if not rules:
            raise IntegrityConfigurationError("At least one integrity rule is required")
        seen: Set[str] = set()
        for rule in rules:
            if rule.rule_id in seen:
                raise IntegrityConfigurationError(f"Duplicated rule_id: {rule.rule_id}")
            seen.add(rule.rule_id)
            if self.strict_columns and rule.columns:
                self._ensure_columns(df, rule.columns)

    def _ensure_rule_columns(self, df: "pd.DataFrame", rule: IntegrityRule) -> None:
        if not rule.columns:
            raise IntegrityConfigurationError(f"Rule {rule.rule_id} requires at least one column")
        self._ensure_columns(df, rule.columns)

    def _ensure_columns(self, df: "pd.DataFrame", columns: Iterable[str]) -> None:
        missing = [str(c) for c in columns if c not in df.columns]
        if missing:
            raise IntegrityConfigurationError(f"Missing required columns: {missing}")

    def _null_violations(self, df: "pd.DataFrame", rule: IntegrityRule, message_prefix: str) -> List[IntegrityViolation]:
        violations: List[IntegrityViolation] = []
        for column in rule.columns:
            null_mask = df[column].isna()
            for idx in df.index[null_mask].tolist():
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message=f"{message_prefix}: column={column}",
                        column=str(column),
                        row_index=idx,
                        offending_value=None,
                    )
                )
                if len(violations) >= rule.max_evidence:
                    return violations
        return violations

    def _duplicate_violations(self, df: "pd.DataFrame", rule: IntegrityRule, message_prefix: str) -> List[IntegrityViolation]:
        duplicates = df[df.duplicated(list(rule.columns), keep=False)]
        violations: List[IntegrityViolation] = []
        if duplicates.empty:
            return violations

        grouped = duplicates.groupby(list(rule.columns), dropna=False, sort=False)
        for key, group in grouped:
            normalized_key = key if isinstance(key, tuple) else (key,)
            for idx in group.index.tolist():
                violations.append(
                    IntegrityViolation(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        severity=rule.severity,
                        message=f"{message_prefix}: columns={list(rule.columns)}",
                        row_index=idx,
                        evidence={"key": normalized_key, "duplicate_count": len(group)},
                    )
                )
                if len(violations) >= rule.max_evidence:
                    return violations
        return violations

    def _key_tuples(self, df: "pd.DataFrame", columns: Sequence[str], drop_null: bool = False) -> List[Tuple[Any, Tuple[Any, ...]]]:
        values: List[Tuple[Any, Tuple[Any, ...]]] = []
        for idx, row in df.iterrows():
            key = tuple(row[col] for col in columns)
            if drop_null and any(_is_null(v) for v in key):
                continue
            values.append((idx, key))
        return values

    def _canonical_dataset_payload(self, df: "pd.DataFrame") -> str:
        records: List[JsonDict] = []
        for _, row in df.iterrows():
            records.append({str(col): _canonical_value(row[col]) for col in df.columns})
        return json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    def _build_result(
        self,
        rule: IntegrityRule,
        started: datetime,
        checked_rows: int,
        violations: Sequence[IntegrityViolation],
        metrics: Optional[Mapping[str, Any]] = None,
    ) -> RuleValidationResult:
        status = IntegrityStatus.PASSED if not violations else IntegrityStatus.FAILED
        return RuleValidationResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            status=status,
            severity=rule.severity,
            checked_rows=checked_rows,
            violations=tuple(violations),
            metrics=metrics or {},
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )

    def _error_result(self, rule: IntegrityRule, checked_rows: int, message: str) -> RuleValidationResult:
        now = datetime.now(timezone.utc)
        violation = IntegrityViolation(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            severity=Severity.CRITICAL,
            message="Rule execution error",
            evidence={"error": message},
        )
        return RuleValidationResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            status=IntegrityStatus.ERROR,
            severity=Severity.CRITICAL,
            checked_rows=checked_rows,
            violations=(violation,),
            started_at=now,
            finished_at=now,
            error_message=message,
        )

    def _trim_result_evidence(self, result: RuleValidationResult, allowed: int) -> RuleValidationResult:
        allowed = max(0, allowed)
        return RuleValidationResult(
            rule_id=result.rule_id,
            rule_type=result.rule_type,
            status=result.status,
            severity=result.severity,
            checked_rows=result.checked_rows,
            violations=tuple(result.violations[:allowed]),
            metrics={**dict(result.metrics), "evidence_trimmed": True, "original_violation_count": len(result.violations)},
            started_at=result.started_at,
            finished_at=result.finished_at,
            error_message=result.error_message,
        )

    def _compute_final_status(self, results: Sequence[RuleValidationResult]) -> IntegrityStatus:
        if any(r.status == IntegrityStatus.ERROR for r in results):
            return IntegrityStatus.ERROR
        if any(r.status == IntegrityStatus.FAILED for r in results):
            return IntegrityStatus.FAILED
        return IntegrityStatus.PASSED

    def _publish_rule_metrics(self, result: RuleValidationResult, context: ValidationContext) -> None:
        if not self.metrics_sink:
            return
        tags = {**context.tags(), "rule_type": result.rule_type.value, "rule_id": result.rule_id, "status": result.status.value}
        self.metrics_sink.increment("data_integrity.rule.executed", tags=tags)
        self.metrics_sink.gauge("data_integrity.rule.violations", len(result.violations), tags=tags)
        self.metrics_sink.timing("data_integrity.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_global_metrics(self, result: IntegrityValidationResult, elapsed_ms: float) -> None:
        if not self.metrics_sink:
            return
        tags = {**result.context.tags(), "status": result.status.value}
        self.metrics_sink.increment("data_integrity.validation.executed", tags=tags)
        self.metrics_sink.gauge("data_integrity.validation.rows", result.dataset_rows, tags=tags)
        self.metrics_sink.gauge("data_integrity.validation.violations", len(result.violations), tags=tags)
        self.metrics_sink.timing("data_integrity.validation.duration_ms", elapsed_ms, tags=tags)

    def _emit_audit(self, event_name: str, context: ValidationContext, payload: Mapping[str, Any]) -> None:
        if not self.audit_sink:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "event_name": event_name,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "context": {
                "dataset_name": context.dataset_name,
                "pipeline_name": context.pipeline_name,
                "environment": context.environment,
                "run_id": context.run_id,
                "tenant_id": context.tenant_id,
                "source_system": context.source_system,
                "correlation_id": context.correlation_id,
            },
            "payload": _safe_json_value(dict(payload)),
        }
        self.audit_sink.emit(event)


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd is not None and pd.isna(value):
            return True
    except Exception:
        pass
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def _canonical_value(value: Any) -> str:
    if _is_null(value):
        return "<NULL>"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    if isinstance(value, float):
        if math.isnan(value):
            return "<NULL>"
        return format(value, ".15g")
    return str(value)


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if _is_null(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


class InMemoryAuditSink:
    """Audit sink simples para testes, desenvolvimento local e pipelines unitários."""

    def __init__(self) -> None:
        self.events: List[Mapping[str, Any]] = []

    def emit(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


class InMemoryMetricsSink:
    """Metrics sink simples para testes e desenvolvimento local."""

    def __init__(self) -> None:
        self.counters: MutableMapping[str, int] = defaultdict(int)
        self.gauges: MutableMapping[str, float] = {}
        self.timings: MutableMapping[str, List[float]] = defaultdict(list)

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        self.counters[self._key(name, tags)] += value

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.gauges[self._key(name, tags)] = float(value)

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        self.timings[self._key(name, tags)].append(float(value_ms))

    def timing_summary(self, name: str, tags: Optional[Mapping[str, str]] = None) -> Mapping[str, float]:
        values = self.timings.get(self._key(name, tags), [])
        if not values:
            return {"count": 0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": float(len(values)),
            "min": min(values),
            "max": max(values),
            "avg": statistics.mean(values),
        }

    def _key(self, name: str, tags: Optional[Mapping[str, str]]) -> str:
        if not tags:
            return name
        tag_text = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}|{tag_text}"


def build_default_integrity_rules(
    *,
    primary_key: Optional[Sequence[str]] = None,
    required_columns: Optional[Sequence[str]] = None,
    not_null_columns: Optional[Sequence[str]] = None,
    min_rows: Optional[int] = 1,
) -> List[IntegrityRule]:
    """Factory utilitária para regras comuns em pipelines enterprise."""
    rules: List[IntegrityRule] = []
    if required_columns:
        rules.append(
            IntegrityRule.schema_columns(
                required_columns=required_columns,
                allow_extra=True,
                rule_id="schema_required_columns",
                severity=Severity.ERROR,
            )
        )
    if min_rows is not None:
        rules.append(
            IntegrityRule.row_count(
                min_rows=min_rows,
                rule_id="dataset_minimum_row_count",
                severity=Severity.ERROR,
            )
        )
    if primary_key:
        rules.append(
            IntegrityRule.primary_key(
                columns=primary_key,
                rule_id="primary_key_integrity",
                severity=Severity.CRITICAL,
            )
        )
    if not_null_columns:
        rules.append(
            IntegrityRule.not_null(
                columns=not_null_columns,
                rule_id="required_not_null_columns",
                severity=Severity.ERROR,
            )
        )
    return rules


__all__ = [
    "AuditSink",
    "DataIntegrityError",
    "DataLike",
    "InMemoryAuditSink",
    "InMemoryMetricsSink",
    "IntegrityConfigurationError",
    "IntegrityRule",
    "IntegrityStatus",
    "IntegrityValidationResult",
    "IntegrityValidator",
    "IntegrityViolation",
    "MetricsSink",
    "RuleType",
    "RuleValidationResult",
    "Severity",
    "ValidationContext",
    "build_default_integrity_rules",
]
