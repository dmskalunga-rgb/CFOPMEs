class PayrollModel:

    def optimize(self):
        return {
            "savings": 12000
        }


if __name__ == "__main__":
    model = PayrollModel()

    result = model.optimize()

    print("Payroll Optimization:")
    print(result)