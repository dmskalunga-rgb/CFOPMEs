"""
data/processing/data_cleaner.py

Enterprise-grade data cleaning engine for data platforms.

Purpose
-------
Provides a robust, dependency-light engine for cleaning records in ETL/ELT,
batch jobs, micro-batches, streaming flows, API ingestion and data quality
pipelines.

Core capabilities
-----------------
- Works with dictionaries, dataclasses, namedtuples and objects.
- Field-level cleaning rules.
- Null handling, defaults, type coercion and validation.
- String normalization: trim, lowercase/uppercase, whitespace collapse,
  accent removal, regex replacement and allowed character filtering.
- Numeric normalization: min/max clipping, rounding and outlier treatment.
- Date parsing and ISO formatting.
- Boolean normalization.
- PII/secret redaction helpers.
- Duplicate detection by configurable keys.
- Row filtering and error/dead-letter records.
- Cleaning profile and summary report.
- Optional telemetry integration.
- Standard library only.

Example
-------
cleaner = DataCleaner()
result = cleaner.clean(
    rows,
    schema=CleaningSchema(fields={
        "email": FieldCleaningRule(trim=True, lowercase=True, required=True),
        "amount": FieldCleaningRule(coerce_type=CoerceType.FLOAT, min_value=0),
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
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, MutableMapping, Optional, Pattern, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key|session|jwt|bearer)",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
MULTISPACE_PATTERN = re.compile(r"\s+")
MAX_TEXT_LENGTH = 50_000
MAX_ERRORS = 100_000


class CoerceType(str, Enum):
    NONE = "none"
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    JSON = "json"


class NullStrategy(str, Enum):
    KEEP = "keep"
    DROP_FIELD = "drop_field"
    DEFAULT = "default"
    EMPTY_STRING = "empty_string"
    ZERO = "zero"
    ERROR = "error"


class OutlierStrategy(str, Enum):
    KEEP = "keep"
    CLIP = "clip"
    NULL = "null"
    DROP_ROW = "drop_row"
    ERROR = "error"


class DuplicateStrategy(str, Enum):
    KEEP = "keep"
    DROP = "drop"
    MARK = "mark"
    ERROR = "error"


class CleaningStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    EMPTY = "empty"


class ErrorPolicy(str, Enum):
    CONTINUE = "continue"
    FAIL_FAST = "fail_fast"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class RegexReplacement:
    pattern: str
    replacement: str
    flags: int = 0

    def compiled(self) -> Pattern[str]:
        return re.compile(self.pattern, self.flags)


@dataclass(frozen=True)
class FieldCleaningRule:
    required: bool = False
    nullable: bool = True
    null_strategy: NullStrategy = NullStrategy.KEEP
    default: Any = None
    coerce_type: CoerceType = CoerceType.NONE
    trim: bool = True
    lowercase: bool = False
    uppercase: bool = False
    titlecase: bool = False
    collapse_whitespace: bool = True
    remove_accents: bool = False
    max_length: Optional[int] = None
    min_length: Optional[int] = None
    regex_replacements: Tuple[RegexReplacement, ...] = field(default_factory=tuple)
    allowed_pattern: Optional[str] = None
    redacted: bool = False
    redact_email: bool = False
    redact_phone: bool = False
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    round_digits: Optional[int] = None
    outlier_strategy: OutlierStrategy = OutlierStrategy.KEEP
    allowed_values: Optional[Tuple[Any, ...]] = None
    custom_cleaner: Optional[Callable[[Any, Mapping[str, Any]], Any]] = None
    custom_validator: Optional[Callable[[Any, Mapping[str, Any]], bool]] = None
    output_field: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self, field_name: str) -> None:
        if self.lowercase and self.uppercase:
            raise DataCleaningConfigError(f"Field {field_name}: lowercase and uppercase cannot both be true")
        if self.max_length is not None and self.max_length < 0:
            raise DataCleaningConfigError(f"Field {field_name}: max_length cannot be negative")
        if self.min_length is not None and self.min_length < 0:
            raise DataCleaningConfigError(f"Field {field_name}: min_length cannot be negative")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise DataCleaningConfigError(f"Field {field_name}: min_value cannot be greater than max_value")


@dataclass(frozen=True)
class CleaningSchema:
    fields: Dict[str, FieldCleaningRule] = field(default_factory=dict)
    include_unknown_fields: bool = True
    drop_empty_rows: bool = False
    duplicate_keys: Tuple[str, ...] = field(default_factory=tuple)
    duplicate_strategy: DuplicateStrategy = DuplicateStrategy.KEEP
    row_filter: Optional[Callable[[Mapping[str, Any]], bool]] = None
    row_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        for name, rule in self.fields.items():
            rule.validate(name)
        if self.duplicate_strategy != DuplicateStrategy.KEEP and not self.duplicate_keys:
            raise DataCleaningConfigError("duplicate_keys is required when duplicate_strategy is not KEEP")


@dataclass(frozen=True)
class DataCleanerConfig:
    error_policy: ErrorPolicy = ErrorPolicy.DEAD_LETTER
    max_errors: int = MAX_ERRORS
    telemetry_enabled: bool = True
    include_original_on_error: bool = True
    include_cleaned_rows: bool = True
    max_output_rows: int = 1_000_000
    dead_letter_path: Optional[str] = None
    report_path: Optional[str] = None
    profile_enabled: bool = True

    @classmethod
    def from_env(cls) -> "DataCleanerConfig":
        return cls(
            error_policy=ErrorPolicy(os.getenv("DATA_CLEANER_ERROR_POLICY", ErrorPolicy.DEAD_LETTER.value)),
            max_errors=int_env("DATA_CLEANER_MAX_ERRORS", MAX_ERRORS),
            telemetry_enabled=bool_env("DATA_CLEANER_TELEMETRY_ENABLED", True),
            include_original_on_error=bool_env("DATA_CLEANER_INCLUDE_ORIGINAL_ON_ERROR", True),
            include_cleaned_rows=bool_env("DATA_CLEANER_INCLUDE_CLEANED_ROWS", True),
            max_output_rows=int_env("DATA_CLEANER_MAX_OUTPUT_ROWS", 1_000_000),
            dead_letter_path=os.getenv("DATA_CLEANER_DEAD_LETTER_PATH"),
            report_path=os.getenv("DATA_CLEANER_REPORT_PATH"),
            profile_enabled=bool_env("DATA_CLEANER_PROFILE_ENABLED", True),
        )


@dataclass(frozen=True)
class CleaningErrorRecord:
    id: str
    timestamp: str
    row_index: int
    field: Optional[str]
    error_type: str
    message: str
    original: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class FieldProfile:
    field: str
    count: int
    null_count: int
    empty_count: int
    distinct_count: int
    sample_values: List[Any]
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    avg_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return sanitize_mapping(asdict(self))


@dataclass(frozen=True)
class CleaningResult:
    id: str
    status: CleaningStatus
    started_at: str
    finished_at: str
    duration_ms: float
    input_count: int
    output_count: int
    cleaned_count: int
    dropped_count: int
    duplicate_count: int
    error_count: int
    rows: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[CleaningErrorRecord] = field(default_factory=list)
    profile: Dict[str, FieldProfile] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["errors"] = [e.to_dict() for e in self.errors]
        data["profile"] = {k: v.to_dict() for k, v in self.profile.items()}
        return sanitize_mapping(data)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True, default=safe_json_default)


class DataCleaningError(Exception):
    """Base data cleaning error."""


class DataCleaningConfigError(DataCleaningError):
    """Invalid cleaner configuration."""


class DataCleaningValidationError(DataCleaningError):
    """Data validation failed."""


class DropRow(Exception):
    """Internal signal used to drop a row."""


class DeadLetterWriter:
    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, error: CleaningErrorRecord) -> None:
        if not self.path:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(error.to_dict(), ensure_ascii=False, sort_keys=True, default=safe_json_default) + "\n")


class DataCleaner:
    """Enterprise data cleaner."""

    def __init__(self, config: Optional[DataCleanerConfig] = None) -> None:
        self.config = config or DataCleanerConfig.from_env()
        self.dead_letter = DeadLetterWriter(self.config.dead_letter_path)

    def clean(
        self,
        rows: Iterable[Any],
        *,
        schema: Optional[CleaningSchema] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> CleaningResult:
        schema = schema or CleaningSchema()
        schema.validate()
        started = monotonic_ms()
        started_iso = utc_now_iso()
        output_rows: List[Dict[str, Any]] = []
        errors: List[CleaningErrorRecord] = []
        input_count = 0
        dropped_count = 0
        duplicate_count = 0
        seen_duplicates: set[str] = set()
        profile_builder = ProfileBuilder() if self.config.profile_enabled else None

        with telemetry_operation("data_cleaner.clean", self.config.telemetry_enabled, attributes={"fields": list(schema.fields.keys())}):
            for index, raw_row in enumerate(rows):
                input_count += 1
                try:
                    row = dict(to_mapping(raw_row))
                    if schema.drop_empty_rows and is_empty_row(row):
                        dropped_count += 1
                        continue

                    cleaned = self._clean_row(row, schema)

                    if schema.row_filter and not schema.row_filter(cleaned):
                        dropped_count += 1
                        continue

                    if schema.row_transform:
                        cleaned = schema.row_transform(cleaned)

                    duplicate_key = build_duplicate_key(cleaned, schema.duplicate_keys)
                    if duplicate_key is not None and duplicate_key in seen_duplicates:
                        duplicate_count += 1
                        if schema.duplicate_strategy == DuplicateStrategy.DROP:
                            dropped_count += 1
                            continue
                        if schema.duplicate_strategy == DuplicateStrategy.ERROR:
                            raise DataCleaningValidationError(f"Duplicate row detected for key={duplicate_key}")
                        if schema.duplicate_strategy == DuplicateStrategy.MARK:
                            cleaned["_duplicate"] = True
                    elif duplicate_key is not None:
                        seen_duplicates.add(duplicate_key)

                    if profile_builder:
                        profile_builder.update(cleaned)

                    if self.config.include_cleaned_rows and len(output_rows) < self.config.max_output_rows:
                        output_rows.append(cleaned)
                except DropRow:
                    dropped_count += 1
                except Exception as exc:
                    error = self._build_error(index, None, exc, raw_row)
                    errors.append(error)
                    self.dead_letter.write(error)
                    if len(errors) >= self.config.max_errors:
                        logger.warning("Data cleaner reached max_errors=%s", self.config.max_errors)
                        if self.config.error_policy == ErrorPolicy.FAIL_FAST:
                            raise
                    if self.config.error_policy == ErrorPolicy.FAIL_FAST:
                        raise

        duration_ms = monotonic_ms() - started
        status = determine_status(input_count, output_rows, dropped_count, errors)
        result = CleaningResult(
            id=str(uuid.uuid4()),
            status=status,
            started_at=started_iso,
            finished_at=utc_now_iso(),
            duration_ms=round(duration_ms, 3),
            input_count=input_count,
            output_count=len(output_rows),
            cleaned_count=len(output_rows),
            dropped_count=dropped_count,
            duplicate_count=duplicate_count,
            error_count=len(errors),
            rows=output_rows if self.config.include_cleaned_rows else [],
            errors=errors,
            profile=profile_builder.finalize() if profile_builder else {},
            metadata=sanitize_mapping(dict(metadata or {})),
        )
        self._save_report(result)
        telemetry_metric("data_cleaner.input_count", input_count, self.config.telemetry_enabled)
        telemetry_metric("data_cleaner.output_count", len(output_rows), self.config.telemetry_enabled)
        telemetry_metric("data_cleaner.error_count", len(errors), self.config.telemetry_enabled)
        telemetry_metric("data_cleaner.duration_ms", duration_ms, self.config.telemetry_enabled)
        return result

    def clean_one(self, row: Any, *, schema: Optional[CleaningSchema] = None) -> Dict[str, Any]:
        result = self.clean([row], schema=schema)
        if result.errors:
            raise DataCleaningValidationError(result.errors[0].message)
        return result.rows[0] if result.rows else {}

    def _clean_row(self, row: Mapping[str, Any], schema: CleaningSchema) -> Dict[str, Any]:
        cleaned: Dict[str, Any] = {}
        if schema.include_unknown_fields:
            cleaned.update({str(k): sanitize_value(v) for k, v in row.items() if k not in schema.fields})

        for field_name, rule in schema.fields.items():
            output_field = rule.output_field or field_name
            value = get_field(row, field_name)
            try:
                cleaned_value = clean_value(value, rule, row)
                if cleaned_value is _DROP_FIELD:
                    cleaned.pop(output_field, None)
                    continue
                cleaned[output_field] = cleaned_value
            except DropRow:
                raise
            except Exception as exc:
                raise DataCleaningValidationError(f"Field {field_name}: {exc}") from exc
        return cleaned

    def _build_error(self, row_index: int, field: Optional[str], exc: BaseException, original: Any) -> CleaningErrorRecord:
        return CleaningErrorRecord(
            id=str(uuid.uuid4()),
            timestamp=utc_now_iso(),
            row_index=row_index,
            field=field,
            error_type=exc.__class__.__name__,
            message=str(exc),
            original=sanitize_value(original) if self.config.include_original_on_error else None,
        )

    def _save_report(self, result: CleaningResult) -> None:
        if not self.config.report_path:
            return
        target = Path(self.config.report_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(result.to_json(indent=2), encoding="utf-8")
        tmp.replace(target)


class _DropField:
    pass


_DROP_FIELD = _DropField()


def clean_value(value: Any, rule: FieldCleaningRule, row: Mapping[str, Any]) -> Any:
    if value is None or value == "":
        if rule.required and not rule.nullable:
            raise DataCleaningValidationError("required value is missing")
        if rule.null_strategy == NullStrategy.DROP_FIELD:
            return _DROP_FIELD
        if rule.null_strategy == NullStrategy.DEFAULT:
            value = rule.default
        elif rule.null_strategy == NullStrategy.EMPTY_STRING:
            value = ""
        elif rule.null_strategy == NullStrategy.ZERO:
            value = 0
        elif rule.null_strategy == NullStrategy.ERROR:
            raise DataCleaningValidationError("null value not allowed")
        else:
            return None

    if rule.redacted:
        return "[REDACTED]"

    value = coerce_value(value, rule.coerce_type)

    if isinstance(value, str):
        value = normalize_string(value, rule)

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        value = normalize_number(value, rule)

    if rule.redact_email and isinstance(value, str):
        value = EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    if rule.redact_phone and isinstance(value, str):
        value = PHONE_PATTERN.sub("[PHONE_REDACTED]", value)

    if rule.allowed_values is not None and value not in rule.allowed_values:
        raise DataCleaningValidationError(f"value {value!r} is not in allowed values")

    if rule.custom_cleaner:
        value = rule.custom_cleaner(value, row)

    if rule.custom_validator and not rule.custom_validator(value, row):
        raise DataCleaningValidationError("custom validation failed")

    return sanitize_value(value)


def normalize_string(value: str, rule: FieldCleaningRule) -> str:
    result = value
    if rule.trim:
        result = result.strip()
    if rule.remove_accents:
        result = remove_accents(result)
    for replacement in rule.regex_replacements:
        result = replacement.compiled().sub(replacement.replacement, result)
    if rule.collapse_whitespace:
        result = MULTISPACE_PATTERN.sub(" ", result)
    if rule.lowercase:
        result = result.lower()
    if rule.uppercase:
        result = result.upper()
    if rule.titlecase:
        result = result.title()
    if rule.allowed_pattern:
        allowed = re.compile(rule.allowed_pattern)
        result = "".join(ch for ch in result if allowed.match(ch))
    if rule.max_length is not None and len(result) > rule.max_length:
        result = result[: rule.max_length]
    if rule.min_length is not None and len(result) < rule.min_length:
        raise DataCleaningValidationError(f"string shorter than min_length={rule.min_length}")
    return result


def normalize_number(value: int | float | Decimal, rule: FieldCleaningRule) -> Any:
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise DataCleaningValidationError("invalid numeric value")
    outlier = False
    if rule.min_value is not None and number < rule.min_value:
        outlier = True
        if rule.outlier_strategy == OutlierStrategy.CLIP:
            number = rule.min_value
    if rule.max_value is not None and number > rule.max_value:
        outlier = True
        if rule.outlier_strategy == OutlierStrategy.CLIP:
            number = rule.max_value
    if outlier:
        if rule.outlier_strategy == OutlierStrategy.NULL:
            return None
        if rule.outlier_strategy == OutlierStrategy.DROP_ROW:
            raise DropRow()
        if rule.outlier_strategy == OutlierStrategy.ERROR:
            raise DataCleaningValidationError("numeric value outside allowed range")
    if rule.round_digits is not None:
        number = round(number, rule.round_digits)
    if rule.coerce_type == CoerceType.INTEGER:
        return int(number)
    if rule.coerce_type == CoerceType.DECIMAL:
        return str(Decimal(str(number)))
    return number


def coerce_value(value: Any, coerce_type: CoerceType) -> Any:
    if coerce_type == CoerceType.NONE:
        return value
    if coerce_type == CoerceType.STRING:
        return str(value)
    if coerce_type == CoerceType.INTEGER:
        return int(float(value))
    if coerce_type == CoerceType.FLOAT:
        return float(value)
    if coerce_type == CoerceType.DECIMAL:
        try:
            return Decimal(str(value))
        except InvalidOperation as exc:
            raise DataCleaningValidationError(f"cannot coerce to decimal: {value!r}") from exc
    if coerce_type == CoerceType.BOOLEAN:
        return coerce_bool(value)
    if coerce_type == CoerceType.DATE:
        return coerce_datetime(value).date().isoformat()
    if coerce_type == CoerceType.DATETIME:
        return coerce_datetime(value).isoformat()
    if coerce_type == CoerceType.JSON:
        if isinstance(value, str):
            return json.loads(value)
        return value
    return value


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y", "sim", "s", "on"}:
        return True
    if text in {"0", "false", "f", "no", "n", "nao", "não", "off"}:
        return False
    raise DataCleaningValidationError(f"cannot coerce to boolean: {value!r}")


def coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif isinstance(value, (int, float)):
        raw = float(value)
        dt = datetime.fromtimestamp(raw / 1000.0 if raw > 10_000_000_000 else raw, timezone.utc)
    else:
        text = str(value).strip()
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
    raise DataCleaningValidationError(f"Unsupported row type: {type(row)!r}")


def is_empty_row(row: Mapping[str, Any]) -> bool:
    return all(value is None or value == "" for value in row.values())


def build_duplicate_key(row: Mapping[str, Any], keys: Sequence[str]) -> Optional[str]:
    if not keys:
        return None
    values = [get_field(row, key) for key in keys]
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProfileBuilder:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.nulls: Counter[str] = Counter()
        self.empties: Counter[str] = Counter()
        self.values: Dict[str, Counter[Any]] = defaultdict(Counter)
        self.numeric: Dict[str, List[float]] = defaultdict(list)

    def update(self, row: Mapping[str, Any]) -> None:
        for key, value in row.items():
            field_name = str(key)
            self.counts[field_name] += 1
            if value is None:
                self.nulls[field_name] += 1
            if value == "":
                self.empties[field_name] += 1
            safe = json_hashable(value)
            if len(self.values[field_name]) < 1000:
                self.values[field_name][safe] += 1
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isnan(float(value)):
                self.numeric[field_name].append(float(value))

    def finalize(self) -> Dict[str, FieldProfile]:
        output: Dict[str, FieldProfile] = {}
        for field_name in sorted(self.counts):
            nums = self.numeric.get(field_name, [])
            top_values = [value for value, _count in self.values[field_name].most_common(10)]
            output[field_name] = FieldProfile(
                field=field_name,
                count=self.counts[field_name],
                null_count=self.nulls[field_name],
                empty_count=self.empties[field_name],
                distinct_count=len(self.values[field_name]),
                sample_values=top_values,
                min_value=min(nums) if nums else None,
                max_value=max(nums) if nums else None,
                avg_value=sum(nums) / len(nums) if nums else None,
            )
        return output


def determine_status(input_count: int, rows: Sequence[Mapping[str, Any]], dropped_count: int, errors: Sequence[CleaningErrorRecord]) -> CleaningStatus:
    if input_count == 0:
        return CleaningStatus.EMPTY
    if errors and not rows:
        return CleaningStatus.FAILED
    if errors or dropped_count:
        return CleaningStatus.PARTIAL
    return CleaningStatus.SUCCEEDED


def json_hashable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=safe_json_default)
    except Exception:
        return str(value)


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
        text = text[: MAX_TEXT_LENGTH - 15] + "...[truncated]"
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
        logger.debug("Data cleaner telemetry metric failed", exc_info=True)


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
    "CleaningErrorRecord",
    "CleaningResult",
    "CleaningSchema",
    "CleaningStatus",
    "CoerceType",
    "DataCleaner",
    "DataCleanerConfig",
    "DataCleaningConfigError",
    "DataCleaningError",
    "DataCleaningValidationError",
    "DuplicateStrategy",
    "ErrorPolicy",
    "FieldCleaningRule",
    "FieldProfile",
    "NullStrategy",
    "OutlierStrategy",
    "RegexReplacement",
    "clean_value",
    "coerce_value",
    "normalize_string",
    "normalize_number",
]


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    rows = [
        {"id": " 1 ", "email": " TEST@Example.COM ", "amount": "10.556", "name": " João   Silva "},
        {"id": "2", "email": "bad@example.com", "amount": "-5", "name": "Maria"},
        {"id": "2", "email": "bad@example.com", "amount": "15", "name": "Maria"},
    ]
    schema = CleaningSchema(
        fields={
            "id": FieldCleaningRule(coerce_type=CoerceType.STRING, trim=True, required=True),
            "email": FieldCleaningRule(coerce_type=CoerceType.STRING, trim=True, lowercase=True, required=True),
            "amount": FieldCleaningRule(coerce_type=CoerceType.FLOAT, min_value=0, outlier_strategy=OutlierStrategy.CLIP, round_digits=2),
            "name": FieldCleaningRule(trim=True, collapse_whitespace=True, remove_accents=True, titlecase=True),
        },
        duplicate_keys=("id",),
        duplicate_strategy=DuplicateStrategy.MARK,
    )
    cleaner = DataCleaner(DataCleanerConfig(telemetry_enabled=False))
    print(cleaner.clean(rows, schema=schema).to_json())
