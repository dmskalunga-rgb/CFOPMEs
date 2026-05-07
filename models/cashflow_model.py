from sklearn.ensemble import RandomForestRegressor
import numpy as np

class CashflowModel:

    def __init__(self):
        self.model = RandomForestRegressor(
            n_estimators=300,
            max_depth=12
        )

    def train(self, X, y):
        self.model.fit(X, y)

    def predict(self, X):
        return self.model.predict(X)