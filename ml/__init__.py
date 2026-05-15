# ml/__init__.py
"""
Enterprise Machine Learning package for kwanza-ai-core.

Centraliza compatibilidade, metadados e descoberta dos subpacotes ML:

    ml.anomaly_detection
    ml.configs
    ml.forecasting
    ml.fraud_detection
    ml.governance
    ml.monitoring
    ml.nlp
    ml.pipelines
    ml.serving
    ml.utils

Também mantém compatibilidade com imports legados usados por testes
e versões anteriores da plataforma.
"""

from __future__ import annotations

__package_name__ = "ml"
__version__ = "1.0.0"
__status__ = "active"
__architecture__ = "enterprise_ml_platform"


def package_info() -> dict[str, str]:
    return {
        "package": __package_name__,
        "version": __version__,
        "status": __status__,
        "architecture": __architecture__,
        "purpose": "machine_learning_core",
    }


__all__ = [
    "package_info",
]