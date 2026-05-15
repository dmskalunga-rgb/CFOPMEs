"""
data/processing/normalization.py

Enterprise-grade data normalization module for data platforms.

Purpose
-------
Provides a robust, dependency-light normalization layer for ETL/ELT pipelines,
streaming jobs, APIs, data quality workflows, MDM, feature pipelines and
analytics preparation.

Core capabilities
-----------------
- Row, field and value normalization.
- Text normalization: trim, case, whitespace, accents, punctuation, regex,
  slug/key generation and redaction.
- Numeric normalization: coercion, decimal precision, clipping, scaling and
  safe NaN/Infinity handling.
- Datetime normalization: ISO-8601 output, timezone normalization, epoch parsing.
- Boolean normalization with multilingual truthy/falsy values.
- Identifier/key normalization.
- Schema-driven normalization profiles.
- Reusable normalizer registry.
- Validation, error reports and JSON export.
- Optional telemetry integration.
- Standard library only.

Example
-------
normalizer = DataNormalizer()
result = normalizer.normalize(
    [{"name": " João   Silva ", "active": "sim", "amount": "10,50"}],
    schema=NormalizationSchema(fields={
        "name": NormalizationRule(kind=NormalizationKind.TEXT, remove_accents=True, titlecase=True),
        "active": NormalizationRule(kind=NormalizationKind.BOOLEAN),
        "amount": NormalizationRule(kind=NormalizationKind.NUMERIC, decimal_separator=",", precision=2),
    }),
)
print(result.to_json())
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Pattern, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
MULTISPACE_PATTERN = re.compile(r"\s+")
NON_KEY_PATTERN = re.compile(r"[^a-zA-Z0-9_]+")
MAX_TEXT_LENGTH = 50_000


class NormalizationKind(str, Enum):
    AUTO = "auto"
    TEXT = "text"
    KEY = "key"
    SLUG = "slug"
    NUMERIC = "numeric"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"
    LIST = "list"
    CUSTOM = "custom"


class CaseMode(str, Enum):
    PRESERVE = "preserve"
    LOWER = "lower"
    UPPER = "upper"
    TITLE = "title"
    SNAKE = "snake"
    KEBAB = "kebab"
    CAMEL = "camel"


class NullPolicy(str, Enum):
    KEEP = "keep"
    DEFAULT = "default"
    EMPTY_STRING = "empty_string"
    ZERO = "zero"
    DROP_FIELD = "drop_field"
    ERROR = "error"


class ErrorPolicy(str, Enum):
    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"


class NormalizationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


@dataclass(frozen=True)
class RegexRule:
    pattern: str
    replacement: str
    flags: int = 0

    def compiled(self) -> Pattern[str]:
        return re.compile(self.pattern, self.flags)


@dataclass(frozen=True)
class NormalizationRule:
    kind: NormalizationKind = NormalizationKind.AUTO
    null_policy: NullPolicy = NullPolicy.KEEP
    default: Any = None
    required: bool = False
    output_field: Optional[str] = None
    case_mode: CaseMode = CaseMode.PRESERVE
    trim: bool = True
    collapse_whitespace: bool = True
    remove_accents: bool = False
    remove_punctuation: bool = False
    max_length: Optional[int] = None
    min_length: Optional[int] = None
    regex_rules: Tuple[RegexRule, ...] = field(default_factory=tuple)
    allowed_pattern: Optional[str] = None
    decimal_separator: str = "."
    thousands_separator: Optional[str] = None
    precision: Optional[int] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    timezone_utc: bool = True
    datetime_format: Optional[str] = None
    list_separator: str = ","
    custom_function: Optional[Callable[[Any, Mapping[str, Any]], Any]] = None
    redact: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, field_name: str) -> None:
        if self.required and self.null_policy == NullPolicy.KEEP:
            # Required + KEEP is allowed, but missing values will raise during execution.
            pass
        if self.kind == NormalizationKind.CUSTOM and not self.custom_function:
            raise NormalizationConfigError(f"Field {field_name}: custom kind requires custom_function")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise NormalizationConfigError(f"Field {field_name}: min_value cannot exceed max_value")
        if self.min_length is not None and self.max_length is not None and self.min_length > self.max_length:
            raise NormalizationConfigError(f"Field {field_name}: min_length cannot exceed max_length")


@dataclass(frozen=True)
class NormalizationSchema:
    fields: Dict[str, NormalizationRule] = field(default_factory=dict)
    include_unknown_fields: bool = True
    normalize_unknown_keys: bool = False
    key_case_mode: CaseMode = CaseMode.SNAKE
    row_filter: Optional[Callable[[Mapping[str, Any]], bool]] = None
    row_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for field_name, rule in self.fields.items():
            rule.validate(field_name)


@dataclass(frozen=True)
class NormalizationConfig:
    error_policy: ErrorPolicy = ErrorPolicy.CONTINUE
    telemetry_enabled: bool = True
    include_rows: bool = True
    include_errors: bool = True
    max_output_rows: int = 1_000_000
    report_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "NormalizationConfig":
        return cls(
            error_policy=ErrorPolicy(os.getenv("NORMALIZATION_ERROR_POLICY", ErrorPolicy.CONTINUE.value)),
            telemetry_enabled=bool_env("NORMALIZATION_TELEMETRY_ENABLED", True),
            include_rows=bool_env("NORMALIZATION_INCLUDE_ROWS", True),
            include_errors=bool_env("NORMALIZATION_INCLUDE_ERRORS", True),
            max_output_rows=int_env("NORMALIZATION_MAX_OUTPUT_ROWS", 1_000_000),
            report_path=os.getenv("NORMALIZATION_REPORT_PATH"),
        )


@dataclass(frozen=True)
class NormalizationErrorRecord:
    id: str
    timestamp: str
    row_index: int
    field: Optional[str]
    error_type: str
    message: str
    original: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class NormalizationResult:
    id: str
    status: NormalizationStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    normalized_count: int
    skipped_count: int
    error_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[NormalizationErrorRecord] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["errors"] = [err.to_dict() for err in self.errors]
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class NormalizationError(Exception):
    """Base normalization error."""


class NormalizationConfigError(NormalizationError):
    """Invalid normalization configuration."""


class NormalizationValueError(NormalizationError):
    """Failed to normalize a value."""


class Normalizer(Protocol):
    def normalize_value(self, value: Any, rule: NormalizationRule, row: Mapping[str, Any]) -> Any:
        ...


class _DropField:
    pass


DROP_FIELD = _DropField()


class DataNormalizer:
    """Enterprise data normalizer."""

    def __init__(self, config: Optional[NormalizationConfig] = None) -> None:
        self.config = config or NormalizationConfig.from_env()

    def normalize(
        self,
        rows: Iterable[Any],
        *,
        schema: Optional[NormalizationSchema] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> NormalizationResult:
        schema = schema or NormalizationSchema()
        schema.validate()
        started = monotonic_ms()
        started_iso = utc_now_iso()
        input_count = 0
        skipped_count = 0
        errors: List[NormalizationErrorRecord] = []
        output_rows: List[Dict[str, Any]] = []

        with telemetry_operation("normalization.normalize", self.config.telemetry_enabled, attributes={"fields": list(schema.fields)}):
            for row_index, raw in enumerate(rows):
                input_count += 1
                try:
                    row = dict(to_mapping(raw))
                    normalized = self.normalize_row(row, schema)
                    if schema.row_filter and not schema.row_filter(normalized):
                        skipped_count += 1
                        continue
                    if schema.row_transform:
                        normalized = schema.row_transform(normalized)
                    if self.config.include_rows and len(output_rows) < self.config.max_output_rows:
                        output_rows.append(normalized)
                except Exception as exc:
                    error = NormalizationErrorRecord(
                        id=str(uuid.uuid4()),
                        timestamp=utc_now_iso(),
                        row_index=row_index,
                        field=None,
                        error_type=exc.__class__.__name__,
                        message=str(exc),
                        original=sanitize_value(raw),
                    )
                    errors.append(error)
                    if self.config.error_policy == ErrorPolicy.FAIL_FAST:
                        raise

        duration_ms = monotonic_ms() - started
        status = determine_status(input_count, output_rows, skipped_count, errors)
        result = NormalizationResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            output_count=len(output_rows),
            normalized_count=len(output_rows),
            skipped_count=skipped_count,
            error_count=len(errors),
            rows=output_rows if self.config.include_rows else [],
            errors=errors if self.config.include_errors else [],
            metadata=sanitize_mapping({"schema": schema_to_dict(schema), **dict(metadata or {})}),
        )
        self._save_report(result)
        telemetry_metric("normalization.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("normalization.output_count", len(output_rows), self.config.telemetry_enabled)
        telemetry_metric("normalization.error_count", len(errors), self.config.telemetry_enabled)
        telemetry_metric("normalization.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def normalize_one(self, row: Any, *, schema: Optional[NormalizationSchema] = None) -> Dict[str, Any]:
        result = self.normalize([row], schema=schema)
        if result.errors:
            raise NormalizationValueError(result.errors[0].message)
        return result.rows[0] if result.rows else {}

    def normalize_row(self, row: Mapping[str, Any], schema: NormalizationSchema) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        if schema.include_unknown_fields:
            for key, value in row.items():
                if key in schema.fields:
                    continue
                out_key = normalize_key(key, case_mode=schema.key_case_mode) if schema.normalize_unknown_keys else str(key)
                normalized[out_key] = sanitize_value(value)

        for field_name, rule in schema.fields.items():
            output_field = rule.output_field or field_name
            if schema.normalize_unknown_keys or rule.output_field is None:
                output_field = normalize_key(output_field, case_mode=schema.key_case_mode)
            value = get_field(row, field_name)
            try:
                normalized_value = normalize_value(value, rule, row)
                if normalized_value is DROP_FIELD:
                    normalized.pop(output_field, None)
                    continue
                normalized[output_field] = normalized_value
            except Exception as exc:
                raise NormalizationValueError(f"Field {field_name}: {exc}") from exc
        return normalized

    def _save_report(self, result: NormalizationResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


def normalize_value(value: Any, rule: NormalizationRule, row: Mapping[str, Any]) -> Any:
    if value is None or value == "":
        if rule.required:
            raise NormalizationValueError("required value is missing")
        if rule.null_policy == NullPolicy.KEEP:
            return None
        if rule.null_policy == NullPolicy.DEFAULT:
            value = rule.default
        elif rule.null_policy == NullPolicy.EMPTY_STRING:
            value = ""
        elif rule.null_policy == NullPolicy.ZERO:
            value = 0
        elif rule.null_policy == NullPolicy.DROP_FIELD:
            return DROP_FIELD
        elif rule.null_policy == NullPolicy.ERROR:
            raise NormalizationValueError("null value is not allowed")

    if rule.redact:
        return "[REDACTED]"

    kind = infer_kind(value) if rule.kind == NormalizationKind.AUTO else rule.kind

    if kind == NormalizationKind.TEXT:
        return normalize_text(value, rule)
    if kind == NormalizationKind.KEY:
        return normalize_key(value, case_mode=rule.case_mode)
    if kind == NormalizationKind.SLUG:
        return normalize_slug(value)
    if kind == NormalizationKind.NUMERIC:
        return normalize_number(value, rule, as_integer=False, as_decimal=False)
    if kind == NormalizationKind.INTEGER:
        return int(normalize_number(value, rule, as_integer=True, as_decimal=False))
    if kind == NormalizationKind.DECIMAL:
        return normalize_number(value, rule, as_integer=False, as_decimal=True)
    if kind == NormalizationKind.BOOLEAN:
        return normalize_bool(value)
    if kind == NormalizationKind.DATE:
        return normalize_datetime(value, rule).date().isoformat()
    if kind == NormalizationKind.DATETIME:
        return normalize_datetime(value, rule).isoformat()
    if kind == NormalizationKind.JSON:
        return normalize_json(value)
    if kind == NormalizationKind.LIST:
        return normalize_list(value, rule)
    if kind == NormalizationKind.CUSTOM and rule.custom_function:
        return sanitize_value(rule.custom_function(value, row))
    return sanitize_value(value)


def normalize_text(value: Any, rule: Optional[NormalizationRule] = None) -> str:
    rule = rule or NormalizationRule(kind=NormalizationKind.TEXT)
    text = str(value)
    if rule.trim:
        text = text.strip()
    if rule.remove_accents:
        text = remove_accents(text)
    for regex_rule in rule.regex_rules:
        text = regex_rule.compiled().sub(regex_rule.replacement, text)
    if rule.remove_punctuation:
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    if rule.collapse_whitespace:
        text = MULTISPACE_PATTERN.sub(" ", text)
    if rule.allowed_pattern:
        allowed = re.compile(rule.allowed_pattern)
        text = "".join(ch for ch in text if allowed.match(ch))
    text = apply_case(text, rule.case_mode)
    if rule.max_length is not None and len(text) > rule.max_length:
        text = text[: rule.max_length]
    if rule.min_length is not None and len(text) < rule.min_length:
        raise NormalizationValueError(f"text shorter than min_length={rule.min_length}")
    return text


def normalize_key(value: Any, *, case_mode: CaseMode = CaseMode.SNAKE) -> str:
    text = remove_accents(str(value).strip())
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = NON_KEY_PATTERN.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "field"
    return apply_case(text, case_mode if case_mode != CaseMode.PRESERVE else CaseMode.SNAKE)


def normalize_slug(value: Any) -> str:
    text = remove_accents(str(value).strip().lower())
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def apply_case(text: str, mode: CaseMode) -> str:
    if mode == CaseMode.PRESERVE:
        return text
    if mode == CaseMode.LOWER:
        return text.lower()
    if mode == CaseMode.UPPER:
        return text.upper()
    if mode == CaseMode.TITLE:
        return text.title()
    if mode == CaseMode.SNAKE:
        return normalize_case_tokens(text, "_").lower()
    if mode == CaseMode.KEBAB:
        return normalize_case_tokens(text, "-").lower()
    if mode == CaseMode.CAMEL:
        parts = normalize_case_tokens(text, "_").lower().split("_")
        return parts[0] + "".join(part.title() for part in parts[1:]) if parts else ""
    return text


def normalize_case_tokens(text: str, separator: str) -> str:
    base = remove_accents(text)
    base = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base)
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", base) if part]
    return separator.join(parts)


def normalize_number(value: Any, rule: NormalizationRule, *, as_integer: bool, as_decimal: bool) -> Any:
    raw = value
    if isinstance(value, str):
        raw = value.strip()
        if rule.thousands_separator:
            raw = raw.replace(rule.thousands_separator, "")
        if rule.decimal_separator != ".":
            raw = raw.replace(rule.decimal_separator, ".")
    try:
        number = Decimal(str(raw))
    except InvalidOperation as exc:
        raise NormalizationValueError(f"cannot parse number: {value!r}") from exc
    if not number.is_finite():
        raise NormalizationValueError(f"invalid number: {value!r}")
    if rule.min_value is not None and number < Decimal(str(rule.min_value)):
        number = Decimal(str(rule.min_value))
    if rule.max_value is not None and number > Decimal(str(rule.max_value)):
        number = Decimal(str(rule.max_value))
    if rule.precision is not None:
        quant = Decimal("1") if rule.precision == 0 else Decimal("1." + "0" * rule.precision)
        number = number.quantize(quant, rounding=ROUND_HALF_UP)
    if as_integer:
        return int(number)
    if as_decimal:
        return str(number)
    return float(number)


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    truthy = {"1", "true", "t", "yes", "y", "sim", "s", "on", "ativo", "active"}
    falsy = {"0", "false", "f", "no", "n", "nao", "não", "off", "inativo", "inactive"}
    if text in truthy:
        return True
    if text in falsy:
        return False
    raise NormalizationValueError(f"cannot parse boolean: {value!r}")


def normalize_datetime(value: Any, rule: NormalizationRule) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        raw = float(value)
        dt = datetime.fromtimestamp(raw / 1000.0 if raw > 10_000_000_000 else raw, timezone.utc)
    else:
        text = str(value).strip()
        if rule.datetime_format:
            dt = datetime.strptime(text, rule.datetime_format)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if rule.timezone_utc:
        dt = dt.astimezone(timezone.utc)
    return dt


def normalize_json(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_value(json.loads(value))
    return sanitize_value(value)


def normalize_list(value: Any, rule: NormalizationRule) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, (tuple, set)):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        return [normalize_text(item, dataclasses.replace(rule, kind=NormalizationKind.TEXT)) for item in value.split(rule.list_separator)]
    return [sanitize_value(value)]


def infer_kind(value: Any) -> NormalizationKind:
    if isinstance(value, bool):
        return NormalizationKind.BOOLEAN
    if isinstance(value, int):
        return NormalizationKind.INTEGER
    if isinstance(value, (float, Decimal)):
        return NormalizationKind.NUMERIC
    if isinstance(value, datetime):
        return NormalizationKind.DATETIME
    if isinstance(value, date):
        return NormalizationKind.DATE
    if isinstance(value, (list, tuple, set)):
        return NormalizationKind.LIST
    if isinstance(value, Mapping):
        return NormalizationKind.JSON
    return NormalizationKind.TEXT


def remove_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def get_field(row: Mapping[str, Any], field_path: str) -> Any:
    current: Any = row
    for part in field_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def to_mapping(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if dataclasses.is_dataclass(row):
        return asdict(row)
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "__dict__"):
        return vars(row)
    raise NormalizationValueError(f"Unsupported row type: {type(row)!r}")


def determine_status(input_count: int, rows: Sequence[Mapping[str, Any]], skipped_count: int, errors: Sequence[NormalizationErrorRecord]) -> NormalizationStatus:
    if input_count == 0:
        return NormalizationStatus.EMPTY
    if errors and not rows:
        return NormalizationStatus.FAILED
    if errors or skipped_count:
        return NormalizationStatus.PARTIAL
    return NormalizationStatus.SUCCEEDED


def schema_to_dict(schema: NormalizationSchema) -> Dict[str, Any]:
    data = asdict(schema)
    data["row_filter"] = None
    data["row_transform"] = None
    for field_name, rule in data.get("fields", {}).items():
        rule["custom_function"] = None
    return sanitize_mapping(data)


def stable_hash(value: Any) -> str:
    raw = json.dumps(sanitize_value(value), ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_mapping(values: Mapping[str, Any], *, depth: int = 0) -> Dict[str, Any]:
    if depth > 6:
        return {"_truncated": "max_depth_exceeded"}
    result: Dict[str, Any] = {}
    for key, value in values.items():
        key_str = str(key)
        if SENSITIVE_KEY_PATTERN.search(key_str):
            result[key_str] = "[REDACTED]"
        elif isinstance(value, Mapping):
            result[key_str] = sanitize_mapping(value, depth=depth + 1)
        elif isinstance(value, (list, tuple, set)):
            result[key_str] = [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
        else:
            result[key_str] = sanitize_value(value, depth=depth)
    return result


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return sanitize_mapping(asdict(value), depth=depth + 1)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, depth=depth + 1)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(item, depth=depth + 1) for item in list(value)[:10_000]]
    text = str(value)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=([^\s&]+)", r"\1=[REDACTED]", text)
    if len(text) > MAX_TEXT_LENGTH:
        return text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
    return text


@contextlib.contextmanager
def telemetry_operation(name: str, enabled: bool, attributes: Optional[Mapping[str, Any]] = None) -> Iterator[None]:
    if not enabled:
        yield
        return
    try:
        from data.observability.telemetry import get_telemetry
        telemetry = get_telemetry()
        with telemetry.operation(name, attributes=attributes):
            yield
    except Exception:
        yield


def telemetry_metric(name: str, value: float, enabled: bool) -> None:
    if not enabled:
        return
    try:
        from data.observability.telemetry import get_telemetry
        get_telemetry().gauge(name, value)
    except Exception:
        logger.debug("Normalization telemetry metric failed", exc_info=True)


def monotonic_ms() -> float:
    import time
    return time.perf_counter() * 1000.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "CaseMode",
    "DataNormalizer",
    "DROP_FIELD",
    "ErrorPolicy",
    "NormalizationConfig",
    "NormalizationConfigError",
    "NormalizationError",
    "NormalizationErrorRecord",
    "NormalizationKind",
    "NormalizationResult",
    "NormalizationRule",
    "NormalizationSchema",
    "NormalizationStatus",
    "NormalizationValueError",
    "Normalizer",
    "NullPolicy",
    "RegexRule",
    "apply_case",
    "normalize_bool",
    "normalize_datetime",
    "normalize_key",
    "normalize_number",
    "normalize_slug",
    "normalize_text",
    "normalize_value",
    "stable_hash",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [{"Nome Cliente": " João   Silva ", "ativo": "sim", "valor": "1.234,56", "data": "2026-01-01T10:00:00Z"}]
    schema = NormalizationSchema(
        normalize_unknown_keys=True,
        fields={
            "Nome Cliente": NormalizationRule(kind=NormalizationKind.TEXT, remove_accents=True, case_mode=CaseMode.TITLE),
            "ativo": NormalizationRule(kind=NormalizationKind.BOOLEAN),
            "valor": NormalizationRule(kind=NormalizationKind.NUMERIC, decimal_separator=",", thousands_separator=".", precision=2),
            "data": NormalizationRule(kind=NormalizationKind.DATETIME),
        },
    )
    normalizer = DataNormalizer(NormalizationConfig(telemetry_enabled=False))
    print(normalizer.normalize(rows, schema=schema).to_json())
