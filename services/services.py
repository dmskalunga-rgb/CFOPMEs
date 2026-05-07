from models.cashflow_model import CashflowModel

model = CashflowModel()

def predict_cashflow(X):
    return model.predict(X)