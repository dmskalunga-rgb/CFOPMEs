class BaseModel:

    def train(self, X, y):
        raise NotImplementedError

    def predict(self, X):
        raise NotImplementedError

    def save(self, path: str):
        pass

    def load(self, path: str):
        pass