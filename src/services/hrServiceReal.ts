import { supabase } from '@/integrations/supabase/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  const { data, error } = await supabase
    .from('users')
    .select('tenant_id')
    .eq('id', user.id)
    .single()
  if (error || !data?.tenant_id) throw new Error('Tenant não encontrado')
  return data.tenant_id
}

async function getUserId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  return user.id
}

// ─── Tipos ────────────────────────────────────────────────────────────────────
export type EmployeeStatus  = 'ACTIVE' | 'INACTIVE' | 'ON_LEAVE' | 'TERMINATED'
export type EmploymentType  = 'FULL_TIME' | 'PART_TIME' | 'INTERN' | 'CONSULTANT' | 'FREELANCER'
export type ContractType    = 'PERMANENT' | 'FIXED_TERM' | 'PROBATION' | 'OUTSOURCED'
export type AbsenceType     = 'VACATION' | 'SICK_LEAVE' | 'MATERNITY_LEAVE' | 'PATERNITY_LEAVE' | 'PERSONAL' | 'UNPAID' | 'OTHER'
export type AbsenceStatus   = 'PENDING' | 'APPROVED' | 'REJECTED' | 'CANCELLED'
export type EvalPeriod      = 'MONTHLY' | 'QUARTERLY' | 'SEMI_ANNUAL' | 'ANNUAL'
export type EvalStatus      = 'DRAFT' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
export type ContractStatus  = 'ACTIVE' | 'EXPIRED' | 'TERMINATED' | 'RENEWED'

export interface Employee {
  id:                     string
  tenant_id?:             string
  employee_number?:       string
  full_name:              string
  email?:                 string
  phone?:                 string
  address?:               string
  birth_date?:            string
  gender?:                string
  nationality?:           string
  position:               string
  department?:            string
  hire_date:              string
  termination_date?:      string
  employment_type?:       string
  contract_type?:         string
  gross_salary:           number
  net_salary?:            number
  status:                 EmployeeStatus
  marital_status?:        string
  dependents?:            number
  vacation_days_total?:   number
  vacation_days_used?:    number
  nif?:                   string
  bi_number?:             string
  inss_number?:           string
  bank_name?:             string
  bank_account?:          string
  emergency_contact_name?:  string
  emergency_contact_phone?: string
  performance_score?:     number
  notes?:                 string
  created_at?:            string
  updated_at?:            string
}

export interface EmployeeAbsence {
  id:           string
  tenant_id?:   string
  employee_id:  string
  absence_type: AbsenceType
  start_date:   string
  end_date:     string
  days_count:   number
  reason?:      string
  notes?:       string
  approved_by?: string
  approved_at?: string
  status:       AbsenceStatus
  created_at?:  string
  employee?:    Partial<Employee>
}

export interface EmployeePerformance {
  id:                  string
  tenant_id?:          string
  employee_id:         string
  evaluation_period:   EvalPeriod
  evaluation_date:     string
  evaluator_id?:       string
  overall_score:       number
  productivity_score?: number
  quality_score?:      number
  teamwork_score?:     number
  punctuality_score?:  number
  initiative_score?:   number
  strengths?:          string
  weaknesses?:         string
  goals?:              string
  comments?:           string
  status:              EvalStatus
  created_at?:         string
  employee?:           Partial<Employee>
}

export interface EmployeeContract {
  id:                   string
  tenant_id?:           string
  employee_id:          string
  contract_number?:     string
  contract_type:        string
  start_date:           string
  end_date?:            string
  position:             string
  department?:          string
  gross_salary:         number
  work_hours_per_week?: number
  vacation_days?:       number
  status:               ContractStatus
  notes?:               string
  created_at?:          string
  employee?:            Partial<Employee>
}

export interface HRStats {
  total_employees:    number
  active_employees:   number
  on_leave:           number
  inactive_employees: number
  total_salary_mass:  number
  avg_salary:         number
  pending_absences:   number
  vacations_this_month: number
  avg_performance:    number
  departments: Array<{ name: string; count: number; avg_salary: number }>
}

// ─── Labels ───────────────────────────────────────────────────────────────────
export const ABSENCE_TYPE_LABELS: Record<AbsenceType, string> = {
  VACATION:        'Férias',
  SICK_LEAVE:      'Baixa Médica',
  MATERNITY_LEAVE: 'Licença Maternidade',
  PATERNITY_LEAVE: 'Licença Paternidade',
  PERSONAL:        'Pessoal',
  UNPAID:          'Sem Vencimento',
  OTHER:           'Outro',
}

export const ABSENCE_STATUS_LABELS: Record<AbsenceStatus, string> = {
  PENDING:   'Pendente',
  APPROVED:  'Aprovada',
  REJECTED:  'Rejeitada',
  CANCELLED: 'Cancelada',
}

export const EMPLOYEE_STATUS_LABELS: Record<EmployeeStatus, string> = {
  ACTIVE:     'Activo',
  INACTIVE:   'Inactivo',
  ON_LEAVE:   'Em Licença',
  TERMINATED: 'Saiu',
}

export const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
  PERMANENT:  'Efectivo',
  FIXED_TERM: 'Termo Certo',
  PROBATION:  'Estágio',
  OUTSOURCED: 'Outsourcing',
}

export const EMPLOYMENT_TYPE_LABELS: Record<EmploymentType, string> = {
  FULL_TIME:  'Tempo Inteiro',
  PART_TIME:  'Tempo Parcial',
  INTERN:     'Estagiário',
  CONSULTANT: 'Consultor',
  FREELANCER: 'Freelancer',
}

export const EVAL_PERIOD_LABELS: Record<EvalPeriod, string> = {
  MONTHLY:     'Mensal',
  QUARTERLY:   'Trimestral',
  SEMI_ANNUAL: 'Semestral',
  ANNUAL:      'Anual',
}

export const DEPARTMENTS = [
  'Financeiro', 'Recursos Humanos', 'Tecnologia', 'Comercial',
  'Marketing', 'Operações', 'Jurídico', 'Administração', 'Logística',
]

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Funcionários
// ═══════════════════════════════════════════════════════════════════════════════
export const employeeService = {
  async getAll(): Promise<Employee[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('full_name')
    if (error) throw error
    return (data || []) as Employee[]
  },

  async getById(id: string): Promise<Employee> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('*')
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .single()
    if (error) throw error
    return data as Employee
  },

  async create(input: Omit<Employee, 'id' | 'tenant_id' | 'created_at' | 'updated_at'>): Promise<Employee> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .insert({ ...input, tenant_id: tenantId })
      .select()
      .single()
    if (error) throw error
    return data as Employee
  },

  async update(id: string, input: Partial<Omit<Employee, 'id' | 'tenant_id'>>): Promise<Employee> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .update({ ...input, updated_at: new Date().toISOString() })
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .select()
      .single()
    if (error) throw error
    return data as Employee
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('employees')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },

  exportCSV(employees: Employee[]): void {
    const headers = [
      'Nº', 'Nome Completo', 'Email', 'Telefone', 'Cargo', 'Departamento',
      'Admissão', 'Contrato', 'Salário Bruto', 'Estado',
    ]
    const rows = employees.map(e => [
      e.employee_number || '',
      e.full_name,
      e.email || '',
      e.phone || '',
      e.position,
      e.department || '',
      e.hire_date,
      CONTRACT_TYPE_LABELS[e.contract_type as ContractType] || e.contract_type || '',
      Number(e.gross_salary).toFixed(2),
      EMPLOYEE_STATUS_LABELS[e.status],
    ])
    const csv = [headers, ...rows].map(r => r.map(c => `"${c}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `funcionarios_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Ausências
// ═══════════════════════════════════════════════════════════════════════════════
export const absenceService = {
  async getAll(): Promise<EmployeeAbsence[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_absences')
      .select('*, employee:employees(id, full_name, position, department)')
      .eq('tenant_id', tenantId)
      .order('start_date', { ascending: false })
    if (error) throw error
    return (data || []) as EmployeeAbsence[]
  },

  async getByEmployee(employeeId: string): Promise<EmployeeAbsence[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_absences')
      .select('*')
      .eq('employee_id', employeeId)
      .eq('tenant_id', tenantId)
      .order('start_date', { ascending: false })
    if (error) throw error
    return (data || []) as EmployeeAbsence[]
  },

  async create(input: {
    employee_id: string
    absence_type: AbsenceType
    start_date: string
    end_date: string
    days_count: number
    reason?: string
    notes?: string
  }): Promise<EmployeeAbsence> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_absences')
      .insert({ ...input, tenant_id: tenantId, status: 'PENDING' })
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error
    return data as EmployeeAbsence
  },

  async approve(id: string): Promise<EmployeeAbsence> {
    const tenantId = await getTenantId()
    const userId  = await getUserId()
    const { data, error } = await supabase
      .from('employee_absences')
      .update({ status: 'APPROVED', approved_by: userId, approved_at: new Date().toISOString() })
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error
    return data as EmployeeAbsence
  },

  async reject(id: string, reason?: string): Promise<EmployeeAbsence> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_absences')
      .update({ status: 'REJECTED', notes: reason })
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error
    return data as EmployeeAbsence
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('employee_absences')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Avaliações de Desempenho
// ═══════════════════════════════════════════════════════════════════════════════
export const performanceService = {
  async getAll(): Promise<EmployeePerformance[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_performance')
      .select('*, employee:employees(id, full_name, position)')
      .eq('tenant_id', tenantId)
      .order('evaluation_date', { ascending: false })
    if (error) throw error
    return (data || []) as EmployeePerformance[]
  },

  async getByEmployee(employeeId: string): Promise<EmployeePerformance[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_performance')
      .select('*')
      .eq('employee_id', employeeId)
      .eq('tenant_id', tenantId)
      .order('evaluation_date', { ascending: false })
    if (error) throw error
    return (data || []) as EmployeePerformance[]
  },

  async create(input: {
    employee_id:        string
    evaluation_period:  EvalPeriod
    evaluation_date:    string
    overall_score:      number
    productivity_score?: number
    quality_score?:      number
    teamwork_score?:     number
    punctuality_score?:  number
    initiative_score?:   number
    strengths?:          string
    weaknesses?:         string
    goals?:              string
    comments?:           string
  }): Promise<EmployeePerformance> {
    const tenantId = await getTenantId()
    const evaluatorId = await getUserId()
    const { data, error } = await supabase
      .from('employee_performance')
      .insert({ ...input, tenant_id: tenantId, evaluator_id: evaluatorId, status: 'SUBMITTED' })
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error

    // Actualizar performance_score no funcionário
    await supabase
      .from('employees')
      .update({ performance_score: input.overall_score })
      .eq('id', input.employee_id)
      .eq('tenant_id', tenantId)

    return data as EmployeePerformance
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('employee_performance')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Contratos
// ═══════════════════════════════════════════════════════════════════════════════
export const contractService = {
  async getAll(): Promise<EmployeeContract[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_contracts')
      .select('*, employee:employees(id, full_name, position)')
      .eq('tenant_id', tenantId)
      .order('start_date', { ascending: false })
    if (error) throw error
    return (data || []) as EmployeeContract[]
  },

  async create(input: Omit<EmployeeContract, 'id' | 'tenant_id' | 'created_at'>): Promise<EmployeeContract> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_contracts')
      .insert({ ...input, tenant_id: tenantId })
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error
    return data as EmployeeContract
  },

  async update(id: string, input: Partial<EmployeeContract>): Promise<EmployeeContract> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_contracts')
      .update(input)
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .select('*, employee:employees(id, full_name)')
      .single()
    if (error) throw error
    return data as EmployeeContract
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('employee_contracts')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// Estatísticas de RH
// ═══════════════════════════════════════════════════════════════════════════════
export async function getHRStats(
  employees:   Employee[],
  absences:    EmployeeAbsence[],
  performance: EmployeePerformance[],
): Promise<HRStats> {
  const now = new Date()
  const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`

  const active     = employees.filter(e => e.status === 'ACTIVE')
  const on_leave   = employees.filter(e => e.status === 'ON_LEAVE')
  const inactive   = employees.filter(e => e.status === 'INACTIVE' || e.status === 'TERMINATED')
  const totalSalary = employees.reduce((s, e) => s + Number(e.gross_salary || 0), 0)
  const avgSalary  = employees.length > 0 ? totalSalary / employees.length : 0

  const pendingAbs = absences.filter(a => a.status === 'PENDING').length
  const vacThisMonth = absences.filter(a =>
    a.absence_type === 'VACATION' && a.status === 'APPROVED' && a.start_date.startsWith(thisMonth)
  ).length

  const avgPerf = performance.length > 0
    ? performance.reduce((s, p) => s + Number(p.overall_score || 0), 0) / performance.length
    : 0

  // Por departamento (activos apenas)
  const deptMap = new Map<string, { count: number; salary: number }>()
  active.forEach(e => {
    const dept = e.department || 'Sem Departamento'
    const prev = deptMap.get(dept) || { count: 0, salary: 0 }
    deptMap.set(dept, { count: prev.count + 1, salary: prev.salary + Number(e.gross_salary || 0) })
  })
  const departments = Array.from(deptMap.entries())
    .map(([name, { count, salary }]) => ({
      name, count, avg_salary: count > 0 ? salary / count : 0,
    }))
    .sort((a, b) => b.count - a.count)

  return {
    total_employees:     employees.length,
    active_employees:    active.length,
    on_leave:            on_leave.length,
    inactive_employees:  inactive.length,
    total_salary_mass:   totalSalary,
    avg_salary:          avgSalary,
    pending_absences:    pendingAbs,
    vacations_this_month: vacThisMonth,
    avg_performance:     avgPerf,
    departments,
  }
}
