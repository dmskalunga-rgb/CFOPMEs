from models.nlp_classifier import NLPModel

model = NLPModel()


def classify_text(text: str):

    result = model.predict(text)

    return {
        "module": "nlp",
        "input": text,
        "category": result
    }