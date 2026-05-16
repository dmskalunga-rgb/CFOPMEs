export interface IRTResult {
  grossSalary: number;
  taxableIncome: number;
  taxAmount: number;
  effectiveRate: number;
  bracket: number;
}

export interface INSSResult {
  grossSalary: number;
  employeeContribution: number;
  employerContribution: number;
  totalContribution: number;
}

interface IRTBracket {
  min: number;
  max: number | null;
  rate: number;
  fixedAmount: number;
}

const IRT_BRACKETS: IRTBracket[] = [
  { min: 0, max: 150000, rate: 0, fixedAmount: 0 },
  { min: 150000.01, max: 200000, rate: 0.13, fixedAmount: 0 },
  { min: 200000.01, max: 300000, rate: 0.16, fixedAmount: 6500 },
  { min: 300000.01, max: 500000, rate: 0.18, fixedAmount: 22500 },
  { min: 500000.01, max: 1000000, rate: 0.195, fixedAmount: 58500 },
  { min: 1000000.01, max: 1500000, rate: 0.20, fixedAmount: 156000 },
  { min: 1500000.01, max: 2000000, rate: 0.205, fixedAmount: 256000 },
  { min: 2000000.01, max: 2500000, rate: 0.21, fixedAmount: 358500 },
  { min: 2500000.01, max: 5000000, rate: 0.215, fixedAmount: 463500 },
  { min: 5000000.01, max: 10000000, rate: 0.22, fixedAmount: 1001000 },
  { min: 10000000.01, max: 25000000, rate: 0.225, fixedAmount: 2101000 },
  { min: 25000000.01, max: null, rate: 0.25, fixedAmount: 5476000 },
];

const INSS_EMPLOYEE_RATE = 0.03;
const INSS_EMPLOYER_RATE = 0.08;

const IVA_NORMAL_RATE = 0.14;
const IVA_REDUCED_RATE = 0.05;

function roundToTwoDecimals(value: number): number {
  return Math.round(value * 100) / 100;
}

export const calculateIRT = (grossSalary: number): IRTResult => {
  if (grossSalary < 0) {
    throw new Error("Salário bruto não pode ser negativo");
  }

  if (grossSalary <= 150000) {
    return {
      grossSalary: roundToTwoDecimals(grossSalary),
      taxableIncome: roundToTwoDecimals(grossSalary),
      taxAmount: 0,
      effectiveRate: 0,
      bracket: 1,
    };
  }

  let bracket = IRT_BRACKETS.find(
    (b) => grossSalary >= b.min && (b.max === null || grossSalary <= b.max)
  );

  if (!bracket) {
    bracket = IRT_BRACKETS[IRT_BRACKETS.length - 1];
  }

  const bracketIndex = IRT_BRACKETS.indexOf(bracket) + 1;
  const excessIncome = grossSalary - bracket.min;
  const taxOnExcess = excessIncome * bracket.rate;
  const totalTax = bracket.fixedAmount + taxOnExcess;
  const effectiveRate = (totalTax / grossSalary) * 100;

  return {
    grossSalary: roundToTwoDecimals(grossSalary),
    taxableIncome: roundToTwoDecimals(grossSalary),
    taxAmount: roundToTwoDecimals(totalTax),
    effectiveRate: roundToTwoDecimals(effectiveRate),
    bracket: bracketIndex,
  };
};

export const calculateINSS = (grossSalary: number): INSSResult => {
  if (grossSalary < 0) {
    throw new Error("Salário bruto não pode ser negativo");
  }

  const employeeContribution = grossSalary * INSS_EMPLOYEE_RATE;
  const employerContribution = grossSalary * INSS_EMPLOYER_RATE;
  const totalContribution = employeeContribution + employerContribution;

  return {
    grossSalary: roundToTwoDecimals(grossSalary),
    employeeContribution: roundToTwoDecimals(employeeContribution),
    employerContribution: roundToTwoDecimals(employerContribution),
    totalContribution: roundToTwoDecimals(totalContribution),
  };
};

export const calculateNetSalary = (
  grossSalary: number
): {
  grossSalary: number;
  inssEmployee: number;
  irt: number;
  netSalary: number;
  totalDeductions: number;
} => {
  if (grossSalary < 0) {
    throw new Error("Salário bruto não pode ser negativo");
  }

  const inssResult = calculateINSS(grossSalary);
  const taxableIncome = grossSalary - inssResult.employeeContribution;
  const irtResult = calculateIRT(taxableIncome);
  const totalDeductions = inssResult.employeeContribution + irtResult.taxAmount;
  const netSalary = grossSalary - totalDeductions;

  return {
    grossSalary: roundToTwoDecimals(grossSalary),
    inssEmployee: roundToTwoDecimals(inssResult.employeeContribution),
    irt: roundToTwoDecimals(irtResult.taxAmount),
    netSalary: roundToTwoDecimals(netSalary),
    totalDeductions: roundToTwoDecimals(totalDeductions),
  };
};

export const calculateIVA = (
  amount: number,
  rate: "normal" | "reduced" | "exempt" = "normal"
): {
  baseAmount: number;
  ivaRate: number;
  ivaAmount: number;
  totalAmount: number;
} => {
  if (amount < 0) {
    throw new Error("Montante não pode ser negativo");
  }

  let ivaRate = 0;

  switch (rate) {
    case "normal":
      ivaRate = IVA_NORMAL_RATE;
      break;
    case "reduced":
      ivaRate = IVA_REDUCED_RATE;
      break;
    case "exempt":
      ivaRate = 0;
      break;
    default:
      ivaRate = IVA_NORMAL_RATE;
  }

  const ivaAmount = amount * ivaRate;
  const totalAmount = amount + ivaAmount;

  return {
    baseAmount: roundToTwoDecimals(amount),
    ivaRate: roundToTwoDecimals(ivaRate * 100),
    ivaAmount: roundToTwoDecimals(ivaAmount),
    totalAmount: roundToTwoDecimals(totalAmount),
  };
};
