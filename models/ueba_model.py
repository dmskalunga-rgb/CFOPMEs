from sklearn.preprocessing import StandardScaler

class UEBAmodel:

    def __init__(self):
        self.scaler = StandardScaler()

    def train(self, X):
        self.scaler.fit(X)

    def score(self, X):
        return self.scaler.transform(X)