from models.cashflow_forecast import CashflowForecastModel
from models.ueba_score import UEBAEngine
from models.nlp_classifier import NLPClassifier
import joblib

class InferenceService:

    def predict_cashflow(self, day_index):
        model = joblib.load("models/cashflow_model.pkl")
        return model.predict([[day_index]])[0]

    def detect_ueba(self, data):
        engine = UEBAEngine()
        return engine.score(data)

    def classify_text(self, text):
        nlp = NLPClassifier()
        return nlp.classify(text)