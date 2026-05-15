"""
data/utils/helpers.py

Enterprise-grade general helper utilities.

Este módulo reúne helpers transversais, pequenos e seguros para uso em toda a
plataforma de dados: ingestão, validação, IA, pipelines, observabilidade,
storage, APIs internas e scripts operacionais.

Capacidades principais:
- Datas UTC e parsing tolerante.
- Conversão JSON-safe.
- Manipulação segura de paths.
- Helpers de dict/list: flatten, unflatten, deep merge, get/set nested.
- Chunks, batching e paginação.
- Normalização de strings, slugs e nomes de colunas.
- Leitura tipada de variáveis de ambiente.
- Retry simples, timer/context manager e medição de duração.
- Máscara/redação de campos sensíveis.
- Utilitários de validação defensiva.

Sem dependências externas obrigatórias.
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import random
import re
import string
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    TypeVar,
    Union,
)


T = TypeVar("T")
R = TypeVar("R")
JsonDict = Dict[str, Any]
PathLike = Union[str, os.PathLike[str]]


SENSITIVE_KEYWORDS: Tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "senha",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
    "credential",
    "credentials",
)


@dataclass(frozen=True)
class TimerResult:
    """Resultado de medição de tempo."""

    name: Optional[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": self.duration_ms,
            "metadata": safe_json_value(dict(self.metadata)),
        }


class Timer:
    """Context manager para medir duração de blocos."""

    def __init__(self, name: Optional[str] = None, metadata: Optional[Mapping[str, Any]] = None) -> None:
        self.name = name
        self.metadata = dict(metadata or {})
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self._start_perf: Optional[float] = None
        self.result: Optional[TimerResult] = None

    def __enter__(self) -> "Timer":
        self.started_at = utc_now()
        self._start_perf = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finished_at = utc_now()
        duration_ms = 0.0 if self._start_perf is None else (time.perf_counter() - self._start_perf) * 1000.0
        self.result = TimerResult(
            name=self.name,
            started_at=self.started_at or self.finished_at,
            finished_at=self.finished_at,
            duration_ms=duration_ms,
            metadata=self.metadata,
        )

    @property
    def duration_ms(self) -> float:
        if self._start_perf is None:
            return 0.0
        return (time.perf_counter() - self._start_perf) * 1000.0


# =============================================================================
# Date/time helpers
# =============================================================================

def utc_now() -> datetime:
    """Retorna datetime atual em UTC com timezone."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Retorna timestamp UTC atual em ISO-8601."""
    return utc_now().isoformat()


def parse_datetime(value: Any, *, default_timezone: timezone = timezone.utc) -> Optional[datetime]:
    """Parse tolerante para datetime.

    Aceita datetime, date, timestamp numérico e strings ISO comuns.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=default_timezone)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=default_timezone)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=default_timezone)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_timezone)
        except Exception:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=default_timezone)
        except Exception:
            continue
    return None


def ensure_timezone(value: datetime, tz: timezone = timezone.utc) -> datetime:
    """Garante timezone em datetime."""
    return value if value.tzinfo else value.replace(tzinfo=tz)


def timestamp_ms() -> int:
    """Timestamp atual em milissegundos."""
    return int(time.time() * 1000)


# =============================================================================
# JSON/safe serialization helpers
# =============================================================================

def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários para estrutura JSON-safe."""
    if is_dataclass(value) and not isinstance(value, type):
        return safe_json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json_value(v) for v in value]
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


def to_json(value: Any, *, indent: Optional[int] = 2, sort_keys: bool = True) -> str:
    """Serializa valor para JSON seguro."""
    return json.dumps(safe_json_value(value), ensure_ascii=False, indent=indent, sort_keys=sort_keys, default=str)


def from_json(payload: Union[str, bytes], *, encoding: str = "utf-8", default: Any = None) -> Any:
    """Desserializa JSON com default opcional em caso de erro."""
    try:
        text = payload.decode(encoding) if isinstance(payload, bytes) else payload
        return json.loads(text)
    except Exception:
        return default


# =============================================================================
# Dict helpers
# =============================================================================

def get_nested(payload: Mapping[str, Any], path: str, *, default: Any = None, separator: str = ".") -> Any:
    """Obtém valor em dict aninhado usando path."""
    current: Any = payload
    for part in path.split(separator):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return default
    return current


def set_nested(payload: MutableMapping[str, Any], path: str, value: Any, *, separator: str = ".") -> MutableMapping[str, Any]:
    """Define valor em dict aninhado usando path."""
    current: MutableMapping[str, Any] = payload
    parts = path.split(separator)
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, MutableMapping):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value
    return payload


def delete_nested(payload: MutableMapping[str, Any], path: str, *, separator: str = ".") -> bool:
    """Remove valor aninhado. Retorna True se removeu."""
    current: MutableMapping[str, Any] = payload
    parts = path.split(separator)
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, MutableMapping):
            return False
        current = next_value
    return current.pop(parts[-1], None) is not None


def flatten_dict(payload: Mapping[str, Any], *, separator: str = ".", prefix: str = "") -> Dict[str, Any]:
    """Achata dict aninhado."""
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        next_key = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(flatten_dict(value, separator=separator, prefix=next_key))
        else:
            result[next_key] = value
    return result


def unflatten_dict(payload: Mapping[str, Any], *, separator: str = ".") -> Dict[str, Any]:
    """Reverte flatten_dict."""
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        set_nested(result, str(key), value, separator=separator)
    return result


def deep_merge(*mappings: Mapping[str, Any], overwrite: bool = True) -> Dict[str, Any]:
    """Merge profundo de dicts."""
    result: Dict[str, Any] = {}
    for mapping in mappings:
        for key, value in mapping.items():
            if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
                result[key] = deep_merge(result[key], value, overwrite=overwrite)
            elif overwrite or key not in result:
                result[key] = value
    return result


def pick(payload: Mapping[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Seleciona chaves presentes."""
    wanted = set(keys)
    return {key: value for key, value in payload.items() if key in wanted}


def omit(payload: Mapping[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Remove chaves informadas."""
    blocked = set(keys)
    return {key: value for key, value in payload.items() if key not in blocked}


def remove_none(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove chaves com valor None."""
    return {key: value for key, value in payload.items() if value is not None}


# =============================================================================
# Iterable/list helpers
# =============================================================================

def chunked(values: Sequence[T], size: int) -> Iterator[Tuple[T, ...]]:
    """Divide sequência em chunks."""
    if size <= 0:
        raise ValueError("chunk size must be greater than zero")
    for index in range(0, len(values), size):
        yield tuple(values[index : index + size])


def batch_iterable(values: Iterable[T], size: int) -> Iterator[List[T]]:
    """Divide iterable em batches sem exigir len()."""
    if size <= 0:
        raise ValueError("batch size must be greater than zero")
    batch: List[T] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def unique_preserve_order(values: Iterable[T]) -> List[T]:
    """Remove duplicados preservando ordem."""
    seen: Set[Any] = set()
    result: List[T] = []
    for value in values:
        marker = _hashable_marker(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def first(values: Iterable[T], *, default: Optional[T] = None, predicate: Optional[Callable[[T], bool]] = None) -> Optional[T]:
    """Retorna primeiro item, opcionalmente filtrado por predicado."""
    for value in values:
        if predicate is None or predicate(value):
            return value
    return default


def compact(values: Iterable[Optional[T]]) -> List[T]:
    """Remove None de iterable."""
    return [value for value in values if value is not None]


def count_by(values: Iterable[T], key: Callable[[T], Any]) -> Dict[str, int]:
    """Conta itens por chave derivada."""
    counter: Counter[str] = Counter()
    for value in values:
        counter[str(key(value))] += 1
    return dict(counter)


def group_by(values: Iterable[T], key: Callable[[T], Any]) -> Dict[str, List[T]]:
    """Agrupa itens por chave derivada."""
    grouped: Dict[str, List[T]] = defaultdict(list)
    for value in values:
        grouped[str(key(value))].append(value)
    return dict(grouped)


# =============================================================================
# String helpers
# =============================================================================

def normalize_text(value: Any, *, lowercase: bool = False, strip_accents: bool = False) -> str:
    """Normaliza texto com trim, espaços e opcionalmente acentos/minúsculas."""
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text.strip())
    if strip_accents:
        text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    if lowercase:
        text = text.lower()
    return text


def slugify(value: Any, *, separator: str = "-") -> str:
    """Converte texto em slug seguro."""
    text = normalize_text(value, lowercase=True, strip_accents=True)
    text = re.sub(r"[^a-z0-9]+", separator, text)
    text = re.sub(rf"{re.escape(separator)}+", separator, text)
    return text.strip(separator)


def snake_case(value: Any) -> str:
    """Converte texto em snake_case."""
    text = normalize_text(value, strip_accents=True)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_").lower()


def truncate(value: Any, max_length: int, *, suffix: str = "...") -> str:
    """Trunca string mantendo sufixo."""
    text = str(value)
    if max_length < 0:
        raise ValueError("max_length must be >= 0")
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix


def random_string(length: int = 16, *, alphabet: str = string.ascii_letters + string.digits) -> str:
    """Gera string aleatória não criptográfica."""
    if length <= 0:
        raise ValueError("length must be positive")
    return "".join(random.choice(alphabet) for _ in range(length))


def generate_id(prefix: Optional[str] = None) -> str:
    """Gera UUID textual com prefixo opcional."""
    value = uuid.uuid4().hex
    return f"{prefix}_{value}" if prefix else value


# =============================================================================
# Env/config helpers
# =============================================================================

def env(name: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    """Lê variável de ambiente."""
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    """Lê bool de variável de ambiente."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "sim"}


def env_int(name: str, default: int) -> int:
    """Lê int de variável de ambiente."""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def env_float(name: str, default: float) -> float:
    """Lê float de variável de ambiente."""
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def env_list(name: str, default: Optional[Sequence[str]] = None, *, separator: str = ",") -> List[str]:
    """Lê lista textual de variável de ambiente."""
    value = os.getenv(name)
    if value is None:
        return list(default or [])
    return [item.strip() for item in value.split(separator) if item.strip()]


# =============================================================================
# Path helpers
# =============================================================================

def ensure_dir(path: PathLike) -> Path:
    """Garante existência de diretório."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_parent_dir(path: PathLike) -> Path:
    """Garante existência do diretório pai de um arquivo."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def safe_resolve(path: PathLike, *, base_dir: Optional[PathLike] = None, allow_outside_base: bool = False) -> Path:
    """Resolve caminho e opcionalmente bloqueia path traversal fora de base_dir."""
    target = Path(path)
    if base_dir is not None and not target.is_absolute():
        target = Path(base_dir) / target
    resolved = target.resolve()
    if base_dir is not None and not allow_outside_base:
        base = Path(base_dir).resolve()
        if not str(resolved).startswith(str(base)):
            raise ValueError(f"Unsafe path outside base_dir: {resolved}")
    return resolved


def file_size(path: PathLike) -> int:
    """Retorna tamanho do arquivo."""
    return Path(path).stat().st_size


def human_bytes(size: int) -> str:
    """Formata bytes em unidade legível."""
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


# =============================================================================
# Retry/timing helpers
# =============================================================================

def retry_call(
    func: Callable[..., R],
    *args: Any,
    attempts: int = 3,
    delay_seconds: float = 0.2,
    max_delay_seconds: float = 5.0,
    multiplier: float = 2.0,
    jitter_seconds: float = 0.1,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    **kwargs: Any,
) -> R:
    """Executa função com retry simples."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_error: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return func(*args, **kwargs)
        except exceptions as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(max_delay_seconds, delay_seconds * (multiplier ** (attempt - 1)))
            if jitter_seconds > 0:
                delay += random.uniform(0, jitter_seconds)
            time.sleep(delay)
    raise last_error  # type: ignore[misc]


def timed(func: Callable[..., R]) -> Callable[..., Tuple[R, float]]:
    """Decorator que retorna (resultado, duration_ms)."""
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Tuple[R, float]:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        return result, (time.perf_counter() - start) * 1000.0

    return wrapper


@contextlib.contextmanager
def suppress_if(*exceptions: Type[BaseException], default: Any = None) -> Iterator[Any]:
    """Context manager para suprimir exceções específicas."""
    try:
        yield default
    except exceptions:
        return


# =============================================================================
# Security/redaction helpers
# =============================================================================

def is_sensitive_key(key: str) -> bool:
    """Detecta se nome de chave sugere segredo."""
    normalized = key.lower().replace("-", "_")
    return any(keyword in normalized for keyword in SENSITIVE_KEYWORDS)


def redact(value: Any, *, replacement: str = "[REDACTED]") -> Any:
    """Redige recursivamente campos sensíveis."""
    if isinstance(value, Mapping):
        return {
            str(key): replacement if is_sensitive_key(str(key)) else redact(item, replacement=replacement)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, replacement=replacement) for item in value]
    return value


def mask_text(value: str, *, visible_start: int = 2, visible_end: int = 2, mask: str = "*") -> str:
    """Mascara texto mantendo início/fim visíveis."""
    if len(value) <= visible_start + visible_end:
        return mask * len(value)
    return value[:visible_start] + (mask * (len(value) - visible_start - visible_end)) + value[-visible_end:]


# =============================================================================
# Validation/coercion helpers
# =============================================================================

def coalesce(*values: Any, default: Any = None) -> Any:
    """Retorna primeiro valor não nulo/não vazio."""
    for value in values:
        if value is not None and value != "":
            return value
    return default


def ensure(condition: bool, message: str = "condition failed") -> None:
    """Lança ValueError se condição for falsa."""
    if not condition:
        raise ValueError(message)


def require_not_none(value: Optional[T], name: str = "value") -> T:
    """Garante que valor não seja None."""
    if value is None:
        raise ValueError(f"{name} cannot be None")
    return value


def to_bool(value: Any, *, default: bool = False) -> bool:
    """Converte valor para bool tolerante."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "sim"}


def to_int(value: Any, *, default: Optional[int] = None) -> Optional[int]:
    """Converte valor para int com default."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def to_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    """Converte valor para float com default."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _hashable_marker(value: Any) -> Any:
    try:
        hash(value)
        return value
    except Exception:
        return json.dumps(safe_json_value(value), sort_keys=True, default=str)


__all__ = [
    "JsonDict",
    "PathLike",
    "SENSITIVE_KEYWORDS",
    "Timer",
    "TimerResult",
    "batch_iterable",
    "chunked",
    "coalesce",
    "compact",
    "count_by",
    "deep_merge",
    "delete_nested",
    "ensure",
    "ensure_dir",
    "ensure_parent_dir",
    "ensure_timezone",
    "env",
    "env_bool",
    "env_float",
    "env_int",
    "env_list",
    "file_size",
    "first",
    "flatten_dict",
    "from_json",
    "generate_id",
    "get_nested",
    "group_by",
    "human_bytes",
    "is_sensitive_key",
    "mask_text",
    "normalize_text",
    "omit",
    "parse_datetime",
    "pick",
    "random_string",
    "redact",
    "remove_none",
    "require_not_none",
    "retry_call",
    "safe_json_value",
    "safe_resolve",
    "set_nested",
    "slugify",
    "snake_case",
    "suppress_if",
    "timed",
    "timestamp_ms",
    "to_bool",
    "to_float",
    "to_int",
    "to_json",
    "truncate",
    "unflatten_dict",
    "unique_preserve_order",
    "utc_now",
    "utc_now_iso",
]
