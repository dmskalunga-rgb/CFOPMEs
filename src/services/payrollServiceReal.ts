// =====================================================
// KWANZACONTROL — Payroll Service Real
// Estrutura real: employees(tenant_id, full_name, gross_salary, status ACTIVE)
//                 payslips(tenant_id, payroll_month YYYY-MM, inss_employee, irt, payment_status)
// =====================================================
import { supabase } from '@/integrations/supabase/client'

// ─── Tipos ───────────────────────────────────────────────────────────────────

export interface Employee {
  id: string
  tenant_id: string
  employee_number?: string
  nif?: string
  full_name: string
  email?: string
  phone?: string
  date_of_birth?: string
  position: string
  department?: string
  hire_date: string
  termination_date?: string
  employment_type: string
  gross_salary: number
  bank_name?: string
  bank_account?: string
  iban?: string
  status: 'ACTIVE' | 'INACTIVE' | 'TERMINATED'
  notes?: string
  created_at?: string
  updated_at?: string
}

export interface Payslip {
  id: string
  tenant_id: string
  employee_id: string
  employee_name: string
  employee_nif?: string
  payroll_month: string        // formato YYYY-MM
  payment_date?: string
  gross_salary: number
  allowances: number
  bonuses: number
  overtime: number
  total_earnings: number
  inss_employee: number        // 3% empregado
  inss_employer: number        // 8% empregador
  irt: number
  irt_bracket?: number
  other_deductions: number
  total_deductions: number
  net_salary: number
  pdf_url?: string
  payment_status: 'PENDING' | 'PAID' | 'CANCELLED'
  payment_reference?: string
  notes?: string
  created_by?: string
  created_at?: string
  updated_at?: string
}

export interface PayrollCalcResult {
  gross_salary: number
  allowances: number
  bonuses: number
  overtime: number
  total_earnings: number
  inss_employee: number
  inss_employer: number
  irt: number
  irt_bracket: number
  other_deductions: number
  total_deductions: number
  net_salary: number
}

export interface PayrollStats {
  total_employees: number
  total_gross: number
  total_irt: number
  total_inss_employee: number
  total_inss_employer: number
  total_net: number
  total_cost: number       // gross + inss_employer
  paid_count: number
  pending_count: number
}

// ─── Tabelas IRT Angola (Lei n.º 28/11) ─────────────────────────────────────
// Escalonamento progressivo por escalão
const IRT_BRACKETS = [
  { min: 0,        max: 70000,    rate: 0,     fixed: 0       },
  { min: 70000,    max: 100000,   rate: 0.13,  fixed: 0       },
  { min: 100000,   max: 150000,   rate: 0.16,  fixed: 3900    },
  { min: 150000,   max: 200000,   rate: 0.18,  fixed: 11900   },
  { min: 200000,   max: 300000,   rate: 0.19,  fixed: 20900   },
  { min: 300000,   max: 500000,   rate: 0.20,  fixed: 39900   },
  { min: 500000,   max: 1000000,  rate: 0.21,  fixed: 79900   },
  { min: 1000000,  max: 1500000,  rate: 0.22,  fixed: 184900  },
  { min: 1500000,  max: 2000000,  rate: 0.23,  fixed: 294900  },
  { min: 2000000,  max: 2500000,  rate: 0.24,  fixed: 409900  },
  { min: 2500000,  max: 5000000,  rate: 0.245, fixed: 529900  },
  { min: 5000000,  max: 10000000, rate: 0.25,  fixed: 1142400 },
  { min: 10000000, max: Infinity, rate: 0.25,  fixed: 2392400 },
]

const INSS_EMPLOYEE_RATE = 0.03   // 3% empregado
const INSS_EMPLOYER_RATE = 0.08   // 8% empregador
const INSS_MAX_BASE = 500000      // tecto máximo de incidência

// ─── Cálculo IRT ─────────────────────────────────────────────────────────────
export function calculateIRT(grossSalary: number): { irt: number; bracket: number } {
  for (let i = 0; i < IRT_BRACKETS.length; i++) {
    const b = IRT_BRACKETS[i]
    if (grossSalary >= b.min && grossSalary < b.max) {
      const taxable = grossSalary - b.min
      return { irt: Math.round(b.fixed + taxable * b.rate), bracket: i + 1 }
    }
  }
  return { irt: 0, bracket: 1 }
}

// ─── Cálculo INSS ────────────────────────────────────────────────────────────
export function calculateINSS(grossSalary: number): { employee: number; employer: number } {
  const base = Math.min(grossSalary, INSS_MAX_BASE)
  return {
    employee: Math.round(base * INSS_EMPLOYEE_RATE),
    employer: Math.round(base * INSS_EMPLOYER_RATE),
  }
}

// ─── Cálculo completo ────────────────────────────────────────────────────────
export function calculateFullPayroll(
  grossSalary: number,
  allowances = 0,
  bonuses = 0,
  overtime = 0,
  otherDeductions = 0,
): PayrollCalcResult {
  const totalEarnings = grossSalary + allowances + bonuses + overtime
  const { irt, bracket } = calculateIRT(grossSalary)       // IRT incide só no salário base
  const inss = calculateINSS(grossSalary)                   // INSS incide no salário base
  const totalDeductions = irt + inss.employee + otherDeductions
  const netSalary = Math.max(totalEarnings - totalDeductions, 0)

  return {
    gross_salary: grossSalary,
    allowances,
    bonuses,
    overtime,
    total_earnings: totalEarnings,
    inss_employee: inss.employee,
    inss_employer: inss.employer,
    irt,
    irt_bracket: bracket,
    other_deductions: otherDeductions,
    total_deductions: totalDeductions,
    net_salary: netSalary,
  }
}

// ─── Helpers Supabase ────────────────────────────────────────────────────────
async function getTenantId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null
  const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  if (data?.tenant_id) return data.tenant_id
  const { data: t } = await supabase.from('tenants').select('id').limit(1).maybeSingle()
  return t?.id || null
}

async function getUserId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  return user?.id || null
}

// ─── EMPLOYEES ───────────────────────────────────────────────────────────────

export async function getActiveEmployees(): Promise<Employee[]> {
  const tenantId = await getTenantId()
  if (!tenantId) return []
  const { data, error } = await supabase
    .from('employees')
    .select('*')
    .eq('tenant_id', tenantId)
    .eq('status', 'ACTIVE')
    .order('full_name')
  if (error) { console.error('Erro employees:', error); return [] }
  return data || []
}

export async function getAllEmployees(): Promise<Employee[]> {
  const tenantId = await getTenantId()
  if (!tenantId) return []
  const { data, error } = await supabase
    .from('employees')
    .select('*')
    .eq('tenant_id', tenantId)
    .order('full_name')
  if (error) { console.error('Erro employees:', error); return [] }
  return data || []
}

export async function createEmployee(
  emp: Omit<Employee, 'id' | 'tenant_id' | 'created_at' | 'updated_at'>
): Promise<Employee> {
  const tenantId = await getTenantId()
  if (!tenantId) throw new Error('Não autenticado')
  const { data, error } = await supabase
    .from('employees')
    .insert({ ...emp, tenant_id: tenantId })
    .select().single()
  if (error) throw error
  return data
}

export async function updateEmployee(
  id: string,
  updates: Partial<Omit<Employee, 'id' | 'tenant_id'>>
): Promise<Employee> {
  const tenantId = await getTenantId()
  if (!tenantId) throw new Error('Não autenticado')
  const { data, error } = await supabase
    .from('employees')
    .update({ ...updates, updated_at: new Date().toISOString() })
    .eq('id', id).eq('tenant_id', tenantId)
    .select().single()
  if (error) throw error
  return data
}

export async function deleteEmployee(id: string): Promise<void> {
  const tenantId = await getTenantId()
  if (!tenantId) throw new Error('Não autenticado')
  const { error } = await supabase
    .from('employees').delete()
    .eq('id', id).eq('tenant_id', tenantId)
  if (error) throw error
}

// ─── PAYSLIPS ────────────────────────────────────────────────────────────────

/** Formato: YYYY-MM */
export function toPayrollMonth(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

export async function getPayslipsByMonth(month: number, year: number): Promise<Payslip[]> {
  const tenantId = await getTenantId()
  if (!tenantId) return []
  const pm = toPayrollMonth(year, month)
  const { data, error } = await supabase
    .from('payslips')
    .select('*')
    .eq('tenant_id', tenantId)
    .eq('payroll_month', pm)
    .order('employee_name')
  if (error) { console.error('Erro payslips:', error); return [] }
  return data || []
}

export async function getEmployeePayslips(employeeId: string, limit = 12): Promise<Payslip[]> {
  const tenantId = await getTenantId()
  if (!tenantId) return []
  const { data, error } = await supabase
    .from('payslips')
    .select('*')
    .eq('tenant_id', tenantId)
    .eq('employee_id', employeeId)
    .order('payroll_month', { ascending: false })
    .limit(limit)
  if (error) { console.error('Erro payslips funcionário:', error); return [] }
  return data || []
}

/** Processa folha de pagamento para um mês inteiro */
export async function processMonthlyPayroll(month: number, year: number): Promise<Payslip[]> {
  const tenantId = await getTenantId()
  const userId = await getUserId()
  if (!tenantId) throw new Error('Não autenticado')

  const pm = toPayrollMonth(year, month)

  // Verificar se já existe
  const existing = await getPayslipsByMonth(month, year)
  if (existing.length > 0) {
    throw new Error(`Folha de ${pm} já foi processada (${existing.length} recibos)`)
  }

  const employees = await getActiveEmployees()
  if (employees.length === 0) throw new Error('Nenhum funcionário activo')

  const rows = employees.map(emp => {
    const calc = calculateFullPayroll(emp.gross_salary)
    return {
      tenant_id: tenantId,
      employee_id: emp.id,
      employee_name: emp.full_name,
      employee_nif: emp.nif,
      payroll_month: pm,
      gross_salary: calc.gross_salary,
      allowances: calc.allowances,
      bonuses: calc.bonuses,
      overtime: calc.overtime,
      total_earnings: calc.total_earnings,
      inss_employee: calc.inss_employee,
      inss_employer: calc.inss_employer,
      irt: calc.irt,
      irt_bracket: calc.irt_bracket,
      other_deductions: calc.other_deductions,
      total_deductions: calc.total_deductions,
      net_salary: calc.net_salary,
      payment_status: 'PENDING' as const,
      created_by: userId || undefined,
    }
  })

  const { data, error } = await supabase
    .from('payslips')
    .insert(rows)
    .select()
  if (error) throw error
  return data || []
}

export async function updatePayslipStatus(
  payslipId: string,
  status: 'PENDING' | 'PAID' | 'CANCELLED',
  paymentDate?: string,
  paymentReference?: string,
): Promise<Payslip> {
  const tenantId = await getTenantId()
  if (!tenantId) throw new Error('Não autenticado')
  const upd: Partial<Payslip> = {
    payment_status: status,
    updated_at: new Date().toISOString(),
  }
  if (paymentDate) upd.payment_date = paymentDate
  if (paymentReference) upd.payment_reference = paymentReference
  const { data, error } = await supabase
    .from('payslips')
    .update(upd)
    .eq('id', payslipId).eq('tenant_id', tenantId)
    .select().single()
  if (error) throw error
  return data
}

export async function deletePayslip(payslipId: string): Promise<void> {
  const tenantId = await getTenantId()
  if (!tenantId) throw new Error('Não autenticado')
  const { error } = await supabase
    .from('payslips').delete()
    .eq('id', payslipId).eq('tenant_id', tenantId)
  if (error) throw error
}

/** Recalcular e guardar recibo individual (p.ex. com bónus) */
export async function upsertPayslip(
  employeeId: string,
  month: number,
  year: number,
  bonuses = 0,
  allowances = 0,
  overtime = 0,
  otherDeductions = 0,
): Promise<Payslip> {
  const tenantId = await getTenantId()
  const userId = await getUserId()
  if (!tenantId) throw new Error('Não autenticado')

  const { data: emp, error: empErr } = await supabase
    .from('employees').select('*').eq('id', employeeId).single()
  if (empErr || !emp) throw new Error('Funcionário não encontrado')

  const calc = calculateFullPayroll(emp.gross_salary, allowances, bonuses, overtime, otherDeductions)
  const pm = toPayrollMonth(year, month)

  const row = {
    tenant_id: tenantId,
    employee_id: employeeId,
    employee_name: emp.full_name,
    employee_nif: emp.nif,
    payroll_month: pm,
    gross_salary: calc.gross_salary,
    allowances: calc.allowances,
    bonuses: calc.bonuses,
    overtime: calc.overtime,
    total_earnings: calc.total_earnings,
    inss_employee: calc.inss_employee,
    inss_employer: calc.inss_employer,
    irt: calc.irt,
    irt_bracket: calc.irt_bracket,
    other_deductions: calc.other_deductions,
    total_deductions: calc.total_deductions,
    net_salary: calc.net_salary,
    payment_status: 'PENDING' as const,
    created_by: userId || undefined,
    updated_at: new Date().toISOString(),
  }

  const { data, error } = await supabase
    .from('payslips')
    .upsert(row, { onConflict: 'tenant_id,employee_id,payroll_month' })
    .select().single()
  if (error) throw error
  return data
}

/** Estatísticas de um mês */
export async function getPayrollStats(month: number, year: number): Promise<PayrollStats> {
  const payslips = await getPayslipsByMonth(month, year)
  return {
    total_employees: payslips.length,
    total_gross: payslips.reduce((s, p) => s + Number(p.gross_salary), 0),
    total_irt: payslips.reduce((s, p) => s + Number(p.irt), 0),
    total_inss_employee: payslips.reduce((s, p) => s + Number(p.inss_employee), 0),
    total_inss_employer: payslips.reduce((s, p) => s + Number(p.inss_employer), 0),
    total_net: payslips.reduce((s, p) => s + Number(p.net_salary), 0),
    total_cost: payslips.reduce((s, p) => s + Number(p.gross_salary) + Number(p.inss_employer), 0),
    paid_count: payslips.filter(p => p.payment_status === 'PAID').length,
    pending_count: payslips.filter(p => p.payment_status === 'PENDING').length,
  }
}

/** Exportar CSV da folha de pagamento */
export function exportPayrollCSV(payslips: Payslip[], month: number, year: number): void {
  const monthName = new Date(year, month - 1).toLocaleDateString('pt-AO', { month: 'long', year: 'numeric' })
  const headers = [
    'Funcionário', 'Salário Bruto', 'Subsídios', 'Bónus', 'Horas Extra', 'Total Vencimentos',
    'INSS (3%)', 'IRT', 'Outras Deduções', 'Total Deduções', 'Salário Líquido',
    'Custo Entidade (INSS 8%)', 'Estado', 'Data Pagamento',
  ]
  const rows = payslips.map(p => [
    p.employee_name,
    Number(p.gross_salary).toFixed(2),
    Number(p.allowances).toFixed(2),
    Number(p.bonuses).toFixed(2),
    Number(p.overtime).toFixed(2),
    Number(p.total_earnings).toFixed(2),
    Number(p.inss_employee).toFixed(2),
    Number(p.irt).toFixed(2),
    Number(p.other_deductions).toFixed(2),
    Number(p.total_deductions).toFixed(2),
    Number(p.net_salary).toFixed(2),
    Number(p.inss_employer).toFixed(2),
    p.payment_status === 'PAID' ? 'Pago' : p.payment_status === 'CANCELLED' ? 'Cancelado' : 'Pendente',
    p.payment_date ? new Date(p.payment_date).toLocaleDateString('pt-AO') : '-',
  ])
  const csv = [headers.join(';'), ...rows.map(r => r.join(';'))].join('\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `folha_pagamento_${year}_${String(month).padStart(2, '0')}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
