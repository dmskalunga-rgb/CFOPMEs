import { supabase } from '@/integrations/supabase/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  const { data, error } = await supabase
    .from('users').select('tenant_id').eq('id', user.id).single()
  if (error || !data?.tenant_id) throw new Error('Tenant não encontrado')
  return data.tenant_id as string
}

async function getUserId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  return user.id
}

// ─── Tipos ────────────────────────────────────────────────────────────────────
export type GoalType     = 'REVENUE' | 'PROFIT' | 'EXPENSE_REDUCTION' | 'CASH_FLOW' | 'SAVINGS' | 'INVESTMENT' | 'DEBT_REDUCTION' | 'CUSTOM'
export type GoalStatus   = 'ACTIVE' | 'COMPLETED' | 'CANCELLED' | 'OVERDUE'
export type GoalPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export type ProjType     = 'REVENUE' | 'EXPENSE' | 'CASH_FLOW' | 'PROFIT' | 'BALANCE'
export type ProjPeriod   = 'MONTHLY' | 'QUARTERLY' | 'YEARLY'
export type ProjMethod   = 'LINEAR' | 'EXPONENTIAL' | 'SEASONAL' | 'AI_BASED' | 'MANUAL'
export type ScenarioType = 'OPTIMISTIC' | 'REALISTIC' | 'PESSIMISTIC' | 'CUSTOM'
export type BudgetStatus = 'DRAFT' | 'APPROVED' | 'ACTIVE' | 'CLOSED'
export type BudgetPeriod = 'MONTHLY' | 'QUARTERLY' | 'YEARLY'

export interface FinancialGoal {
  id:                   string
  tenant_id:            string
  goal_name:            string
  goal_type:            GoalType
  category?:            string
  target_amount:        number
  current_amount:       number
  start_date:           string
  end_date:             string
  status:               GoalStatus
  priority:             GoalPriority
  description?:         string
  progress_percentage:  number
  responsible_user_id?: string
  completed_at?:        string
  created_at?:          string
  updated_at?:          string
}

export interface DataPoint {
  month: string
  value: number
}

export interface FinancialProjection {
  id:                string
  tenant_id:         string
  projection_name:   string
  projection_type:   ProjType
  period_type:       ProjPeriod
  start_date:        string
  end_date:          string
  base_amount?:      number
  growth_rate?:      number
  projection_method?: ProjMethod
  data_points:       DataPoint[]
  confidence_level?: number
  assumptions?:      string
  status:            'DRAFT' | 'ACTIVE' | 'ARCHIVED'
  created_at?:       string
}

export interface ScenarioMetrics {
  revenue:  number
  expenses: number
  profit:   number
  margin:   string
  growth:   string
}

export interface FinancialScenario {
  id:                       string
  tenant_id:                string
  scenario_name:            string
  scenario_type:            ScenarioType
  description?:             string
  assumptions?:             Record<string, string>
  key_metrics?:             ScenarioMetrics
  probability?:             number
  impact_analysis?:         string
  revenue_projection_id?:   string
  expense_projection_id?:   string
  cash_flow_projection_id?: string
  status:                   'ACTIVE' | 'ARCHIVED'
  created_at?:              string
}

export interface BudgetCategory {
  name:        string
  allocated:   number
  spent:       number
  percentage:  number
}

export interface BudgetPlan {
  id:               string
  tenant_id:        string
  budget_name:      string
  fiscal_year:      number
  period_type:      BudgetPeriod
  total_budget:     number
  allocated_budget: number
  spent_budget:     number
  categories:       BudgetCategory[]
  status:           BudgetStatus
  approved_by?:     string
  approved_at?:     string
  created_at?:      string
}

// ─── Labels ───────────────────────────────────────────────────────────────────
export const GOAL_TYPE_LABELS: Record<GoalType, string> = {
  REVENUE:           'Receita',
  PROFIT:            'Lucro',
  EXPENSE_REDUCTION: 'Redução de Custos',
  CASH_FLOW:         'Fluxo de Caixa',
  SAVINGS:           'Poupança',
  INVESTMENT:        'Investimento',
  DEBT_REDUCTION:    'Redução de Dívida',
  CUSTOM:            'Personalizado',
}

export const GOAL_STATUS_LABELS: Record<GoalStatus, string> = {
  ACTIVE:    'Activa',
  COMPLETED: 'Concluída',
  CANCELLED: 'Cancelada',
  OVERDUE:   'Em Atraso',
}

export const GOAL_PRIORITY_LABELS: Record<GoalPriority, string> = {
  LOW:      'Baixa',
  MEDIUM:   'Média',
  HIGH:     'Alta',
  CRITICAL: 'Crítica',
}

export const SCENARIO_LABELS: Record<ScenarioType, string> = {
  OPTIMISTIC:  'Optimista',
  REALISTIC:   'Realista',
  PESSIMISTIC: 'Pessimista',
  CUSTOM:      'Personalizado',
}

export const PROJ_TYPE_LABELS: Record<ProjType, string> = {
  REVENUE:    'Receita',
  EXPENSE:    'Despesa',
  CASH_FLOW:  'Fluxo de Caixa',
  PROFIT:     'Lucro',
  BALANCE:    'Balanço',
}

export const PROJ_PERIOD_LABELS: Record<ProjPeriod, string> = {
  MONTHLY:   'Mensal',
  QUARTERLY: 'Trimestral',
  YEARLY:    'Anual',
}

export const BUDGET_STATUS_LABELS: Record<BudgetStatus, string> = {
  DRAFT:    'Rascunho',
  APPROVED: 'Aprovado',
  ACTIVE:   'Activo',
  CLOSED:   'Encerrado',
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Metas Financeiras
// ═══════════════════════════════════════════════════════════════════════════════
export const goalsService = {
  async getAll(): Promise<FinancialGoal[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('financial_goals')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
    if (error) throw error
    return (data || []) as FinancialGoal[]
  },

  async create(input: Omit<FinancialGoal, 'id' | 'tenant_id' | 'created_at' | 'updated_at'>): Promise<FinancialGoal> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const progress = input.target_amount > 0
      ? Math.min(100, (input.current_amount / input.target_amount) * 100)
      : 0
    const { data, error } = await supabase
      .from('financial_goals')
      .insert({ ...input, tenant_id: tenantId, created_by: userId, progress_percentage: progress })
      .select().single()
    if (error) throw error
    return data as FinancialGoal
  },

  async update(id: string, input: Partial<FinancialGoal>): Promise<FinancialGoal> {
    const tenantId = await getTenantId()
    if (input.current_amount !== undefined && input.target_amount !== undefined) {
      input.progress_percentage = Math.min(100, (input.current_amount / input.target_amount) * 100)
    }
    const { data, error } = await supabase
      .from('financial_goals')
      .update({ ...input, updated_at: new Date().toISOString() })
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return data as FinancialGoal
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('financial_goals').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },

  async updateProgress(id: string, current: number): Promise<FinancialGoal> {
    const tenantId = await getTenantId()
    const { data: goal } = await supabase
      .from('financial_goals').select('target_amount').eq('id', id).single()
    const progress = goal ? Math.min(100, (current / Number(goal.target_amount)) * 100) : 0
    const status: GoalStatus = progress >= 100 ? 'COMPLETED' : 'ACTIVE'
    return this.update(id, {
      current_amount: current,
      progress_percentage: progress,
      status,
      completed_at: status === 'COMPLETED' ? new Date().toISOString() : undefined,
    })
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Projeções Financeiras
// ═══════════════════════════════════════════════════════════════════════════════
export const projectionsService = {
  async getAll(): Promise<FinancialProjection[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('financial_projections')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      data_points: typeof r.data_points === 'string' ? JSON.parse(r.data_points) : (r.data_points || []),
    })) as FinancialProjection[]
  },

  async create(input: Omit<FinancialProjection, 'id' | 'tenant_id' | 'created_at'>): Promise<FinancialProjection> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const { data, error } = await supabase
      .from('financial_projections')
      .insert({ ...input, tenant_id: tenantId, created_by: userId })
      .select().single()
    if (error) throw error
    return { ...data, data_points: data.data_points || [] } as FinancialProjection
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('financial_projections').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },

  /** Gerar pontos de projeção (cliente-side, sem Edge Function) */
  generatePoints(baseAmount: number, growthRate: number, method: ProjMethod, periods: number): DataPoint[] {
    const months = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    const points: DataPoint[] = []
    for (let i = 0; i < periods; i++) {
      let value: number
      if (method === 'LINEAR') {
        value = baseAmount * (1 + (growthRate / 100) * (i / periods))
      } else if (method === 'EXPONENTIAL') {
        value = baseAmount * Math.pow(1 + growthRate / 100, i / 12)
      } else {
        // MANUAL / SEASONAL — crescimento linear com variação sazonal
        const seasonal = i % 12 === 11 ? 1.15 : i % 12 === 5 ? 0.9 : 1.0
        value = baseAmount * (1 + (growthRate / 100) * (i / periods)) * seasonal
      }
      points.push({ month: months[i % 12], value: Math.round(value) })
    }
    return points
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Cenários Financeiros
// ═══════════════════════════════════════════════════════════════════════════════
export const scenariosService = {
  async getAll(): Promise<FinancialScenario[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('financial_scenarios')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      assumptions: typeof r.assumptions === 'string' ? JSON.parse(r.assumptions) : (r.assumptions || {}),
      key_metrics:  typeof r.key_metrics === 'string'  ? JSON.parse(r.key_metrics)  : (r.key_metrics || {}),
    })) as FinancialScenario[]
  },

  async create(input: Omit<FinancialScenario, 'id' | 'tenant_id' | 'created_at'>): Promise<FinancialScenario> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const { data, error } = await supabase
      .from('financial_scenarios')
      .insert({ ...input, tenant_id: tenantId, created_by: userId })
      .select().single()
    if (error) throw error
    return data as FinancialScenario
  },

  async update(id: string, input: Partial<FinancialScenario>): Promise<FinancialScenario> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('financial_scenarios')
      .update({ ...input, updated_at: new Date().toISOString() })
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return data as FinancialScenario
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('financial_scenarios').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Orçamentos
// ═══════════════════════════════════════════════════════════════════════════════
export const budgetsService = {
  async getAll(): Promise<BudgetPlan[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('budget_plans')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('fiscal_year', { ascending: false })
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      categories: typeof r.categories === 'string' ? JSON.parse(r.categories) : (r.categories || []),
    })) as BudgetPlan[]
  },

  async create(input: Omit<BudgetPlan, 'id' | 'tenant_id' | 'created_at'>): Promise<BudgetPlan> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const { data, error } = await supabase
      .from('budget_plans')
      .insert({ ...input, tenant_id: tenantId, created_by: userId })
      .select().single()
    if (error) throw error
    return { ...data, categories: data.categories || [] } as BudgetPlan
  },

  async update(id: string, input: Partial<BudgetPlan>): Promise<BudgetPlan> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('budget_plans')
      .update({ ...input, updated_at: new Date().toISOString() })
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return { ...data, categories: data.categories || [] } as BudgetPlan
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('budget_plans').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ─── Também exportar como objecto unificado (retrocompatibilidade) ────────────
export const financialPlanningService = {
  goals:       goalsService,
  projections: projectionsService,
  scenarios:   scenariosService,
  budgets:     budgetsService,
}
