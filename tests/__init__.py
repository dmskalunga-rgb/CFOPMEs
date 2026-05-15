# kwanza-ai-core/tests/__init__.py
"""
Test suite package for kwanza-ai-core.

Este pacote centraliza testes unitários, integração, contrato, segurança,
observabilidade e regressão para garantir qualidade enterprise.
"""

from __future__ import annotations

import os
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_ROOT.parent

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_DEBUG", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("DB_DRIVER", "sqlite")
os.environ.setdefault("SQLITE_PATH", str(PROJECT_ROOT / "storage" / "test_kwanza_ai.db"))
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_BASE_PATH", str(PROJECT_ROOT / "storage" / "tests"))
os.environ.setdefault("METRICS_ENABLED", "true")
os.environ.setdefault("TRACER_ENABLED", "false")
os.environ.setdefault("ALERTING_ENABLED", "false")
os.environ.setdefault("AUDIT_ENABLED", "true")


__all__ = [
    "TESTS_ROOT",
    "PROJECT_ROOT",
]