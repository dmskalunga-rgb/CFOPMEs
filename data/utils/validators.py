"""
data/utils/validators.py

Enterprise-grade validation utility toolkit.

Este módulo centraliza validadores utilitários e reutilizáveis para toda a
plataforma de dados: ingestão, validação, serialização, configuração, APIs,
paths, strings, números, datas, schemas simples e regras compostas.

Capacidades principais:
- Validação estruturada com ValidationResult e ValidationIssue.
- Predicados reutilizáveis para valores comuns.
- Validação de strings, emails, URLs, UUIDs, CPF/CNPJ, números, ranges e datas.
- Validação de paths com proteção contra path traversal.
- Validação de mappings/configurações com campos obrigatórios e tipos esperados.
- Composição AND/OR/NOT de validadores.
- Decorator para validar argumentos de funções.
- API sem dependências externas obrigatórias.
"""

from __future__ import annotations

import functools
import ipaddress
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Pattern, Sequence, Tuple, Type, TypeVar, Union
from urllib.parse import urlparse


T = TypeVar("T")
JsonDict = Dict[str, Any]
PathLike = Union[str, os.PathLike[str]]
ValidatorFn = Callable[[Any], bool]
StructuredValidatorFn = Callable[[Any], "ValidationResult"]


class ValidationSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"


class ValidationCode(str, Enum):
    REQUIRED = "REQUIRED"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_VALUE = "INVALID_VALUE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    TOO_SHORT = "TOO_SHORT"
    TOO_LONG = "TOO_LONG"
    NOT_ALLOWED = "NOT_ALLOWED"
    PATH_UNSAFE = "PATH_UNSAFE"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    FIELD_MISSING = "FIELD_MISSING"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    CUSTOM = "CUSTOM"


class ValidatorError(Exception):
    """Erro base de validação utilitária."""


class ValidationFailedError(ValidatorError):
    """Erro lançado quando validação estruturada falha."""

    def __init__(self, result: "ValidationResult") -> None:
        self.result = result
        super().__init__(result.summary())


@dataclass(frozen=True)
class ValidationIssue:
    """Issue de validação utilitária."""

    code: ValidationCode
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    field: Optional[str] = None
    value: Any = None
    expected: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "code": self.code.value,
            "message": self.message,
            "severity": self.severity.value,
            "field": self.field,
            "value": safe_json_value(self.value),
            "expected": safe_json_value(self.expected),
            "metadata": safe_json_value(dict(self.metadata)),
        }


@dataclass(frozen=True)
class ValidationResult:
    """Resultado estruturado de validação."""

    status: ValidationStatus
    issues: Tuple[ValidationIssue, ...] = field(default_factory=tuple)
    value: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {ValidationStatus.PASSED, ValidationStatus.WARNING}

    @property
    def failed(self) -> bool:
        return not self.ok

    @staticmethod
    def passed(value: Any = None, **metadata: Any) -> "ValidationResult":
        return ValidationResult(status=ValidationStatus.PASSED, value=value, metadata=metadata)

    @staticmethod
    def failed(*issues: ValidationIssue, value: Any = None, **metadata: Any) -> "ValidationResult":
        return ValidationResult(status=ValidationStatus.FAILED, issues=tuple(issues), value=value, metadata=metadata)

    @staticmethod
    def warning(*issues: ValidationIssue, value: Any = None, **metadata: Any) -> "ValidationResult":
        return ValidationResult(status=ValidationStatus.WARNING, issues=tuple(issues), value=value, metadata=metadata)

    def raise_for_failure(self) -> None:
        if self.failed:
            raise ValidationFailedError(self)

    def summary(self) -> str:
        return f"ValidationResult(status={self.status.value}, issues={len(self.issues)})"

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status.value,
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "value": safe_json_value(self.value),
            "metadata": safe_json_value(dict(self.metadata)),
            "summary": self.summary(),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


@dataclass(frozen=True)
class FieldRule:
    """Regra simples para validação de campo em Mapping."""

    name: str
    required: bool = True
    expected_type: Optional[Union[Type[Any], Tuple[Type[Any], ...]]] = None
    validator: Optional[ValidatorFn] = None
    allowed_values: Optional[Iterable[Any]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[Union[str, Pattern[str]]] = None
    description: Optional[str] = None


EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
CPF_PATTERN = re.compile(r"^\d{3}\.?\d{3}\.?\d{3}-?\d{2}$")
CNPJ_PATTERN = re.compile(r"^\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
COLUMN_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# =============================================================================
# Basic predicates
# =============================================================================

def is_present(value: Any) -> bool:
    return value is not None and value != ""


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return len(value) == 0  # type: ignore[arg-type]
    except Exception:
        return False


def is_type(value: Any, expected_type: Union[Type[Any], Tuple[Type[Any], ...]]) -> bool:
    return isinstance(value, expected_type)


def is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def is_int(value: Any, *, allow_bool: bool = False) -> bool:
    return isinstance(value, int) and (allow_bool or not isinstance(value, bool))


def is_float(value: Any) -> bool:
    return isinstance(value, float) and not math.isnan(value)


def is_number(value: Any, *, allow_bool: bool = False, finite: bool = True) -> bool:
    if isinstance(value, bool) and not allow_bool:
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) if finite else True


def is_between(value: Any, minimum: Optional[float] = None, maximum: Optional[float] = None, *, inclusive: bool = True) -> bool:
    if not is_number(value):
        return False
    number = float(value)
    if minimum is not None:
        if number < minimum or (not inclusive and number == minimum):
            return False
    if maximum is not None:
        if number > maximum or (not inclusive and number == maximum):
            return False
    return True


def is_string(value: Any, *, min_length: Optional[int] = None, max_length: Optional[int] = None, strip: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip() if strip else value
    if min_length is not None and len(text) < min_length:
        return False
    if max_length is not None and len(text) > max_length:
        return False
    return True


def matches_pattern(value: Any, pattern: Union[str, Pattern[str]]) -> bool:
    if not isinstance(value, str):
        return False
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    return compiled.search(value) is not None


def is_email(value: Any) -> bool:
    return isinstance(value, str) and EMAIL_PATTERN.match(value.strip()) is not None


def is_url(value: Any, *, require_scheme: bool = True, allowed_schemes: Optional[Iterable[str]] = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value.strip())
    if require_scheme and not parsed.scheme:
        return False
    if allowed_schemes and parsed.scheme.lower() not in {scheme.lower() for scheme in allowed_schemes}:
        return False
    return bool(parsed.netloc) if parsed.scheme else bool(parsed.path)


def is_uuid(value: Any, *, version: Optional[int] = None) -> bool:
    try:
        parsed = uuid.UUID(str(value), version=version)
        return str(parsed) == str(value).lower() or parsed.hex == str(value).replace("-", "").lower()
    except Exception:
        return False


def is_ip_address(value: Any, *, version: Optional[int] = None) -> bool:
    try:
        ip = ipaddress.ip_address(str(value))
        return version is None or ip.version == version
    except Exception:
        return False


def is_cpf_format(value: Any) -> bool:
    return isinstance(value, str) and CPF_PATTERN.match(value.strip()) is not None


def is_cnpj_format(value: Any) -> bool:
    return isinstance(value, str) and CNPJ_PATTERN.match(value.strip()) is not None


def is_slug(value: Any) -> bool:
    return isinstance(value, str) and SLUG_PATTERN.match(value) is not None


def is_snake_case(value: Any) -> bool:
    return isinstance(value, str) and SNAKE_CASE_PATTERN.match(value) is not None


def is_column_name(value: Any) -> bool:
    return isinstance(value, str) and COLUMN_NAME_PATTERN.match(value) is not None


def is_datetime_like(value: Any) -> bool:
    return parse_datetime(value) is not None


def is_date_range(start: Any, end: Any, *, allow_equal: bool = True) -> bool:
    start_dt = parse_datetime(start)
    end_dt = parse_datetime(end)
    if start_dt is None or end_dt is None:
        return False
    return start_dt <= end_dt if allow_equal else start_dt < end_dt


# =============================================================================
# Path validators
# =============================================================================

def is_safe_path(path: PathLike, *, base_dir: Optional[PathLike] = None, allow_absolute: bool = True) -> bool:
    try:
        target = Path(path)
        if target.is_absolute() and not allow_absolute:
            return False
        if base_dir is not None:
            base = Path(base_dir).resolve()
            resolved = (base / target).resolve() if not target.is_absolute() else target.resolve()
            return str(resolved).startswith(str(base))
        parts = target.parts
        return ".." not in parts
    except Exception:
        return False


def path_exists(path: PathLike) -> bool:
    return Path(path).exists()


def is_file(path: PathLike) -> bool:
    return Path(path).is_file()


def is_dir(path: PathLike) -> bool:
    return Path(path).is_dir()


def has_allowed_extension(path: PathLike, extensions: Iterable[str]) -> bool:
    allowed = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in extensions}
    return Path(path).suffix.lower() in allowed


# =============================================================================
# Structured validation
# =============================================================================

def validate_required(value: Any, *, field: Optional[str] = None) -> ValidationResult:
    if is_present(value):
        return ValidationResult.passed(value=value)
    return ValidationResult.failed(
        ValidationIssue(
            code=ValidationCode.REQUIRED,
            message="Value is required",
            field=field,
            value=value,
        ),
        value=value,
    )


def validate_type(value: Any, expected_type: Union[Type[Any], Tuple[Type[Any], ...]], *, field: Optional[str] = None) -> ValidationResult:
    if isinstance(value, expected_type):
        return ValidationResult.passed(value=value)
    return ValidationResult.failed(
        ValidationIssue(
            code=ValidationCode.TYPE_MISMATCH,
            message="Invalid value type",
            field=field,
            value=value,
            expected=str(expected_type),
        ),
        value=value,
    )


def validate_range(value: Any, minimum: Optional[float] = None, maximum: Optional[float] = None, *, field: Optional[str] = None) -> ValidationResult:
    if is_between(value, minimum, maximum):
        return ValidationResult.passed(value=value)
    return ValidationResult.failed(
        ValidationIssue(
            code=ValidationCode.OUT_OF_RANGE,
            message="Value is out of range",
            field=field,
            value=value,
            expected={"minimum": minimum, "maximum": maximum},
        ),
        value=value,
    )


def validate_length(value: Any, min_length: Optional[int] = None, max_length: Optional[int] = None, *, field: Optional[str] = None) -> ValidationResult:
    try:
        length = len(value)
    except Exception:
        return ValidationResult.failed(
            ValidationIssue(ValidationCode.INVALID_VALUE, "Value has no length", field=field, value=value),
            value=value,
        )
    if min_length is not None and length < min_length:
        return ValidationResult.failed(
            ValidationIssue(ValidationCode.TOO_SHORT, "Value is too short", field=field, value=value, expected=min_length),
            value=value,
        )
    if max_length is not None and length > max_length:
        return ValidationResult.failed(
            ValidationIssue(ValidationCode.TOO_LONG, "Value is too long", field=field, value=value, expected=max_length),
            value=value,
        )
    return ValidationResult.passed(value=value)


def validate_pattern(value: Any, pattern: Union[str, Pattern[str]], *, field: Optional[str] = None) -> ValidationResult:
    if matches_pattern(value, pattern):
        return ValidationResult.passed(value=value)
    return ValidationResult.failed(
        ValidationIssue(
            code=ValidationCode.INVALID_FORMAT,
            message="Value does not match required pattern",
            field=field,
            value=value,
            expected=pattern.pattern if hasattr(pattern, "pattern") else str(pattern),
        ),
        value=value,
    )


def validate_allowed_values(value: Any, allowed_values: Iterable[Any], *, field: Optional[str] = None) -> ValidationResult:
    allowed_tuple = tuple(allowed_values)
    if value in allowed_tuple:
        return ValidationResult.passed(value=value)
    return ValidationResult.failed(
        ValidationIssue(
            code=ValidationCode.NOT_ALLOWED,
            message="Value is not allowed",
            field=field,
            value=value,
            expected=allowed_tuple,
        ),
        value=value,
    )


def validate_mapping(payload: Mapping[str, Any], rules: Sequence[FieldRule]) -> ValidationResult:
    issues: List[ValidationIssue] = []
    for rule in rules:
        exists = rule.name in payload
        value = payload.get(rule.name)
        if rule.required and not exists:
            issues.append(ValidationIssue(ValidationCode.FIELD_MISSING, "Required field is missing", field=rule.name))
            continue
        if not exists:
            continue
        if rule.expected_type is not None and not isinstance(value, rule.expected_type):
            issues.append(
                ValidationIssue(
                    ValidationCode.TYPE_MISMATCH,
                    "Invalid field type",
                    field=rule.name,
                    value=value,
                    expected=str(rule.expected_type),
                )
            )
            continue
        if rule.allowed_values is not None and value not in tuple(rule.allowed_values):
            issues.append(ValidationIssue(ValidationCode.NOT_ALLOWED, "Field value is not allowed", field=rule.name, value=value))
        if rule.min_value is not None or rule.max_value is not None:
            if not is_between(value, rule.min_value, rule.max_value):
                issues.append(ValidationIssue(ValidationCode.OUT_OF_RANGE, "Field value is out of range", field=rule.name, value=value))
        if rule.min_length is not None or rule.max_length is not None:
            length_result = validate_length(value, rule.min_length, rule.max_length, field=rule.name)
            issues.extend(length_result.issues)
        if rule.pattern is not None and not matches_pattern(value, rule.pattern):
            issues.append(ValidationIssue(ValidationCode.INVALID_FORMAT, "Field has invalid format", field=rule.name, value=value))
        if rule.validator is not None and not rule.validator(value):
            issues.append(ValidationIssue(ValidationCode.CUSTOM, "Custom validator rejected field", field=rule.name, value=value))
    return ValidationResult.failed(*issues, value=payload) if issues else ValidationResult.passed(value=payload)


def validate_path(
    path: PathLike,
    *,
    base_dir: Optional[PathLike] = None,
    must_exist: bool = False,
    file: bool = False,
    directory: bool = False,
    allowed_extensions: Optional[Iterable[str]] = None,
) -> ValidationResult:
    if not is_safe_path(path, base_dir=base_dir):
        return ValidationResult.failed(ValidationIssue(ValidationCode.PATH_UNSAFE, "Path is unsafe", value=str(path)))
    target = Path(path)
    if must_exist and not target.exists():
        return ValidationResult.failed(ValidationIssue(ValidationCode.PATH_NOT_FOUND, "Path does not exist", value=str(path)))
    if file and target.exists() and not target.is_file():
        return ValidationResult.failed(ValidationIssue(ValidationCode.INVALID_VALUE, "Path is not a file", value=str(path)))
    if directory and target.exists() and not target.is_dir():
        return ValidationResult.failed(ValidationIssue(ValidationCode.INVALID_VALUE, "Path is not a directory", value=str(path)))
    if allowed_extensions is not None and not has_allowed_extension(target, allowed_extensions):
        return ValidationResult.failed(
            ValidationIssue(ValidationCode.NOT_ALLOWED, "File extension is not allowed", value=str(path), expected=tuple(allowed_extensions))
        )
    return ValidationResult.passed(value=str(target))


# =============================================================================
# Composition helpers
# =============================================================================

def all_of(*validators: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return all(validator(value) for validator in validators)
    return _validator


def any_of(*validators: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return any(validator(value) for validator in validators)
    return _validator


def none_of(*validators: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return not any(validator(value) for validator in validators)
    return _validator


def not_(validator: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return not validator(value)
    return _validator


def optional(validator: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return value is None or value == "" or validator(value)
    return _validator


def required(validator: ValidatorFn) -> ValidatorFn:
    def _validator(value: Any) -> bool:
        return is_present(value) and validator(value)
    return _validator


# =============================================================================
# Decorators and utility helpers
# =============================================================================

def validate_arguments(*validators: Callable[..., bool]) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator para validar argumentos de função com predicados."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            for validator in validators:
                if not validator(*args, **kwargs):
                    raise ValidationFailedError(
                        ValidationResult.failed(
                            ValidationIssue(
                                ValidationCode.CUSTOM,
                                f"Argument validator failed: {getattr(validator, '__name__', repr(validator))}",
                            )
                        )
                    )
            return func(*args, **kwargs)
        return wrapper
    return decorator


def combine_results(*results: ValidationResult) -> ValidationResult:
    issues: List[ValidationIssue] = []
    for result in results:
        issues.extend(result.issues)
    if issues:
        return ValidationResult.failed(*issues)
    if any(result.status == ValidationStatus.WARNING for result in results):
        return ValidationResult.warning()
    return ValidationResult.passed()


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def safe_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def assert_valid(result: ValidationResult) -> ValidationResult:
    result.raise_for_failure()
    return result


__all__ = [
    "CNPJ_PATTERN",
    "COLUMN_NAME_PATTERN",
    "CPF_PATTERN",
    "EMAIL_PATTERN",
    "FieldRule",
    "JsonDict",
    "PathLike",
    "SLUG_PATTERN",
    "SNAKE_CASE_PATTERN",
    "StructuredValidatorFn",
    "ValidationCode",
    "ValidationFailedError",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
    "ValidationStatus",
    "ValidatorError",
    "ValidatorFn",
    "all_of",
    "any_of",
    "assert_valid",
    "combine_results",
    "has_allowed_extension",
    "is_between",
    "is_bool",
    "is_cnpj_format",
    "is_column_name",
    "is_cpf_format",
    "is_date_range",
    "is_datetime_like",
    "is_dir",
    "is_email",
    "is_empty",
    "is_file",
    "is_float",
    "is_int",
    "is_ip_address",
    "is_number",
    "is_present",
    "is_safe_path",
    "is_slug",
    "is_snake_case",
    "is_string",
    "is_type",
    "is_url",
    "is_uuid",
    "matches_pattern",
    "none_of",
    "not_",
    "optional",
    "parse_datetime",
    "path_exists",
    "required",
    "safe_json_value",
    "validate_allowed_values",
    "validate_arguments",
    "validate_length",
    "validate_mapping",
    "validate_path",
    "validate_pattern",
    "validate_range",
    "validate_required",
    "validate_type",
]
