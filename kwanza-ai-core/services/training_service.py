from models.cashflow_forecast import CashflowForecastModel
from models.fraud_detection import FraudDetectionModel

class TrainingService:

    def train_cashflow(self, df):
        model = CashflowForecastModel()
        model.train(df)
        return "Cashflow trained"

    def train_fraud(self, df):
        model = FraudDetectionModel()
        model.train(df)
        return "Fraud model trained"