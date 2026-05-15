class NLPModel:

    def predict(self, text):
        return "financial_query"


if __name__ == "__main__":
    model = NLPModel()

    result = model.predict(
        "Quero prever fluxo de caixa"
    )

    print("NLP Classification:")
    print(result)