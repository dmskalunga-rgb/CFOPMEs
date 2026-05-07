class FraudModel:

    def score(self):
        return 0.12


if __name__ == "__main__":
    model = FraudModel()

    result = model.score()

    print("Fraud Score:")
    print(result)