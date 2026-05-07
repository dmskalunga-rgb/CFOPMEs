from models.fraud_detection import FraudModel

model = FraudModel()


def detect_fraud():

    score = model.score()

    return {
        "module": "fraud",
        "risk_score": float(score),
        "status": "analyzed"
    }