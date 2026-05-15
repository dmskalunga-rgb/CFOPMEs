# app/__init__.py
"""
Enterprise compatibility application package for kwanza-ai-core.

Este pacote existe para manter compatibilidade com imports legados como:

    from app.schemas.serializers import ...

A arquitetura principal do projeto permanece organizada em:

    core/
    infrastructure/
    services/
    ml/
    models/
    observability/
    pipelines/
    feature_store/

Este pacote deve funcionar como uma camada leve de compatibilidade,
não como nova fonte principal de lógica de negócio.
"""

from __future__ import annotations

__package_name__ = "app"
__purpose__ = "enterprise_compatibility_layer"
__status__ = "active"


def package_info() -> dict[str, str]:
    return {
        "package": __package_name__,
        "purpose": __purpose__,
        "status": __status__,
        "architecture": "compatibility_adapter",
    }


__all__ = [
    "package_info",
]