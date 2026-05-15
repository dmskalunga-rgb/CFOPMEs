# models/cashflow_forecast.py
"""
Backward-compatible cashflow forecast model.

Mantém compatibilidade com imports legados:

    from models.cashflow_forecast import ...

A implementação principal do domínio cashflow permanece em:

    models.cashflow
    models.forecasting.cashflow_ai
"""

from __future__ import annotations

try:
    from models.cashflow import *  # noqa: F401,F403
except Exception as exc:
    CASHFLOW_IMPORT_ERROR = exc
else:
    CASHFLOW_IMPORT_ERROR = None


try:
    from models.forecasting.cashflow_ai import *  # noqa: F401,F403
except Exception as exc:
    CASHFLOW_AI_IMPORT_ERROR = exc
else:
    CASHFLOW_AI_IMPORT_ERROR = None


class CashflowForecastCompatibilityError(RuntimeError):
    pass


def package_info() -> dict[str, str]:
    return {
        "package": "models.cashflow_forecast",
        "purpose": "enterprise_backward_compatibility",
        "primary_sources": "models.cashflow, models.forecasting.cashflow_ai",
        "cashflow_import": "ok" if CASHFLOW_IMPORT_ERROR is None else repr(CASHFLOW_IMPORT_ERROR),
        "cashflow_ai_import": "ok" if CASHFLOW_AI_IMPORT_ERROR is None else repr(CASHFLOW_AI_IMPORT_ERROR),
        "status": "active"
        if CASHFLOW_IMPORT_ERROR is None or CASHFLOW_AI_IMPORT_ERROR is None
        else "degraded",
    }


def ensure_available() -> None:
    if CASHFLOW_IMPORT_ERROR is not None and CASHFLOW_AI_IMPORT_ERROR is not None:
        raise CashflowForecastCompatibilityError(
            "Nenhuma implementação de cashflow forecast pôde ser importada. "
            f"models.cashflow={CASHFLOW_IMPORT_ERROR!r}; "
            f"models.forecasting.cashflow_ai={CASHFLOW_AI_IMPORT_ERROR!r}"
        )


ensure_available()


__all__ = [
    name
    for name in globals()
    if not name.startswith("_")
]