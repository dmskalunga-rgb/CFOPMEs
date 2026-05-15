from fastapi import FastAPI

from services.cashflow_service import predict_cashflow
from services.revenue_service import predict_revenue
from services.fraud_service import detect_fraud
from services.nlp_service import classify_text
from services.payroll_service import optimize_payroll
from services.ueba_service import compute_ueba

app = FastAPI(
    title="KwanzaControl CFO AI Engine",
    version="1.0.0"
)

@app.get("/")
def home():
    return {
        "status": "online",
        "system": "CFO AI Engine"
    }

@app.get("/cashflow")
def cashflow():
    return predict_cashflow()

@app.get("/revenue")
def revenue():
    return predict_revenue()

@app.get("/fraud")
def fraud():
    return detect_fraud()

@app.post("/nlp")
def nlp(text: str):
    return classify_text(text)

@app.get("/payroll")
def payroll():
    return optimize_payroll()

@app.get("/ueba")
def ueba():
    return compute_ueba()