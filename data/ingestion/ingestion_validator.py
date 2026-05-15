#!/usr/bin/env python3
"""
data/ingestion/ingestion_validator.py

Enterprise-grade ingestion validator.

Objetivo:
- Validar registros de ingestão antes de persistir, publicar ou processar.
- Suportar regras declarativas por campo: obrigatório, tipo, regex, enum, range,
  tamanho, data, email, URL, CPF/CNPJ simples, transformações e validações customizadas.
- Gerar relatório estruturado com erros, warnings, métricas de qualidade e amostras.
- Funcionar sem dependências externas obrigatórias.

Uso:
    from data.ingestion.ingestion_validator import IngestionValidator, ValidationSchema, FieldRule

    schema = ValidationSchema(
        name="transactions",
        fields=[
            FieldRule("id", required=True, data_type="str"),
            FieldRule("amount", required=True, data_type="decimal", min_value=0),
            FieldRule("created_at", required=True, data_type="datetime"),
        ],
        unique_fields=["id"],
    )

    validator = IngestionValidator(schema)
    report = validator.validate_records(records)
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union
from urllib.parse import urlparse

try:
    from data.ingestion import IngestionRecord
except Exception:  # pragma: no cover
    @dataclass(frozen=True)
    class IngestionRecord:  # type: ignore
        payload: Any
        source: str = "unknown"
        record_id: str = field(default_factory=lambda: f"ing_{uuid.uuid4().hex[:20]}")
        tenant_id: Optional[str] = None
        metadata: Dict[str, Any] = field(default_factory=dict)


VALIDATOR_VERSION = "1.0.0"
DEFAULT_TIMEZONE = timezone.utc


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class DataType(str, Enum):
    ANY = "any"
    STR = "str"
    INT = "int"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOL = "bool"
    DATE = "date"
    DATETIME = "datetime"
    LIST = "list"
    DICT = "dict"
    EMAIL = "email"
    URL = "url"
    UUID = "uuid"


@dataclass(frozen=True)
class ValidationIssue:
    issue_id: str
    severity: ValidationSeverity
    code: str
    message: str
    record_id: Optional[str] = None
    field: Optional[str] = None
    value_preview: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "record_id": self.record_id,
            "field": self.field,
            "value_preview": self.value_preview,
            "metadata": sanitize_metadata(self.metadata),
        }


@dataclass(frozen=True)
class FieldRule:
    name: str
    required: bool = False
    data_type: Union[DataType, str] = DataType.ANY
    nullable: bool = True
    min_value: Optional[Union[int, float, str, Decimal]] = None
    max_value: Optional[Union[int, float, str, Decimal]] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    regex: Optional[str] = None
    enum_values: Optional[Sequence[Any]] = None
    default: Any = None
    strip: bool = True
    coerce: bool = True
    warning_only: bool = False
    description: str = ""
    custom_validator: Optional[Callable[[Any, Mapping[str, Any]], Optional[str]]] = None

    @property
    def type_enum(self) -> DataType:
        return self.data_type if isinstance(self.data_type, DataType) else DataType(str(self.data_type))


@dataclass(frozen=True)
class CrossFieldRule:
    name: str
    fields: Sequence[str]
    validator: Callable[[Mapping[str, Any]], Optional[str]]
    severity: ValidationSeverity = ValidationSeverity.ERROR
    description: str = ""


@dataclass(frozen=True)
class ValidationSchema:
    name: str
    fields: Sequence[FieldRule] = field(default_factory=list)
    unique_fields: Sequence[str] = field(default_factory=list)
    required_any_of: Sequence[Sequence[str]] = field(default_factory=list)
    cross_field_rules: Sequence[CrossFieldRule] = field(default_factory=list)
    allow_unknown_fields: bool = True
    fail_fast: bool = False
    max_issues: int = 10_000
    sample_invalid_records: int = 50
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def field_map(self) -> Dict[str, FieldRule]:
        return {rule.name: rule for rule in self.fields}


@dataclass(frozen=True)
class RecordValidationResult:
    record_id: str
    valid: bool
    issues: List[ValidationIssue]
    normalized_payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
            "normalized_payload": sanitize_metadata(self.normalized_payload),
        }


@dataclass(frozen=True)
class ValidationReport:
    report_id: str
    schema_name: str
    status: ValidationStatus
    total_records: int
    valid_records: int
    invalid_records: int
    error_count: int
    warning_count: int
    issue_counts: Dict[str, int]
    field_issue_counts: Dict[str, int]
    quality_score: float
    issues: List[ValidationIssue]
    invalid_samples: List[Dict[str, Any]]
    started_at: str
    finished_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == ValidationStatus.PASSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "schema_name": self.schema_name,
            "status": self.status.value,
            "total_records": self.total_records,
            "valid_records": self.valid_records,
            "invalid_records": self.invalid_records,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issue_counts": dict(self.issue_counts),
            "field_issue_counts": dict(self.field_issue_counts),
            "quality_score": self.quality_score,
            "issues": [issue.to_dict() for issue in self.issues],
            "invalid_samples": self.invalid_samples,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": sanitize_metadata(self.metadata),
            "ok": self.ok,
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


class IngestionValidationError(Exception):
    """Base ingestion validation error."""


class SchemaValidationError(IngestionValidationError):
    """Invalid validation schema."""


class IngestionValidator:
    def __init__(self, schema: ValidationSchema) -> None:
        self.schema = schema
        self._validate_schema()

    def validate_records(self, records: Sequence[IngestionRecord]) -> ValidationReport:
        started = utc_now_iso()
        issues: List[ValidationIssue] = []
        invalid_samples: List[Dict[str, Any]] = []
        valid_count = 0
        invalid_count = 0
        seen_unique: DefaultDict[str, Set[str]] = defaultdict(set)

        for record in records:
            result = self.validate_record(record)

            duplicate_issues = self._validate_uniqueness(record, result.normalized_payload, seen_unique)
            if duplicate_issues:
                result_issues = list(result.issues) + duplicate_issues
                result = RecordValidationResult(record_id=record.record_id, valid=False, issues=result_issues, normalized_payload=result.normalized_payload)

            issues.extend(result.issues)
            if result.valid:
                valid_count += 1
            else:
                invalid_count += 1
                if len(invalid_samples) < self.schema.sample_invalid_records:
                    invalid_samples.append({
                        "record_id": record.record_id,
                        "payload_preview": preview_value(record.payload),
                        "issues": [issue.to_dict() for issue in result.issues[:10]],
                    })

            if self.schema.fail_fast and result.issues:
                break
            if len(issues) >= self.schema.max_issues:
                issues.append(make_issue(ValidationSeverity.WARNING, "max_issues_reached", "Limite máximo de issues atingido"))
                break

        error_count = sum(1 for issue in issues if issue.severity == ValidationSeverity.ERROR)
        warning_count = sum(1 for issue in issues if issue.severity == ValidationSeverity.WARNING)
        issue_counts = Counter(issue.code for issue in issues)
        field_issue_counts = Counter(issue.field or "__record__" for issue in issues)
        status = ValidationStatus.PASSED if error_count == 0 else ValidationStatus.PARTIAL if valid_count > 0 else ValidationStatus.FAILED
        total = len(records)
        quality_score = calculate_quality_score(total, invalid_count, error_count, warning_count)

        return ValidationReport(
            report_id=f"val_{uuid.uuid4().hex[:20]}",
            schema_name=self.schema.name,
            status=status,
            total_records=total,
            valid_records=valid_count,
            invalid_records=invalid_count,
            error_count=error_count,
            warning_count=warning_count,
            issue_counts=dict(issue_counts),
            field_issue_counts=dict(field_issue_counts),
            quality_score=quality_score,
            issues=issues[: self.schema.max_issues],
            invalid_samples=invalid_samples,
            started_at=started,
            finished_at=utc_now_iso(),
            metadata={"validator_version": VALIDATOR_VERSION, **sanitize_metadata(self.schema.metadata)},
        )

    def validate_record(self, record: IngestionRecord) -> RecordValidationResult:
        payload = record.payload if isinstance(record.payload, Mapping) else {"value": record.payload}
        normalized = dict(payload)
        issues: List[ValidationIssue] = []
        field_map = self.schema.field_map

        if not self.schema.allow_unknown_fields:
            unknown = set(payload.keys()) - set(field_map.keys())
            for field_name in sorted(unknown):
                issues.append(make_issue(ValidationSeverity.ERROR, "unknown_field", f"Campo não permitido: {field_name}", record.record_id, field_name, payload.get(field_name)))

        for rule in self.schema.fields:
            value_present = rule.name in payload
            value = payload.get(rule.name, rule.default)
            if rule.strip and isinstance(value, str):
                value = value.strip()
            if not value_present and rule.default is not None:
                normalized[rule.name] = rule.default
                value_present = True

            field_issues, coerced_value = self._validate_field(rule, value, value_present, payload, record.record_id)
            issues.extend(field_issues)
            if value_present or rule.default is not None:
                normalized[rule.name] = coerced_value

        issues.extend(self._validate_required_any_of(record.record_id, normalized))
        issues.extend(self._validate_cross_field_rules(record.record_id, normalized))

        has_error = any(issue.severity == ValidationSeverity.ERROR for issue in issues)
        return RecordValidationResult(record_id=record.record_id, valid=not has_error, issues=issues, normalized_payload=normalized)

    def raise_if_invalid(self, records: Sequence[IngestionRecord]) -> ValidationReport:
        report = self.validate_records(records)
        if not report.ok:
            raise IngestionValidationError(f"Validação falhou: errors={report.error_count}, invalid_records={report.invalid_records}")
        return report

    def _validate_field(
        self,
        rule: FieldRule,
        value: Any,
        value_present: bool,
        payload: Mapping[str, Any],
        record_id: str,
    ) -> Tuple[List[ValidationIssue], Any]:
        issues: List[ValidationIssue] = []
        severity = ValidationSeverity.WARNING if rule.warning_only else ValidationSeverity.ERROR

        if rule.required and (not value_present or is_empty(value)):
            issues.append(make_issue(severity, "required_field_missing", f"Campo obrigatório ausente: {rule.name}", record_id, rule.name, value))
            return issues, value

        if not value_present or value is None:
            if not rule.nullable:
                issues.append(make_issue(severity, "null_not_allowed", f"Campo não permite nulo: {rule.name}", record_id, rule.name, value))
            return issues, value

        coerced = value
        if rule.coerce:
            try:
                coerced = coerce_value(value, rule.type_enum)
            except Exception as exc:  # noqa: BLE001
                issues.append(make_issue(severity, "type_coercion_failed", f"Falha ao converter {rule.name} para {rule.type_enum.value}: {exc}", record_id, rule.name, value))
                return issues, value

        if rule.type_enum != DataType.ANY and not validate_type(coerced, rule.type_enum):
            issues.append(make_issue(severity, "invalid_type", f"Tipo inválido para {rule.name}. Esperado={rule.type_enum.value}", record_id, rule.name, value))
            return issues, coerced

        if rule.min_length is not None and len(str(coerced)) < rule.min_length:
            issues.append(make_issue(severity, "min_length_violation", f"Campo {rule.name} menor que {rule.min_length}", record_id, rule.name, value))
        if rule.max_length is not None and len(str(coerced)) > rule.max_length:
            issues.append(make_issue(severity, "max_length_violation", f"Campo {rule.name} maior que {rule.max_length}", record_id, rule.name, value))
        if rule.regex and not re.match(rule.regex, str(coerced)):
            issues.append(make_issue(severity, "regex_violation", f"Campo {rule.name} não corresponde ao padrão", record_id, rule.name, value))
        if rule.enum_values is not None and coerced not in set(rule.enum_values):
            issues.append(make_issue(severity, "enum_violation", f"Campo {rule.name} fora do domínio permitido", record_id, rule.name, value, {"allowed": list(rule.enum_values)}))

        if rule.min_value is not None or rule.max_value is not None:
            numeric_issues = self._validate_numeric_range(rule, coerced, record_id, value, severity)
            issues.extend(numeric_issues)

        if rule.custom_validator:
            try:
                message = rule.custom_validator(coerced, payload)
                if message:
                    issues.append(make_issue(severity, "custom_validator_failed", message, record_id, rule.name, value))
            except Exception as exc:  # noqa: BLE001
                issues.append(make_issue(severity, "custom_validator_exception", f"Validador customizado falhou: {exc}", record_id, rule.name, value))

        return issues, coerced

    def _validate_numeric_range(self, rule: FieldRule, value: Any, record_id: str, original: Any, severity: ValidationSeverity) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        try:
            current = to_decimal(value)
        except Exception:
            return [make_issue(severity, "range_not_numeric", f"Campo {rule.name} não é numérico para range", record_id, rule.name, original)]
        if rule.min_value is not None and current < to_decimal(rule.min_value):
            issues.append(make_issue(severity, "min_value_violation", f"Campo {rule.name} menor que {rule.min_value}", record_id, rule.name, original))
        if rule.max_value is not None and current > to_decimal(rule.max_value):
            issues.append(make_issue(severity, "max_value_violation", f"Campo {rule.name} maior que {rule.max_value}", record_id, rule.name, original))
        return issues

    def _validate_required_any_of(self, record_id: str, payload: Mapping[str, Any]) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        for group in self.schema.required_any_of:
            if not any(not is_empty(payload.get(field)) for field in group):
                issues.append(make_issue(ValidationSeverity.ERROR, "required_any_of_missing", f"Pelo menos um campo é obrigatório: {', '.join(group)}", record_id, None, None, {"fields": list(group)}))
        return issues

    def _validate_cross_field_rules(self, record_id: str, payload: Mapping[str, Any]) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        for rule in self.schema.cross_field_rules:
            try:
                message = rule.validator(payload)
                if message:
                    issues.append(make_issue(rule.severity, "cross_field_rule_failed", message, record_id, None, None, {"rule": rule.name, "fields": list(rule.fields)}))
            except Exception as exc:  # noqa: BLE001
                issues.append(make_issue(ValidationSeverity.ERROR, "cross_field_rule_exception", f"Regra cruzada {rule.name} falhou: {exc}", record_id, None, None))
        return issues

    def _validate_uniqueness(self, record: IngestionRecord, payload: Mapping[str, Any], seen_unique: DefaultDict[str, Set[str]]) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []
        for field_name in self.schema.unique_fields:
            value = payload.get(field_name)
            if is_empty(value):
                continue
            key = stable_hash(value)
            if key in seen_unique[field_name]:
                issues.append(make_issue(ValidationSeverity.ERROR, "duplicate_field_value", f"Valor duplicado para campo único: {field_name}", record.record_id, field_name, value))
            seen_unique[field_name].add(key)
        return issues

    def _validate_schema(self) -> None:
        if not self.schema.name.strip():
            raise SchemaValidationError("schema.name é obrigatório")
        field_names = [rule.name for rule in self.schema.fields]
        duplicates = [name for name, count in Counter(field_names).items() if count > 1]
        if duplicates:
            raise SchemaValidationError(f"Campos duplicados no schema: {duplicates}")
        for rule in self.schema.fields:
            if not rule.name or not re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*$", rule.name):
                raise SchemaValidationError(f"Nome de campo inválido: {rule.name}")
            if rule.regex:
                re.compile(rule.regex)
            if rule.min_length is not None and rule.min_length < 0:
                raise SchemaValidationError(f"min_length inválido em {rule.name}")
            if rule.max_length is not None and rule.max_length < 0:
                raise SchemaValidationError(f"max_length inválido em {rule.name}")
            if rule.min_length is not None and rule.max_length is not None and rule.min_length > rule.max_length:
                raise SchemaValidationError(f"min_length > max_length em {rule.name}")


def coerce_value(value: Any, data_type: DataType) -> Any:
    if data_type == DataType.ANY:
        return value
    if data_type == DataType.STR:
        return str(value)
    if data_type == DataType.INT:
        if isinstance(value, bool):
            raise ValueError("bool não é int válido")
        return int(value)
    if data_type == DataType.FLOAT:
        if isinstance(value, bool):
            raise ValueError("bool não é float válido")
        return float(value)
    if data_type == DataType.DECIMAL:
        return to_decimal(value)
    if data_type == DataType.BOOL:
        return to_bool(value)
    if data_type == DataType.DATE:
        return parse_date(value)
    if data_type == DataType.DATETIME:
        return parse_datetime(value)
    if data_type == DataType.LIST:
        if isinstance(value, list):
            return value
        raise ValueError("valor não é lista")
    if data_type == DataType.DICT:
        if isinstance(value, Mapping):
            return dict(value)
        raise ValueError("valor não é dict")
    if data_type == DataType.EMAIL:
        text = str(value).strip().lower()
        if not is_email(text):
            raise ValueError("email inválido")
        return text
    if data_type == DataType.URL:
        text = str(value).strip()
        if not is_url(text):
            raise ValueError("url inválida")
        return text
    if data_type == DataType.UUID:
        import uuid as uuid_module
        return str(uuid_module.UUID(str(value)))
    return value


def validate_type(value: Any, data_type: DataType) -> bool:
    if data_type == DataType.ANY:
        return True
    if data_type == DataType.STR:
        return isinstance(value, str)
    if data_type == DataType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == DataType.FLOAT:
        return isinstance(value, float) and math.isfinite(value)
    if data_type == DataType.DECIMAL:
        return isinstance(value, Decimal)
    if data_type == DataType.BOOL:
        return isinstance(value, bool)
    if data_type == DataType.DATE:
        return isinstance(value, date) and not isinstance(value, datetime)
    if data_type == DataType.DATETIME:
        return isinstance(value, datetime)
    if data_type == DataType.LIST:
        return isinstance(value, list)
    if data_type == DataType.DICT:
        return isinstance(value, Mapping)
    if data_type == DataType.EMAIL:
        return isinstance(value, str) and is_email(value)
    if data_type == DataType.URL:
        return isinstance(value, str) and is_url(value)
    if data_type == DataType.UUID:
        return isinstance(value, str) and bool(re.match(r"^[0-9a-fA-F-]{36}$", value))
    return True


def to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"decimal inválido: {value}") from exc


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "sim", "s", "on"}:
        return True
    if text in {"0", "false", "no", "n", "não", "nao", "off"}:
        return False
    raise ValueError(f"boolean inválido: {value}")


def parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip().replace("Z", "+00:00")
    if "T" in text or " " in text:
        return datetime.fromisoformat(text).date()
    return date.fromisoformat(text[:10])


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=DEFAULT_TIMEZONE)
    return parsed


def is_email(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", value))


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def make_issue(
    severity: ValidationSeverity,
    code: str,
    message: str,
    record_id: Optional[str] = None,
    field: Optional[str] = None,
    value: Any = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> ValidationIssue:
    return ValidationIssue(
        issue_id=f"iss_{uuid.uuid4().hex[:16]}",
        severity=severity,
        code=code,
        message=message,
        record_id=record_id,
        field=field,
        value_preview=preview_value(value),
        metadata=dict(metadata or {}),
    )


def preview_value(value: Any, limit: int = 200) -> Optional[str]:
    if value is None:
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return text[:limit]


def stable_hash(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = repr(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def calculate_quality_score(total: int, invalid: int, errors: int, warnings: int) -> float:
    if total <= 0:
        return 100.0
    invalid_penalty = (invalid / total) * 70
    error_penalty = min(errors / max(total, 1), 1) * 25
    warning_penalty = min(warnings / max(total, 1), 1) * 5
    return round(max(0.0, 100.0 - invalid_penalty - error_penalty - warning_penalty), 4)


def sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    sensitive = {"password", "secret", "token", "api_key", "apikey", "authorization", "cookie"}
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if any(item in key_text.lower() for item in sensitive):
            result[key_text] = "[REDACTED]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            result[key_text] = value
        else:
            result[key_text] = str(value)[:500]
    return result


def utc_now_iso() -> str:
    return datetime.now(tz=DEFAULT_TIMEZONE).isoformat()


# Common reusable schemas
TRANSACTION_SCHEMA = ValidationSchema(
    name="transaction",
    fields=[
        FieldRule("transaction_id", required=True, data_type=DataType.STR, min_length=1, max_length=128),
        FieldRule("amount", required=True, data_type=DataType.DECIMAL, min_value=0),
        FieldRule("currency", required=True, data_type=DataType.STR, regex=r"^[A-Z]{3}$"),
        FieldRule("timestamp", required=True, data_type=DataType.DATETIME),
        FieldRule("account_id", required=False, data_type=DataType.STR, max_length=128),
    ],
    unique_fields=["transaction_id"],
)

CUSTOMER_SCHEMA = ValidationSchema(
    name="customer",
    fields=[
        FieldRule("customer_id", required=True, data_type=DataType.STR, min_length=1, max_length=128),
        FieldRule("email", required=False, data_type=DataType.EMAIL, warning_only=True),
        FieldRule("name", required=False, data_type=DataType.STR, max_length=255),
        FieldRule("created_at", required=False, data_type=DataType.DATETIME),
    ],
    unique_fields=["customer_id"],
)


__all__ = [
    "VALIDATOR_VERSION",
    "ValidationSeverity",
    "ValidationStatus",
    "DataType",
    "ValidationIssue",
    "FieldRule",
    "CrossFieldRule",
    "ValidationSchema",
    "RecordValidationResult",
    "ValidationReport",
    "IngestionValidationError",
    "SchemaValidationError",
    "IngestionValidator",
    "TRANSACTION_SCHEMA",
    "CUSTOMER_SCHEMA",
    "coerce_value",
    "validate_type",
    "to_decimal",
    "parse_date",
    "parse_datetime",
    "is_email",
    "is_url",
    "make_issue",
]
