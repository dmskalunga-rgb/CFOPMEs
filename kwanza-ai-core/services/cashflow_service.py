from models.cashflow_forecast import (
    CashflowForecastModel
)

import pandas as pd
import numpy as np

model = CashflowForecastModel()


def predict_cashflow():

    df = pd.DataFrame({
        "ds": pd.date_range(
            "2024-01-01",
            periods=30
        ),
        "y": np.random.randint(
            2000,
            8000,
            30
        )
    })

    model.train_prophet(df)

    forecast = model.forecast_prophet(7)

    return {
        "module": "cashflow",
        "forecast": forecast.to_dict(
            orient="records"
        )
    }