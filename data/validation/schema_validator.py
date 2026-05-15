"""
data/validation/schema_validator.py

Enterprise-grade schema validator.

Este módulo implementa validação avançada de schema para pipelines de dados,
contratos de dados, lakehouse, data warehouse, APIs, eventos e camadas de
qualidade/governança.

Capacidades principais:
- Validação de colunas obrigatórias, opcionais e extras.
- Validação de tipos lógicos e físicos.
- Constraints de nulidade, unicidade, regex, ranges, enum, comprimento e precisão.
- Validação de compatibilidade entre versões de schema.
- Detecção de schema drift e mudanças breaking/non-breaking.
- Suporte a pandas DataFrame e lista de dicionários.
- Relatório estruturado com severidade, evidências seguras e score.
- Audit sink e metrics sink plugáveis.
- Design tipado, extensível e pronto para arquitetura enterprise.

Exemplo:
    contract = SchemaContract(
        name="customers",
        version="1.0.0",
        fields=[
            SchemaField("id", DataType.INTEGER, nullable=False, unique=True),
            SchemaField("email", DataType.STRING, nullable=False, regex=r"^[^@]+@[^@]+\\.[^@]+$"),
            SchemaField("status", DataType.STRING, allowed_values={"ACTIVE", "INACTIVE"}),
        ],
        allow_extra_fields=False,
    )

    result = SchemaValidator().validate(
        dataset=df,
        contract=contract,
        context=SchemaValidationContext(dataset_name="customers")
    )

    if not result.is_valid:
        raise SchemaValidationError(result.summary())
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
FieldValidator = Callable[[Any, Mapping[str, Any]], bool]


class SchemaSeverity(str, Enum):
    """Severidade de uma violação de schema."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SchemaStatus(str, Enum):
    """Status da validação de schema."""

    PASSED = "PASSED"
    WARNING = "WARNING"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class DataType(str, Enum):
    """Tipos lógicos suportados pelo contrato de schema."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    DATETIME = "DATETIME"
    TIMESTAMP = "TIMESTAMP"
    JSON = "JSON"
    ARRAY = "ARRAY"
    OBJECT = "OBJECT"
    UUID = "UUID"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    BINARY = "BINARY"
    ANY = "ANY"


class CompatibilityMode(str, Enum):
    """Modo de compatibilidade entre versões de schema."""

    BACKWARD = "BACKWARD"
    FORWARD = "FORWARD"
    FULL = "FULL"
    NONE = "NONE"


class SchemaChangeType(str, Enum):
    """Tipos de mudança entre schemas."""

    FIELD_ADDED = "FIELD_ADDED"
    FIELD_REMOVED = "FIELD_REMOVED"
    TYPE_CHANGED = "TYPE_CHANGED"
    NULLABILITY_CHANGED = "NULLABILITY_CHANGED"
    CONSTRAINT_CHANGED = "CONSTRAINT_CHANGED"
    METADATA_CHANGED = "METADATA_CHANGED"
    ORDER_CHANGED = "ORDER_CHANGED"


class SchemaValidationError(Exception):
    """Erro para falha bloqueante de schema."""


class SchemaConfigurationError(Exception):
    """Erro de configuração inválida de contrato/schema."""


class AuditSink(Protocol):
    """Contrato para envio de auditoria."""

    def emit(self, event: Mapping[str, Any]) -> None:
        """Emite evento de auditoria."""


class MetricsSink(Protocol):
    """Contrato para publicação de métricas."""

    def increment(self, name: str, value: int = 1, tags: Optional[Mapping[str, str]] = None) -> None:
        """Incrementa contador."""

    def gauge(self, name: str, value: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica gauge."""

    def timing(self, name: str, value_ms: float, tags: Optional[Mapping[str, str]] = None) -> None:
        """Publica latência."""


@dataclass(frozen=True)
class SchemaValidationContext:
    """Contexto operacional da validação de schema."""

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
class SchemaField:
    """Definição de um campo no contrato de schema."""

    name: str
    data_type: DataType
    nullable: bool = True
    required: bool = True
    unique: bool = False
    primary_key: bool = False
    allowed_values: Optional[Set[Any]] = None
    regex: Optional[Union[str, Pattern[str]]] = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    default: Optional[Any] = None
    description: Optional[str] = None
    severity: SchemaSeverity = SchemaSeverity.ERROR
    custom_validator: Optional[FieldValidator] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def compiled_regex(self) -> Optional[Pattern[str]]:
        if self.regex is None:
            return None
        if isinstance(self.regex, str):
            return re.compile(self.regex)
        return self.regex

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "data_type": self.data_type.value,
            "nullable": self.nullable,
            "required": self.required,
            "unique": self.unique,
            "primary_key": self.primary_key,
            "allowed_values": sorted(map(str, self.allowed_values)) if self.allowed_values is not None else None,
            "regex": self.regex.pattern if hasattr(self.regex, "pattern") else self.regex,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "precision": self.precision,
            "scale": self.scale,
            "default": _safe_json_value(self.default),
            "description": self.description,
            "severity": self.severity.value,
            "metadata": _safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class SchemaContract:
    """Contrato declarativo de schema."""

    name: str
    version: str
    fields: Tuple[SchemaField, ...]
    allow_extra_fields: bool = True
    enforce_column_order: bool = False
    compatibility_mode: CompatibilityMode = CompatibilityMode.BACKWARD
    description: Optional[str] = None
    owner: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [field.name for field in self.fields]
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        if duplicates:
            raise SchemaConfigurationError(f"Duplicated field names in schema contract: {duplicates}")

    @property
    def field_map(self) -> Dict[str, SchemaField]:
        return {field.name: field for field in self.fields}

    @property
    def required_fields(self) -> Tuple[SchemaField, ...]:
        return tuple(field for field in self.fields if field.required)

    @property
    def primary_key_fields(self) -> Tuple[SchemaField, ...]:
        return tuple(field for field in self.fields if field.primary_key)

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "version": self.version,
            "fields": [field.to_dict() for field in self.fields],
            "allow_extra_fields": self.allow_extra_fields,
            "enforce_column_order": self.enforce_column_order,
            "compatibility_mode": self.compatibility_mode.value,
            "description": self.description,
            "owner": self.owner,
            "tags": list(self.tags),
            "metadata": _safe_json_value(dict(self.metadata)),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class SchemaViolation:
    """Violação de schema detectada."""

    violation_id: str
    field_name: Optional[str]
    severity: SchemaSeverity
    status: SchemaStatus
    message: str
    row_index: Optional[Any] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    offending_value: Optional[Any] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "violation_id": self.violation_id,
            "field_name": self.field_name,
            "severity": self.severity.value,
            "status": self.status.value,
            "message": self.message,
            "row_index": self.row_index,
            "expected": _safe_json_value(self.expected),
            "actual": _safe_json_value(self.actual),
            "offending_value": _safe_json_value(self.offending_value),
            "evidence": _safe_json_value(dict(self.evidence)),
        }


@dataclass(frozen=True)
class FieldValidationProfile:
    """Perfil de validação por campo."""

    field_name: str
    expected_type: DataType
    physical_type: str
    present: bool
    checked_values: int
    invalid_values: int
    null_values: int
    distinct_values: int
    score: float

    def to_dict(self) -> JsonDict:
        return {
            "field_name": self.field_name,
            "expected_type": self.expected_type.value,
            "physical_type": self.physical_type,
            "present": self.present,
            "checked_values": self.checked_values,
            "invalid_values": self.invalid_values,
            "null_values": self.null_values,
            "distinct_values": self.distinct_values,
            "score": self.score,
        }


@dataclass(frozen=True)
class SchemaChange:
    """Mudança detectada entre duas versões de schema."""

    change_type: SchemaChangeType
    field_name: Optional[str]
    breaking: bool
    severity: SchemaSeverity
    message: str
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None

    def to_dict(self) -> JsonDict:
        return {
            "change_type": self.change_type.value,
            "field_name": self.field_name,
            "breaking": self.breaking,
            "severity": self.severity.value,
            "message": self.message,
            "old_value": _safe_json_value(self.old_value),
            "new_value": _safe_json_value(self.new_value),
        }


@dataclass(frozen=True)
class SchemaCompatibilityResult:
    """Resultado de compatibilidade entre schemas."""

    old_schema: str
    old_version: str
    new_schema: str
    new_version: str
    mode: CompatibilityMode
    compatible: bool
    changes: Tuple[SchemaChange, ...]

    def to_dict(self) -> JsonDict:
        return {
            "old_schema": self.old_schema,
            "old_version": self.old_version,
            "new_schema": self.new_schema,
            "new_version": self.new_version,
            "mode": self.mode.value,
            "compatible": self.compatible,
            "changes": [change.to_dict() for change in self.changes],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class SchemaValidationResult:
    """Resultado consolidado da validação de schema."""

    context: SchemaValidationContext
    contract_name: str
    contract_version: str
    status: SchemaStatus
    score: float
    violations: Tuple[SchemaViolation, ...]
    field_profiles: Tuple[FieldValidationProfile, ...]
    started_at: datetime
    finished_at: datetime
    dataset_rows: int
    dataset_columns: Tuple[str, ...]

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds() * 1000.0)

    @property
    def is_valid(self) -> bool:
        return self.status in {SchemaStatus.PASSED, SchemaStatus.WARNING}

    def summary(self) -> str:
        counts = Counter(v.severity.value for v in self.violations)
        return (
            f"SchemaValidationResult(dataset={self.context.dataset_name}, "
            f"contract={self.contract_name}@{self.contract_version}, "
            f"status={self.status.value}, score={self.score:.4f}, rows={self.dataset_rows}, "
            f"columns={len(self.dataset_columns)}, violations={len(self.violations)}, "
            f"critical={counts.get('CRITICAL', 0)}, errors={counts.get('ERROR', 0)}, "
            f"warnings={counts.get('WARNING', 0)}, duration_ms={self.duration_ms:.2f})"
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
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "status": self.status.value,
            "score": self.score,
            "dataset_rows": self.dataset_rows,
            "dataset_columns": list(self.dataset_columns),
            "violations": [violation.to_dict() for violation in self.violations],
            "field_profiles": [profile.to_dict() for profile in self.field_profiles],
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def raise_for_failure(self) -> None:
        if self.status in {SchemaStatus.FAILED, SchemaStatus.ERROR}:
            raise SchemaValidationError(self.summary())


class SchemaValidator:
    """Motor enterprise de validação de schema e contratos de dados."""

    def __init__(
        self,
        *,
        audit_sink: Optional[AuditSink] = None,
        metrics_sink: Optional[MetricsSink] = None,
        fail_fast: bool = False,
        strict_schema: bool = True,
        max_evidence: int = 500,
        min_score: float = 0.99,
        warning_score: float = 0.995,
    ) -> None:
        self.audit_sink = audit_sink
        self.metrics_sink = metrics_sink
        self.fail_fast = fail_fast
        self.strict_schema = strict_schema
        self.max_evidence = max_evidence
        self.min_score = min_score
        self.warning_score = warning_score

    def validate(
        self,
        dataset: DataLike,
        contract: SchemaContract,
        context: SchemaValidationContext,
    ) -> SchemaValidationResult:
        """Valida um dataset contra um contrato de schema."""
        started = datetime.now(timezone.utc)
        start_perf = time.perf_counter()

        try:
            df = self._to_dataframe(dataset)
            self._validate_inputs(df, contract, context)

            self._emit_audit(
                "schema_validation_started",
                context,
                {
                    "contract": contract.name,
                    "version": contract.version,
                    "rows": len(df),
                    "columns": list(map(str, df.columns)),
                },
            )

            violations: List[SchemaViolation] = []
            field_profiles: List[FieldValidationProfile] = []

            violations.extend(self._validate_columns(df, contract))
            if self.fail_fast and self._has_blocking(violations):
                return self._finalize(context, contract, df, started, violations, field_profiles, start_perf)

            order_violations = self._validate_column_order(df, contract)
            violations.extend(order_violations)

            for field_def in contract.fields:
                profile, field_violations = self._validate_field(df, field_def)
                field_profiles.append(profile)
                violations.extend(field_violations)
                if len(violations) >= self.max_evidence:
                    violations = violations[: self.max_evidence]
                    break
                if self.fail_fast and self._has_blocking(field_violations):
                    break

            primary_key_violations = self._validate_primary_key(df, contract)
            violations.extend(primary_key_violations)
            violations = violations[: self.max_evidence]

            result = self._finalize(context, contract, df, started, violations, field_profiles, start_perf)
            return result
        except Exception as exc:
            logger.exception("Schema validation failed")
            violation = SchemaViolation(
                violation_id=str(uuid.uuid4()),
                field_name=None,
                severity=SchemaSeverity.CRITICAL,
                status=SchemaStatus.ERROR,
                message="Schema validation execution error",
                evidence={"error": str(exc)},
            )
            df_rows = 0
            df_columns: Tuple[str, ...] = tuple()
            try:
                df_tmp = self._to_dataframe(dataset)
                df_rows = len(df_tmp)
                df_columns = tuple(map(str, df_tmp.columns))
            except Exception:
                pass
            result = SchemaValidationResult(
                context=context,
                contract_name=contract.name,
                contract_version=contract.version,
                status=SchemaStatus.ERROR,
                score=0.0,
                violations=(violation,),
                field_profiles=tuple(),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                dataset_rows=df_rows,
                dataset_columns=df_columns,
            )
            self._emit_audit("schema_validation_error", context, {"contract": contract.name, "error": str(exc)})
            return result

    def infer_contract(
        self,
        dataset: DataLike,
        *,
        name: str,
        version: str = "1.0.0",
        allow_extra_fields: bool = True,
        sample_size: Optional[int] = None,
    ) -> SchemaContract:
        """Infere um contrato inicial a partir de um dataset."""
        df = self._to_dataframe(dataset)
        if sample_size is not None:
            df = df.head(sample_size)
        fields: List[SchemaField] = []
        for column in df.columns:
            series = df[column]
            data_type = infer_data_type(series)
            nullable = bool(series.isna().any())
            distinct = int(series.nunique(dropna=True))
            unique = distinct == int(series.notna().sum()) and len(series) > 0
            min_length: Optional[int] = None
            max_length: Optional[int] = None
            min_value: Optional[Union[int, float]] = None
            max_value: Optional[Union[int, float]] = None

            if data_type == DataType.STRING:
                lengths = series.dropna().map(lambda value: len(str(value)))
                if not lengths.empty:
                    min_length = int(lengths.min())
                    max_length = int(lengths.max())
            if data_type in {DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL}:
                numeric = pd.to_numeric(series, errors="coerce").dropna()
                if not numeric.empty:
                    min_value = float(numeric.min())
                    max_value = float(numeric.max())

            fields.append(
                SchemaField(
                    name=str(column),
                    data_type=data_type,
                    nullable=nullable,
                    required=True,
                    unique=unique,
                    min_value=min_value,
                    max_value=max_value,
                    min_length=min_length,
                    max_length=max_length,
                    metadata={"inferred": True, "physical_dtype": str(series.dtype)},
                )
            )
        return SchemaContract(name=name, version=version, fields=tuple(fields), allow_extra_fields=allow_extra_fields)

    def compare_contracts(
        self,
        old: SchemaContract,
        new: SchemaContract,
        mode: Optional[CompatibilityMode] = None,
    ) -> SchemaCompatibilityResult:
        """Compara duas versões de contrato e avalia compatibilidade."""
        mode = mode or old.compatibility_mode
        old_fields = old.field_map
        new_fields = new.field_map
        changes: List[SchemaChange] = []

        for field_name, old_field in old_fields.items():
            if field_name not in new_fields:
                breaking = mode in {CompatibilityMode.BACKWARD, CompatibilityMode.FULL}
                changes.append(
                    SchemaChange(
                        SchemaChangeType.FIELD_REMOVED,
                        field_name,
                        breaking,
                        SchemaSeverity.CRITICAL if breaking else SchemaSeverity.WARNING,
                        "Field removed from new schema",
                        old_value=old_field.to_dict(),
                        new_value=None,
                    )
                )
                continue

            new_field = new_fields[field_name]
            if old_field.data_type != new_field.data_type:
                breaking = not _is_type_widening(old_field.data_type, new_field.data_type)
                changes.append(
                    SchemaChange(
                        SchemaChangeType.TYPE_CHANGED,
                        field_name,
                        breaking,
                        SchemaSeverity.CRITICAL if breaking else SchemaSeverity.WARNING,
                        "Field type changed",
                        old_value=old_field.data_type.value,
                        new_value=new_field.data_type.value,
                    )
                )

            if old_field.nullable != new_field.nullable:
                breaking = old_field.nullable and not new_field.nullable
                changes.append(
                    SchemaChange(
                        SchemaChangeType.NULLABILITY_CHANGED,
                        field_name,
                        breaking,
                        SchemaSeverity.CRITICAL if breaking else SchemaSeverity.INFO,
                        "Field nullability changed",
                        old_value=old_field.nullable,
                        new_value=new_field.nullable,
                    )
                )

            constraint_changes = self._compare_field_constraints(old_field, new_field)
            changes.extend(constraint_changes)

        for field_name, new_field in new_fields.items():
            if field_name not in old_fields:
                breaking = new_field.required and not new_field.nullable and new_field.default is None
                if mode == CompatibilityMode.FORWARD:
                    breaking = True
                changes.append(
                    SchemaChange(
                        SchemaChangeType.FIELD_ADDED,
                        field_name,
                        breaking,
                        SchemaSeverity.CRITICAL if breaking else SchemaSeverity.INFO,
                        "Field added to new schema",
                        old_value=None,
                        new_value=new_field.to_dict(),
                    )
                )

        if old.enforce_column_order or new.enforce_column_order:
            old_order = [field.name for field in old.fields]
            new_order = [field.name for field in new.fields if field.name in old_fields]
            if old_order != new_order[: len(old_order)]:
                changes.append(
                    SchemaChange(
                        SchemaChangeType.ORDER_CHANGED,
                        None,
                        bool(old.enforce_column_order or new.enforce_column_order),
                        SchemaSeverity.WARNING,
                        "Column order changed",
                        old_value=old_order,
                        new_value=[field.name for field in new.fields],
                    )
                )

        compatible = not any(change.breaking for change in changes) if mode != CompatibilityMode.NONE else True
        return SchemaCompatibilityResult(
            old_schema=old.name,
            old_version=old.version,
            new_schema=new.name,
            new_version=new.version,
            mode=mode,
            compatible=compatible,
            changes=tuple(changes),
        )

    def detect_drift(self, dataset: DataLike, baseline_contract: SchemaContract) -> SchemaCompatibilityResult:
        """Infere schema atual e compara com contrato base para detectar drift."""
        current = self.infer_contract(dataset, name=baseline_contract.name, version="inferred-current")
        return self.compare_contracts(baseline_contract, current, mode=CompatibilityMode.BACKWARD)

    def _validate_columns(self, df: "pd.DataFrame", contract: SchemaContract) -> List[SchemaViolation]:
        actual = set(map(str, df.columns))
        expected = set(contract.field_map.keys())
        violations: List[SchemaViolation] = []

        for field_def in contract.required_fields:
            if field_def.name not in actual:
                violations.append(
                    self._violation(
                        field_def.name,
                        SchemaSeverity.CRITICAL if field_def.primary_key else field_def.severity,
                        "Required field is missing",
                        expected="present",
                        actual="missing",
                    )
                )

        if not contract.allow_extra_fields:
            for column in sorted(actual - expected):
                violations.append(
                    self._violation(
                        column,
                        SchemaSeverity.ERROR,
                        "Unexpected extra field detected",
                        expected="not present",
                        actual="present",
                    )
                )

        return violations

    def _validate_column_order(self, df: "pd.DataFrame", contract: SchemaContract) -> List[SchemaViolation]:
        if not contract.enforce_column_order:
            return []
        actual_order = [str(column) for column in df.columns if str(column) in contract.field_map]
        expected_order = [field.name for field in contract.fields if field.name in actual_order]
        if actual_order != expected_order:
            return [
                self._violation(
                    None,
                    SchemaSeverity.WARNING,
                    "Column order differs from contract",
                    expected=expected_order,
                    actual=actual_order,
                )
            ]
        return []

    def _validate_field(self, df: "pd.DataFrame", field_def: SchemaField) -> Tuple[FieldValidationProfile, List[SchemaViolation]]:
        if field_def.name not in df.columns:
            profile = FieldValidationProfile(
                field_name=field_def.name,
                expected_type=field_def.data_type,
                physical_type="missing",
                present=False,
                checked_values=0,
                invalid_values=0,
                null_values=0,
                distinct_values=0,
                score=0.0 if field_def.required else 1.0,
            )
            return profile, []

        series = df[field_def.name]
        physical_type = str(series.dtype)
        violations: List[SchemaViolation] = []
        checked = 0
        invalid = 0
        nulls = int(series.isna().sum())
        distinct = int(series.nunique(dropna=True))

        if not field_def.nullable:
            for idx in series.index[series.isna()].tolist()[: self.max_evidence]:
                invalid += 1
                violations.append(
                    self._violation(
                        field_def.name,
                        SchemaSeverity.CRITICAL if field_def.primary_key else field_def.severity,
                        "Field is not nullable but null value was found",
                        row_index=idx,
                        expected="non-null",
                        actual="null",
                    )
                )
                if len(violations) >= self.max_evidence:
                    break

        for idx, value in series.items():
            if _is_null(value):
                continue
            checked += 1
            value_violations = self._validate_value(field_def, value, idx)
            if value_violations:
                invalid += 1
                violations.extend(value_violations)
                if len(violations) >= self.max_evidence:
                    violations = violations[: self.max_evidence]
                    break

        if field_def.unique:
            duplicate_mask = series.duplicated(keep=False) & series.notna()
            duplicate_count = int(duplicate_mask.sum())
            if duplicate_count > 0:
                invalid += duplicate_count
                for idx in series.index[duplicate_mask].tolist()[: max(0, self.max_evidence - len(violations))]:
                    violations.append(
                        self._violation(
                            field_def.name,
                            field_def.severity,
                            "Field requires unique values but duplicate was found",
                            row_index=idx,
                            expected="unique",
                            actual="duplicate",
                            offending_value=series.loc[idx],
                        )
                    )

        total_checks = checked + (0 if field_def.nullable else nulls)
        score = 1.0 if total_checks == 0 else max(0.0, 1.0 - invalid / max(total_checks, 1))
        profile = FieldValidationProfile(
            field_name=field_def.name,
            expected_type=field_def.data_type,
            physical_type=physical_type,
            present=True,
            checked_values=checked,
            invalid_values=invalid,
            null_values=nulls,
            distinct_values=distinct,
            score=round(score, 6),
        )
        return profile, violations

    def _validate_value(self, field_def: SchemaField, value: Any, row_index: Any) -> List[SchemaViolation]:
        violations: List[SchemaViolation] = []

        if not value_matches_type(value, field_def.data_type):
            violations.append(
                self._violation(
                    field_def.name,
                    field_def.severity,
                    "Value does not match expected logical type",
                    row_index=row_index,
                    expected=field_def.data_type.value,
                    actual=type(value).__name__,
                    offending_value=value,
                )
            )
            return violations

        if field_def.allowed_values is not None and value not in field_def.allowed_values:
            violations.append(
                self._violation(
                    field_def.name,
                    field_def.severity,
                    "Value is outside allowed enum/domain",
                    row_index=row_index,
                    expected=sorted(map(str, field_def.allowed_values)),
                    actual=value,
                    offending_value=value,
                )
            )

        regex = field_def.compiled_regex()
        if regex is not None and not regex.search(str(value)):
            violations.append(
                self._violation(
                    field_def.name,
                    field_def.severity,
                    "Value does not match regex constraint",
                    row_index=row_index,
                    expected=regex.pattern,
                    actual=value,
                    offending_value=value,
                )
            )

        if field_def.min_length is not None or field_def.max_length is not None:
            length = len(str(value))
            invalid_length = (
                field_def.min_length is not None and length < field_def.min_length
            ) or (
                field_def.max_length is not None and length > field_def.max_length
            )
            if invalid_length:
                violations.append(
                    self._violation(
                        field_def.name,
                        field_def.severity,
                        "Value length outside configured bounds",
                        row_index=row_index,
                        expected={"min_length": field_def.min_length, "max_length": field_def.max_length},
                        actual=length,
                        offending_value=value,
                    )
                )

        if field_def.min_value is not None or field_def.max_value is not None:
            try:
                numeric = float(value)
                invalid_range = (
                    field_def.min_value is not None and numeric < float(field_def.min_value)
                ) or (
                    field_def.max_value is not None and numeric > float(field_def.max_value)
                )
                if invalid_range:
                    violations.append(
                        self._violation(
                            field_def.name,
                            field_def.severity,
                            "Numeric value outside configured range",
                            row_index=row_index,
                            expected={"min_value": field_def.min_value, "max_value": field_def.max_value},
                            actual=numeric,
                            offending_value=value,
                        )
                    )
            except Exception:
                violations.append(
                    self._violation(
                        field_def.name,
                        field_def.severity,
                        "Value cannot be converted to numeric for range validation",
                        row_index=row_index,
                        expected="numeric",
                        actual=type(value).__name__,
                        offending_value=value,
                    )
                )

        if field_def.scale is not None or field_def.precision is not None:
            precision_violation = validate_precision_scale(value, field_def.precision, field_def.scale)
            if precision_violation is not None:
                violations.append(
                    self._violation(
                        field_def.name,
                        field_def.severity,
                        "Numeric precision/scale constraint failed",
                        row_index=row_index,
                        expected={"precision": field_def.precision, "scale": field_def.scale},
                        actual=precision_violation,
                        offending_value=value,
                    )
                )

        if field_def.custom_validator is not None:
            try:
                ok = bool(field_def.custom_validator(value, {"field": field_def.name, "row_index": row_index}))
            except Exception as exc:
                ok = False
                custom_error = str(exc)
            else:
                custom_error = None
            if not ok:
                violations.append(
                    self._violation(
                        field_def.name,
                        field_def.severity,
                        "Custom field validator failed",
                        row_index=row_index,
                        offending_value=value,
                        evidence={"error": custom_error} if custom_error else {},
                    )
                )

        return violations

    def _validate_primary_key(self, df: "pd.DataFrame", contract: SchemaContract) -> List[SchemaViolation]:
        pk_fields = contract.primary_key_fields
        if not pk_fields:
            return []
        pk_names = [field.name for field in pk_fields]
        if any(name not in df.columns for name in pk_names):
            return []
        duplicate_mask = df.duplicated(pk_names, keep=False)
        violations: List[SchemaViolation] = []
        for idx, row in df[duplicate_mask].head(self.max_evidence).iterrows():
            key = tuple(_safe_json_value(row[name]) for name in pk_names)
            violations.append(
                self._violation(
                    ",".join(pk_names),
                    SchemaSeverity.CRITICAL,
                    "Primary key duplicate detected",
                    row_index=idx,
                    expected="unique primary key",
                    actual=key,
                    evidence={"primary_key_fields": pk_names},
                )
            )
        return violations

    def _compare_field_constraints(self, old: SchemaField, new: SchemaField) -> List[SchemaChange]:
        changes: List[SchemaChange] = []
        checks = [
            ("allowed_values", old.allowed_values, new.allowed_values),
            ("regex", getattr(old.regex, "pattern", old.regex), getattr(new.regex, "pattern", new.regex)),
            ("min_value", old.min_value, new.min_value),
            ("max_value", old.max_value, new.max_value),
            ("min_length", old.min_length, new.min_length),
            ("max_length", old.max_length, new.max_length),
            ("precision", old.precision, new.precision),
            ("scale", old.scale, new.scale),
            ("unique", old.unique, new.unique),
            ("primary_key", old.primary_key, new.primary_key),
        ]
        for name, old_value, new_value in checks:
            if old_value != new_value:
                breaking = _constraint_change_is_breaking(name, old_value, new_value)
                changes.append(
                    SchemaChange(
                        SchemaChangeType.CONSTRAINT_CHANGED,
                        old.name,
                        breaking,
                        SchemaSeverity.CRITICAL if breaking else SchemaSeverity.WARNING,
                        f"Field constraint changed: {name}",
                        old_value={name: _safe_json_value(old_value)},
                        new_value={name: _safe_json_value(new_value)},
                    )
                )
        return changes

    def _finalize(
        self,
        context: SchemaValidationContext,
        contract: SchemaContract,
        df: "pd.DataFrame",
        started: datetime,
        violations: Sequence[SchemaViolation],
        field_profiles: Sequence[FieldValidationProfile],
        start_perf: float,
    ) -> SchemaValidationResult:
        score = self._compute_score(violations, field_profiles, contract)
        status = self._compute_status(violations, score)
        result = SchemaValidationResult(
            context=context,
            contract_name=contract.name,
            contract_version=contract.version,
            status=status,
            score=score,
            violations=tuple(violations[: self.max_evidence]),
            field_profiles=tuple(field_profiles),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            dataset_rows=len(df),
            dataset_columns=tuple(map(str, df.columns)),
        )
        elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
        self._publish_metrics(result, elapsed_ms)
        self._emit_audit(
            "schema_validation_finished",
            context,
            {
                "contract": contract.name,
                "version": contract.version,
                "status": result.status.value,
                "score": result.score,
                "violation_count": len(result.violations),
                "summary": result.summary(),
            },
        )
        return result

    def _compute_score(self, violations: Sequence[SchemaViolation], profiles: Sequence[FieldValidationProfile], contract: SchemaContract) -> float:
        if not contract.fields:
            return 1.0 if not violations else 0.0
        profile_score = sum(profile.score for profile in profiles) / max(len(contract.fields), 1)
        severity_penalty = {
            SchemaSeverity.INFO: 0.005,
            SchemaSeverity.WARNING: 0.01,
            SchemaSeverity.ERROR: 0.05,
            SchemaSeverity.CRITICAL: 0.15,
        }
        penalty = sum(severity_penalty[v.severity] for v in violations)
        return round(max(0.0, min(1.0, profile_score - penalty)), 6)

    def _compute_status(self, violations: Sequence[SchemaViolation], score: float) -> SchemaStatus:
        if any(v.status == SchemaStatus.ERROR for v in violations):
            return SchemaStatus.ERROR
        if any(v.severity == SchemaSeverity.CRITICAL for v in violations):
            return SchemaStatus.FAILED
        if score < self.min_score:
            return SchemaStatus.FAILED
        if violations or score < self.warning_score:
            return SchemaStatus.WARNING
        return SchemaStatus.PASSED

    def _has_blocking(self, violations: Sequence[SchemaViolation]) -> bool:
        return any(v.severity in {SchemaSeverity.ERROR, SchemaSeverity.CRITICAL} for v in violations)

    def _violation(
        self,
        field_name: Optional[str],
        severity: SchemaSeverity,
        message: str,
        *,
        row_index: Optional[Any] = None,
        expected: Optional[Any] = None,
        actual: Optional[Any] = None,
        offending_value: Optional[Any] = None,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> SchemaViolation:
        status = SchemaStatus.FAILED if severity in {SchemaSeverity.ERROR, SchemaSeverity.CRITICAL} else SchemaStatus.WARNING
        return SchemaViolation(
            violation_id=str(uuid.uuid4()),
            field_name=field_name,
            severity=severity,
            status=status,
            message=message,
            row_index=row_index,
            expected=expected,
            actual=actual,
            offending_value=offending_value,
            evidence=evidence or {},
        )

    def _to_dataframe(self, dataset: DataLike) -> "pd.DataFrame":
        if pd is None:
            raise ImportError("pandas is required for SchemaValidator. Install with: pip install pandas")
        if dataset is None:
            raise SchemaConfigurationError("dataset cannot be None")
        if isinstance(dataset, pd.DataFrame):
            return dataset.copy(deep=False)
        if isinstance(dataset, Sequence):
            return pd.DataFrame(list(dataset))
        raise SchemaConfigurationError(f"Unsupported dataset type: {type(dataset)!r}")

    def _validate_inputs(self, df: "pd.DataFrame", contract: SchemaContract, context: SchemaValidationContext) -> None:
        if not context.dataset_name:
            raise SchemaConfigurationError("context.dataset_name is required")
        if not contract.name:
            raise SchemaConfigurationError("contract.name is required")
        if not contract.version:
            raise SchemaConfigurationError("contract.version is required")
        if not contract.fields:
            raise SchemaConfigurationError("contract.fields cannot be empty")

    def _publish_metrics(self, result: SchemaValidationResult, elapsed_ms: float) -> None:
        if not self.metrics_sink:
            return
        tags = {
            **result.context.tags(),
            "contract": result.contract_name,
            "version": result.contract_version,
            "status": result.status.value,
        }
        self.metrics_sink.increment("schema.validation.executed", tags=tags)
        self.metrics_sink.gauge("schema.validation.score", result.score, tags=tags)
        self.metrics_sink.gauge("schema.validation.violations", len(result.violations), tags=tags)
        self.metrics_sink.gauge("schema.validation.rows", result.dataset_rows, tags=tags)
        self.metrics_sink.timing("schema.validation.duration_ms", elapsed_ms, tags=tags)

        for violation in result.violations:
            v_tags = {
                **tags,
                "severity": violation.severity.value,
                "field": violation.field_name or "dataset",
            }
            self.metrics_sink.increment("schema.violation.detected", tags=v_tags)

    def _emit_audit(self, event_name: str, context: SchemaValidationContext, payload: Mapping[str, Any]) -> None:
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


def infer_data_type(series: "pd.Series") -> DataType:
    """Infere tipo lógico a partir de uma série pandas."""
    if pd is None:
        return DataType.ANY
    non_null = series.dropna()
    if non_null.empty:
        return DataType.ANY

    dtype_text = str(series.dtype).lower()
    if "bool" in dtype_text:
        return DataType.BOOLEAN
    if "int" in dtype_text:
        return DataType.INTEGER
    if "float" in dtype_text:
        return DataType.FLOAT
    if "datetime" in dtype_text:
        return DataType.TIMESTAMP

    sample = non_null.head(100).map(str)
    if sample.map(_looks_like_email).mean() >= 0.95:
        return DataType.EMAIL
    if sample.map(_looks_like_uuid).mean() >= 0.95:
        return DataType.UUID
    if sample.map(lambda x: _parse_datetime(x) is not None).mean() >= 0.95:
        return DataType.DATETIME
    if sample.map(lambda x: _is_int_like(x)).mean() >= 0.95:
        return DataType.INTEGER
    if sample.map(lambda x: _is_float_like(x)).mean() >= 0.95:
        return DataType.FLOAT
    if sample.map(_looks_like_json).mean() >= 0.95:
        return DataType.JSON
    return DataType.STRING


def value_matches_type(value: Any, data_type: DataType) -> bool:
    """Valida se um valor corresponde ao tipo lógico esperado."""
    if _is_null(value):
        return True
    if data_type == DataType.ANY:
        return True
    if data_type == DataType.STRING:
        return isinstance(value, str)
    if data_type == DataType.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool) or _is_int_like(str(value))
    if data_type in {DataType.FLOAT, DataType.DECIMAL}:
        return _is_float_like(str(value))
    if data_type == DataType.BOOLEAN:
        return isinstance(value, bool) or str(value).lower() in {"true", "false", "0", "1", "yes", "no"}
    if data_type == DataType.DATE:
        parsed = _parse_datetime(value)
        return parsed is not None
    if data_type in {DataType.DATETIME, DataType.TIMESTAMP}:
        return _parse_datetime(value) is not None
    if data_type == DataType.JSON:
        if isinstance(value, (dict, list)):
            return True
        return _looks_like_json(str(value))
    if data_type == DataType.ARRAY:
        return isinstance(value, (list, tuple))
    if data_type == DataType.OBJECT:
        return isinstance(value, Mapping)
    if data_type == DataType.UUID:
        return _looks_like_uuid(str(value))
    if data_type == DataType.EMAIL:
        return _looks_like_email(str(value))
    if data_type == DataType.PHONE:
        return bool(re.match(r"^\+?\d{0,3}\s?\(?\d{2,3}\)?\s?\d{4,5}[-\s]?\d{4}$", str(value)))
    if data_type == DataType.BINARY:
        return isinstance(value, (bytes, bytearray))
    return True


def validate_precision_scale(value: Any, precision: Optional[int], scale: Optional[int]) -> Optional[Mapping[str, Any]]:
    """Valida precisão e escala numérica, retornando detalhes em caso de falha."""
    text = str(value)
    if not _is_float_like(text):
        return {"reason": "not_numeric"}
    normalized = text.strip().replace("-", "")
    if "e" in normalized.lower():
        normalized = f"{float(text):f}".rstrip("0").rstrip(".")
    integer_part, _, decimal_part = normalized.partition(".")
    actual_precision = len(integer_part.lstrip("0") + decimal_part)
    actual_scale = len(decimal_part)
    if precision is not None and actual_precision > precision:
        return {"precision": actual_precision, "scale": actual_scale, "reason": "precision_exceeded"}
    if scale is not None and actual_scale > scale:
        return {"precision": actual_precision, "scale": actual_scale, "reason": "scale_exceeded"}
    return None


def build_contract_from_dict(payload: Mapping[str, Any]) -> SchemaContract:
    """Constrói SchemaContract a partir de dicionário serializado."""
    fields = []
    for item in payload.get("fields", []):
        fields.append(
            SchemaField(
                name=item["name"],
                data_type=DataType(item["data_type"]),
                nullable=bool(item.get("nullable", True)),
                required=bool(item.get("required", True)),
                unique=bool(item.get("unique", False)),
                primary_key=bool(item.get("primary_key", False)),
                allowed_values=set(item["allowed_values"]) if item.get("allowed_values") is not None else None,
                regex=item.get("regex"),
                min_value=item.get("min_value"),
                max_value=item.get("max_value"),
                min_length=item.get("min_length"),
                max_length=item.get("max_length"),
                precision=item.get("precision"),
                scale=item.get("scale"),
                default=item.get("default"),
                description=item.get("description"),
                severity=SchemaSeverity(item.get("severity", SchemaSeverity.ERROR.value)),
                metadata=item.get("metadata", {}),
            )
        )
    return SchemaContract(
        name=payload["name"],
        version=payload["version"],
        fields=tuple(fields),
        allow_extra_fields=bool(payload.get("allow_extra_fields", True)),
        enforce_column_order=bool(payload.get("enforce_column_order", False)),
        compatibility_mode=CompatibilityMode(payload.get("compatibility_mode", CompatibilityMode.BACKWARD.value)),
        description=payload.get("description"),
        owner=payload.get("owner"),
        tags=tuple(payload.get("tags", ())),
        metadata=payload.get("metadata", {}),
    )


def _constraint_change_is_breaking(name: str, old_value: Any, new_value: Any) -> bool:
    if name in {"unique", "primary_key"}:
        return bool(new_value) and not bool(old_value)
    if name == "allowed_values":
        if old_value is None:
            return new_value is not None
        if new_value is None:
            return False
        return not set(old_value).issubset(set(new_value))
    if name in {"regex"}:
        return old_value != new_value and new_value is not None
    if name in {"min_value", "min_length"}:
        if old_value is None:
            return new_value is not None
        if new_value is None:
            return False
        return new_value > old_value
    if name in {"max_value", "max_length"}:
        if old_value is None:
            return new_value is not None
        if new_value is None:
            return False
        return new_value < old_value
    if name in {"precision", "scale"}:
        if old_value is None:
            return new_value is not None
        if new_value is None:
            return False
        return new_value < old_value
    return old_value != new_value


def _is_type_widening(old: DataType, new: DataType) -> bool:
    widening = {
        DataType.INTEGER: {DataType.FLOAT, DataType.DECIMAL, DataType.STRING},
        DataType.FLOAT: {DataType.DECIMAL, DataType.STRING},
        DataType.DECIMAL: {DataType.STRING},
        DataType.DATE: {DataType.DATETIME, DataType.TIMESTAMP, DataType.STRING},
        DataType.DATETIME: {DataType.TIMESTAMP, DataType.STRING},
        DataType.UUID: {DataType.STRING},
        DataType.EMAIL: {DataType.STRING},
        DataType.PHONE: {DataType.STRING},
    }
    return new in widening.get(old, set()) or new == DataType.ANY


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        if pd is not None:
            parsed = pd.to_datetime(value, errors="coerce", utc=True)
            if pd.isna(parsed):
                return None
            return parsed.to_pydatetime()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _is_int_like(value: str) -> bool:
    return bool(re.match(r"^-?\d+$", value.strip()))


def _is_float_like(value: str) -> bool:
    try:
        float(value)
        return True
    except Exception:
        return False


def _looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", value.strip(), re.IGNORECASE))


def _looks_like_uuid(value: str) -> bool:
    return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", value.strip(), re.IGNORECASE))


def _looks_like_json(value: str) -> bool:
    text = value.strip()
    if not ((text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))):
        return False
    try:
        json.loads(text)
        return True
    except Exception:
        return False


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
    "CompatibilityMode",
    "DataLike",
    "DataType",
    "FieldValidationProfile",
    "InMemoryAuditSink",
    "InMemoryMetricsSink",
    "MetricsSink",
    "SchemaChange",
    "SchemaChangeType",
    "SchemaCompatibilityResult",
    "SchemaConfigurationError",
    "SchemaContract",
    "SchemaField",
    "SchemaSeverity",
    "SchemaStatus",
    "SchemaValidationContext",
    "SchemaValidationError",
    "SchemaValidationResult",
    "SchemaValidator",
    "SchemaViolation",
    "build_contract_from_dict",
    "infer_data_type",
    "validate_precision_scale",
    "value_matches_type",
]
