# app/schemas/__init__.py
"""
Enterprise compatibility package for application schemas.

Mantém compatibilidade com imports legados:

    from app.schemas.serializers import ...

A implementação principal dos serializers permanece em:

    ml.utils.serializers
"""

from __future__ import annotations

try:
    from app.schemas.serializers import *  # noqa: F401,F403
except Exception:
    pass


def package_info() -> dict[str, str]:
    return {
        "package": "app.schemas",
        "purpose": "compatibility_layer",
        "source": "ml.utils.serializers",
        "status": "active",
    }


__all__ = [
    name
    for name in globals()
    if not name.startswith("_")
]