from models.payroll_optimizer import PayrollModel

model = PayrollModel()


def optimize_payroll():

    result = model.optimize()

    return {
        "module": "payroll",
        "optimized": result
    }