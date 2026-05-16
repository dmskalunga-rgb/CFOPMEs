// ============================================================
// KWANZACONTROL – Advanced HR Service
// Analytics e IA para Gestão de Pessoas
// 100% dados reais do Supabase – sem dados simulados
// 2026-04-18
// ============================================================
import { supabase } from '@/integrations/supabase/client'

// ── Helpers ──────────────────────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  // Usar RPC SECURITY DEFINER para bypassar RLS de forma segura
  const { data, error } = await supabase.rpc('get_current_tenant_id')
  if (!error && data) return data as string

  // Fallback: tentar directamente
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  const { data: u } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  if (u?.tenant_id) return u.tenant_id
  throw new Error('Tenant não encontrado')
}

// ── Tipos ─────────────────────────────────────────────────────────────────────
export interface HRAnalyticsSnapshot {
  id: string
  tenant_id: string
  snapshot_month: string
  total_employees: number
  active_employees: number
  on_leave: number
  terminated: number
  total_salary_mass: number
  avg_salary: number
  avg_performance: number
  turnover_rate: number
  absenteeism_rate: number
  retention_rate: number
  pending_absences: number
  dept_breakdown: DeptBreakdown[]
  created_at: string
  updated_at: string
}

export interface DeptBreakdown {
  dept: string
  count: number
  avg_salary: number
  avg_performance: number
}

export interface HRAIInsight {
  id: string
  tenant_id: string
  employee_id: string | null
  insight_type: InsightType
  risk_level: 'low' | 'medium' | 'high'
  probability: number
  title: string
  description: string
  recommendation: string
  factors: InsightFactor[]
  estimated_impact: string
  is_active: boolean
  resolved_at?: string | null
  generated_at: string
  expires_at: string
  employee?: { full_name: string; position: string; department: string }
}

export type InsightType =
  | 'FLIGHT_RISK'
  | 'HIGH_POTENTIAL'
  | 'BURNOUT_RISK'
  | 'PERFORMANCE_DECLINE'
  | 'PROMOTION_READY'

export interface InsightFactor {
  factor: string
  value: string
}

export interface HRKPIs {
  totalEmployees: number
  activeEmployees: number
  onLeave: number
  terminated: number
  totalSalaryMass: number
  avgSalary: number
  avgPerformance: number
  retentionRate: number
  absenteeismRate: number
  turnoverRate: number
  pendingAbsences: number
  activeInsights: number
  highRiskInsights: number
}

export interface DeptStats {
  name: string
  count: number
  avg_salary: number
  avg_performance: number
  pct: number
}

export interface PerfDistribution {
  range: string
  count: number
  pct: number
}

export interface AbsenceStats {
  type: string
  label: string
  count: number
  days: number
  pct: number
}

export interface SalaryRange {
  range: string
  min: number
  max: number
  count: number
  pct: number
}

// ── Labels ────────────────────────────────────────────────────────────────────
export const INSIGHT_TYPE_LABELS: Record<InsightType, string> = {
  FLIGHT_RISK:         'Risco de Saída',
  HIGH_POTENTIAL:      'Alto Potencial',
  BURNOUT_RISK:        'Risco de Burnout',
  PERFORMANCE_DECLINE: 'Queda de Desempenho',
  PROMOTION_READY:     'Pronto para Promoção',
}

export const INSIGHT_TYPE_COLORS: Record<InsightType, string> = {
  FLIGHT_RISK:         'text-red-600',
  HIGH_POTENTIAL:      'text-green-600',
  BURNOUT_RISK:        'text-orange-600',
  PERFORMANCE_DECLINE: 'text-yellow-600',
  PROMOTION_READY:     'text-blue-600',
}

export const ABSENCE_TYPE_LABELS: Record<string, string> = {
  VACATION:        'Férias',
  SICK_LEAVE:      'Baixa Médica',
  MATERNITY_LEAVE: 'Licença Maternidade',
  PATERNITY_LEAVE: 'Licença Paternidade',
  PERSONAL:        'Pessoal',
  UNPAID:          'Sem Vencimento',
  OTHER:           'Outro',
}

// ── 1. Serviço de Analytics: KPIs calculados em tempo real ────────────────────
export const hrAnalyticsService = {

  async getKPIs(): Promise<HRKPIs> {
    const tenantId = await getTenantId()

    const [empsResult, absResult, perfResult, insightsResult] = await Promise.allSettled([
      supabase.from('employees').select('status, gross_salary, performance_score').eq('tenant_id', tenantId),
      supabase.from('employee_absences').select('status').eq('tenant_id', tenantId),
      supabase.from('employee_performance').select('overall_score').eq('tenant_id', tenantId),
      supabase.from('hr_ai_insights').select('risk_level').eq('tenant_id', tenantId).eq('is_active', true),
    ])

    const emps    = empsResult.status === 'fulfilled' ? (empsResult.value.data ?? []) : []
    const abs     = absResult.status  === 'fulfilled' ? (absResult.value.data ?? [])  : []
    const perfs   = perfResult.status === 'fulfilled' ? (perfResult.value.data ?? []) : []
    const insights = insightsResult.status === 'fulfilled' ? (insightsResult.value.data ?? []) : []

    const total      = emps.length
    const active     = emps.filter(e => e.status === 'ACTIVE').length
    const onLeave    = emps.filter(e => e.status === 'ON_LEAVE').length
    const terminated = emps.filter(e => ['INACTIVE','TERMINATED'].includes(e.status)).length
    const salaries   = emps.filter(e => e.status === 'ACTIVE').map(e => Number(e.gross_salary) || 0)
    const totalSalary = salaries.reduce((s, v) => s + v, 0)
    const avgSalary   = salaries.length > 0 ? totalSalary / salaries.length : 0
    const avgPerf     = perfs.length > 0
      ? perfs.reduce((s, p) => s + Number(p.overall_score || 0), 0) / perfs.length : 0
    const pendingAbs  = abs.filter(a => a.status === 'PENDING').length
    const retentionRate = total > 0 ? ((total - terminated) / total) * 100 : 100
    const absenteeismRate = total > 0 ? (onLeave / total) * 100 : 0
    const turnoverRate = total > 0 ? (terminated / total) * 100 : 0
    const highRisk = insights.filter(i => i.risk_level === 'high').length

    return {
      totalEmployees: total,
      activeEmployees: active,
      onLeave,
      terminated,
      totalSalaryMass: totalSalary,
      avgSalary,
      avgPerformance: avgPerf,
      retentionRate,
      absenteeismRate,
      turnoverRate,
      pendingAbsences: pendingAbs,
      activeInsights: insights.length,
      highRiskInsights: highRisk,
    }
  },

  async getDeptStats(): Promise<DeptStats[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('department, gross_salary, performance_score')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
    if (error || !data) return []

    const map = new Map<string, { salaries: number[]; scores: number[] }>()
    data.forEach(e => {
      const dept = e.department || 'Sem Departamento'
      const prev = map.get(dept) || { salaries: [], scores: [] }
      prev.salaries.push(Number(e.gross_salary) || 0)
      if (e.performance_score != null) prev.scores.push(Number(e.performance_score))
      map.set(dept, prev)
    })

    const total = data.length
    return Array.from(map.entries()).map(([name, { salaries, scores }]) => ({
      name,
      count: salaries.length,
      avg_salary: salaries.length > 0 ? salaries.reduce((a, b) => a + b, 0) / salaries.length : 0,
      avg_performance: scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : 0,
      pct: total > 0 ? (salaries.length / total) * 100 : 0,
    })).sort((a, b) => b.count - a.count)
  },

  async getPerfDistribution(): Promise<PerfDistribution[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('performance_score')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
      .not('performance_score', 'is', null)
    if (error || !data) return []

    const ranges = [
      { range: '0-1', min: 0, max: 1 },
      { range: '1-2', min: 1, max: 2 },
      { range: '2-3', min: 2, max: 3 },
      { range: '3-4', min: 3, max: 4 },
      { range: '4-5', min: 4, max: 5 },
    ]
    const total = data.length
    return ranges.map(r => {
      const count = data.filter(e => {
        const s = Number(e.performance_score)
        return s >= r.min && (r.max === 5 ? s <= 5 : s < r.max)
      }).length
      return { range: r.range, count, pct: total > 0 ? (count / total) * 100 : 0 }
    })
  },

  async getAbsenceStats(): Promise<AbsenceStats[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_absences')
      .select('absence_type, days_count')
      .eq('tenant_id', tenantId)
      .eq('status', 'APPROVED')
    if (error || !data) return []

    const map = new Map<string, { count: number; days: number }>()
    data.forEach(a => {
      const t = a.absence_type || 'OTHER'
      const prev = map.get(t) || { count: 0, days: 0 }
      map.set(t, { count: prev.count + 1, days: prev.days + Number(a.days_count || 0) })
    })
    const total = data.length
    return Array.from(map.entries()).map(([type, { count, days }]) => ({
      type, label: ABSENCE_TYPE_LABELS[type] || type,
      count, days, pct: total > 0 ? (count / total) * 100 : 0,
    })).sort((a, b) => b.count - a.count)
  },

  async getSalaryRanges(): Promise<SalaryRange[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('gross_salary')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
    if (error || !data) return []

    const brackets = [
      { range: '< 100k',       min: 0,       max: 100000  },
      { range: '100k–250k',    min: 100000,   max: 250000  },
      { range: '250k–500k',    min: 250000,   max: 500000  },
      { range: '500k–1M',      min: 500000,   max: 1000000 },
      { range: '> 1M',         min: 1000000,  max: Infinity },
    ]
    const total = data.length
    return brackets.map(b => {
      const count = data.filter(e => {
        const s = Number(e.gross_salary) || 0
        return s >= b.min && s < b.max
      }).length
      return { range: b.range, min: b.min, max: b.max, count, pct: total > 0 ? (count / total) * 100 : 0 }
    }).filter(b => b.count > 0)
  },

  async getMonthlySnapshots(nMonths = 6): Promise<HRAnalyticsSnapshot[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('hr_analytics_snapshots')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('snapshot_month', { ascending: true })
      .limit(nMonths)
    if (error || !data) return []
    return data as HRAnalyticsSnapshot[]
  },
}

// ── 2. Serviço de Insights de IA ─────────────────────────────────────────────
export const hrInsightsService = {

  async getAll(): Promise<HRAIInsight[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('hr_ai_insights')
      .select(`
        *,
        employee:employees(full_name, position, department)
      `)
      .eq('tenant_id', tenantId)
      .eq('is_active', true)
      .order('generated_at', { ascending: false })
    if (error || !data) return []
    return data.map(d => ({
      ...d,
      factors: Array.isArray(d.factors) ? d.factors : [],
    })) as HRAIInsight[]
  },

  async regenerate(): Promise<HRAIInsight[]> {
    // Regenera insights baseados nos dados actuais dos funcionários
    const tenantId = await getTenantId()

    // Desactivar insights anteriores
    await supabase
      .from('hr_ai_insights')
      .update({ is_active: false })
      .eq('tenant_id', tenantId)

    // Buscar dados actuais de employees + performance + absences
    const [empsRes, absRes] = await Promise.all([
      supabase.from('employees')
        .select('id, full_name, position, department, performance_score, gross_salary, status')
        .eq('tenant_id', tenantId).eq('status', 'ACTIVE'),
      supabase.from('employee_absences')
        .select('employee_id, absence_type, days_count, start_date')
        .eq('tenant_id', tenantId).eq('status', 'APPROVED')
        .gte('start_date', new Date(Date.now() - 90*24*60*60*1000).toISOString().split('T')[0]),
    ])

    const employees = empsRes.data || []
    const absences  = absRes.data  || []

    const absMap = new Map<string, number>()
    absences.forEach(a => absMap.set(a.employee_id, (absMap.get(a.employee_id) || 0) + 1))

    const toInsert: Omit<HRAIInsight, 'id' | 'created_at' | 'updated_at' | 'employee'>[] = []

    employees.forEach(e => {
      const score   = Number(e.performance_score) || 0
      const absCount = absMap.get(e.id) || 0
      const hasScore = e.performance_score != null

      // Risco de saída
      if (hasScore && score < 3.5) {
        toInsert.push({
          tenant_id: tenantId,
          employee_id: e.id,
          insight_type: 'FLIGHT_RISK',
          risk_level: score < 2.5 ? 'high' : 'medium',
          probability: score < 2.5 ? 72 : 45,
          title: `Risco de Saída: ${e.full_name}`,
          description: `Score ${score.toFixed(1)}/5. Indicadores de desengajamento detectados.`,
          recommendation: 'Conversa individual urgente. Avaliar expectativas e oferecer plano de retenção.',
          factors: [
            { factor: 'Performance Score', value: score.toFixed(1) },
            { factor: 'Departamento',      value: e.department || 'Geral' },
          ],
          estimated_impact: 'Custo de substituição: 2-3x salário anual',
          is_active: true,
          generated_at: new Date().toISOString(),
          expires_at:   new Date(Date.now() + 30*24*60*60*1000).toISOString(),
        })
      }

      // Alto potencial
      if (hasScore && score >= 4.0) {
        toInsert.push({
          tenant_id: tenantId,
          employee_id: e.id,
          insight_type: 'HIGH_POTENTIAL',
          risk_level: 'low',
          probability: Math.min(score * 18, 95),
          title: `Alto Potencial: ${e.full_name}`,
          description: `Score ${score.toFixed(1)}/5. Candidato a cargo de liderança.`,
          recommendation: 'Plano de carreira acelerado. Mentoria e formação em liderança.',
          factors: [
            { factor: 'Performance Score', value: score.toFixed(1) },
            { factor: 'Cargo',             value: e.position || 'N/A' },
          ],
          estimated_impact: 'Potencial +30-50% produtividade do departamento',
          is_active: true,
          generated_at: new Date().toISOString(),
          expires_at:   new Date(Date.now() + 30*24*60*60*1000).toISOString(),
        })
      }

      // Burnout
      if (absCount >= 2) {
        toInsert.push({
          tenant_id: tenantId,
          employee_id: e.id,
          insight_type: 'BURNOUT_RISK',
          risk_level: absCount >= 3 ? 'high' : 'medium',
          probability: absCount >= 3 ? 70 : 55,
          title: `Risco de Burnout: ${e.full_name}`,
          description: `${absCount} ausências nos últimos 90 dias. Possível sobrecarga.`,
          recommendation: 'Avaliar carga de trabalho. Redistribuir tarefas. Apoio psicológico.',
          factors: [
            { factor: 'Ausências recentes', value: absCount.toString() },
            { factor: 'Departamento',       value: e.department || 'Geral' },
          ],
          estimated_impact: 'Redução 15-25% produtividade se não tratado',
          is_active: true,
          generated_at: new Date().toISOString(),
          expires_at:   new Date(Date.now() + 30*24*60*60*1000).toISOString(),
        })
      }
    })

    if (toInsert.length > 0) {
      await supabase.from('hr_ai_insights').insert(toInsert.map(i => ({
        ...i,
        factors: i.factors as unknown as string,
      })))
    }

    return this.getAll()
  },

  async resolve(id: string): Promise<void> {
    const tenantId = await getTenantId()
    await supabase
      .from('hr_ai_insights')
      .update({ is_active: false, resolved_at: new Date().toISOString() })
      .eq('id', id)
      .eq('tenant_id', tenantId)
  },
}

// ── 3. Serviço de Performance Analytics ──────────────────────────────────────
export const performanceAnalyticsService = {

  async getTopPerformers(limit = 5) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('id, full_name, position, department, performance_score, gross_salary')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
      .not('performance_score', 'is', null)
      .order('performance_score', { ascending: false })
      .limit(limit)
    if (error || !data) return []
    return data
  },

  async getLowPerformers(limit = 5) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('id, full_name, position, department, performance_score, gross_salary')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE')
      .not('performance_score', 'is', null)
      .order('performance_score', { ascending: true })
      .limit(limit)
    if (error || !data) return []
    return data
  },

  async getRecentEvaluations(limit = 10) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employee_performance')
      .select(`
        id, overall_score, evaluation_period, evaluation_date,
        productivity_score, quality_score, teamwork_score, punctuality_score,
        employee:employees(full_name, position, department)
      `)
      .eq('tenant_id', tenantId)
      .order('evaluation_date', { ascending: false })
      .limit(limit)
    if (error || !data) return []
    return data
  },
}

// ── 4. Serviço de Payroll Analytics ──────────────────────────────────────────
export const payrollAnalyticsService = {

  async getMonthlyPayrollSummary(nMonths = 6) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('payslips')
      .select('payroll_month, gross_salary, net_salary, inss_employee, inss_employer, irt, total_earnings, total_deductions')
      .eq('tenant_id', tenantId)
      .order('payroll_month', { ascending: true })

    if (error || !data) return []

    // Agrupar por mês
    const map = new Map<string, {
      month: string; count: number
      totalGross: number; totalNet: number
      totalInss: number; totalIrt: number
    }>()

    data.forEach(p => {
      const m = p.payroll_month || ''
      const prev = map.get(m) || { month: m, count: 0, totalGross: 0, totalNet: 0, totalInss: 0, totalIrt: 0 }
      map.set(m, {
        month: m, count: prev.count + 1,
        totalGross: prev.totalGross + Number(p.gross_salary || 0),
        totalNet:   prev.totalNet   + Number(p.net_salary  || 0),
        totalInss:  prev.totalInss  + Number(p.inss_employee || 0) + Number(p.inss_employer || 0),
        totalIrt:   prev.totalIrt   + Number(p.irt || 0),
      })
    })

    return Array.from(map.values())
      .sort((a, b) => a.month.localeCompare(b.month))
      .slice(-nMonths)
      .map(m => ({
        ...m,
        label: new Date(m.month + '-01').toLocaleDateString('pt-PT', { month: 'short', year: '2-digit' }),
      }))
  },
}

// ── 5. Serviço de Rotatividade (Turnover) ─────────────────────────────────────
export const turnoverService = {

  async getTurnoverByDept() {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('department, status')
      .eq('tenant_id', tenantId)
    if (error || !data) return []

    const deptMap = new Map<string, { total: number; left: number }>()
    data.forEach(e => {
      const dept = e.department || 'Sem Departamento'
      const prev = deptMap.get(dept) || { total: 0, left: 0 }
      deptMap.set(dept, {
        total: prev.total + 1,
        left:  prev.left + (['INACTIVE','TERMINATED'].includes(e.status) ? 1 : 0),
      })
    })
    return Array.from(deptMap.entries()).map(([dept, { total, left }]) => ({
      dept, total, left,
      rate: total > 0 ? (left / total) * 100 : 0,
    })).filter(d => d.total > 0).sort((a, b) => b.rate - a.rate)
  },

  async getRecentTerminations(limit = 5) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('employees')
      .select('id, full_name, position, department, termination_date, gross_salary')
      .eq('tenant_id', tenantId)
      .in('status', ['INACTIVE','TERMINATED'])
      .not('termination_date', 'is', null)
      .order('termination_date', { ascending: false })
      .limit(limit)
    if (error || !data) return []
    return data
  },
}

// Exportação legada (mantida para compatibilidade)
export { payrollAnalyticsService as payrollBatchService }
export default {
  analytics:   hrAnalyticsService,
  insights:    hrInsightsService,
  performance: performanceAnalyticsService,
  payroll:     payrollAnalyticsService,
  turnover:    turnoverService,
}
