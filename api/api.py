from services.cashflow_service import predict_cashflow

@app.get("/cashflow")
def cashflow():
    return predict_cashflow([[1,2,3]])