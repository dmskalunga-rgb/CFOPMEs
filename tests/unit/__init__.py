# =========================================================
# TESTS / UNIT / __init__.py
# KWANZACONTROL ENTERPRISE CFO AI
# Enterprise Unit Testing Bootstrap
# =========================================================

"""
Enterprise Unit Testing Package
================================

Este módulo inicializa toda a camada de testes unitários
do ecossistema KwanzaControl CFO AI.

Objetivos:
-----------
- Centralizar configuração global de testes
- Garantir isolamento multi-tenant
- Permitir mocks enterprise
- Habilitar observabilidade
- Padronizar fixtures
- Integrar CI/CD pipelines
- Integrar cobertura ML/AI
- Preparar pytest enterprise architecture

Estrutura:
-----------
tests/
└── unit/
    ├── __init__.py
    ├── test_fraud_model.py
    ├── test_cashflow_model.py
    ├── test_revenue_model.py
    ├── test_ueba_model.py
    ├── test_api.py
    ├── test_services.py
    ├── test_monitoring.py
    ├── test_governance.py

Compatível:
------------
- pytest
- unittest
- GitHub Actions
- Render CI/CD
- Docker pipelines
- Supabase environments
- Enterprise observability stack
"""

from __future__ import annotations

import os
import sys
import logging

from pathlib import Path

# =========================================================
# ROOT PATH
# =========================================================

ROOT_DIR = Path(
    __file__
).resolve().parents[2]

if str(ROOT_DIR) not in sys.path:

    sys.path.append(
        str(ROOT_DIR)
    )

# =========================================================
# TEST ENVIRONMENT
# =========================================================

os.environ.setdefault(
    "ENVIRONMENT",
    "test"
)

os.environ.setdefault(
    "TESTING",
    "true"
)

os.environ.setdefault(
    "LOG_LEVEL",
    "INFO"
)

os.environ.setdefault(
    "ENABLE_OBSERVABILITY",
    "true"
)

os.environ.setdefault(
    "ENABLE_AUDIT_LOGGING",
    "true"
)

# =========================================================
# LOGGER CONFIG
# =========================================================

logger = logging.getLogger(
    "kwanzacontrol.tests.unit"
)

logger.setLevel(logging.INFO)

if not logger.handlers:

    stream_handler = (
        logging.StreamHandler()
    )

    formatter = logging.Formatter(

        "[%(asctime)s] "
        "[%(levelname)s] "
        "[%(name)s] "
        "%(message)s"
    )

    stream_handler.setFormatter(
        formatter
    )

    logger.addHandler(
        stream_handler
    )

# =========================================================
# TEST CONSTANTS
# =========================================================

TEST_TENANT_ID = (
    "tenant-test-001"
)

TEST_USER_ID = (
    "test-user-enterprise"
)

TEST_ENVIRONMENT = (
    "enterprise-testing"
)

TEST_MODEL_VERSION = (
    "1.0.0-test"
)

# =========================================================
# TEST FLAGS
# =========================================================

ENABLE_FAKE_DATABASE = True

ENABLE_FAKE_SUPABASE = True

ENABLE_FAKE_REDIS = True

ENABLE_FAKE_MONITORING = True

ENABLE_FAKE_ALERTS = True

ENABLE_FAKE_ML_MODELS = True

# =========================================================
# ENTERPRISE TEST UTILITIES
# =========================================================

def test_banner():

    print("""

=========================================================
KWANZACONTROL CFO AI
ENTERPRISE UNIT TEST ENVIRONMENT
=========================================================

Environment: TEST
Observability: ENABLED
Audit Logging: ENABLED
Isolation: MULTI-TENANT
CI/CD Ready: TRUE
ML Governance: ENABLED

=========================================================

""")


def validate_environment():

    required_vars = [

        "ENVIRONMENT",

        "TESTING",

        "LOG_LEVEL"
    ]

    missing = []

    for var in required_vars:

        if not os.getenv(var):

            missing.append(var)

    if missing:

        raise EnvironmentError(

            f"Missing environment variables: "
            f"{missing}"
        )

    logger.info({

        "service":
            "unit_test_environment",

        "status":
            "validated",

        "environment":
            os.getenv("ENVIRONMENT")
    })


# =========================================================
# TEST BOOTSTRAP
# =========================================================

def bootstrap_tests():

    validate_environment()

    logger.info({

        "service":
            "test_bootstrap",

        "testing":
            True,

        "tenant":
            TEST_TENANT_ID
    })

    test_banner()

# =========================================================
# AUTO BOOTSTRAP
# =========================================================

bootstrap_tests()

# =========================================================
# EXPORTS
# =========================================================

__all__ = [

    "ROOT_DIR",

    "TEST_TENANT_ID",

    "TEST_USER_ID",

    "TEST_ENVIRONMENT",

    "TEST_MODEL_VERSION",

    "ENABLE_FAKE_DATABASE",

    "ENABLE_FAKE_SUPABASE",

    "ENABLE_FAKE_REDIS",

    "ENABLE_FAKE_MONITORING",

    "ENABLE_FAKE_ALERTS",

    "ENABLE_FAKE_ML_MODELS",

    "bootstrap_tests",

    "validate_environment",

    "logger"
]

# =========================================================
# LOCAL EXECUTION
# =========================================================

if __name__ == "__main__":

    print("\nUNIT TEST PACKAGE INITIALIZED\n")

    print("ROOT_DIR:")
    print(ROOT_DIR)

    print("\nENVIRONMENT:")
    print(os.getenv("ENVIRONMENT"))

    print("\nTEST TENANT:")
    print(TEST_TENANT_ID)

    print("\nBOOTSTRAP STATUS:")
    print("SUCCESS\n")