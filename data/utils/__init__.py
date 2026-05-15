"""
data/utils/__init__.py

Enterprise-grade utilities package initializer.

Este pacote centraliza utilitários transversais usados por módulos de dados,
ingestão, validação, IA, observabilidade, configuração, segurança e pipelines.

Objetivos:
- Expor uma API pública estável para utilitários comuns.
- Evitar imports pesados no carregamento inicial do pacote.
- Padronizar metadados, versionamento e descoberta de capacidades.
- Fornecer helpers mínimos e seguros para uso imediato.
- Permitir imports opcionais/lazy sem quebrar execução em ambientes parciais.

Exemplo:
    from data.utils import package_info, safe_import, utc_now_iso

    print(package_info())
    module = safe_import("json")
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import platform
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


__title__ = "data.utils"
__description__ = "Enterprise utility layer for data platform modules"
__version__ = "1.0.0"
__author__ = "Data Platform Team"
__license__ = "Proprietary"


logger = logging.getLogger(__name__)

JsonDict = Dict[str, Any]


class RuntimeEnvironment(str, Enum):
    """Ambientes operacionais padronizados."""

    LOCAL = "local"
    DEVELOPMENT = "development"
    STAGING = "staging"
    HOMOLOGATION = "homologation"
    PRODUCTION = "production"
    TEST = "test"
    UNKNOWN = "unknown"


class ImportStatus(str, Enum):
    """Status de importação lazy/opcional."""

    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OptionalImportResult:
    """Resultado seguro de uma tentativa de import opcional."""

    module_name: str
    status: ImportStatus
    module: Optional[Any] = None
    error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self.status == ImportStatus.AVAILABLE and self.module is not None

    def require(self) -> Any:
        if not self.available:
            raise ImportError(f"Optional dependency not available: {self.module_name}. Error: {self.error}")
        return self.module

    def to_dict(self) -> JsonDict:
        return {
            "module_name": self.module_name,
            "status": self.status.value,
            "available": self.available,
            "error": self.error,
        }


@dataclass(frozen=True)
class PackageInfo:
    """Metadados do pacote data.utils."""

    title: str = __title__
    description: str = __description__
    version: str = __version__
    author: str = __author__
    license: str = __license__
    python_version: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=platform.platform)
    runtime_environment: RuntimeEnvironment = field(default_factory=lambda: detect_environment())

    def to_dict(self) -> JsonDict:
        return {
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "python_version": self.python_version,
            "platform": self.platform,
            "runtime_environment": self.runtime_environment.value,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


OPTIONAL_MODULES: Tuple[str, ...] = (
    "pandas",
    "numpy",
    "pyarrow",
    "pydantic",
    "yaml",
    "requests",
    "sqlalchemy",
    "psycopg2",
    "redis",
    "kafka",
)


_PUBLIC_SUBMODULES: Tuple[str, ...] = (
    "config",
    "datetime_utils",
    "file_utils",
    "hashing",
    "json_utils",
    "logging_utils",
    "security",
    "serialization",
    "string_utils",
    "typing_utils",
)


def package_info() -> PackageInfo:
    """Retorna metadados do pacote."""
    return PackageInfo()


def detect_environment(value: Optional[str] = None) -> RuntimeEnvironment:
    """Detecta ambiente operacional a partir de variável explícita ou env vars comuns."""
    raw = (
        value
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or os.getenv("ENV")
        or os.getenv("PYTHON_ENV")
        or "unknown"
    )
    normalized = str(raw).strip().lower()
    aliases = {
        "dev": RuntimeEnvironment.DEVELOPMENT,
        "development": RuntimeEnvironment.DEVELOPMENT,
        "local": RuntimeEnvironment.LOCAL,
        "localhost": RuntimeEnvironment.LOCAL,
        "stage": RuntimeEnvironment.STAGING,
        "staging": RuntimeEnvironment.STAGING,
        "hml": RuntimeEnvironment.HOMOLOGATION,
        "homolog": RuntimeEnvironment.HOMOLOGATION,
        "homologation": RuntimeEnvironment.HOMOLOGATION,
        "prod": RuntimeEnvironment.PRODUCTION,
        "production": RuntimeEnvironment.PRODUCTION,
        "test": RuntimeEnvironment.TEST,
        "testing": RuntimeEnvironment.TEST,
        "ci": RuntimeEnvironment.TEST,
    }
    return aliases.get(normalized, RuntimeEnvironment.UNKNOWN)


def is_production() -> bool:
    """Indica se o runtime atual está em produção."""
    return detect_environment() == RuntimeEnvironment.PRODUCTION


def is_local() -> bool:
    """Indica se o runtime atual está em ambiente local."""
    return detect_environment() == RuntimeEnvironment.LOCAL


def utc_now() -> datetime:
    """Retorna datetime atual em UTC com timezone."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Retorna timestamp atual UTC em ISO-8601."""
    return utc_now().isoformat()


def safe_import(module_name: str, *, package: Optional[str] = None) -> OptionalImportResult:
    """Importa módulo opcional de forma segura, sem quebrar o runtime."""
    try:
        module = importlib.import_module(module_name, package=package)
        return OptionalImportResult(module_name=module_name, status=ImportStatus.AVAILABLE, module=module)
    except ModuleNotFoundError as exc:
        return OptionalImportResult(module_name=module_name, status=ImportStatus.MISSING, error=str(exc))
    except Exception as exc:  # pragma: no cover - proteção defensiva
        logger.debug("Optional import failed for %s", module_name, exc_info=True)
        return OptionalImportResult(module_name=module_name, status=ImportStatus.FAILED, error=str(exc))


def optional_dependencies_status(modules: Optional[Sequence[str]] = None) -> Dict[str, JsonDict]:
    """Retorna status de dependências opcionais comuns."""
    selected = modules or OPTIONAL_MODULES
    return {name: safe_import(name).to_dict() for name in selected}


def safe_json_value(value: Any) -> Any:
    """Converte valores arbitrários em estrutura JSON-safe."""
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


def to_json(value: Any, *, indent: int = 2, sort_keys: bool = True) -> str:
    """Serializa valor para JSON usando conversão segura."""
    return json.dumps(safe_json_value(value), ensure_ascii=False, indent=indent, sort_keys=sort_keys, default=str)


def ensure_directory(path: os.PathLike[str] | str) -> Path:
    """Garante que um diretório exista e retorna Path normalizado."""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_project_path(*parts: str, base: Optional[os.PathLike[str] | str] = None) -> Path:
    """Resolve caminho de projeto de forma portável."""
    root = Path(base) if base is not None else Path.cwd()
    return root.joinpath(*parts).resolve()


def coalesce(*values: Any, default: Any = None) -> Any:
    """Retorna o primeiro valor não nulo/não vazio."""
    for value in values:
        if value is not None and value != "":
            return value
    return default


def chunked(values: Sequence[Any], size: int) -> Iterable[Tuple[Any, ...]]:
    """Divide uma sequência em chunks imutáveis."""
    if size <= 0:
        raise ValueError("chunk size must be greater than zero")
    for index in range(0, len(values), size):
        yield tuple(values[index : index + size])


def flatten_dict(payload: Mapping[str, Any], *, separator: str = ".", prefix: str = "") -> Dict[str, Any]:
    """Achata um dicionário aninhado."""
    result: Dict[str, Any] = {}
    for key, value in payload.items():
        new_key = f"{prefix}{separator}{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(flatten_dict(value, separator=separator, prefix=new_key))
        else:
            result[new_key] = value
    return result


def get_nested(payload: Mapping[str, Any], path: str, *, default: Any = None, separator: str = ".") -> Any:
    """Obtém valor aninhado por path separado por ponto."""
    current: Any = payload
    for part in path.split(separator):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return default
    return current


def set_nested(payload: Dict[str, Any], path: str, value: Any, *, separator: str = ".") -> Dict[str, Any]:
    """Define valor aninhado por path separado por ponto."""
    current = payload
    parts = path.split(separator)
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value
    return payload


def exported_symbols() -> Tuple[str, ...]:
    """Retorna os símbolos públicos exportados pelo pacote."""
    return __all__


def available_submodules() -> Tuple[str, ...]:
    """Retorna submódulos públicos planejados para data.utils."""
    return _PUBLIC_SUBMODULES


__all__ = (
    "ImportStatus",
    "JsonDict",
    "OPTIONAL_MODULES",
    "OptionalImportResult",
    "PackageInfo",
    "RuntimeEnvironment",
    "available_submodules",
    "chunked",
    "coalesce",
    "detect_environment",
    "ensure_directory",
    "exported_symbols",
    "flatten_dict",
    "get_nested",
    "is_local",
    "is_production",
    "optional_dependencies_status",
    "package_info",
    "resolve_project_path",
    "safe_import",
    "safe_json_value",
    "set_nested",
    "to_json",
    "utc_now",
    "utc_now_iso",
)
