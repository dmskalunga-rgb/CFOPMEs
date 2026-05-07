from models.revenue_prediction import RevenueModel
import numpy as np

model = RevenueModel()


def predict_revenue():

    data = np.random.rand(10, 3)

    prediction = model.predict(data)

    return {
        "module": "revenue",
        "prediction": prediction.tolist()
    }