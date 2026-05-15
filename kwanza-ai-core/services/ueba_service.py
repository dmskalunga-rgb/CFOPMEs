from models.ueba_score import UEBAModel

model = UEBAModel()


def compute_ueba():

    score = model.compute()

    return {
        "module": "ueba",
        "score": score,
        "risk": "low" if score < 0.5 else "high"
    }