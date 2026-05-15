"""
data/validation/quality_validator.py

Enterprise-grade data quality validator.

Este módulo implementa uma camada robusta e extensível para avaliação de
qualidade de dados em pipelines batch, streaming, lakehouse, data warehouse,
APIs internas, produtos analíticos e camadas de governança.

Capacidades principais:
- Validação de dimensões clássicas de qualidade: completude, unicidade,
  validade, consistência, acurácia proxy, frescor, estabilidade e conformidade.
- Regras declarativas com severidade, thresholds, amostragem e evidências seguras.
- Score consolidado por regra, dimensão, coluna e dataset.
- Detecção de nulos, duplicados, padrões inválidos, domínios inválidos,
  ranges, outliers, cardinalidade anômala, schema drift e dados obsoletos.
- Integração com auditoria, métricas e observabilidade.
- Suporte a pandas DataFrame e lista de dicionários.
- Design tipado, defensivo e preparado para uso enterprise.

Exemplo:
    validator = QualityValidator()
    result = validator.validate(
        dataset=df,
        rules=[
            QualityRule.completeness(columns=["id", "email"], min_ratio=0.99),
            QualityRule.uniqueness(columns=["id"], min_ratio=1.0),
            QualityRule.allowed_values(column="status", values={"ACTIVE", "INACTIVE"}),
        ],
        context=QualityValidationContext(dataset_name="customers")
    )

    if not result.is_acceptable:
        raise DataQualityError(result.summary())
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
    Pattern,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
)

try:
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
DataLike = Union["pd.DataFrame", Sequence[Mapping[str, Any]]]
RuleHandler = Callable[["pd.DataFrame", "QualityRule", "QualityValidationContext"], "QualityRuleResult"]
CustomQualityCheck = Callable[["pd.DataFrame", "QualityRule", "QualityValidationContext"], "QualityRuleResult"]


class QualitySeverity(str, Enum):
    """Severidade de uma falha de qualidade."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class QualityStatus(str, Enum):
    """Status de execução ou resultado de qualidade."""

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class QualityDimension(str, Enum):
    """Dimensões clássicas e operacionais de qualidade de dados."""

    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    VALIDITY = "VALIDITY"
    CONSISTENCY = "CONSISTENCY"
    ACCURACY = "ACCURACY"
    TIMELINESS = "TIMELINESS"
    FRESHNESS = "FRESHNESS"
    STABILITY = "STABILITY"
    CONFORMITY = "CONFORMITY"
    INTEGRITY = "INTEGRITY"
    OBSERVABILITY = "OBSERVABILITY"
    CUSTOM = "CUSTOM"


class QualityRuleType(str, Enum):
    """Tipos nativos de regras de qualidade."""

    COMPLETENESS = "COMPLETENESS"
    UNIQUENESS = "UNIQUENESS"
    ALLOWED_VALUES = "ALLOWED_VALUES"
    RANGE = "RANGE"
    REGEX = "REGEX"
    TYPE_CONFORMANCE = "TYPE_CONFORMANCE"
    ROW_COUNT = "ROW_COUNT"
    SCHEMA = "SCHEMA"
    FRESHNESS = "FRESHNESS"
    TIMESTAMP_NOT_FUTURE = "TIMESTAMP_NOT_FUTURE"
    OUTLIER_ZSCORE = "OUTLIER_ZSCORE"
    OUTLIER_IQR = "OUTLIER_IQR"
    CARDINALITY = "CARDINALITY"
    STRING_LENGTH = "STRING_LENGTH"
    NUMERIC_PRECISION = "NUMERIC_PRECISION"
    CROSS_FIELD_CONSISTENCY = "CROSS_FIELD_CONSISTENCY"
    CUSTOM = "CUSTOM"


class DataQualityError(Exception):
    """Erro para qualidade insuficiente ou falha bloqueante."""


class QualityConfigurationError(Exception):
    """Erro de configuração de regra, contexto ou dataset."""


class AuditSink(Protocol):
    """Contrato para auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Emite evento de auditoria."""


class MetricsSink(Protocol):
    """Contrato para métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica gauge."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica tempo."""


@dataclass(frozen=True)
class QualityValidationContext:
    """Contexto operacional da validação de qualidade."""

    dataset_name: str
    pipeline_name: Optional[str] = None
    environment: str = "production"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: Optional[str] = None
    source_system: Optional[str] = None
    data_product: Optional[str] = None
    data_owner: Optional[str] = None
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
            "product": self.data_product or "unknown",
            "owner": self.data_owner or "unknown",
        }


@dataclass(frozen=True)
class QualityIssue:
    """Evidência segura de problema de qualidade."""

    rule_id: str
    rule_type: QualityRuleType
    dimension: QualityDimension
    severity: QualitySeverity
    message: str
    column: Optional[str] = None
    row_index: Optional[Any] = None
    offending_value: Optional[Any] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "dimension": self.dimension.value,
            "severity": self.severity.value,
            "message": self.message,
            "column": self.column,
            "row_index": self.row_index,
            "offending_value": _safe_json_value(self.offending_value),
            "expected": _safe_json_value(self.expected),
            "actual": _safe_json_value(self.actual),
            "evidence": _safe_json_value(dict(self.evidence)),
        }


@dataclass(frozen=True)
class QualityRule:
    """Regra declarativa de qualidade de dados."""

    rule_type: QualityRuleType
    dimension: QualityDimension
    rule_id: str = field(default_factory=lambda: f"dq_{uuid.uuid4().hex[:12]}")
    columns: Tuple[str, ...] = field(default_factory=tuple)
    severity: QualitySeverity = QualitySeverity.ERROR
    enabled: bool = True
    min_score: float = 1.0
    warning_score: Optional[float] = None
    max_evidence: int = 50
    description: Optional[str] = None
    params: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def completeness(columns: Sequence[str], min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params["min_ratio"] = min_ratio
        return QualityRule(
            QualityRuleType.COMPLETENESS,
            QualityDimension.COMPLETENESS,
            columns=tuple(columns),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def uniqueness(columns: Sequence[str], min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params["min_ratio"] = min_ratio
        return QualityRule(
            QualityRuleType.UNIQUENESS,
            QualityDimension.UNIQUENESS,
            columns=tuple(columns),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def allowed_values(column: str, values: Iterable[Any], min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params["values"] = set(values)
        params["min_ratio"] = min_ratio
        return QualityRule(
            QualityRuleType.ALLOWED_VALUES,
            QualityDimension.VALIDITY,
            columns=(column,),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def range(column: str, min_value: Optional[float] = None, max_value: Optional[float] = None, min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_value": min_value, "max_value": max_value, "min_ratio": min_ratio})
        return QualityRule(
            QualityRuleType.RANGE,
            QualityDimension.VALIDITY,
            columns=(column,),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def regex(column: str, pattern: Union[str, Pattern[str]], min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"pattern": pattern, "min_ratio": min_ratio})
        return QualityRule(
            QualityRuleType.REGEX,
            QualityDimension.CONFORMITY,
            columns=(column,),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def row_count(min_rows: Optional[int] = None, max_rows: Optional[int] = None, expected_rows: Optional[int] = None, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_rows": min_rows, "max_rows": max_rows, "expected_rows": expected_rows})
        return QualityRule(QualityRuleType.ROW_COUNT, QualityDimension.OBSERVABILITY, params=params, **kwargs)

    @staticmethod
    def schema(required_columns: Sequence[str], allow_extra: bool = True, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"required_columns": tuple(required_columns), "allow_extra": allow_extra})
        return QualityRule(QualityRuleType.SCHEMA, QualityDimension.CONFORMITY, params=params, **kwargs)

    @staticmethod
    def freshness(timestamp_column: str, max_age_seconds: int, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params["max_age_seconds"] = max_age_seconds
        return QualityRule(
            QualityRuleType.FRESHNESS,
            QualityDimension.FRESHNESS,
            columns=(timestamp_column,),
            params=params,
            **kwargs,
        )

    @staticmethod
    def outlier_zscore(column: str, threshold: float = 3.0, max_outlier_ratio: float = 0.01, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"threshold": threshold, "max_outlier_ratio": max_outlier_ratio})
        return QualityRule(
            QualityRuleType.OUTLIER_ZSCORE,
            QualityDimension.STABILITY,
            columns=(column,),
            min_score=1.0 - max_outlier_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def cardinality(column: str, min_distinct: Optional[int] = None, max_distinct: Optional[int] = None, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_distinct": min_distinct, "max_distinct": max_distinct})
        return QualityRule(
            QualityRuleType.CARDINALITY,
            QualityDimension.STABILITY,
            columns=(column,),
            params=params,
            **kwargs,
        )

    @staticmethod
    def string_length(column: str, min_length: Optional[int] = None, max_length: Optional[int] = None, min_ratio: float = 1.0, **kwargs: Any) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params.update({"min_length": min_length, "max_length": max_length, "min_ratio": min_ratio})
        return QualityRule(
            QualityRuleType.STRING_LENGTH,
            QualityDimension.CONFORMITY,
            columns=(column,),
            min_score=min_ratio,
            params=params,
            **kwargs,
        )

    @staticmethod
    def custom(
        handler: CustomQualityCheck,
        dimension: QualityDimension = QualityDimension.CUSTOM,
        columns: Sequence[str] = (),
        rule_id: Optional[str] = None,
        **kwargs: Any,
    ) -> "QualityRule":
        params = dict(kwargs.pop("params", {}))
        params["handler"] = handler
        return QualityRule(
            QualityRuleType.CUSTOM,
            dimension,
            rule_id=rule_id or f"custom_dq_{uuid.uuid4().hex[:12]}",
            columns=tuple(columns),
            params=params,
            **kwargs,
        )


@dataclass(frozen=True)
class QualityRuleResult:
    """Resultado individual de uma regra de qualidade."""

    rule_id: str
    rule_type: QualityRuleType
    dimension: QualityDimension
    status: QualityStatus
    severity: QualitySeverity
    score: float
    checked_rows: int
    passed_rows: int
    failed_rows: int
    issues: Tuple[QualityIssue, ...] = field(default_factory=tuple)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    error_message: Optional[str] = None

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_acceptable(self) -> bool:
        return self.status in {QualityStatus.PASSED, QualityStatus.WARNING, QualityStatus.SKIPPED}

    def to_dict(self) -> JsonDict:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type.value,
            "dimension": self.dimension.value,
            "status": self.status.value,
            "severity": self.severity.value,
            "score": self.score,
            "checked_rows": self.checked_rows,
            "passed_rows": self.passed_rows,
            "failed_rows": self.failed_rows,
            "issue_count": len(self.issues),
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": _safe_json_value(dict(self.metrics)),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class QualityDimensionScore:
    """Score agregado por dimensão de qualidade."""

    dimension: QualityDimension
    score: float
    rule_count: int
    failed_rule_count: int
    issue_count: int

    def to_dict(self) -> JsonDict:
        return {
            "dimension": self.dimension.value,
            "score": self.score,
            "rule_count": self.rule_count,
            "failed_rule_count": self.failed_rule_count,
            "issue_count": self.issue_count,
        }


@dataclass(frozen=True)
class QualityValidationResult:
    """Resultado consolidado da validação de qualidade."""

    context: QualityValidationContext
    status: QualityStatus
    score: float
    dimension_scores: Tuple[QualityDimensionScore, ...]
    rule_results: Tuple[QualityRuleResult, ...]
    started_at: datetime
    finished_at: datetime
    dataset_rows: int
    dataset_columns: Tuple[str, ...]

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def issues(self) -> Tuple[QualityIssue, ...]:
        values: List[QualityIssue] = []
        for result in self.rule_results:
            values.extend(result.issues)
        return tuple(values)

    @property
    def is_acceptable(self) -> bool:
        return self.status in {QualityStatus.PASSED, QualityStatus.WARNING}

    def summary(self) -> str:
        counts = Counter(result.status.value for result in self.rule_results)
        return (
            f"QualityValidationResult(dataset={self.context.dataset_name}, "
            f"status={self.status.value}, score={self.score:.4f}, rows={self.dataset_rows}, "
            f"rules={len(self.rule_results)}, passed={counts.get('PASSED', 0)}, "
            f"warnings={counts.get('WARNING', 0)}, failed={counts.get('FAILED', 0)}, "
            f"errors={counts.get('ERROR', 0)}, issues={len(self.issues)}, "
            f"duration_ms={self.duration_ms:.2f})"
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
                "data_product": self.context.data_product,
                "data_owner": self.context.data_owner,
                "correlation_id": self.context.correlation_id,
                "execution_ts": self.context.execution_ts.isoformat(),
                "metadata": _safe_json_value(dict(self.context.metadata)),
            },
            "status": self.status.value,
            "score": self.score,
            "dataset_rows": self.dataset_rows,
            "dataset_columns": list(self.dataset_columns),
            "dimension_scores": [score.to_dict() for score in self.dimension_scores],
            "rule_results": [result.to_dict() for result in self.rule_results],
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def raise_for_failure(self) -> None:
        if self.status in {QualityStatus.FAILED, QualityStatus.ERROR}:
            raise DataQualityError(self.summary())


class QualityValidator:
    """Motor enterprise de validação de qualidade de dados."""

    def __init__(
        self,
        *,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        fail_fast: bool = False,
        min_dataset_score: float = 0.95,
        warning_dataset_score: float = 0.98,
        strict_columns: bool = True,
        max_global_evidence: int = 500,
    ) -> None:
        self.audit_sink = audit_sink
        self.metrics_sink = metrics_sink
        self.fail_fast = fail_fast
        self.min_dataset_score = min_dataset_score
        self.warning_dataset_score = warning_dataset_score
        self.strict_columns = strict_columns
        self.max_global_evidence = max_global_evidence
        self._handlers: Dict[QualityRuleType, RuleHandler] = {
            QualityRuleType.COMPLETENESS: self._validate_completeness,
            QualityRuleType.UNIQUENESS: self._validate_uniqueness,
            QualityRuleType.ALLOWED_VALUES: self._validate_allowed_values,
            QualityRuleType.RANGE: self._validate_range,
            QualityRuleType.REGEX: self._validate_regex,
            QualityRuleType.TYPE_CONFORMANCE: self._validate_type_conformance,
            QualityRuleType.ROW_COUNT: self._validate_row_count,
            QualityRuleType.SCHEMA: self._validate_schema,
            QualityRuleType.FRESHNESS: self._validate_freshness,
            QualityRuleType.TIMESTAMP_NOT_FUTURE: self._validate_timestamp_not_future,
            QualityRuleType.OUTLIER_ZSCORE: self._validate_outlier_zscore,
            QualityRuleType.OUTLIER_IQR: self._validate_outlier_iqr,
            QualityRuleType.CARDINALITY: self._validate_cardinality,
            QualityRuleType.STRING_LENGTH: self._validate_string_length,
            QualityRuleType.NUMERIC_PRECISION: self._validate_numeric_precision,
            QualityRuleType.CROSS_FIELD_CONSISTENCY: self._validate_cross_field_consistency,
            QualityRuleType.CUSTOM: self._validate_custom,
        }

    def register_handler(self, rule_type: QualityRuleType, handler: RuleHandler) -> None:
        """Registra handler customizado para um tipo de regra."""
        self._handlers[rule_type] = handler

    def validate(
        self,
        dataset: DataLike,
        rules: Sequence[QualityRule],
        context: QualityValidationContext,
    ) -> QualityValidationResult:
        """Executa validação de qualidade para o dataset e regras informadas."""
        started = datetime.now(timezone.utc)
        start_perf = time.perf_counter()
        df = self._to_dataframe(dataset)
        self._validate_inputs(df, rules, context)

        self._emit_audit(
            "quality_validation_started",
            context,
            {"row_count": len(df), "columns": list(map(str, df.columns)), "rule_count": len(rules)},
        )

        results: List[QualityRuleResult] = []
        global_issues = 0

        for rule in rules:
            if not rule.enabled:
                results.append(
                    QualityRuleResult(
                        rule_id=rule.rule_id,
                        rule_type=rule.rule_type,
                        dimension=rule.dimension,
                        status=QualityStatus.SKIPPED,
                        severity=rule.severity,
                        score=1.0,
                        checked_rows=len(df),
                        passed_rows=len(df),
                        failed_rows=0,
                        metrics={"reason": "disabled"},
                    )
                )
                continue

            handler = self._handlers.get(rule.rule_type)
            if handler is None:
                result = self._error_result(rule, len(df), f"No handler registered for {rule.rule_type.value}")
                results.append(result)
                if self.fail_fast:
                    break
                continue

            try:
                result = handler(df, rule, context)
                if global_issues + len(result.issues) > self.max_global_evidence:
                    result = self._trim_evidence(result, self.max_global_evidence - global_issues)
                global_issues += len(result.issues)
                results.append(result)
                self._publish_rule_metrics(result, context)
                if self.fail_fast and result.status in {QualityStatus.FAILED, QualityStatus.ERROR}:
                    break
            except Exception as exc:
                logger.exception("Quality rule failed unexpectedly: %s", rule.rule_id)
                result = self._error_result(rule, len(df), str(exc))
                results.append(result)
                self._publish_rule_metrics(result, context)
                if self.fail_fast:
                    break

        score = self._compute_dataset_score(results)
        dimension_scores = self._compute_dimension_scores(results)
        status = self._compute_final_status(results, score)
        finished = datetime.now(timezone.utc)

        result = QualityValidationResult(
            context=context,
            status=status,
            score=score,
            dimension_scores=tuple(dimension_scores),
            rule_results=tuple(results),
            started_at=started,
            finished_at=finished,
            dataset_rows=len(df),
            dataset_columns=tuple(map(str, df.columns)),
        )

        elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
        self._publish_global_metrics(result, elapsed_ms)
        self._emit_audit(
            "quality_validation_finished",
            context,
            {
                "status": result.status.value,
                "score": result.score,
                "issue_count": len(result.issues),
                "summary": result.summary(),
            },
        )
        return result

    def profile(self, dataset: DataLike) -> JsonDict:
        """Gera perfil estatístico simples para apoio à criação de regras."""
        df = self._to_dataframe(dataset)
        columns: Dict[str, Any] = {}
        for column in df.columns:
            series = df[column]
            non_null = int(series.notna().sum())
            profile: Dict[str, Any] = {
                "dtype": str(series.dtype),
                "rows": len(series),
                "non_null": non_null,
                "nulls": int(series.isna().sum()),
                "null_ratio": float(series.isna().sum() / max(len(series), 1)),
                "distinct": int(series.nunique(dropna=True)),
                "distinct_ratio": float(series.nunique(dropna=True) / max(non_null, 1)),
            }
            numeric = _to_numeric_series(series)
            if numeric is not None and len(numeric) > 0:
                profile.update(
                    {
                        "min": _safe_json_value(numeric.min()),
                        "max": _safe_json_value(numeric.max()),
                        "mean": _safe_json_value(numeric.mean()),
                        "std": _safe_json_value(numeric.std(ddof=0)),
                    }
                )
            columns[str(column)] = profile
        return {"rows": len(df), "columns": columns}

    def _validate_completeness(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        issues: List[QualityIssue] = []
        checked = len(df) * len(rule.columns)
        failed = 0

        for column in rule.columns:
            mask = df[column].isna()
            failed += int(mask.sum())
            for idx in df.index[mask].tolist()[: rule.max_evidence - len(issues)]:
                issues.append(
                    QualityIssue(
                        rule.rule_id,
                        rule.rule_type,
                        rule.dimension,
                        rule.severity,
                        "Null or missing value detected",
                        column=str(column),
                        row_index=idx,
                    )
                )
                if len(issues) >= rule.max_evidence:
                    break

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_uniqueness(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        checked = len(df)
        duplicate_mask = df.duplicated(list(rule.columns), keep=False)
        failed = int(duplicate_mask.sum())
        issues: List[QualityIssue] = []
        duplicates = df[duplicate_mask]

        for idx, row in duplicates.head(rule.max_evidence).iterrows():
            key = tuple(_safe_json_value(row[column]) for column in rule.columns)
            issues.append(
                QualityIssue(
                    rule.rule_id,
                    rule.rule_type,
                    rule.dimension,
                    rule.severity,
                    "Duplicate key detected",
                    row_index=idx,
                    expected="unique key",
                    actual=key,
                    evidence={"columns": list(rule.columns), "key": key},
                )
            )

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_allowed_values(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        allowed = set(rule.params.get("values", set()))
        if not allowed:
            raise QualityConfigurationError("values is required for ALLOWED_VALUES")
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        issues: List[QualityIssue] = []
        checked = int(df[column].notna().sum())
        failed = 0

        for idx, value in df[column].items():
            if _is_null(value):
                continue
            if value not in allowed:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(
                        QualityIssue(
                            rule.rule_id,
                            rule.rule_type,
                            rule.dimension,
                            rule.severity,
                            "Value outside allowed domain",
                            column=str(column),
                            row_index=idx,
                            offending_value=value,
                            expected=sorted(map(str, allowed)),
                            actual=value,
                        )
                    )

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_range(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        min_value = rule.params.get("min_value")
        max_value = rule.params.get("max_value")
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        if min_value is None and max_value is None:
            raise QualityConfigurationError("min_value or max_value is required for RANGE")

        checked = 0
        failed = 0
        issues: List[QualityIssue] = []
        for idx, value in df[column].items():
            if _is_null(value):
                continue
            checked += 1
            try:
                numeric = float(value)
            except Exception:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Non numeric value for range validation", column, idx, value, {"min": min_value, "max": max_value}))
                continue
            invalid = (min_value is not None and numeric < float(min_value)) or (max_value is not None and numeric > float(max_value))
            if invalid:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Value outside configured range", column, idx, value, {"min": min_value, "max": max_value}))

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_regex(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        pattern = rule.params.get("pattern")
        if pattern is None:
            raise QualityConfigurationError("pattern is required for REGEX")
        compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        checked = 0
        failed = 0
        issues: List[QualityIssue] = []

        for idx, value in df[column].items():
            if _is_null(value):
                continue
            checked += 1
            if not compiled.search(str(value)):
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Value does not match required pattern", column, idx, value, {"pattern": compiled.pattern}))

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_type_conformance(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        expected_type = str(rule.params.get("expected_type", "")).lower()
        if not expected_type:
            raise QualityConfigurationError("expected_type is required for TYPE_CONFORMANCE")
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        column = rule.columns[0]
        checked = 0
        failed = 0
        issues: List[QualityIssue] = []

        for idx, value in df[column].items():
            if _is_null(value):
                continue
            checked += 1
            if not _matches_type(value, expected_type):
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Value does not conform to expected type", column, idx, value, {"expected_type": expected_type}))

        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_row_count(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        row_count = len(df)
        min_rows = rule.params.get("min_rows")
        max_rows = rule.params.get("max_rows")
        expected_rows = rule.params.get("expected_rows")
        issues: List[QualityIssue] = []

        if expected_rows is not None and row_count != int(expected_rows):
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Row count differs from expected", expected=expected_rows, actual=row_count))
        if min_rows is not None and row_count < int(min_rows):
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Row count below minimum", expected=f">= {min_rows}", actual=row_count))
        if max_rows is not None and row_count > int(max_rows):
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Row count above maximum", expected=f"<= {max_rows}", actual=row_count))

        failed = 1 if issues else 0
        score = 0.0 if issues else 1.0
        return self._build_result(rule, started, 1, 1 - failed, failed, score, rule.min_score, issues, metrics={"row_count": row_count})

    def _validate_schema(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        required = set(map(str, rule.params.get("required_columns", ())))
        allow_extra = bool(rule.params.get("allow_extra", True))
        actual = set(map(str, df.columns))
        missing = sorted(required - actual)
        extra = sorted(actual - required) if not allow_extra else []
        issues: List[QualityIssue] = []
        for column in missing:
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Required column is missing", column=column))
        for column in extra:
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Unexpected extra column detected", column=column))
        checked = max(len(required), 1)
        failed = len(missing) + len(extra)
        score = max(0.0, 1.0 - failed / checked)
        return self._build_result(rule, started, checked, checked - failed, failed, score, rule.min_score, issues, metrics={"missing": missing, "extra": extra})

    def _validate_freshness(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        max_age_seconds = int(rule.params.get("max_age_seconds"))
        timestamps = _to_datetime_series(df[column])
        if timestamps is None or timestamps.dropna().empty:
            issue = QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "No valid timestamps available for freshness validation", column=str(column))
            return self._build_result(rule, started, 1, 0, 1, 0.0, rule.min_score, [issue])
        latest = timestamps.dropna().max()
        now = context.execution_ts
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (now - latest).total_seconds())
        failed = 1 if age_seconds > max_age_seconds else 0
        issues = []
        if failed:
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Dataset freshness is outside SLA", column=str(column), expected=f"<= {max_age_seconds}s", actual=age_seconds))
        return self._build_result(rule, started, 1, 1 - failed, failed, 0.0 if failed else 1.0, rule.min_score, issues, metrics={"latest_timestamp": latest.isoformat(), "age_seconds": age_seconds})

    def _validate_timestamp_not_future(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        timestamps = _to_datetime_series(df[column])
        if timestamps is None:
            raise QualityConfigurationError("Column cannot be parsed as datetime")
        checked = int(timestamps.notna().sum())
        failed = 0
        issues: List[QualityIssue] = []
        now = pd.Timestamp(context.execution_ts) if pd is not None else context.execution_ts
        for idx, value in timestamps.items():
            if pd.isna(value):
                continue
            if value.tzinfo is None and getattr(now, "tzinfo", None) is not None:
                value = value.tz_localize(timezone.utc)
            if value > now:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Timestamp is in the future", column, idx, str(value), {"now": str(now)}))
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, rule.min_score, issues)

    def _validate_outlier_zscore(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        threshold = float(rule.params.get("threshold", 3.0))
        max_outlier_ratio = float(rule.params.get("max_outlier_ratio", 0.01))
        numeric = _to_numeric_series(df[column])
        if numeric is None or numeric.empty:
            raise QualityConfigurationError("Column must be numeric for OUTLIER_ZSCORE")
        mean = float(numeric.mean())
        std = float(numeric.std(ddof=0))
        issues: List[QualityIssue] = []
        if std == 0:
            return self._build_result(rule, started, len(numeric), len(numeric), 0, 1.0, 1.0 - max_outlier_ratio, issues, metrics={"mean": mean, "std": std})
        failed_indices = []
        for idx, value in numeric.items():
            zscore = abs((float(value) - mean) / std)
            if zscore > threshold:
                failed_indices.append(idx)
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Numeric outlier detected by z-score", column, idx, value, {"zscore": zscore, "threshold": threshold}))
        checked = len(numeric)
        failed = len(failed_indices)
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, 1.0 - max_outlier_ratio, issues, metrics={"mean": mean, "std": std})

    def _validate_outlier_iqr(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        multiplier = float(rule.params.get("multiplier", 1.5))
        max_outlier_ratio = float(rule.params.get("max_outlier_ratio", 0.01))
        numeric = _to_numeric_series(df[column])
        if numeric is None or numeric.empty:
            raise QualityConfigurationError("Column must be numeric for OUTLIER_IQR")
        q1 = float(numeric.quantile(0.25))
        q3 = float(numeric.quantile(0.75))
        iqr = q3 - q1
        lower = q1 - multiplier * iqr
        upper = q3 + multiplier * iqr
        issues: List[QualityIssue] = []
        failed = 0
        for idx, value in numeric.items():
            if value < lower or value > upper:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Numeric outlier detected by IQR", column, idx, value, {"lower": lower, "upper": upper, "multiplier": multiplier}))
        checked = len(numeric)
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, 1.0 - max_outlier_ratio, issues, metrics={"q1": q1, "q3": q3, "iqr": iqr})

    def _validate_cardinality(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        distinct = int(df[column].nunique(dropna=True))
        min_distinct = rule.params.get("min_distinct")
        max_distinct = rule.params.get("max_distinct")
        issues: List[QualityIssue] = []
        if min_distinct is not None and distinct < int(min_distinct):
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Distinct count below minimum", column=str(column), expected=f">= {min_distinct}", actual=distinct))
        if max_distinct is not None and distinct > int(max_distinct):
            issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Distinct count above maximum", column=str(column), expected=f"<= {max_distinct}", actual=distinct))
        failed = 1 if issues else 0
        return self._build_result(rule, started, 1, 1 - failed, failed, 0.0 if failed else 1.0, rule.min_score, issues, metrics={"distinct": distinct})

    def _validate_string_length(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        min_length = rule.params.get("min_length")
        max_length = rule.params.get("max_length")
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        checked = 0
        failed = 0
        issues: List[QualityIssue] = []
        for idx, value in df[column].items():
            if _is_null(value):
                continue
            checked += 1
            length = len(str(value))
            invalid = (min_length is not None and length < int(min_length)) or (max_length is not None and length > int(max_length))
            if invalid:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "String length outside configured bounds", column, idx, value, {"length": length, "min_length": min_length, "max_length": max_length}))
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_numeric_precision(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        column = rule.columns[0]
        max_scale = int(rule.params.get("max_scale", 2))
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        checked = 0
        failed = 0
        issues: List[QualityIssue] = []
        for idx, value in df[column].items():
            if _is_null(value):
                continue
            checked += 1
            text = str(value)
            scale = len(text.split(".", 1)[1]) if "." in text else 0
            if scale > max_scale:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(self._issue(rule, "Numeric scale exceeds configured precision", column, idx, value, {"scale": scale, "max_scale": max_scale}))
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_cross_field_consistency(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        started = datetime.now(timezone.utc)
        self._ensure_rule_columns(df, rule)
        predicate = rule.params.get("predicate")
        if not callable(predicate):
            raise QualityConfigurationError("predicate callable is required for CROSS_FIELD_CONSISTENCY")
        min_ratio = float(rule.params.get("min_ratio", rule.min_score))
        checked = 0
        failed = 0
        issues: List[QualityIssue] = []
        for idx, row in df.iterrows():
            checked += 1
            row_map = {str(column): row[column] for column in df.columns}
            try:
                ok = bool(predicate(row_map))
            except Exception as exc:
                ok = False
                row_map = {"predicate_error": str(exc)}
            if not ok:
                failed += 1
                if len(issues) < rule.max_evidence:
                    issues.append(QualityIssue(rule.rule_id, rule.rule_type, rule.dimension, rule.severity, "Cross-field consistency predicate failed", row_index=idx, evidence={"columns": list(rule.columns)}))
        score = 1.0 if checked == 0 else (checked - failed) / checked
        return self._build_result(rule, started, checked, checked - failed, failed, score, min_ratio, issues)

    def _validate_custom(self, df: "pd.DataFrame", rule: QualityRule, context: QualityValidationContext) -> QualityRuleResult:
        handler = rule.params.get("handler")
        if not callable(handler):
            raise QualityConfigurationError("handler callable is required for CUSTOM")
        return handler(df, rule, context)

    def _to_dataframe(self, dataset: DataLike) -> "pd.DataFrame":
        if pd is None:
            raise ImportError("pandas is required for QualityValidator. Install with: pip install pandas")
        if dataset is None:
            raise QualityConfigurationError("dataset cannot be None")
        if isinstance(dataset, pd.DataFrame):
            return dataset.copy(deep=False)
        if isinstance(dataset, Sequence):
            return pd.DataFrame(list(dataset))
        raise QualityConfigurationError(f"Unsupported dataset type: {type(dataset)!r}")

    def _validate_inputs(self, df: "pd.DataFrame", rules: Sequence[QualityRule], context: QualityValidationContext) -> None:
        if not context.dataset_name:
            raise QualityConfigurationError("context.dataset_name is required")
        if not rules:
            raise QualityConfigurationError("At least one quality rule is required")
        seen: Set[str] = set()
        for rule in rules:
            if rule.rule_id in seen:
                raise QualityConfigurationError(f"Duplicated rule_id: {rule.rule_id}")
            seen.add(rule.rule_id)
            if self.strict_columns and rule.columns:
                self._ensure_columns(df, rule.columns)
            if not 0.0 <= rule.min_score <= 1.0:
                raise QualityConfigurationError(f"rule.min_score must be between 0 and 1: {rule.rule_id}")

    def _ensure_rule_columns(self, df: "pd.DataFrame", rule: QualityRule) -> None:
        if not rule.columns:
            raise QualityConfigurationError(f"Rule {rule.rule_id} requires at least one column")
        self._ensure_columns(df, rule.columns)

    def _ensure_columns(self, df: "pd.DataFrame", columns: Iterable[str]) -> None:
        missing = [str(column) for column in columns if column not in df.columns]
        if missing:
            raise QualityConfigurationError(f"Missing required columns: {missing}")

    def _issue(self, rule: QualityRule, message: str, column: str, idx: Any, value: Any, evidence: Mapping[str, Any]) -> QualityIssue:
        return QualityIssue(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            dimension=rule.dimension,
            severity=rule.severity,
            message=message,
            column=str(column),
            row_index=idx,
            offending_value=value,
            evidence=evidence,
        )

    def _build_result(
        self,
        rule: QualityRule,
        started: datetime,
        checked_rows: int,
        passed_rows: int,
        failed_rows: int,
        score: float,
        min_score: float,
        issues: Sequence[QualityIssue],
        metrics: Optional[Mapping[str, Any]] = None,
    ) -> QualityRuleResult:
        score = round(max(0.0, min(1.0, float(score))), 6)
        warning_score = rule.warning_score
        if score >= min_score:
            status = QualityStatus.PASSED
        elif warning_score is not None and score >= warning_score:
            status = QualityStatus.WARNING
        else:
            status = QualityStatus.FAILED
        return QualityRuleResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            dimension=rule.dimension,
            status=status,
            severity=rule.severity,
            score=score,
            checked_rows=int(checked_rows),
            passed_rows=max(0, int(passed_rows)),
            failed_rows=max(0, int(failed_rows)),
            issues=tuple(issues),
            metrics=metrics or {},
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )

    def _error_result(self, rule: QualityRule, checked_rows: int, message: str) -> QualityRuleResult:
        now = datetime.now(timezone.utc)
        issue = QualityIssue(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            dimension=rule.dimension,
            severity=QualitySeverity.CRITICAL,
            message="Rule execution error",
            evidence={"error": message},
        )
        return QualityRuleResult(
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            dimension=rule.dimension,
            status=QualityStatus.ERROR,
            severity=QualitySeverity.CRITICAL,
            score=0.0,
            checked_rows=checked_rows,
            passed_rows=0,
            failed_rows=checked_rows,
            issues=(issue,),
            started_at=now,
            finished_at=now,
            error_message=message,
        )

    def _trim_evidence(self, result: QualityRuleResult, allowed: int) -> QualityRuleResult:
        allowed = max(0, allowed)
        return QualityRuleResult(
            rule_id=result.rule_id,
            rule_type=result.rule_type,
            dimension=result.dimension,
            status=result.status,
            severity=result.severity,
            score=result.score,
            checked_rows=result.checked_rows,
            passed_rows=result.passed_rows,
            failed_rows=result.failed_rows,
            issues=tuple(result.issues[:allowed]),
            metrics={**dict(result.metrics), "evidence_trimmed": True, "original_issue_count": len(result.issues)},
            started_at=result.started_at,
            finished_at=result.finished_at,
            error_message=result.error_message,
        )

    def _compute_dataset_score(self, results: Sequence[QualityRuleResult]) -> float:
        active = [result for result in results if result.status != QualityStatus.SKIPPED]
        if not active:
            return 1.0
        weights = {
            QualitySeverity.INFO: 0.5,
            QualitySeverity.WARNING: 0.75,
            QualitySeverity.ERROR: 1.0,
            QualitySeverity.CRITICAL: 1.5,
        }
        weighted_sum = sum(result.score * weights[result.severity] for result in active)
        weight_total = sum(weights[result.severity] for result in active)
        return round(weighted_sum / max(weight_total, 1e-9), 6)

    def _compute_dimension_scores(self, results: Sequence[QualityRuleResult]) -> List[QualityDimensionScore]:
        grouped: Dict[QualityDimension, List[QualityRuleResult]] = defaultdict(list)
        for result in results:
            if result.status != QualityStatus.SKIPPED:
                grouped[result.dimension].append(result)
        scores: List[QualityDimensionScore] = []
        for dimension, values in grouped.items():
            score = sum(v.score for v in values) / max(len(values), 1)
            scores.append(
                QualityDimensionScore(
                    dimension=dimension,
                    score=round(score, 6),
                    rule_count=len(values),
                    failed_rule_count=sum(1 for v in values if v.status in {QualityStatus.FAILED, QualityStatus.ERROR}),
                    issue_count=sum(len(v.issues) for v in values),
                )
            )
        return sorted(scores, key=lambda item: item.dimension.value)

    def _compute_final_status(self, results: Sequence[QualityRuleResult], score: float) -> QualityStatus:
        if any(result.status == QualityStatus.ERROR for result in results):
            return QualityStatus.ERROR
        if any(result.status == QualityStatus.FAILED and result.severity == QualitySeverity.CRITICAL for result in results):
            return QualityStatus.FAILED
        if score < self.min_dataset_score:
            return QualityStatus.FAILED
        if score < self.warning_dataset_score or any(result.status == QualityStatus.WARNING for result in results):
            return QualityStatus.WARNING
        if any(result.status == QualityStatus.FAILED for result in results):
            return QualityStatus.WARNING
        return QualityStatus.PASSED

    def _publish_rule_metrics(self, result: QualityRuleResult, context: QualityValidationContext) -> None:
        if not self.metrics_sink:
            return
        tags = {
            **context.tags(),
            "rule_id": result.rule_id,
            "rule_type": result.rule_type.value,
            "dimension": result.dimension.value,
            "status": result.status.value,
        }
        self.metrics_sink.increment("data_quality.rule.executed", tags=tags)
        self.metrics_sink.gauge("data_quality.rule.score", result.score, tags=tags)
        self.metrics_sink.gauge("data_quality.rule.issues", len(result.issues), tags=tags)
        self.metrics_sink.timing("data_quality.rule.duration_ms", result.duration_ms, tags=tags)

    def _publish_global_metrics(self, result: QualityValidationResult, elapsed_ms: float) -> None:
        if not self.metrics_sink:
            return
        tags = {**result.context.tags(), "status": result.status.value}
        self.metrics_sink.increment("data_quality.validation.executed", tags=tags)
        self.metrics_sink.gauge("data_quality.validation.score", result.score, tags=tags)
        self.metrics_sink.gauge("data_quality.validation.issues", len(result.issues), tags=tags)
        self.metrics_sink.gauge("data_quality.validation.rows", result.dataset_rows, tags=tags)
        self.metrics_sink.timing("data_quality.validation.duration_ms", elapsed_ms, tags=tags)

    def _emit_audit(self, event_name: str, context: QualityValidationContext, payload: Mapping[str, Any]) -> None:
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
                "data_product": context.data_product,
                "data_owner": context.data_owner,
                "correlation_id": context.correlation_id,
            },
            "payload": _safe_json_value(dict(payload)),
        }
        self.audit_sink.emit(event)


class InMemoryAuditSink:
    """Audit sink simples para testes e desenvolvimento local."""

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
            return {"count": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {"count": float(len(values)), "min": min(values), "max": max(values), "avg": statistics.mean(values)}

    def _key(self, name: str, tags: Optional[Mapping[str, str]]) -> str:
        if not tags:
            return name
        tag_text = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}|{tag_text}"


def build_standard_quality_rules(
    *,
    required_columns: Optional[Sequence[str]] = None,
    primary_key: Optional[Sequence[str]] = None,
    not_null_columns: Optional[Sequence[str]] = None,
    min_rows: Optional[int] = 1,
) -> List[QualityRule]:
    """Factory de regras comuns para pipelines enterprise."""
    rules: List[QualityRule] = []
    if required_columns:
        rules.append(QualityRule.schema(required_columns, rule_id="dq_schema_required_columns", severity=QualitySeverity.ERROR))
    if min_rows is not None:
        rules.append(QualityRule.row_count(min_rows=min_rows, rule_id="dq_minimum_row_count", severity=QualitySeverity.ERROR))
    if primary_key:
        rules.append(QualityRule.uniqueness(primary_key, rule_id="dq_primary_key_uniqueness", severity=QualitySeverity.CRITICAL))
        rules.append(QualityRule.completeness(primary_key, rule_id="dq_primary_key_completeness", severity=QualitySeverity.CRITICAL))
    if not_null_columns:
        rules.append(QualityRule.completeness(not_null_columns, rule_id="dq_required_columns_completeness", severity=QualitySeverity.ERROR))
    return rules


def _matches_type(value: Any, expected_type: str) -> bool:
    if expected_type in {"str", "string", "text"}:
        return isinstance(value, str)
    if expected_type in {"int", "integer"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type in {"float", "decimal", "number", "numeric"}:
        try:
            float(value)
            return True
        except Exception:
            return False
    if expected_type in {"bool", "boolean"}:
        return isinstance(value, bool)
    if expected_type in {"date", "datetime", "timestamp"}:
        return _parse_datetime(value) is not None
    return isinstance(value, type(value).__class__)


def _to_numeric_series(series: "pd.Series") -> Optional["pd.Series"]:
    if pd is None:
        return None
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    return numeric


def _to_datetime_series(series: "pd.Series") -> Optional["pd.Series"]:
    if pd is None:
        return None
    try:
        parsed = pd.to_datetime(series, errors="coerce", utc=True)
        return parsed
    except Exception:
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed
    except Exception:
        return None


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


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if _is_null(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


__all__ = [
    "AuditSink",
    "CustomQualityCheck",
    "DataLike",
    "DataQualityError",
    "InMemoryAuditSink",
    "InMemoryMetricsSink",
    "MetricsSink",
    "QualityConfigurationError",
    "QualityDimension",
    "QualityDimensionScore",
    "QualityIssue",
    "QualityRule",
    "QualityRuleResult",
    "QualityRuleType",
    "QualitySeverity",
    "QualityStatus",
    "QualityValidationContext",
    "QualityValidationResult",
    "QualityValidator",
    "build_standard_quality_rules",
]
