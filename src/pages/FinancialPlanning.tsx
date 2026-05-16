import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  TrendingUp, Target, BarChart3, DollarSign, Plus, Edit, Trash2,
  RefreshCw, Download, ChevronRight, Loader2, AlertTriangle,
  CheckCircle2, Clock, TrendingDown, Activity, Layers,
  ArrowUpRight, ArrowDownRight, Flag, PieChart, Briefcase,
} from 'lucide-react'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart as RePieChart, Pie, Cell,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { Progress } from '@/components/ui/progress'
import { toast } from 'sonner'
import {
  goalsService, scenariosService, projectionsService, budgetsService,
  GOAL_TYPE_LABELS, GOAL_STATUS_LABELS, GOAL_PRIORITY_LABELS,
  SCENARIO_LABELS, PROJ_TYPE_LABELS, PROJ_PERIOD_LABELS, BUDGET_STATUS_LABELS,
  type FinancialGoal, type FinancialScenario, type FinancialProjection,
  type BudgetPlan, type BudgetCategory, type ScenarioMetrics,
  type GoalType, type GoalStatus, type GoalPriority, type ScenarioType,
  type ProjType, type ProjPeriod, type ProjMethod, type BudgetStatus, type BudgetPeriod,
} from '@/services/financialPlanningService'

// ─── Formatadores ─────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0, maximumFractionDigits: 0 }).format(v)

const fmtM = (v: number) => {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`
  if (Math.abs(v) >= 1_000)    return `${(v / 1_000).toFixed(0)}K`
  return String(v)
}

const fmtDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'

// ─── Cores dos cenários ───────────────────────────────────────────────────────
const SCENARIO_COLORS: Record<ScenarioType, { bg: string; text: string; border: string; chart: string }> = {
  OPTIMISTIC:  { bg: 'bg-green-50',  text: 'text-green-700',  border: 'border-green-200', chart: '#22c55e' },
  REALISTIC:   { bg: 'bg-blue-50',   text: 'text-blue-700',   border: 'border-blue-200',  chart: '#3b82f6' },
  PESSIMISTIC: { bg: 'bg-red-50',    text: 'text-red-700',    border: 'border-red-200',   chart: '#ef4444' },
  CUSTOM:      { bg: 'bg-purple-50', text: 'text-purple-700', border: 'border-purple-200',chart: '#a855f7' },
}

const PRIORITY_STYLE: Record<GoalPriority, { bg: string; text: string; border: string }> = {
  CRITICAL: { bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-200' },
  HIGH:     { bg: 'bg-orange-100', text: 'text-orange-700', border: 'border-orange-200' },
  MEDIUM:   { bg: 'bg-yellow-100', text: 'text-yellow-700', border: 'border-yellow-200' },
  LOW:      { bg: 'bg-gray-100',   text: 'text-gray-600',   border: 'border-gray-200' },
}

const STATUS_STYLE: Record<GoalStatus, { bg: string; text: string }> = {
  ACTIVE:    { bg: 'bg-blue-100',   text: 'text-blue-700' },
  COMPLETED: { bg: 'bg-green-100',  text: 'text-green-700' },
  CANCELLED: { bg: 'bg-gray-100',   text: 'text-gray-600' },
  OVERDUE:   { bg: 'bg-red-100',    text: 'text-red-700' },
}

const PIE_COLORS = ['#3b82f6','#22c55e','#f59e0b','#ef4444','#a855f7','#06b6d4','#ec4899','#84cc16']

// ─── Formulários empty ────────────────────────────────────────────────────────
const EMPTY_GOAL = {
  goal_name: '', goal_type: 'REVENUE' as GoalType, category: '',
  target_amount: 0, current_amount: 0,
  start_date: new Date().toISOString().split('T')[0],
  end_date: new Date(new Date().getFullYear(), 11, 31).toISOString().split('T')[0],
  status: 'ACTIVE' as GoalStatus, priority: 'MEDIUM' as GoalPriority,
  description: '', progress_percentage: 0,
}

const EMPTY_BUDGET = {
  budget_name: '', fiscal_year: new Date().getFullYear(),
  period_type: 'YEARLY' as BudgetPeriod, total_budget: 0,
  allocated_budget: 0, spent_budget: 0,
  categories: [] as BudgetCategory[], status: 'DRAFT' as BudgetStatus,
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function FinancialPlanning() {
  const [goals,        setGoals]        = useState<FinancialGoal[]>([])
  const [scenarios,    setScenarios]    = useState<FinancialScenario[]>([])
  const [projections,  setProjections]  = useState<FinancialProjection[]>([])
  const [budgets,      setBudgets]      = useState<BudgetPlan[]>([])
  const [loading,      setLoading]      = useState(true)
  const [submitting,   setSubmitting]   = useState(false)
  const [activeTab,    setActiveTab]    = useState('overview')
  const [selectedScenario, setSelectedScenario] = useState<FinancialScenario | null>(null)

  // Diálogos
  const [goalDialog,     setGoalDialog]     = useState(false)
  const [budgetDialog,   setBudgetDialog]   = useState(false)
  const [progressDialog, setProgressDialog] = useState(false)
  const [editingGoal,    setEditingGoal]    = useState<FinancialGoal | null>(null)
  const [progressGoal,   setProgressGoal]   = useState<FinancialGoal | null>(null)
  const [newProgress,    setNewProgress]    = useState<number>(0)

  // Forms
  const [goalForm,   setGoalForm]   = useState({ ...EMPTY_GOAL })
  const [budgetForm, setBudgetForm] = useState({ ...EMPTY_BUDGET })

  // ─── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [g, s, p, b] = await Promise.all([
        goalsService.getAll(),
        scenariosService.getAll(),
        projectionsService.getAll(),
        budgetsService.getAll(),
      ])
      setGoals(g)
      setScenarios(s)
      setProjections(p)
      setBudgets(b)
      if (s.length > 0 && !selectedScenario) {
        setSelectedScenario(s.find(x => x.scenario_type === 'REALISTIC') || s[0])
      }
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados de planejamento. Verifique a sua ligação.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Estatísticas ────────────────────────────────────────────────────────
  const stats = useMemo(() => {
    const activeGoals     = goals.filter(g => g.status === 'ACTIVE')
    const completedGoals  = goals.filter(g => g.status === 'COMPLETED')
    const overdueGoals    = goals.filter(g => g.status === 'OVERDUE')
    const criticalGoals   = goals.filter(g => g.priority === 'CRITICAL' && g.status === 'ACTIVE')
    const avgProgress     = activeGoals.length > 0
      ? activeGoals.reduce((s, g) => s + Number(g.progress_percentage), 0) / activeGoals.length
      : 0
    const totalTarget     = goals.reduce((s, g) => s + Number(g.target_amount), 0)
    const totalCurrent    = goals.reduce((s, g) => s + Number(g.current_amount), 0)

    const activeBudget    = budgets.find(b => b.status === 'ACTIVE' && b.period_type === 'YEARLY')
    const budgetExec      = activeBudget
      ? (Number(activeBudget.spent_budget) / Number(activeBudget.total_budget)) * 100 : 0

    return {
      total: goals.length, active: activeGoals.length,
      completed: completedGoals.length, overdue: overdueGoals.length,
      critical: criticalGoals.length, avgProgress,
      totalTarget, totalCurrent, budgetExec,
      activeBudget,
    }
  }, [goals, budgets])

  // ─── Dados do gráfico de cenários ─────────────────────────────────────────
  const scenarioChartData = useMemo(() => {
    if (projections.length === 0) return []
    const revProjs = projections.filter(p => p.projection_type === 'REVENUE')
    if (revProjs.length === 0) return []

    const months = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    return months.map((month, idx) => {
      const row: Record<string, number | string> = { month }
      revProjs.forEach(proj => {
        const pts = proj.data_points || []
        const pt  = pts[idx]
        if (pt) {
          const key = proj.projection_name.includes('Optim') ? 'optimista'
            : proj.projection_name.includes('Realis') ? 'realista' : 'pessimista'
          row[key] = Number(pt.value)
        }
      })
      return row
    })
  }, [projections])

  // ─── Dados do gráfico de metas (pie) ──────────────────────────────────────
  const goalTypeChartData = useMemo(() => {
    const map = new Map<string, number>()
    goals.forEach(g => {
      const label = GOAL_TYPE_LABELS[g.goal_type] || g.goal_type
      map.set(label, (map.get(label) || 0) + 1)
    })
    return Array.from(map.entries()).map(([name, value]) => ({ name, value }))
  }, [goals])

  // ─── Cash Flow do cenário seleccionado ───────────────────────────────────
  const cashFlowData = useMemo(() => {
    if (!selectedScenario) return []
    const cfProjId = selectedScenario.cash_flow_projection_id
    const proj = projections.find(p => p.id === cfProjId)
    if (!proj) {
      // Fallback: calcular receita - despesa das projeções disponíveis
      const revProj = projections.find(p =>
        p.projection_type === 'REVENUE' &&
        p.projection_name.toLowerCase().includes(
          selectedScenario.scenario_type === 'OPTIMISTIC' ? 'optim'
          : selectedScenario.scenario_type === 'PESSIMISTIC' ? 'pessim' : 'realis'
        )
      )
      const expProj = projections.find(p =>
        p.projection_type === 'EXPENSE' &&
        p.projection_name.toLowerCase().includes(
          selectedScenario.scenario_type === 'OPTIMISTIC' ? 'optim'
          : selectedScenario.scenario_type === 'PESSIMISTIC' ? 'pessim' : 'realis'
        )
      )
      if (!revProj) return []
      return (revProj.data_points || []).map((pt, i) => ({
        month: pt.month,
        receita: Number(pt.value),
        despesa: expProj ? Number((expProj.data_points || [])[i]?.value || 0) : 0,
        lucro: expProj
          ? Number(pt.value) - Number((expProj.data_points || [])[i]?.value || 0)
          : Number(pt.value),
      }))
    }
    return (proj.data_points || []).map(pt => ({ month: pt.month, cashflow: Number(pt.value) }))
  }, [selectedScenario, projections])

  // ─── Metas: guardar ───────────────────────────────────────────────────────
  const handleSaveGoal = async () => {
    if (!goalForm.goal_name.trim()) { toast.error('Nome da meta é obrigatório'); return }
    if (Number(goalForm.target_amount) <= 0) { toast.error('Valor alvo deve ser maior que zero'); return }
    setSubmitting(true)
    try {
      const progress = Number(goalForm.target_amount) > 0
        ? Math.min(100, (Number(goalForm.current_amount) / Number(goalForm.target_amount)) * 100)
        : 0
      if (editingGoal) {
        const updated = await goalsService.update(editingGoal.id, { ...goalForm, progress_percentage: progress })
        setGoals(prev => prev.map(g => g.id === updated.id ? updated : g))
        toast.success('Meta actualizada!')
      } else {
        const created = await goalsService.create({ ...goalForm, progress_percentage: progress })
        setGoals(prev => [created, ...prev])
        toast.success('Meta criada!')
      }
      setGoalDialog(false); setEditingGoal(null); setGoalForm({ ...EMPTY_GOAL })
    } catch (err) {
      console.error(err); toast.error('Erro ao guardar meta')
    } finally { setSubmitting(false) }
  }

  const openEditGoal = (goal: FinancialGoal) => {
    setEditingGoal(goal)
    setGoalForm({
      goal_name: goal.goal_name, goal_type: goal.goal_type, category: goal.category || '',
      target_amount: Number(goal.target_amount), current_amount: Number(goal.current_amount),
      start_date: goal.start_date, end_date: goal.end_date,
      status: goal.status, priority: goal.priority,
      description: goal.description || '', progress_percentage: Number(goal.progress_percentage),
    })
    setGoalDialog(true)
  }

  const handleDeleteGoal = async (id: string, name: string) => {
    if (!confirm(`Eliminar meta "${name}"?`)) return
    try {
      await goalsService.delete(id)
      setGoals(prev => prev.filter(g => g.id !== id))
      toast.success('Meta eliminada')
    } catch { toast.error('Erro ao eliminar') }
  }

  // ─── Progresso inline ─────────────────────────────────────────────────────
  const openProgress = (goal: FinancialGoal) => {
    setProgressGoal(goal)
    setNewProgress(Number(goal.current_amount))
    setProgressDialog(true)
  }

  const handleSaveProgress = async () => {
    if (!progressGoal) return
    setSubmitting(true)
    try {
      const updated = await goalsService.updateProgress(progressGoal.id, newProgress)
      setGoals(prev => prev.map(g => g.id === updated.id ? updated : g))
      toast.success('Progresso actualizado!')
      setProgressDialog(false)
    } catch (err) {
      console.error(err); toast.error('Erro ao actualizar progresso')
    } finally { setSubmitting(false) }
  }

  // ─── Orçamento: guardar ────────────────────────────────────────────────────
  const handleSaveBudget = async () => {
    if (!budgetForm.budget_name.trim()) { toast.error('Nome do orçamento é obrigatório'); return }
    if (Number(budgetForm.total_budget) <= 0) { toast.error('Valor total deve ser maior que zero'); return }
    setSubmitting(true)
    try {
      const created = await budgetsService.create({
        ...budgetForm,
        total_budget: Number(budgetForm.total_budget),
        allocated_budget: Number(budgetForm.allocated_budget),
        spent_budget: Number(budgetForm.spent_budget),
      })
      setBudgets(prev => [created, ...prev])
      toast.success('Orçamento criado!')
      setBudgetDialog(false); setBudgetForm({ ...EMPTY_BUDGET })
    } catch (err) {
      console.error(err); toast.error('Erro ao criar orçamento')
    } finally { setSubmitting(false) }
  }

  const handleDeleteBudget = async (id: string, name: string) => {
    if (!confirm(`Eliminar orçamento "${name}"?`)) return
    try {
      await budgetsService.delete(id)
      setBudgets(prev => prev.filter(b => b.id !== id))
      toast.success('Orçamento eliminado')
    } catch { toast.error('Erro ao eliminar') }
  }

  // ─── Exportar CSV de metas ─────────────────────────────────────────────────
  const exportGoalsCSV = () => {
    const headers = ['Meta','Tipo','Prioridade','Valor Alvo','Valor Actual','Progresso %','Estado','Início','Fim']
    const rows = goals.map(g => [
      g.goal_name,
      GOAL_TYPE_LABELS[g.goal_type] || g.goal_type,
      GOAL_PRIORITY_LABELS[g.priority] || g.priority,
      Number(g.target_amount).toFixed(2),
      Number(g.current_amount).toFixed(2),
      Number(g.progress_percentage).toFixed(1),
      GOAL_STATUS_LABELS[g.status] || g.status,
      g.start_date, g.end_date,
    ])
    const csv = [headers, ...rows].map(r => r.map(c => `"${c}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob)
    a.download = `metas_financeiras_${new Date().toISOString().split('T')[0]}.csv`; a.click()
    URL.revokeObjectURL(a.href)
  }

  // ─── Skeleton ──────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="space-y-6 p-6">
      <Skeleton className="h-10 w-72" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => <Card key={i}><CardContent className="p-6"><Skeleton className="h-20 w-full" /></CardContent></Card>)}
      </div>
      <Card><CardContent className="p-6"><Skeleton className="h-80 w-full" /></CardContent></Card>
    </div>
  )

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Planejamento Financeiro</h1>
          <p className="text-muted-foreground">Cenários, metas e projeções para {new Date().getFullYear()}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />Atualizar
          </Button>
          <Button variant="outline" size="sm" onClick={exportGoalsCSV} disabled={goals.length === 0}>
            <Download className="h-4 w-4 mr-2" />Exportar Metas
          </Button>
          <Button size="sm" onClick={() => { setEditingGoal(null); setGoalForm({ ...EMPTY_GOAL }); setGoalDialog(true) }}>
            <Plus className="h-4 w-4 mr-2" />Nova Meta
          </Button>
        </div>
      </div>

      {/* ── KPIs ── */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          {
            title: 'Metas Activas', value: stats.active, sub: `${stats.completed} concluídas · ${stats.overdue} em atraso`,
            icon: <Target className="h-5 w-5 text-blue-600" />, bg: 'bg-blue-50', color: 'text-blue-700',
          },
          {
            title: 'Progresso Médio', value: `${stats.avgProgress.toFixed(0)}%`, sub: `${stats.critical} meta(s) crítica(s)`,
            icon: <Activity className="h-5 w-5 text-green-600" />, bg: 'bg-green-50', color: 'text-green-700',
          },
          {
            title: 'Total em Metas', value: fmtM(stats.totalTarget) + ' AOA', sub: `Realizado: ${fmtM(stats.totalCurrent)} AOA`,
            icon: <DollarSign className="h-5 w-5 text-purple-600" />, bg: 'bg-purple-50', color: 'text-purple-700',
          },
          {
            title: 'Exec. Orçamento', value: `${stats.budgetExec.toFixed(0)}%`, sub: stats.activeBudget ? stats.activeBudget.budget_name : 'Sem orçamento activo',
            icon: <Briefcase className="h-5 w-5 text-orange-600" />, bg: 'bg-orange-50', color: 'text-orange-700',
          },
        ].map((m, i) => (
          <motion.div key={i} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}>
            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-sm font-medium text-muted-foreground">{m.title}</p>
                  <div className={`p-2 rounded-lg ${m.bg}`}>{m.icon}</div>
                </div>
                <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
                <p className="text-xs text-muted-foreground mt-1">{m.sub}</p>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </div>

      {/* ── Tabs ── */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
          <TabsTrigger value="overview"><BarChart3 className="h-4 w-4 mr-1.5" />Visão Geral</TabsTrigger>
          <TabsTrigger value="goals"><Target className="h-4 w-4 mr-1.5" />Metas</TabsTrigger>
          <TabsTrigger value="scenarios"><Layers className="h-4 w-4 mr-1.5" />Cenários</TabsTrigger>
          <TabsTrigger value="projections"><TrendingUp className="h-4 w-4 mr-1.5" />Projeções</TabsTrigger>
          <TabsTrigger value="budget"><PieChart className="h-4 w-4 mr-1.5" />Orçamento</TabsTrigger>
        </TabsList>

        {/* ════ TAB: VISÃO GERAL ════ */}
        <TabsContent value="overview" className="mt-4 space-y-4">
          <div className="grid gap-4 md:grid-cols-2">

            {/* Gráfico de cenários receita */}
            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle>Projeção de Receita por Cenário — 2026</CardTitle>
                <CardDescription>Comparação dos 3 cenários ao longo dos 12 meses</CardDescription>
              </CardHeader>
              <CardContent>
                {scenarioChartData.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-48 gap-3 text-muted-foreground">
                    <TrendingUp className="h-12 w-12 opacity-15" />
                    <p className="text-sm">Sem projeções disponíveis</p>
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={280}>
                    <AreaChart data={scenarioChartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                      <defs>
                        {[{k:'optimista',c:'#22c55e'},{k:'realista',c:'#3b82f6'},{k:'pessimista',c:'#ef4444'}].map(({k,c}) => (
                          <linearGradient key={k} id={`grad-${k}`} x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%"  stopColor={c} stopOpacity={0.15} />
                            <stop offset="95%" stopColor={c} stopOpacity={0} />
                          </linearGradient>
                        ))}
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
                      <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                      <YAxis tickFormatter={v => fmtM(v)} tick={{ fontSize: 11 }} />
                      <Tooltip formatter={(v) => fmt(Number(v))} />
                      <Legend />
                      <Area type="monotone" dataKey="optimista"  stroke="#22c55e" fill="url(#grad-optimista)"  strokeWidth={2} name="Optimista" />
                      <Area type="monotone" dataKey="realista"   stroke="#3b82f6" fill="url(#grad-realista)"   strokeWidth={2.5} name="Realista" strokeDasharray="0" />
                      <Area type="monotone" dataKey="pessimista" stroke="#ef4444" fill="url(#grad-pessimista)" strokeWidth={2} name="Pessimista" />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Metas por tipo (pie) */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Metas por Tipo</CardTitle>
                <CardDescription>{goals.length} metas registadas</CardDescription>
              </CardHeader>
              <CardContent>
                {goalTypeChartData.length === 0 ? (
                  <div className="flex items-center justify-center h-36 text-muted-foreground text-sm">Sem metas</div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <RePieChart>
                      <Pie data={goalTypeChartData} cx="50%" cy="50%" innerRadius={45} outerRadius={75}
                        dataKey="value" nameKey="name" label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        labelLine={false}>
                        {goalTypeChartData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                      </Pie>
                      <Tooltip />
                    </RePieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Cash flow do cenário seleccionado */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-base">Fluxo de Caixa</CardTitle>
                    <CardDescription>
                      Cenário: {selectedScenario ? SCENARIO_LABELS[selectedScenario.scenario_type] : '—'}
                    </CardDescription>
                  </div>
                  <Select
                    value={selectedScenario?.id || 'none'}
                    onValueChange={v => {
                      const sc = scenarios.find(s => s.id === v)
                      if (sc) setSelectedScenario(sc)
                    }}
                  >
                    <SelectTrigger className="w-36 h-8 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {scenarios.map(s => (
                        <SelectItem key={s.id} value={s.id}>{SCENARIO_LABELS[s.scenario_type]}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </CardHeader>
              <CardContent>
                {cashFlowData.length === 0 ? (
                  <div className="flex items-center justify-center h-36 text-muted-foreground text-sm">Sem dados</div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={cashFlowData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
                      <XAxis dataKey="month" tick={{ fontSize: 10 }} />
                      <YAxis tickFormatter={v => fmtM(v)} tick={{ fontSize: 10 }} />
                      <Tooltip formatter={v => fmt(Number(v))} />
                      {'receita' in (cashFlowData[0] || {}) ? (
                        <>
                          <Bar dataKey="receita" fill="#3b82f6" name="Receita" radius={[2,2,0,0]} />
                          <Bar dataKey="despesa" fill="#ef4444" name="Despesa" radius={[2,2,0,0]} />
                        </>
                      ) : (
                        <Bar dataKey="cashflow" name="Cash Flow"
                          fill={selectedScenario?.scenario_type === 'PESSIMISTIC' ? '#ef4444' : '#22c55e'}
                          radius={[2,2,0,0]} />
                      )}
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Metas críticas / em atraso */}
          {stats.critical > 0 && (
            <Card className="border-red-200 bg-red-50/30">
              <CardHeader className="pb-2">
                <CardTitle className="text-base text-red-700 flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4" />
                  Metas Críticas que Requerem Atenção ({stats.critical})
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {goals.filter(g => g.priority === 'CRITICAL' && g.status === 'ACTIVE').map(g => (
                    <div key={g.id} className="flex items-center justify-between gap-2 rounded-md border border-red-200 bg-white px-3 py-2">
                      <div>
                        <p className="text-sm font-medium">{g.goal_name}</p>
                        <p className="text-xs text-muted-foreground">
                          {fmt(Number(g.current_amount))} / {fmt(Number(g.target_amount))} · prazo: {fmtDate(g.end_date)}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="w-24">
                          <Progress value={Number(g.progress_percentage)} className="h-2" />
                          <p className="text-xs text-center mt-0.5">{Number(g.progress_percentage).toFixed(0)}%</p>
                        </div>
                        <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => openProgress(g)}>
                          Actualizar
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ════ TAB: METAS ════ */}
        <TabsContent value="goals" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Metas Financeiras ({goals.length})</CardTitle>
                  <CardDescription>
                    {stats.active} activas · {stats.completed} concluídas · {stats.overdue} em atraso
                  </CardDescription>
                </div>
                <Button size="sm" onClick={() => { setEditingGoal(null); setGoalForm({ ...EMPTY_GOAL }); setGoalDialog(true) }}>
                  <Plus className="h-4 w-4 mr-2" />Nova Meta
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {goals.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <Target className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhuma meta definida</p>
                  <Button size="sm" onClick={() => { setEditingGoal(null); setGoalForm({ ...EMPTY_GOAL }); setGoalDialog(true) }}>
                    <Plus className="h-4 w-4 mr-2" />Criar Primeira Meta
                  </Button>
                </div>
              ) : (
                <div className="space-y-3">
                  <AnimatePresence>
                    {goals.map((goal, idx) => {
                      const progress   = Number(goal.progress_percentage)
                      const priority   = PRIORITY_STYLE[goal.priority]
                      const statusSt   = STATUS_STYLE[goal.status]
                      const daysLeft   = Math.ceil((new Date(goal.end_date).getTime() - Date.now()) / 86400000)
                      const isOverdue  = daysLeft < 0 && goal.status === 'ACTIVE'
                      return (
                        <motion.div key={goal.id}
                          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -6 }} transition={{ delay: idx * 0.03 }}
                          className="rounded-lg border p-4 hover:bg-muted/20 transition-colors"
                        >
                          <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                            <div className="flex-1">
                              <div className="flex flex-wrap items-center gap-2 mb-1.5">
                                <Flag className={`h-4 w-4 ${priority.text}`} />
                                <p className="font-semibold">{goal.goal_name}</p>
                                <Badge className={`${priority.bg} ${priority.text} ${priority.border} border text-xs py-0`}>
                                  {GOAL_PRIORITY_LABELS[goal.priority]}
                                </Badge>
                                <Badge className={`${statusSt.bg} ${statusSt.text} text-xs py-0`}>
                                  {GOAL_STATUS_LABELS[goal.status]}
                                </Badge>
                                <Badge variant="outline" className="text-xs py-0">
                                  {GOAL_TYPE_LABELS[goal.goal_type]}
                                </Badge>
                              </div>
                              {goal.description && (
                                <p className="text-xs text-muted-foreground mb-2 line-clamp-1">{goal.description}</p>
                              )}
                              <div className="space-y-1">
                                <div className="flex justify-between text-xs text-muted-foreground">
                                  <span>Progresso</span>
                                  <span className="font-medium text-foreground">{progress.toFixed(1)}%</span>
                                </div>
                                <Progress value={Math.min(100, progress)}
                                  className={`h-2 ${progress >= 100 ? '[&>div]:bg-green-500' : progress >= 70 ? '[&>div]:bg-blue-500' : progress >= 40 ? '[&>div]:bg-yellow-500' : '[&>div]:bg-red-500'}`}
                                />
                                <div className="flex justify-between text-xs text-muted-foreground">
                                  <span>{fmt(Number(goal.current_amount))} realizado</span>
                                  <span>meta: {fmt(Number(goal.target_amount))}</span>
                                </div>
                              </div>
                            </div>
                            <div className="flex flex-col items-end gap-2 shrink-0">
                              <div className="text-right">
                                <p className="text-xs text-muted-foreground">Prazo</p>
                                <p className={`text-xs font-medium ${isOverdue ? 'text-red-600' : 'text-muted-foreground'}`}>
                                  {fmtDate(goal.end_date)}
                                  {isOverdue ? ' ⚠' : daysLeft <= 30 ? ` (${daysLeft}d)` : ''}
                                </p>
                              </div>
                              <div className="flex gap-1">
                                <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => openProgress(goal)}>
                                  Actualizar
                                </Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEditGoal(goal)}>
                                  <Edit className="h-3.5 w-3.5" />
                                </Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive hover:text-destructive"
                                  onClick={() => handleDeleteGoal(goal.id, goal.goal_name)}>
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </div>
                            </div>
                          </div>
                        </motion.div>
                      )
                    })}
                  </AnimatePresence>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TAB: CENÁRIOS ════ */}
        <TabsContent value="scenarios" className="mt-4">
          {scenarios.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                <Layers className="h-14 w-14 opacity-15" />
                <p className="text-sm">Nenhum cenário disponível</p>
                <p className="text-xs">Os cenários são criados automaticamente com base nas projeções</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {/* Cards de cenários lado a lado */}
              <div className="grid gap-4 md:grid-cols-3">
                {scenarios.map((sc, idx) => {
                  const colors  = SCENARIO_COLORS[sc.scenario_type]
                  const metrics = (sc.key_metrics || {}) as Partial<ScenarioMetrics>
                  const isSelected = selectedScenario?.id === sc.id
                  return (
                    <motion.div key={sc.id}
                      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: idx * 0.08 }}>
                      <Card
                        className={`cursor-pointer transition-all ${isSelected ? `ring-2 ring-offset-1 ${colors.border.replace('border-','ring-')}` : 'hover:shadow-md'} ${colors.border} border`}
                        onClick={() => setSelectedScenario(sc)}
                      >
                        <CardHeader className={`${colors.bg} rounded-t-lg pb-3`}>
                          <div className="flex items-center justify-between">
                            <CardTitle className={`text-base ${colors.text}`}>{sc.scenario_name}</CardTitle>
                            <Badge className={`${colors.bg} ${colors.text} ${colors.border} border text-xs`}>
                              {sc.probability ? `${sc.probability}%` : '—'}
                            </Badge>
                          </div>
                          <CardDescription className={colors.text + ' opacity-75 text-xs'}>
                            {sc.description ? sc.description.substring(0, 80) + '...' : ''}
                          </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-4 space-y-3">
                          {metrics.revenue && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">Receita Proj.</span>
                              <span className="font-bold text-green-600">
                                <ArrowUpRight className="h-3.5 w-3.5 inline" />
                                {fmtM(Number(metrics.revenue))} AOA
                              </span>
                            </div>
                          )}
                          {metrics.expenses && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">Despesas Proj.</span>
                              <span className="font-medium text-red-600">
                                <ArrowDownRight className="h-3.5 w-3.5 inline" />
                                {fmtM(Number(metrics.expenses))} AOA
                              </span>
                            </div>
                          )}
                          {metrics.profit !== undefined && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">Lucro Proj.</span>
                              <span className={`font-bold ${Number(metrics.profit) >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                                {fmtM(Number(metrics.profit))} AOA
                              </span>
                            </div>
                          )}
                          {metrics.margin && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">Margem</span>
                              <span className="font-semibold">{String(metrics.margin)}</span>
                            </div>
                          )}
                          {metrics.growth && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">Crescimento</span>
                              <span className="font-medium">{String(metrics.growth)}</span>
                            </div>
                          )}
                          {sc.impact_analysis && (
                            <>
                              <Separator />
                              <p className="text-xs text-muted-foreground line-clamp-2">{sc.impact_analysis}</p>
                            </>
                          )}
                          {/* Premissas */}
                          {sc.assumptions && Object.keys(sc.assumptions).length > 0 && (
                            <div className="space-y-1">
                              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Premissas</p>
                              <div className="flex flex-wrap gap-1">
                                {Object.entries(sc.assumptions).map(([k, v]) => (
                                  <Badge key={k} variant="outline" className="text-xs py-0">
                                    {k}: {String(v)}
                                  </Badge>
                                ))}
                              </div>
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </motion.div>
                  )
                })}
              </div>

              {/* Comparação em tabela */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Comparação de Métricas</CardTitle>
                  <CardDescription>Visão consolidada dos 3 cenários</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b">
                          <th className="text-left pb-2 text-muted-foreground font-medium">Métrica</th>
                          {scenarios.map(sc => (
                            <th key={sc.id} className={`text-right pb-2 font-semibold ${SCENARIO_COLORS[sc.scenario_type].text}`}>
                              {SCENARIO_LABELS[sc.scenario_type]}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {[
                          { key: 'revenue',  label: 'Receita Total', isCurrency: true },
                          { key: 'expenses', label: 'Despesas Totais', isCurrency: true },
                          { key: 'profit',   label: 'Lucro Líquido', isCurrency: true },
                          { key: 'margin',   label: 'Margem Líquida', isCurrency: false },
                          { key: 'growth',   label: 'Crescimento', isCurrency: false },
                        ].map(row => (
                          <tr key={row.key} className="hover:bg-muted/20">
                            <td className="py-2.5 text-muted-foreground">{row.label}</td>
                            {scenarios.map(sc => {
                              const metricsRow = (sc.key_metrics || {}) as Partial<ScenarioMetrics>
                              const val = metricsRow[row.key as keyof ScenarioMetrics]
                              const numVal = typeof val === 'number' ? val : 0
                              return (
                                <td key={sc.id} className={`py-2.5 text-right font-medium ${
                                  row.key === 'profit' && numVal < 0 ? 'text-red-600' :
                                  row.key === 'profit' && numVal > 0 ? 'text-green-600' : ''
                                }`}>
                                  {val === undefined ? '—' : row.isCurrency
                                    ? fmtM(numVal) + ' AOA'
                                    : String(val)}
                                </td>
                              )
                            })}
                          </tr>
                        ))}
                        <tr className="hover:bg-muted/20">
                          <td className="py-2.5 text-muted-foreground">Probabilidade</td>
                          {scenarios.map(sc => (
                            <td key={sc.id} className="py-2.5 text-right font-medium">
                              {sc.probability ? `${sc.probability}%` : '—'}
                            </td>
                          ))}
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>

        {/* ════ TAB: PROJEÇÕES ════ */}
        <TabsContent value="projections" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Projeções Financeiras ({projections.length})</CardTitle>
              <CardDescription>
                Projeções de receita, despesa e fluxo de caixa por cenário
              </CardDescription>
            </CardHeader>
            <CardContent>
              {projections.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <TrendingUp className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhuma projeção disponível</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* Agrupado por tipo */}
                  {(['REVENUE','EXPENSE','CASH_FLOW'] as ProjType[]).map(type => {
                    const typeProjs = projections.filter(p => p.projection_type === type)
                    if (typeProjs.length === 0) return null
                    const colors = type === 'REVENUE' ? ['#22c55e','#3b82f6','#ef4444']
                      : type === 'EXPENSE' ? ['#f59e0b','#f97316','#ef4444']
                      : ['#3b82f6','#22c55e','#ef4444']
                    const keys   = typeProjs.map((_, i) =>
                      `${type.toLowerCase()}_${i === 0 ? 'optimista' : i === 1 ? 'realista' : 'pessimista'}`
                    )
                    // Montar dados mensais
                    const chartData = (typeProjs[0]?.data_points || []).map((pt, i) => {
                      const row: Record<string, number | string> = { month: pt.month }
                      typeProjs.forEach((proj, j) => {
                        const dp = (proj.data_points || [])[i]
                        if (dp) row[keys[j]] = Number(dp.value)
                      })
                      return row
                    })
                    return (
                      <div key={type}>
                        <p className="text-sm font-semibold mb-2 text-muted-foreground uppercase tracking-wide">
                          {PROJ_TYPE_LABELS[type]}
                        </p>
                        <ResponsiveContainer width="100%" height={220}>
                          <LineChart data={chartData} margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" className="stroke-border/50" />
                            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                            <YAxis tickFormatter={v => fmtM(v)} tick={{ fontSize: 11 }} />
                            <Tooltip formatter={v => fmt(Number(v))} />
                            <Legend />
                            {typeProjs.map((proj, j) => (
                              <Line key={proj.id} type="monotone" dataKey={keys[j]}
                                stroke={colors[j]} strokeWidth={j === 1 ? 2.5 : 1.5}
                                strokeDasharray={j === 2 ? '5 3' : '0'}
                                dot={false} name={
                                  proj.projection_name.includes('Optim') ? 'Optimista'
                                  : proj.projection_name.includes('Realis') ? 'Realista' : 'Pessimista'
                                }
                              />
                            ))}
                          </LineChart>
                        </ResponsiveContainer>
                        <div className="grid grid-cols-3 gap-3 mt-2">
                          {typeProjs.map((proj, j) => {
                            const pts  = proj.data_points || []
                            const last = pts[pts.length - 1]?.value || 0
                            const first = pts[0]?.value || 0
                            const variation = first > 0 ? ((Number(last) - Number(first)) / Number(first)) * 100 : 0
                            return (
                              <div key={proj.id} className="rounded-lg border p-3 text-sm">
                                <p className="font-medium text-xs text-muted-foreground">
                                  {j === 0 ? 'Optimista' : j === 1 ? 'Realista' : 'Pessimista'}
                                </p>
                                <p className="font-bold">{fmtM(Number(last))} AOA</p>
                                <p className={`text-xs ${variation >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {variation >= 0 ? '↑' : '↓'} {Math.abs(variation).toFixed(1)}% (Jan→Dez)
                                </p>
                                <p className="text-xs text-muted-foreground">
                                  Conf.: {proj.confidence_level ? `${proj.confidence_level}%` : '—'}
                                </p>
                              </div>
                            )
                          })}
                        </div>
                        <Separator className="mt-4" />
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TAB: ORÇAMENTO ════ */}
        <TabsContent value="budget" className="mt-4 space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">{budgets.length} plano(s) de orçamento</p>
            <Button size="sm" onClick={() => { setBudgetForm({ ...EMPTY_BUDGET }); setBudgetDialog(true) }}>
              <Plus className="h-4 w-4 mr-2" />Novo Orçamento
            </Button>
          </div>

          {budgets.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                <PieChart className="h-14 w-14 opacity-15" />
                <p className="text-sm">Nenhum orçamento criado</p>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-4">
              {budgets.map((budget, bIdx) => {
                const execPct = Number(budget.total_budget) > 0
                  ? (Number(budget.spent_budget) / Number(budget.total_budget)) * 100 : 0
                const allocPct = Number(budget.total_budget) > 0
                  ? (Number(budget.allocated_budget) / Number(budget.total_budget)) * 100 : 0
                const cats = budget.categories || []
                const statusBadgeStyle: Record<BudgetStatus, string> = {
                  DRAFT:    'bg-gray-100 text-gray-600 border-gray-200',
                  APPROVED: 'bg-blue-100 text-blue-700 border-blue-200',
                  ACTIVE:   'bg-green-100 text-green-700 border-green-200',
                  CLOSED:   'bg-gray-100 text-gray-500 border-gray-200',
                }
                return (
                  <motion.div key={budget.id}
                    initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: bIdx * 0.07 }}>
                    <Card>
                      <CardHeader>
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <CardTitle className="text-base">{budget.budget_name}</CardTitle>
                            <CardDescription>
                              Ano Fiscal: {budget.fiscal_year} · {PROJ_PERIOD_LABELS[budget.period_type as ProjPeriod] || budget.period_type}
                            </CardDescription>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge className={`border text-xs ${statusBadgeStyle[budget.status]}`}>
                              {BUDGET_STATUS_LABELS[budget.status]}
                            </Badge>
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                              onClick={() => handleDeleteBudget(budget.id, budget.budget_name)}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {/* KPIs do orçamento */}
                        <div className="grid grid-cols-3 gap-3">
                          {[
                            { label: 'Total Orçado',   value: fmt(Number(budget.total_budget)),    color: 'text-foreground' },
                            { label: 'Alocado',        value: fmt(Number(budget.allocated_budget)), color: 'text-blue-600' },
                            { label: 'Gasto',          value: fmt(Number(budget.spent_budget)),     color: 'text-orange-600' },
                          ].map((k, i) => (
                            <div key={i} className="rounded-lg border p-3 text-center">
                              <p className="text-xs text-muted-foreground">{k.label}</p>
                              <p className={`font-bold text-sm ${k.color}`}>{k.value}</p>
                            </div>
                          ))}
                        </div>

                        {/* Barras de execução */}
                        <div className="space-y-2">
                          <div className="flex justify-between text-xs">
                            <span className="text-muted-foreground">Execução Orçamental</span>
                            <span className="font-medium">{execPct.toFixed(1)}%</span>
                          </div>
                          <Progress value={Math.min(100, execPct)}
                            className={`h-3 ${execPct > 90 ? '[&>div]:bg-red-500' : execPct > 70 ? '[&>div]:bg-orange-500' : '[&>div]:bg-green-500'}`}
                          />
                          <div className="flex justify-between text-xs">
                            <span className="text-muted-foreground">Alocação</span>
                            <span className="font-medium">{allocPct.toFixed(1)}%</span>
                          </div>
                          <Progress value={Math.min(100, allocPct)} className="h-2" />
                        </div>

                        {/* Categorias */}
                        {cats.length > 0 && (
                          <>
                            <Separator />
                            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Categorias</p>
                            <div className="space-y-2">
                              {cats.map((cat: BudgetCategory, ci: number) => {
                                const catExec = cat.allocated > 0 ? (cat.spent / cat.allocated) * 100 : 0
                                return (
                                  <div key={ci} className="space-y-1">
                                    <div className="flex justify-between text-xs">
                                      <span className="font-medium">{cat.name}</span>
                                      <span className="text-muted-foreground">
                                        {fmt(cat.spent)} / {fmt(cat.allocated)} ({catExec.toFixed(0)}%)
                                      </span>
                                    </div>
                                    <Progress value={Math.min(100, catExec)}
                                      className={`h-1.5 ${catExec > 90 ? '[&>div]:bg-red-500' : catExec > 70 ? '[&>div]:bg-orange-400' : '[&>div]:bg-blue-500'}`}
                                    />
                                  </div>
                                )
                              })}
                            </div>
                          </>
                        )}
                      </CardContent>
                    </Card>
                  </motion.div>
                )
              })}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ══════ MODAL: Criar/Editar Meta ══════ */}
      <Dialog open={goalDialog} onOpenChange={v => { if (!v) { setGoalDialog(false); setEditingGoal(null); setGoalForm({ ...EMPTY_GOAL }) } }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingGoal ? `Editar: ${editingGoal.goal_name}` : 'Nova Meta Financeira'}</DialogTitle>
            <DialogDescription>Defina os parâmetros e indicadores da meta</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1">
              <Label>Nome da Meta <span className="text-destructive">*</span></Label>
              <Input value={goalForm.goal_name} onChange={e => setGoalForm(f => ({ ...f, goal_name: e.target.value }))}
                placeholder="Ex: Receita Anual 2026" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Tipo</Label>
                <Select value={goalForm.goal_type} onValueChange={v => setGoalForm(f => ({ ...f, goal_type: v as GoalType }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(GOAL_TYPE_LABELS) as GoalType[]).map(k => (
                      <SelectItem key={k} value={k}>{GOAL_TYPE_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Prioridade</Label>
                <Select value={goalForm.priority} onValueChange={v => setGoalForm(f => ({ ...f, priority: v as GoalPriority }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(GOAL_PRIORITY_LABELS) as GoalPriority[]).map(k => (
                      <SelectItem key={k} value={k}>{GOAL_PRIORITY_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Valor Alvo (AOA) <span className="text-destructive">*</span></Label>
                <Input type="number" min="0" step="100000"
                  value={goalForm.target_amount || ''}
                  onChange={e => setGoalForm(f => ({ ...f, target_amount: parseFloat(e.target.value) || 0 }))}
                  placeholder="0" />
              </div>
              <div className="space-y-1">
                <Label>Valor Actual (AOA)</Label>
                <Input type="number" min="0" step="100000"
                  value={goalForm.current_amount || ''}
                  onChange={e => setGoalForm(f => ({ ...f, current_amount: parseFloat(e.target.value) || 0 }))}
                  placeholder="0" />
              </div>
              <div className="space-y-1">
                <Label>Data Início</Label>
                <Input type="date" value={goalForm.start_date}
                  onChange={e => setGoalForm(f => ({ ...f, start_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Data Fim</Label>
                <Input type="date" value={goalForm.end_date}
                  onChange={e => setGoalForm(f => ({ ...f, end_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Estado</Label>
                <Select value={goalForm.status} onValueChange={v => setGoalForm(f => ({ ...f, status: v as GoalStatus }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(GOAL_STATUS_LABELS) as GoalStatus[]).map(k => (
                      <SelectItem key={k} value={k}>{GOAL_STATUS_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Categoria</Label>
                <Input value={goalForm.category || ''} onChange={e => setGoalForm(f => ({ ...f, category: e.target.value }))}
                  placeholder="Ex: Operacional" />
              </div>
            </div>
            {goalForm.target_amount > 0 && (
              <div className="rounded-md bg-muted/40 p-3">
                <p className="text-xs text-muted-foreground mb-1">Progresso calculado</p>
                <Progress value={Math.min(100, (Number(goalForm.current_amount) / Number(goalForm.target_amount)) * 100)} className="h-2" />
                <p className="text-xs font-medium mt-1">
                  {((Number(goalForm.current_amount) / Number(goalForm.target_amount)) * 100).toFixed(1)}%
                  · Restante: {fmt(Number(goalForm.target_amount) - Number(goalForm.current_amount))}
                </p>
              </div>
            )}
            <div className="space-y-1">
              <Label>Descrição</Label>
              <Textarea rows={2} value={goalForm.description || ''}
                onChange={e => setGoalForm(f => ({ ...f, description: e.target.value }))}
                placeholder="Descreva o contexto e estratégia para atingir esta meta..." />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => { setGoalDialog(false); setEditingGoal(null) }} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveGoal} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingGoal ? 'Guardar Alterações' : 'Criar Meta'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Actualizar Progresso ══════ */}
      <Dialog open={progressDialog} onOpenChange={setProgressDialog}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Actualizar Progresso</DialogTitle>
            <DialogDescription>{progressGoal?.goal_name}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1">
              <Label>Valor Actual (AOA)</Label>
              <Input type="number" min="0" step="100000"
                value={newProgress || ''}
                onChange={e => setNewProgress(parseFloat(e.target.value) || 0)} />
            </div>
            {progressGoal && (
              <div className="rounded-md bg-muted/40 p-3 space-y-1">
                <Progress value={Math.min(100, (newProgress / Number(progressGoal.target_amount)) * 100)} className="h-3" />
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>{((newProgress / Number(progressGoal.target_amount)) * 100).toFixed(1)}%</span>
                  <span>Alvo: {fmt(Number(progressGoal.target_amount))}</span>
                </div>
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setProgressDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveProgress} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Guardar
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Novo Orçamento ══════ */}
      <Dialog open={budgetDialog} onOpenChange={v => { if (!v) setBudgetDialog(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Novo Plano de Orçamento</DialogTitle>
            <DialogDescription>Defina o orçamento para o período</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1">
              <Label>Nome <span className="text-destructive">*</span></Label>
              <Input value={budgetForm.budget_name} onChange={e => setBudgetForm(f => ({ ...f, budget_name: e.target.value }))}
                placeholder="Ex: Orçamento Operacional 2026" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Ano Fiscal</Label>
                <Input type="number" value={budgetForm.fiscal_year}
                  onChange={e => setBudgetForm(f => ({ ...f, fiscal_year: parseInt(e.target.value) || new Date().getFullYear() }))} />
              </div>
              <div className="space-y-1">
                <Label>Período</Label>
                <Select value={budgetForm.period_type} onValueChange={v => setBudgetForm(f => ({ ...f, period_type: v as BudgetPeriod }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="MONTHLY">Mensal</SelectItem>
                    <SelectItem value="QUARTERLY">Trimestral</SelectItem>
                    <SelectItem value="YEARLY">Anual</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Total Orçado (AOA) <span className="text-destructive">*</span></Label>
                <Input type="number" min="0" step="1000000"
                  value={budgetForm.total_budget || ''}
                  onChange={e => setBudgetForm(f => ({ ...f, total_budget: parseFloat(e.target.value) || 0 }))} />
              </div>
              <div className="space-y-1">
                <Label>Estado</Label>
                <Select value={budgetForm.status} onValueChange={v => setBudgetForm(f => ({ ...f, status: v as BudgetStatus }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(BUDGET_STATUS_LABELS) as BudgetStatus[]).map(k => (
                      <SelectItem key={k} value={k}>{BUDGET_STATUS_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setBudgetDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveBudget} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Criar Orçamento
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
