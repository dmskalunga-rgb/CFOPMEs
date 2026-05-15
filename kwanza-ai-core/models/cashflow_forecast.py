import pandas as pd
import numpy as np
from prophet import Prophet
from sklearn.ensemble import RandomForestRegressor


class CashflowForecastModel:

    def __init__(self):
        self.prophet_model = Prophet()
        self.ml_model = RandomForestRegressor()
        self.is_trained = False

    # -------------------------
    # PROPHET FORECAST
    # -------------------------
    def train_prophet(self, df):
        self.prophet_model.fit(df)

    def forecast_prophet(self, periods=7):

        future = self.prophet_model.make_future_dataframe(
            periods=periods
        )

        forecast = self.prophet_model.predict(future)

        return forecast[[
            "ds",
            "yhat",
            "yhat_lower",
            "yhat_upper"
        ]]

    # -------------------------
    # MACHINE LEARNING
    # -------------------------
    def train_ml(self, X, y):
        self.ml_model.fit(X, y)
        self.is_trained = True

    def predict_ml(self, X):

        if not self.is_trained:
            raise Exception(
                "Modelo ML não treinado"
            )

        return self.ml_model.predict(X)


# ---------------------------------
# TESTE LOCAL
# ---------------------------------
if __name__ == "__main__":

    df = pd.DataFrame({
        "ds": pd.date_range(
            "2024-01-01",
            periods=30
        ),
        "y": np.random.randint(
            1000,
            5000,
            30
        )
    })

    model = CashflowForecastModel()

    model.train_prophet(df)

    forecast = model.forecast_prophet(7)

    print("Cashflow Forecast:")
    print(forecast.head())