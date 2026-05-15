import numpy as np


class RevenueModel:

    def predict(self, data):
        return np.mean(data, axis=1)


if __name__ == "__main__":
    model = RevenueModel()

    data = np.array([
        [100, 200, 300],
        [400, 500, 600]
    ])

    prediction = model.predict(data)

    print("Revenue Prediction:")
    print(prediction)