class UEBAModel:

    def compute(self):
        return 0.27


if __name__ == "__main__":
    model = UEBAModel()

    result = model.compute()

    print("UEBA Score:")
    print(result)