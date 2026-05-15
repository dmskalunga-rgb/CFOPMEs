"""
schema_mapper.py
================

Enterprise-grade schema mapping engine for data processing pipelines.

Key capabilities
----------------
- Declarative field mapping from source schemas to target schemas.
- Type coercion with strict/tolerant modes.
- Field aliases and fallback source paths.
- Nested path extraction and assignment using dot notation.
- Default values, required fields, nullable fields, trimming, normalization.
- Derived fields using safe callables.
- Row-level validation rules and field-level validators.
- Error collection with severity, codes, context and audit traces.
- Batch mapping for dictionaries, lists of dictionaries and pandas DataFrames.
- Mapping reports with counters, timings and data quality metrics.
- Extensible transformer registry.

This module is dependency-light. pandas support is optional.

Example
-------
>>> spec = SchemaMapSpec(
...     name="customer_v1_to_v2",
...     fields=[
...         FieldMap(target="customer_id", sources=["id", "customer.id"], dtype="str", required=True),
...         FieldMap(target="name", sources=["full_name", "name"], dtype="str", trim=True),
...         FieldMap(target="age", sources=["age"], dtype="int", nullable=True),
...         FieldMap(target="created_at", sources=["createdAt"], dtype="datetime", nullable=True),
...     ],
... )
>>> mapper = SchemaMapper(spec)
>>> result = mapper.map_record({"id": 10, "full_name": " Ana ", "age": "31"})
>>> result.output
{'customer_id': '10', 'name': 'Ana', 'age': 31, 'created_at': None}
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import decimal
import enum
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    DefaultDict,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

try:  # Optional dependency.
    import pandas as pd  # type: ignore
except Exception:  # pragma: no cover
    pd = None  # type: ignore

logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]
Record = Mapping[str, Any]
MutableRecord = MutableMapping[str, Any]
Validator = Callable[[Any, Record], Optional[str]]
Transformer = Callable[[Any, Record], Any]
Deriver = Callable[[Record], Any]


class MappingMode(str, enum.Enum):
    """Controls how mapping errors are handled."""

    STRICT = "strict"
    TOLERANT = "tolerant"


class ErrorSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class UnknownFieldPolicy(str, enum.Enum):
    IGNORE = "ignore"
    INCLUDE = "include"
    FAIL = "fail"


class DuplicateTargetPolicy(str, enum.Enum):
    LAST_WRITE_WINS = "last_write_wins"
    FIRST_WRITE_WINS = "first_write_wins"
    FAIL = "fail"


class SchemaMappingException(Exception):
    """Raised when mapping cannot continue in strict mode."""

    def __init__(self, message: str, errors: Optional[List["MappingError"]] = None) -> None:
        super().__init__(message)
        self.errors = errors or []


@dataclass(frozen=True)
class MappingError:
    code: str
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    target: Optional[str] = None
    source: Optional[str] = None
    value_preview: Optional[str] = None
    record_index: Optional[int] = None
    context: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "target": self.target,
            "source": self.source,
            "value_preview": self.value_preview,
            "record_index": self.record_index,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class FieldMap:
    """
    Declarative mapping for one target field.

    Parameters
    ----------
    target:
        Target field path. Dot notation creates nested dictionaries.
    sources:
        Ordered list of source field paths. The first existing non-empty value is used,
        unless allow_empty_source=True.
    dtype:
        Target dtype: str, int, float, decimal, bool, date, datetime, list, dict, json, uuid,
        or any registered transformer name.
    required:
        When True, missing/non-null value is considered an error.
    nullable:
        Whether None is allowed after mapping/coercion.
    default:
        Static default value if no source is found or source is empty.
    default_factory:
        Callable default factory executed per record.
    transform:
        Callable or registered transformer name applied before dtype coercion.
    validators:
        Field validators returning None when valid or an error message when invalid.
    derive:
        Callable that derives value from the whole input record. Takes precedence over sources.
    trim:
        Strip string values before transformation/coercion.
    normalize_blank:
        Convert empty strings to None.
    allow_empty_source:
        Treat empty string as valid source value.
    metadata:
        Free-form metadata useful for lineage/catalog integrations.
    """

    target: str
    sources: Sequence[str] = field(default_factory=list)
    dtype: Optional[str] = None
    required: bool = False
    nullable: bool = True
    default: Any = None
    default_factory: Optional[Callable[[], Any]] = None
    transform: Optional[Union[str, Transformer]] = None
    validators: Sequence[Validator] = field(default_factory=list)
    derive: Optional[Deriver] = None
    trim: bool = False
    normalize_blank: bool = True
    allow_empty_source: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.target or not isinstance(self.target, str):
            raise ValueError("FieldMap.target must be a non-empty string")
        if self.default is not None and self.default_factory is not None:
            raise ValueError(f"FieldMap({self.target}) cannot define both default and default_factory")


@dataclass(frozen=True)
class SchemaMapSpec:
    name: str
    fields: Sequence[FieldMap]
    version: str = "1.0.0"
    mode: MappingMode = MappingMode.TOLERANT
    unknown_field_policy: UnknownFieldPolicy = UnknownFieldPolicy.IGNORE
    duplicate_target_policy: DuplicateTargetPolicy = DuplicateTargetPolicy.FAIL
    row_validators: Sequence[Callable[[MutableRecord], Optional[str]]] = field(default_factory=list)
    include_lineage: bool = False
    include_audit_hash: bool = False
    metadata: JsonDict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SchemaMapSpec.name is required")
        if not self.fields:
            raise ValueError("SchemaMapSpec.fields cannot be empty")


@dataclass
class RecordMappingResult:
    output: JsonDict
    errors: List[MappingError] = field(default_factory=list)
    lineage: JsonDict = field(default_factory=dict)
    success: bool = True
    record_index: Optional[int] = None

    def to_dict(self) -> JsonDict:
        return {
            "success": self.success,
            "record_index": self.record_index,
            "output": self.output,
            "lineage": self.lineage,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class BatchMappingReport:
    spec_name: str
    spec_version: str
    total_records: int = 0
    successful_records: int = 0
    failed_records: int = 0
    warning_records: int = 0
    error_counts: Counter = field(default_factory=Counter)
    field_source_counts: DefaultDict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 3)

    @property
    def success_rate(self) -> float:
        if self.total_records == 0:
            return 0.0
        return round(self.successful_records / self.total_records, 6)

    def finish(self) -> None:
        self.finished_at = time.time()

    def to_dict(self) -> JsonDict:
        return {
            "spec_name": self.spec_name,
            "spec_version": self.spec_version,
            "total_records": self.total_records,
            "successful_records": self.successful_records,
            "failed_records": self.failed_records,
            "warning_records": self.warning_records,
            "success_rate": self.success_rate,
            "duration_ms": self.duration_ms,
            "error_counts": dict(self.error_counts),
            "field_source_counts": {k: dict(v) for k, v in self.field_source_counts.items()},
        }


@dataclass
class BatchMappingResult:
    records: List[JsonDict]
    errors: List[MappingError]
    report: BatchMappingReport
    record_results: Optional[List[RecordMappingResult]] = None

    def to_dict(self) -> JsonDict:
        return {
            "records": self.records,
            "errors": [e.to_dict() for e in self.errors],
            "report": self.report.to_dict(),
            "record_results": [r.to_dict() for r in self.record_results] if self.record_results else None,
        }


_MISSING = object()


def _preview(value: Any, max_len: int = 160) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _is_blank(value: Any) -> bool:
    return isinstance(value, str) and value.strip() == ""


def get_path(record: Record, path: str, default: Any = _MISSING) -> Any:
    """Read nested dict/list values using dot notation, e.g. 'customer.address.city'."""
    current: Any = record
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            if not part.isdigit():
                return default
            idx = int(part)
            if idx >= len(current):
                return default
            current = current[idx]
        else:
            return default
    return current


def set_path(record: MutableRecord, path: str, value: Any, overwrite: bool = True) -> bool:
    """Set nested dict value using dot notation. Returns True when written."""
    parts = path.split(".")
    current: MutableRecord = record
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], MutableMapping):
            current[part] = {}
        current = current[part]
    leaf = parts[-1]
    if not overwrite and leaf in current:
        return False
    current[leaf] = value
    return True


def flatten_keys(record: Record, prefix: str = "") -> List[str]:
    keys: List[str] = []
    for key, value in record.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        keys.append(full_key)
        if isinstance(value, Mapping):
            keys.extend(flatten_keys(value, full_key))
    return keys


class TypeCoercer:
    """Centralized type coercion with clear error messages."""

    TRUE_VALUES = {"true", "t", "yes", "y", "1", "sim", "s"}
    FALSE_VALUES = {"false", "f", "no", "n", "0", "nao", "não"}

    @classmethod
    def coerce(cls, value: Any, dtype: Optional[str]) -> Any:
        if dtype is None or value is None:
            return value
        normalized = dtype.lower().strip()
        if normalized in {"any", "object"}:
            return value
        if normalized in {"str", "string", "text"}:
            return str(value)
        if normalized in {"int", "integer"}:
            return cls._to_int(value)
        if normalized in {"float", "double", "number"}:
            return cls._to_float(value)
        if normalized in {"decimal", "money"}:
            return cls._to_decimal(value)
        if normalized in {"bool", "boolean"}:
            return cls._to_bool(value)
        if normalized == "date":
            return cls._to_date(value)
        if normalized in {"datetime", "timestamp"}:
            return cls._to_datetime(value)
        if normalized in {"list", "array"}:
            return cls._to_list(value)
        if normalized in {"dict", "map", "object_map"}:
            return cls._to_dict(value)
        if normalized == "json":
            return cls._to_json(value)
        if normalized == "uuid":
            return str(uuid.UUID(str(value)))
        raise ValueError(f"Unsupported dtype: {dtype}")

    @staticmethod
    def _to_int(value: Any) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError(f"Cannot convert non-integer float to int: {value}")
            return int(value)
        text = str(value).strip().replace("_", "")
        if re.fullmatch(r"[-+]?\d+\.0+", text):
            text = text.split(".")[0]
        return int(text)

    @staticmethod
    def _to_float(value: Any) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result = float(value)
        else:
            text = str(value).strip().replace("_", "").replace(",", ".")
            result = float(text)
        if not math.isfinite(result):
            raise ValueError(f"Float value is not finite: {value}")
        return result

    @staticmethod
    def _to_decimal(value: Any) -> decimal.Decimal:
        if isinstance(value, decimal.Decimal):
            return value
        text = str(value).strip().replace("_", "").replace(",", ".")
        return decimal.Decimal(text)

    @classmethod
    def _to_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in cls.TRUE_VALUES:
            return True
        if text in cls.FALSE_VALUES:
            return False
        raise ValueError(f"Cannot convert to bool: {value}")

    @staticmethod
    def _to_date(value: Any) -> _dt.date:
        if isinstance(value, _dt.datetime):
            return value.date()
        if isinstance(value, _dt.date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return _dt.datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        return _dt.date.fromisoformat(text)

    @staticmethod
    def _to_datetime(value: Any) -> _dt.datetime:
        if isinstance(value, _dt.datetime):
            return value
        if isinstance(value, _dt.date):
            return _dt.datetime.combine(value, _dt.time.min)
        text = str(value).strip().replace("Z", "+00:00")
        try:
            return _dt.datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
                try:
                    return _dt.datetime.strptime(text, fmt)
                except ValueError:
                    pass
            raise

    @staticmethod
    def _to_list(value: Any) -> List[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                parsed = json.loads(text)
                if not isinstance(parsed, list):
                    raise ValueError("JSON value is not a list")
                return parsed
            return [part.strip() for part in text.split(",") if part.strip()]
        return [value]

    @staticmethod
    def _to_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise ValueError("JSON value is not a dict")
            return parsed
        raise ValueError(f"Cannot convert to dict: {type(value).__name__}")

    @staticmethod
    def _to_json(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        json.dumps(value, default=str)
        return value


class TransformerRegistry:
    """Named transformation registry for reusable schema transformations."""

    def __init__(self) -> None:
        self._transformers: Dict[str, Transformer] = {}
        self.register_defaults()

    def register(self, name: str, fn: Transformer, replace: bool = False) -> None:
        if not name:
            raise ValueError("Transformer name is required")
        key = name.lower().strip()
        if key in self._transformers and not replace:
            raise ValueError(f"Transformer already registered: {name}")
        self._transformers[key] = fn

    def get(self, name: str) -> Transformer:
        key = name.lower().strip()
        if key not in self._transformers:
            raise KeyError(f"Transformer not found: {name}")
        return self._transformers[key]

    def register_defaults(self) -> None:
        self._transformers.update(
            {
                "lower": lambda v, r: v.lower() if isinstance(v, str) else v,
                "upper": lambda v, r: v.upper() if isinstance(v, str) else v,
                "title": lambda v, r: v.title() if isinstance(v, str) else v,
                "strip": lambda v, r: v.strip() if isinstance(v, str) else v,
                "digits_only": lambda v, r: re.sub(r"\D+", "", v) if isinstance(v, str) else v,
                "null_if_blank": lambda v, r: None if _is_blank(v) else v,
                "sha256": lambda v, r: hashlib.sha256(str(v).encode("utf-8")).hexdigest() if v is not None else None,
            }
        )


class SchemaMapper:
    """Enterprise schema mapping engine."""

    def __init__(
        self,
        spec: SchemaMapSpec,
        *,
        transformer_registry: Optional[TransformerRegistry] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.spec = spec
        self.transformers = transformer_registry or TransformerRegistry()
        self.logger = logger_ or logger
        self._validate_spec()

    def _validate_spec(self) -> None:
        targets = [f.target for f in self.spec.fields]
        duplicates = [target for target, count in Counter(targets).items() if count > 1]
        if duplicates and self.spec.duplicate_target_policy == DuplicateTargetPolicy.FAIL:
            raise ValueError(f"Duplicate target mappings are not allowed: {duplicates}")

    @classmethod
    def from_dict(cls, config: Mapping[str, Any]) -> "SchemaMapper":
        """Build a mapper from a plain dict config, useful for YAML/JSON configs."""
        fields = []
        for raw in config.get("fields", []):
            raw_copy = dict(raw)
            fields.append(FieldMap(**raw_copy))
        spec = SchemaMapSpec(
            name=config["name"],
            version=config.get("version", "1.0.0"),
            fields=fields,
            mode=MappingMode(config.get("mode", MappingMode.TOLERANT.value)),
            unknown_field_policy=UnknownFieldPolicy(config.get("unknown_field_policy", UnknownFieldPolicy.IGNORE.value)),
            duplicate_target_policy=DuplicateTargetPolicy(
                config.get("duplicate_target_policy", DuplicateTargetPolicy.FAIL.value)
            ),
            include_lineage=bool(config.get("include_lineage", False)),
            include_audit_hash=bool(config.get("include_audit_hash", False)),
            metadata=dict(config.get("metadata", {})),
        )
        return cls(spec)

    def map_record(self, record: Record, record_index: Optional[int] = None) -> RecordMappingResult:
        if not isinstance(record, Mapping):
            raise TypeError("record must be a mapping/dict")

        output: JsonDict = {}
        lineage: JsonDict = {}
        errors: List[MappingError] = []
        written_targets: set[str] = set()

        if self.spec.unknown_field_policy == UnknownFieldPolicy.FAIL:
            mapped_sources = {src for field_map in self.spec.fields for src in field_map.sources}
            for key in flatten_keys(record):
                if key not in mapped_sources:
                    errors.append(
                        MappingError(
                            code="UNKNOWN_FIELD",
                            message=f"Unknown source field: {key}",
                            severity=ErrorSeverity.ERROR,
                            source=key,
                            record_index=record_index,
                        )
                    )

        if self.spec.unknown_field_policy == UnknownFieldPolicy.INCLUDE:
            output.update(dict(record))

        for field_map in self.spec.fields:
            value, selected_source, field_errors = self._resolve_field(field_map, record, record_index)
            errors.extend(field_errors)

            if field_map.target in written_targets:
                if self.spec.duplicate_target_policy == DuplicateTargetPolicy.FIRST_WRITE_WINS:
                    continue
                if self.spec.duplicate_target_policy == DuplicateTargetPolicy.FAIL:
                    errors.append(
                        MappingError(
                            code="DUPLICATE_TARGET_WRITE",
                            message=f"Duplicate target write: {field_map.target}",
                            target=field_map.target,
                            record_index=record_index,
                        )
                    )
                    continue

            set_path(output, field_map.target, value, overwrite=True)
            written_targets.add(field_map.target)

            if self.spec.include_lineage:
                lineage[field_map.target] = {
                    "source": selected_source,
                    "dtype": field_map.dtype,
                    "required": field_map.required,
                    "metadata": dict(field_map.metadata),
                }

        for validator in self.spec.row_validators:
            try:
                message = validator(output)
                if message:
                    errors.append(
                        MappingError(
                            code="ROW_VALIDATION_FAILED",
                            message=message,
                            record_index=record_index,
                        )
                    )
            except Exception as exc:
                errors.append(
                    MappingError(
                        code="ROW_VALIDATOR_EXCEPTION",
                        message=str(exc),
                        severity=ErrorSeverity.ERROR,
                        record_index=record_index,
                    )
                )

        if self.spec.include_audit_hash:
            output["_schema_mapping_audit_hash"] = self._audit_hash(record, output)

        success = not any(e.severity in {ErrorSeverity.ERROR, ErrorSeverity.CRITICAL} for e in errors)
        result = RecordMappingResult(
            output=output,
            errors=errors,
            lineage=lineage,
            success=success,
            record_index=record_index,
        )

        if not success and self.spec.mode == MappingMode.STRICT:
            raise SchemaMappingException("Schema mapping failed in strict mode", errors)

        return result

    def _resolve_field(
        self,
        field_map: FieldMap,
        record: Record,
        record_index: Optional[int],
    ) -> Tuple[Any, Optional[str], List[MappingError]]:
        errors: List[MappingError] = []
        selected_source: Optional[str] = None

        try:
            if field_map.derive is not None:
                value = field_map.derive(record)
                selected_source = "<derived>"
            else:
                value, selected_source = self._select_source_value(field_map, record)

            if value is _MISSING:
                value = self._default_value(field_map)

            if isinstance(value, str) and field_map.trim:
                value = value.strip()

            if field_map.normalize_blank and _is_blank(value):
                value = None

            if value is None:
                if field_map.required:
                    errors.append(
                        MappingError(
                            code="REQUIRED_FIELD_MISSING",
                            message=f"Required field is missing: {field_map.target}",
                            target=field_map.target,
                            source=selected_source,
                            record_index=record_index,
                        )
                    )
                if not field_map.nullable:
                    errors.append(
                        MappingError(
                            code="NULL_NOT_ALLOWED",
                            message=f"Null value is not allowed: {field_map.target}",
                            target=field_map.target,
                            source=selected_source,
                            record_index=record_index,
                        )
                    )
                return value, selected_source, errors

            value = self._apply_transform(field_map, value, record)
            value = TypeCoercer.coerce(value, field_map.dtype)

            for validator in field_map.validators:
                message = validator(value, record)
                if message:
                    errors.append(
                        MappingError(
                            code="FIELD_VALIDATION_FAILED",
                            message=message,
                            target=field_map.target,
                            source=selected_source,
                            value_preview=_preview(value),
                            record_index=record_index,
                        )
                    )

            return value, selected_source, errors
        except Exception as exc:
            errors.append(
                MappingError(
                    code="FIELD_MAPPING_EXCEPTION",
                    message=str(exc),
                    target=field_map.target,
                    source=selected_source,
                    value_preview=None if "value" not in locals() else _preview(value),
                    record_index=record_index,
                )
            )
            return None, selected_source, errors

    def _select_source_value(self, field_map: FieldMap, record: Record) -> Tuple[Any, Optional[str]]:
        for source in field_map.sources:
            value = get_path(record, source, _MISSING)
            if value is _MISSING:
                continue
            if _is_blank(value) and not field_map.allow_empty_source:
                continue
            return value, source
        return _MISSING, None

    @staticmethod
    def _default_value(field_map: FieldMap) -> Any:
        if field_map.default_factory is not None:
            return field_map.default_factory()
        return field_map.default

    def _apply_transform(self, field_map: FieldMap, value: Any, record: Record) -> Any:
        if field_map.transform is None:
            return value
        if isinstance(field_map.transform, str):
            return self.transformers.get(field_map.transform)(value, record)
        return field_map.transform(value, record)

    @staticmethod
    def _audit_hash(source: Record, output: Record) -> str:
        payload = json.dumps(
            {"source": source, "output": output},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def map_records(
        self,
        records: Iterable[Record],
        *,
        include_record_results: bool = False,
    ) -> BatchMappingResult:
        report = BatchMappingReport(spec_name=self.spec.name, spec_version=self.spec.version)
        outputs: List[JsonDict] = []
        all_errors: List[MappingError] = []
        record_results: List[RecordMappingResult] = []

        for idx, record in enumerate(records):
            report.total_records += 1
            result = self.map_record(record, record_index=idx)
            outputs.append(result.output)
            all_errors.extend(result.errors)

            if include_record_results:
                record_results.append(result)

            if result.success:
                report.successful_records += 1
            else:
                report.failed_records += 1

            if any(e.severity == ErrorSeverity.WARNING for e in result.errors):
                report.warning_records += 1

            for error in result.errors:
                report.error_counts[error.code] += 1

            if self.spec.include_lineage:
                for target, meta in result.lineage.items():
                    report.field_source_counts[target][meta.get("source") or "<none>"] += 1

        report.finish()
        return BatchMappingResult(
            records=outputs,
            errors=all_errors,
            report=report,
            record_results=record_results if include_record_results else None,
        )

    def map_dataframe(self, dataframe: Any, *, include_errors_column: bool = True) -> Any:
        """Map a pandas DataFrame and return a mapped DataFrame."""
        if pd is None:
            raise RuntimeError("pandas is not installed")
        if not hasattr(dataframe, "to_dict"):
            raise TypeError("dataframe must be a pandas DataFrame")

        batch = self.map_records(dataframe.to_dict(orient="records"), include_record_results=True)
        mapped_df = pd.DataFrame(batch.records)
        if include_errors_column and batch.record_results is not None:
            mapped_df["_mapping_success"] = [r.success for r in batch.record_results]
            mapped_df["_mapping_errors"] = [json.dumps([e.to_dict() for e in r.errors], ensure_ascii=False) for r in batch.record_results]
        mapped_df.attrs["schema_mapping_report"] = batch.report.to_dict()
        return mapped_df

    def dry_run(self, records: Iterable[Record], sample_size: int = 100) -> JsonDict:
        """Run a sample mapping and return only report/errors, useful for CI and pipeline validation."""
        sample: List[Record] = []
        for idx, record in enumerate(records):
            if idx >= sample_size:
                break
            sample.append(record)
        result = self.map_records(sample, include_record_results=False)
        return {
            "report": result.report.to_dict(),
            "errors": [e.to_dict() for e in result.errors[:100]],
            "error_limit_reached": len(result.errors) > 100,
        }

    def describe(self) -> JsonDict:
        """Return a serializable description of the mapping spec."""
        return {
            "name": self.spec.name,
            "version": self.spec.version,
            "mode": self.spec.mode.value,
            "unknown_field_policy": self.spec.unknown_field_policy.value,
            "duplicate_target_policy": self.spec.duplicate_target_policy.value,
            "include_lineage": self.spec.include_lineage,
            "include_audit_hash": self.spec.include_audit_hash,
            "fields": [
                {
                    "target": f.target,
                    "sources": list(f.sources),
                    "dtype": f.dtype,
                    "required": f.required,
                    "nullable": f.nullable,
                    "has_default": f.default is not None or f.default_factory is not None,
                    "transform": f.transform if isinstance(f.transform, str) else None,
                    "derived": f.derive is not None,
                    "metadata": dict(f.metadata),
                }
                for f in self.spec.fields
            ],
            "metadata": dict(self.spec.metadata),
        }


# -----------------------------
# Common validators
# -----------------------------


def min_length(length: int) -> Validator:
    def _validator(value: Any, _: Record) -> Optional[str]:
        if value is not None and len(str(value)) < length:
            return f"Value length must be >= {length}"
        return None

    return _validator


def max_length(length: int) -> Validator:
    def _validator(value: Any, _: Record) -> Optional[str]:
        if value is not None and len(str(value)) > length:
            return f"Value length must be <= {length}"
        return None

    return _validator


def regex_match(pattern: str, message: Optional[str] = None) -> Validator:
    compiled = re.compile(pattern)

    def _validator(value: Any, _: Record) -> Optional[str]:
        if value is not None and not compiled.fullmatch(str(value)):
            return message or f"Value does not match pattern: {pattern}"
        return None

    return _validator


def in_set(allowed: Iterable[Any]) -> Validator:
    allowed_set = set(allowed)

    def _validator(value: Any, _: Record) -> Optional[str]:
        if value not in allowed_set:
            return f"Value must be one of: {sorted(allowed_set)}"
        return None

    return _validator


def range_between(min_value: Optional[float] = None, max_value: Optional[float] = None) -> Validator:
    def _validator(value: Any, _: Record) -> Optional[str]:
        if value is None:
            return None
        numeric = float(value)
        if min_value is not None and numeric < min_value:
            return f"Value must be >= {min_value}"
        if max_value is not None and numeric > max_value:
            return f"Value must be <= {max_value}"
        return None

    return _validator


def not_in_future(value: Any, _: Record) -> Optional[str]:
    if value is None:
        return None
    today = _dt.date.today()
    comparable = value.date() if isinstance(value, _dt.datetime) else value
    if isinstance(comparable, _dt.date) and comparable > today:
        return "Date cannot be in the future"
    return None


# -----------------------------
# Example factory
# -----------------------------


def build_customer_mapper() -> SchemaMapper:
    """Example mapper factory for quick tests and documentation."""
    spec = SchemaMapSpec(
        name="customer_schema_mapper",
        version="2.0.0",
        mode=MappingMode.TOLERANT,
        include_lineage=True,
        include_audit_hash=True,
        fields=[
            FieldMap(
                target="customer.id",
                sources=["customer_id", "id", "customer.id"],
                dtype="str",
                required=True,
                nullable=False,
                trim=True,
                validators=[min_length(1), max_length(64)],
            ),
            FieldMap(
                target="customer.name",
                sources=["name", "full_name", "customer.name"],
                dtype="str",
                required=True,
                nullable=False,
                trim=True,
                transform="title",
                validators=[min_length(2), max_length(120)],
            ),
            FieldMap(
                target="customer.email",
                sources=["email", "customer.email"],
                dtype="str",
                trim=True,
                transform="lower",
                validators=[regex_match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "Invalid email format")],
            ),
            FieldMap(
                target="customer.age",
                sources=["age", "customer.age"],
                dtype="int",
                nullable=True,
                validators=[range_between(0, 130)],
            ),
            FieldMap(
                target="customer.created_at",
                sources=["created_at", "createdAt", "customer.created_at"],
                dtype="datetime",
                nullable=True,
            ),
            FieldMap(
                target="customer.source_system",
                default="unknown",
                dtype="str",
                nullable=False,
            ),
            FieldMap(
                target="customer.name_hash",
                sources=["name", "full_name", "customer.name"],
                dtype="str",
                transform="sha256",
                nullable=True,
                metadata={"pii": "hashed"},
            ),
        ],
    )
    return SchemaMapper(spec)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    mapper = build_customer_mapper()
    sample_records = [
        {"id": 1, "full_name": " ana silva ", "email": "ANA@EXAMPLE.COM", "age": "31"},
        {"id": 2, "name": "b", "email": "invalid", "age": "200"},
        {"name": "sem id", "email": "semid@example.com"},
    ]

    batch_result = mapper.map_records(sample_records, include_record_results=True)
    print(json.dumps(batch_result.to_dict(), ensure_ascii=False, indent=2, default=str))
