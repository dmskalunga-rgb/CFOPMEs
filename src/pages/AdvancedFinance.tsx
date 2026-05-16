/**
 * AdvancedFinance.tsx
 * Página de Finanças Avançadas — Projeções, Análises e IA Financeira
 * Totalmente baseada em dados reais do Supabase. Sem dados simulados.
 */
import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  TrendingUp, DollarSign, Brain, RefreshCw,
  BarChart3, Layers, Target, AlertTriangle, Lightbulb,
  CheckCircle2, AlertCircle, Loader2, Activity, ArrowUpRight,
  ArrowDownRight, Minus, PieChart, Zap, Shield,
  ChevronDown, ChevronUp,
} from 'lucide-react'
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart as RePieChart, Pie, Cell, ReferenceLine,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { toast } from 'sonner'
import {
  historicalService, forecastService, categoryService,
  aiInsightsService, scenarioAnalysisService, budgetVarianceService,
  type MonthlyAggregate, type CashFlowForecast, type CategoryBreakdown,
  type AIInsight, type AdvancedFinanceSummary,
} from '@/services/advancedFinanceService'

// ─── Formatadores ─────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)
const fmtM = (v: number) => {
  const abs = Math.abs(v)
  const s = v < 0 ? '-' : ''
  if (abs >= 1_000_000_000) return `${s}${(abs / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000)     return `${s}${(abs / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000)         return `${s}${(abs / 1_000).toFixed(0)}K`
  return `${s}${abs.toFixed(0)}`
}
const pct = (v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
const fmtPct = (v: number) => `${v.toFixed(1)}%`

// ─── Cores ────────────────────────────────────────────────────────────────────
const PIE_COLORS = ['#6366f1','#3b82f6','#a855f7','#f59e0b','#22c55e','#ef4444','#94a3b8','#0ea5e9','#f97316']

const INSIGHT_CONFIG: Record<AIInsight['type'], { bg: string; text: string; border: string; icon: React.ReactNode; label: string }> = {
  opportunity:     { bg: 'bg-green-50',  text: 'text-green-700',  border: 'border-green-200', icon: <TrendingUp className="h-4 w-4" />,   label: 'Oportunidade' },
  risk:            { bg: 'bg-red-50',    text: 'text-red-700',    border: 'border-red-200',   icon: <AlertTriangle className="h-4 w-4" />, label: 'Risco' },
  recommendation:  { bg: 'bg-blue-50',   text: 'text-blue-700',   border: 'border-blue-200',  icon: <Lightbulb className="h-4 w-4" />,    label: 'Recomendação' },
  alert:           { bg: 'bg-orange-50', text: 'text-orange-700', border: 'border-orange-200',icon: <AlertCircle className="h-4 w-4" />,  label: 'Alerta' },
}

const PRIORITY_BADGE: Record<AIInsight['priority'], string> = {
  low:      'bg-gray-100 text-gray-600',
  medium:   'bg-blue-100 text-blue-700',
  high:     'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
}
const PRIORITY_LABEL: Record<AIInsight['priority'], string> = {
  low: 'Baixa', medium: 'Média', high: 'Alta', critical: 'Crítica',
}

// ─── Tooltip customizado ──────────────────────────────────────────────────────
interface TooltipProps {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string }>
  label?: string
}
function CustomTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border bg-card shadow-lg p-3 text-sm">
      <p className="font-semibold mb-2">{label}</p>
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full" style={{ backgroundColor: p.color }} />
          <span className="text-muted-foreground">{p.name}:</span>
          <span className="font-medium">{fmtM(p.value)} AOA</span>
        </div>
      ))}
    </div>
  )
}

// ─── Skeleton card ────────────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <Card><CardContent className="p-6"><Skeleton className="h-16 w-full" /></CardContent></Card>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function AdvancedFinance() {
  const [activeTab, setActiveTab] = useState('overview')

  // Dados
  const [loadError, setLoadError]   = useState<string | null>(null)
  const [summary,    setSummary]    = useState<AdvancedFinanceSummary | null>(null)
  const [aggregates, setAggregates] = useState<MonthlyAggregate[]>([])
  const [forecasts,  setForecasts]  = useState<CashFlowForecast[]>([])
  const [categories, setCategories] = useState<CategoryBreakdown[]>([])
  const [insights,   setInsights]   = useState<AIInsight[]>([])
  const [scenarios,  setScenarios]  = useState<unknown[]>([])
  const [projections, setProjections] = useState<unknown[]>([])
  const [budgetVariance, setBudgetVariance] = useState<unknown[]>([])

  // Estado de carregamento por secção
  const [loadingMain,     setLoadingMain]     = useState(true)
  const [loadingForecast, setLoadingForecast] = useState(false)
  const [loadingInsights, setLoadingInsights] = useState(false)
  const [loadingScenarios, setLoadingScenarios] = useState(false)
  const [generatingIA,    setGeneratingIA]    = useState(false)

  // Expandir insight
  const [expandedInsight, setExpandedInsight] = useState<string | null>(null)

  // ─── Carregar dados principais ───────────────────────────────────────────
  const loadMain = useCallback(async () => {
    setLoadingMain(true)
    setLoadError(null)
    try {
      // Carregar em paralelo — se uma falhar, as outras continuam
      const [sumResult, aggsResult, catsResult] = await Promise.allSettled([
        historicalService.getSummary(6),
        historicalService.getMonthlyAggregates(12),
        categoryService.getExpenseBreakdown(6),
      ])

      if (sumResult.status  === 'fulfilled') setSummary(sumResult.value)
      if (aggsResult.status === 'fulfilled') setAggregates(aggsResult.value)
      if (catsResult.status === 'fulfilled') setCategories(catsResult.value)

      // Só mostrar erro se TODAS falharam
      const allFailed = [sumResult, aggsResult, catsResult].every(r => r.status === 'rejected')
      if (allFailed) {
        const firstErr = (sumResult as PromiseRejectedResult).reason
        const msg = firstErr?.message || 'Erro desconhecido'
        setLoadError(msg)
        console.error('[AdvancedFinance] Erro ao carregar dados:', firstErr)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar dados'
      setLoadError(msg)
      console.error('[AdvancedFinance] Erro inesperado:', err)
    } finally {
      setLoadingMain(false)
    }
  }, [])

  // ─── Carregar previsões ──────────────────────────────────────────────────
  const loadForecasts = useCallback(async () => {
    setLoadingForecast(true)
    try {
      // Tentar carregar guardadas primeiro; se não houver, gerar
      const saved = await forecastService.getSavedForecasts()
      if (saved.length > 0) {
        // Combinar com histórico real
        const hist = aggregates.slice(-6).map(a => ({
          forecast_month: a.month,
          label: a.label,
          predicted_income:  a.income,
          predicted_expense: a.expense,
          predicted_balance: a.balance,
          actual_income:     a.income,
          actual_expense:    a.expense,
          confidence: 1.0,
          trend: 'stable' as const,
          is_future: false,
        }))
        setForecasts([...hist, ...saved])
      } else {
        const generated = await forecastService.generateForecasts(6)
        setForecasts(generated)
      }
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar previsões.')
    } finally {
      setLoadingForecast(false)
    }
  }, [aggregates])

  // ─── Carregar insights IA ────────────────────────────────────────────────
  const loadInsights = useCallback(async () => {
    setLoadingInsights(true)
    try {
      const ins = await aiInsightsService.generateInsights()
      setInsights(ins)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao gerar insights IA.')
    } finally {
      setLoadingInsights(false)
    }
  }, [])

  // ─── Carregar cenários e orçamento ──────────────────────────────────────
  const loadScenarios = useCallback(async () => {
    setLoadingScenarios(true)
    try {
      const [sc, pr, bv] = await Promise.all([
        scenarioAnalysisService.getScenarios(),
        scenarioAnalysisService.getProjections(),
        budgetVarianceService.getBudgetVariance(),
      ])
      setScenarios(sc)
      setProjections(pr)
      setBudgetVariance(bv)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar cenários.')
    } finally {
      setLoadingScenarios(false)
    }
  }, [])

  // ─── Efeitos ─────────────────────────────────────────────────────────────
  useEffect(() => { loadMain() }, [loadMain])

  useEffect(() => {
    // Carregar secções secundárias mesmo sem agregados (podem não ter transacções)
    if (!loadingMain) {
      loadForecasts()
      loadInsights()
      loadScenarios()
    }
  }, [loadingMain, aggregates.length])

  // ─── Regenerar previsões IA ──────────────────────────────────────────────
  const handleRegenerateForecasts = async () => {
    setGeneratingIA(true)
    toast.info('A recalcular previsões com dados actualizados...')
    try {
      const generated = await forecastService.generateForecasts(6)
      setForecasts(generated)
      const ins = await aiInsightsService.generateInsights()
      setInsights(ins)
      toast.success('Previsões e insights actualizados!')
    } catch (err) {
      console.error(err)
      toast.error('Erro ao regenerar previsões.')
    } finally {
      setGeneratingIA(false)
    }
  }

  const handleRefresh = async () => {
    await loadMain()
    await Promise.all([loadForecasts(), loadInsights(), loadScenarios()])
    toast.success('Dados actualizados!')
  }

  // ─── Loading principal ────────────────────────────────────────────────────
  if (loadingMain) return (
    <div className="space-y-6 p-6">
      <Skeleton className="h-10 w-80" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
      </div>
      <Skeleton className="h-72 w-full rounded-xl" />
    </div>
  )

  // ─── Estado de erro (sem dados, não autenticado, etc.) ────────────────────
  if (loadError && !summary && aggregates.length === 0) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 p-6">
      <div className="p-4 rounded-full bg-red-50">
        <AlertCircle className="h-10 w-10 text-red-500" />
      </div>
      <div className="text-center">
        <h2 className="text-xl font-semibold mb-1">Sem dados disponíveis</h2>
        <p className="text-muted-foreground text-sm max-w-md">
          {loadError.includes('Tenant') || loadError.includes('autenticado')
            ? 'O perfil do utilizador não tem empresa associada. Certifique-se de que iniciou sessão correctamente.'
            : 'Não foi possível carregar dados financeiros. Adicione transacções na secção Finanças para ver análises aqui.'
          }
        </p>
        <p className="text-xs text-muted-foreground mt-2 font-mono bg-muted px-3 py-1 rounded inline-block">{loadError}</p>
      </div>
      <Button onClick={loadMain} variant="outline" size="sm">
        <RefreshCw className="h-4 w-4 mr-2" />Tentar novamente
      </Button>
    </div>
  )

  // ─── Dados para gráficos ──────────────────────────────────────────────────
  const forecastChartData = forecasts.map(f => ({
    name:    f.label,
    Receita: f.predicted_income,
    Despesa: f.predicted_expense,
    Saldo:   f.predicted_balance,
    Confiança: Math.round(f.confidence * 100),
    isFuture: f.is_future,
  }))

  const categoryPieData = categories.slice(0, 8).map(c => ({
    name:  c.category,
    value: c.total,
  }))

  const aggChartData = aggregates.map(a => ({
    name:    a.label,
    Receita: a.income,
    Despesa: a.expense,
    Saldo:   a.balance,
  }))

  const currentMonth = new Date().toISOString().slice(0, 7)

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Finanças Avançadas</h1>
          <p className="text-muted-foreground">Projeções, análises preditivas e inteligência artificial financeira</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4 mr-2" />Atualizar
          </Button>
          <Button size="sm" onClick={handleRegenerateForecasts} disabled={generatingIA}>
            {generatingIA
              ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" />A calcular...</>
              : <><Brain className="h-4 w-4 mr-2" />Gerar Análise IA</>
            }
          </Button>
        </div>
      </div>

      {/* ── KPIs ── */}
      {summary && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: 'Receita (6 meses)',
              value: fmtM(summary.totalIncome6M) + ' AOA',
              sub: `${pct(summary.incomeGrowth)} vs mês anterior`,
              icon: <TrendingUp className="h-5 w-5 text-green-600" />,
              bg: 'bg-green-50', color: 'text-green-700',
              trendUp: summary.incomeGrowth >= 0,
            },
            {
              title: 'Despesas (6 meses)',
              value: fmtM(summary.totalExpense6M) + ' AOA',
              sub: `${pct(summary.expenseGrowth)} vs mês anterior`,
              icon: <BarChart3 className="h-5 w-5 text-red-600" />,
              bg: 'bg-red-50', color: 'text-red-700',
              trendUp: summary.expenseGrowth <= 0,
            },
            {
              title: 'Saldo Acumulado',
              value: fmtM(summary.totalBalance6M) + ' AOA',
              sub: `Margem: ${fmtPct(summary.ratios.profitMargin)}`,
              icon: <DollarSign className="h-5 w-5 text-blue-600" />,
              bg: 'bg-blue-50', color: summary.totalBalance6M >= 0 ? 'text-blue-700' : 'text-red-700',
              trendUp: summary.totalBalance6M >= 0,
            },
            {
              title: 'Insights IA',
              value: String(insights.length),
              sub: `${insights.filter(i => i.priority === 'critical' || i.priority === 'high').length} de alta prioridade`,
              icon: <Brain className="h-5 w-5 text-purple-600" />,
              bg: 'bg-purple-50', color: 'text-purple-700',
              trendUp: true,
            },
          ].map((m, i) => (
            <motion.div key={i} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}>
              <Card>
                <CardContent className="p-6">
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-sm font-medium text-muted-foreground">{m.title}</p>
                    <div className={`p-2 rounded-lg ${m.bg}`}>{m.icon}</div>
                  </div>
                  <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
                  <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                    {m.trendUp
                      ? <ArrowUpRight className="h-3 w-3 text-green-500" />
                      : <ArrowDownRight className="h-3 w-3 text-red-500" />
                    }
                    {m.sub}
                  </p>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      )}

      {/* ── Rácios financeiros ── */}
      {summary && (
        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
          {[
            { label: 'Rácio Liquidez', value: summary.ratios.liquidityRatio.toFixed(2), good: summary.ratios.liquidityRatio >= 1.2, unit: 'x' },
            { label: 'Rácio Despesas', value: fmtPct(summary.ratios.expenseRatio), good: summary.ratios.expenseRatio < 75, unit: '' },
            { label: 'Crescimento', value: pct(summary.ratios.growthRate), good: summary.ratios.growthRate > 0, unit: '' },
            { label: 'Margem Lucro', value: fmtPct(summary.ratios.profitMargin), good: summary.ratios.profitMargin > 15, unit: '' },
            { label: 'Burn Rate/mês', value: fmtM(summary.ratios.burnRate), good: true, unit: ' AOA' },
            { label: 'Runway', value: summary.ratios.runway > 0 ? `${summary.ratios.runway.toFixed(1)}` : '—', good: summary.ratios.runway >= 6, unit: 'meses' },
          ].map((r, i) => (
            <Card key={i} className="overflow-hidden">
              <CardContent className="p-4">
                <p className="text-xs text-muted-foreground mb-1">{r.label}</p>
                <div className="flex items-baseline gap-1">
                  <span className={`text-lg font-bold ${r.good ? 'text-green-600' : 'text-red-600'}`}>{r.value}</span>
                  <span className="text-xs text-muted-foreground">{r.unit}</span>
                </div>
                <div className={`h-1 mt-2 rounded-full ${r.good ? 'bg-green-500' : 'bg-red-400'}`} />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ── Tabs ── */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid grid-cols-5 w-full max-w-2xl">
          <TabsTrigger value="overview"><Activity className="h-4 w-4 mr-1" />Visão Geral</TabsTrigger>
          <TabsTrigger value="forecast"><TrendingUp className="h-4 w-4 mr-1" />Previsões</TabsTrigger>
          <TabsTrigger value="ai"><Brain className="h-4 w-4 mr-1" />IA</TabsTrigger>
          <TabsTrigger value="categories"><PieChart className="h-4 w-4 mr-1" />Categorias</TabsTrigger>
          <TabsTrigger value="scenarios"><Layers className="h-4 w-4 mr-1" />Cenários</TabsTrigger>
        </TabsList>

        {/* ════ VISÃO GERAL ════ */}
        <TabsContent value="overview" className="mt-4 space-y-4">
          {/* Gráfico histórico 12 meses */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="h-5 w-5 text-blue-600" />
                Evolução Financeira — 12 Meses
              </CardTitle>
              <CardDescription>Receitas e despesas mensais reais das transacções</CardDescription>
            </CardHeader>
            <CardContent>
              {aggChartData.every(d => d.Receita === 0 && d.Despesa === 0) ? (
                <div className="flex flex-col items-center justify-center h-48 gap-3 text-muted-foreground">
                  <Activity className="h-12 w-12 opacity-15" />
                  <p className="text-sm">Sem transacções no período</p>
                  <p className="text-xs">Adicione transacções na secção Finanças</p>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={aggChartData} margin={{ top: 10, right: 20, left: 20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border/30" />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmtM(v)} />
                    <Tooltip content={<CustomTooltip />} />
                    <Legend />
                    <Bar dataKey="Receita" fill="#22c55e" radius={[3,3,0,0]} />
                    <Bar dataKey="Despesa" fill="#ef4444" radius={[3,3,0,0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Saldo acumulado */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Saldo Mensal Acumulado</CardTitle>
            </CardHeader>
            <CardContent>
              {aggChartData.every(d => d.Saldo === 0) ? (
                <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">Sem dados de saldo</div>
              ) : (
                <ResponsiveContainer width="100%" height={180}>
                  <AreaChart data={aggChartData} margin={{ top: 10, right: 20, left: 20, bottom: 0 }}>
                    <defs>
                      <linearGradient id="saldoGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-border/30" />
                    <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmtM(v)} />
                    <Tooltip content={<CustomTooltip />} />
                    <ReferenceLine y={0} stroke="#94a3b8" strokeDasharray="4 4" />
                    <Area dataKey="Saldo" stroke="#6366f1" fill="url(#saldoGrad)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          {/* Mini KPIs do mês actual */}
          {summary && (
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Mês Actual</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {[
                    { label: 'Receita', value: summary.currentMonthIncome, color: 'text-green-600' },
                    { label: 'Despesa', value: summary.currentMonthExpense, color: 'text-red-600' },
                    { label: 'Saldo',   value: summary.currentMonthBalance, color: summary.currentMonthBalance >= 0 ? 'text-blue-600' : 'text-red-600' },
                  ].map((item, i) => (
                    <div key={i} className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">{item.label}</span>
                      <span className={`font-bold ${item.color}`}>{fmt(item.value)}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Contas a Receber</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {[
                    { label: 'Total pendente', value: summary.receivables, color: 'text-blue-600' },
                    { label: 'Em atraso',       value: summary.overdueReceivables, color: 'text-red-600' },
                    { label: 'Dentro do prazo', value: Math.max(0, summary.receivables - summary.overdueReceivables), color: 'text-green-600' },
                  ].map((item, i) => (
                    <div key={i} className="flex items-center justify-between">
                      <span className="text-sm text-muted-foreground">{item.label}</span>
                      <span className={`font-bold ${item.color}`}>{fmt(item.value)}</span>
                    </div>
                  ))}
                  {summary.overdueReceivables > 0 && (
                    <Progress
                      value={summary.receivables > 0 ? (summary.overdueReceivables / summary.receivables) * 100 : 0}
                      className="h-2 [&>div]:bg-red-500"
                    />
                  )}
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>

        {/* ════ PREVISÕES ════ */}
        <TabsContent value="forecast" className="mt-4 space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <TrendingUp className="h-5 w-5 text-purple-600" />
                    Previsão de Fluxo de Caixa
                  </CardTitle>
                  <CardDescription>
                    6 meses históricos (real) + 6 meses futuros (IA — média móvel com tendência linear)
                  </CardDescription>
                </div>
                {loadingForecast && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
              </div>
            </CardHeader>
            <CardContent>
              {forecasts.length === 0 ? (
                <div className="flex flex-col items-center py-16 gap-3 text-muted-foreground">
                  <TrendingUp className="h-12 w-12 opacity-15" />
                  <p className="text-sm">Sem dados para calcular previsões</p>
                  <Button size="sm" onClick={handleRegenerateForecasts}>
                    <Brain className="h-4 w-4 mr-2" />Gerar Previsões
                  </Button>
                </div>
              ) : (
                <>
                  <ResponsiveContainer width="100%" height={300}>
                    <AreaChart data={forecastChartData} margin={{ top: 10, right: 20, left: 20, bottom: 0 }}>
                      <defs>
                        <linearGradient id="incGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#22c55e" stopOpacity={0.25} />
                          <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="expGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#ef4444" stopOpacity={0.25} />
                          <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-border/30" />
                      <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmtM(v)} />
                      <Tooltip content={<CustomTooltip />} />
                      <Legend />
                      <ReferenceLine
                        x={forecasts.find(f => f.is_future)?.label}
                        stroke="#94a3b8" strokeDasharray="6 3"
                        label={{ value: 'Hoje', position: 'insideTopLeft', fontSize: 11, fill: '#94a3b8' }}
                      />
                      <Area dataKey="Receita" stroke="#22c55e" fill="url(#incGrad)" strokeWidth={2} />
                      <Area dataKey="Despesa" stroke="#ef4444" fill="url(#expGrad)" strokeWidth={2} />
                    </AreaChart>
                  </ResponsiveContainer>

                  {/* Tabela de previsões */}
                  <div className="mt-6 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b">
                          <th className="text-left pb-2 text-muted-foreground font-medium">Período</th>
                          <th className="text-right pb-2 text-muted-foreground font-medium">Receita Prev.</th>
                          <th className="text-right pb-2 text-muted-foreground font-medium">Despesa Prev.</th>
                          <th className="text-right pb-2 text-muted-foreground font-medium">Saldo</th>
                          <th className="text-center pb-2 text-muted-foreground font-medium hidden md:table-cell">Confiança</th>
                          <th className="text-center pb-2 text-muted-foreground font-medium hidden lg:table-cell">Tipo</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {forecasts.map((f, i) => (
                          <motion.tr key={i}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            transition={{ delay: i * 0.04 }}
                            className={`${f.is_future ? 'bg-muted/20' : ''} hover:bg-muted/10`}
                          >
                            <td className="py-2.5 font-medium">
                              <div className="flex items-center gap-2">
                                {f.is_future
                                  ? <Zap className="h-3.5 w-3.5 text-purple-500" />
                                  : <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                                }
                                {f.label}
                              </div>
                            </td>
                            <td className="py-2.5 text-right text-green-600 font-medium">{fmtM(f.predicted_income)} AOA</td>
                            <td className="py-2.5 text-right text-red-600 font-medium">{fmtM(f.predicted_expense)} AOA</td>
                            <td className={`py-2.5 text-right font-bold ${f.predicted_balance >= 0 ? 'text-blue-600' : 'text-red-600'}`}>
                              {f.predicted_balance >= 0 ? '+' : ''}{fmtM(f.predicted_balance)} AOA
                            </td>
                            <td className="py-2.5 text-center hidden md:table-cell">
                              <div className="flex items-center justify-center gap-1">
                                <Progress value={f.confidence * 100} className="h-1.5 w-16" />
                                <span className="text-xs text-muted-foreground">{Math.round(f.confidence * 100)}%</span>
                              </div>
                            </td>
                            <td className="py-2.5 text-center hidden lg:table-cell">
                              <Badge variant="outline" className={`text-xs ${f.is_future ? 'border-purple-200 text-purple-700' : 'border-green-200 text-green-700'}`}>
                                {f.is_future ? 'IA Prev.' : 'Real'}
                              </Badge>
                            </td>
                          </motion.tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ IA INSIGHTS ════ */}
        <TabsContent value="ai" className="mt-4 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Insights de Inteligência Artificial</h2>
              <p className="text-sm text-muted-foreground">
                Análise automática gerada a partir dos dados reais das suas transacções
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={loadInsights} disabled={loadingInsights}>
              {loadingInsights
                ? <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                : <RefreshCw className="h-4 w-4 mr-2" />
              }
              Reanalisar
            </Button>
          </div>

          {loadingInsights ? (
            <div className="space-y-3">
              {[...Array(4)].map((_, i) => (
                <Card key={i}><CardContent className="p-5"><Skeleton className="h-16 w-full" /></CardContent></Card>
              ))}
            </div>
          ) : insights.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center py-16 gap-3 text-muted-foreground">
                <Brain className="h-14 w-14 opacity-15" />
                <p className="text-sm font-medium">Nenhum insight disponível</p>
                <p className="text-xs">Adicione transacções para a IA gerar análises automáticas</p>
                <Button size="sm" onClick={handleRegenerateForecasts} disabled={generatingIA}>
                  <Brain className="h-4 w-4 mr-2" />Gerar Análise
                </Button>
              </CardContent>
            </Card>
          ) : (
            <>
              {/* Resumo de insights por tipo */}
              <div className="grid gap-3 md:grid-cols-4">
                {(['opportunity','risk','recommendation','alert'] as AIInsight['type'][]).map(type => {
                  const cnt = insights.filter(i => i.type === type).length
                  const cfg = INSIGHT_CONFIG[type]
                  return (
                    <Card key={type} className={`border ${cfg.border}`}>
                      <CardContent className="p-4 flex items-center gap-3">
                        <div className={`p-2.5 rounded-lg ${cfg.bg} ${cfg.text}`}>{cfg.icon}</div>
                        <div>
                          <p className="text-2xl font-bold">{cnt}</p>
                          <p className={`text-xs ${cfg.text}`}>{cfg.label}{cnt !== 1 ? 's' : ''}</p>
                        </div>
                      </CardContent>
                    </Card>
                  )
                })}
              </div>

              {/* Lista de insights */}
              <div className="space-y-3">
                {insights
                  .sort((a, b) => {
                    const order = { critical: 0, high: 1, medium: 2, low: 3 }
                    return order[a.priority] - order[b.priority]
                  })
                  .map((insight, i) => {
                    const cfg = INSIGHT_CONFIG[insight.type]
                    const isExpanded = expandedInsight === insight.id
                    return (
                      <motion.div key={insight.id}
                        initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
                        <Card className={`border ${cfg.border} overflow-hidden`}>
                          <CardContent className="p-0">
                            <div
                              className="flex items-start gap-3 p-4 cursor-pointer hover:bg-muted/10 transition-colors"
                              onClick={() => setExpandedInsight(isExpanded ? null : insight.id)}
                            >
                              <div className={`p-2.5 rounded-lg ${cfg.bg} ${cfg.text} shrink-0 mt-0.5`}>
                                {cfg.icon}
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap mb-1">
                                  <p className="font-semibold">{insight.title}</p>
                                  <Badge className={`text-xs ${cfg.bg} ${cfg.text} border-0`}>{cfg.label}</Badge>
                                  <Badge className={`text-xs ${PRIORITY_BADGE[insight.priority]}`}>
                                    {PRIORITY_LABEL[insight.priority]}
                                  </Badge>
                                </div>
                                <p className="text-sm text-muted-foreground">{insight.description}</p>
                                {insight.category && (
                                  <p className="text-xs text-muted-foreground mt-1">Categoria: {insight.category}</p>
                                )}
                              </div>
                              <div className="text-right shrink-0">
                                <p className={`text-lg font-bold ${insight.impact >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {insight.impact >= 0 ? '+' : ''}{fmtM(insight.impact)} AOA
                                </p>
                                <p className="text-xs text-muted-foreground">impacto est.</p>
                                <div className="flex items-center gap-1 mt-1 justify-end">
                                  <Shield className="h-3 w-3 text-muted-foreground" />
                                  <span className="text-xs text-muted-foreground">{Math.round(insight.confidence * 100)}%</span>
                                  {isExpanded
                                    ? <ChevronUp className="h-4 w-4 text-muted-foreground" />
                                    : <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                  }
                                </div>
                              </div>
                            </div>
                            {isExpanded && insight.action_items.length > 0 && (
                              <div className={`px-4 pb-4 pt-0 ${cfg.bg} border-t ${cfg.border}`}>
                                <p className={`text-xs font-semibold mb-2 ${cfg.text}`}>Acções recomendadas:</p>
                                <ul className="space-y-1">
                                  {insight.action_items.map((item, j) => (
                                    <li key={j} className="flex items-start gap-2 text-sm">
                                      <CheckCircle2 className={`h-3.5 w-3.5 mt-0.5 ${cfg.text} shrink-0`} />
                                      <span>{item}</span>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      </motion.div>
                    )
                  })}
              </div>
            </>
          )}
        </TabsContent>

        {/* ════ CATEGORIAS ════ */}
        <TabsContent value="categories" className="mt-4 space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            {/* Pie Chart */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Distribuição de Despesas</CardTitle>
                <CardDescription>Por categoria — últimos 6 meses</CardDescription>
              </CardHeader>
              <CardContent>
                {categoryPieData.length === 0 ? (
                  <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                    Sem despesas registadas
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={240}>
                    <RePieChart>
                      <Pie
                        data={categoryPieData}
                        cx="50%" cy="50%"
                        innerRadius={55} outerRadius={90}
                        dataKey="value" nameKey="name"
                        label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                        labelLine={false}
                      >
                        {categoryPieData.map((_, i) => (
                          <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v: number) => fmt(v)} />
                    </RePieChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Tabela de categorias */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Análise por Categoria</CardTitle>
                <CardDescription>Tendência vs. período anterior</CardDescription>
              </CardHeader>
              <CardContent>
                {categories.length === 0 ? (
                  <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                    Sem dados de categorias
                  </div>
                ) : (
                  <div className="space-y-3">
                    {categories.slice(0, 8).map((cat, i) => (
                      <div key={i} className="space-y-1">
                        <div className="flex items-center justify-between text-sm">
                          <div className="flex items-center gap-2">
                            <div className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: PIE_COLORS[i % PIE_COLORS.length] }} />
                            <span className="font-medium">{cat.category}</span>
                            <span className="text-muted-foreground text-xs">({cat.count}×)</span>
                          </div>
                          <div className="flex items-center gap-2">
                            {cat.trend === 'increasing'
                              ? <ArrowUpRight className="h-3.5 w-3.5 text-red-500" />
                              : cat.trend === 'decreasing'
                              ? <ArrowDownRight className="h-3.5 w-3.5 text-green-500" />
                              : <Minus className="h-3.5 w-3.5 text-muted-foreground" />
                            }
                            <span className="font-medium">{fmtM(cat.total)} AOA</span>
                            <span className="text-muted-foreground text-xs w-10 text-right">{cat.pct.toFixed(1)}%</span>
                          </div>
                        </div>
                        <Progress value={cat.pct} className="h-1.5" style={{ '--progress-color': PIE_COLORS[i % PIE_COLORS.length] } as React.CSSProperties} />
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Gráfico de barras de categorias */}
          {categories.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Valor Total por Categoria</CardTitle>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={categories.slice(0, 8).map(c => ({ name: c.category.slice(0, 12), value: c.total }))}
                    layout="vertical" margin={{ top: 0, right: 60, bottom: 0, left: 100 }}>
                    <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={v => fmtM(v)} />
                    <YAxis dataKey="name" type="category" tick={{ fontSize: 11 }} width={95} />
                    <Tooltip formatter={(v: number) => [fmt(v), 'Total']} />
                    <Bar dataKey="value" radius={[0,4,4,0]}>
                      {categories.slice(0, 8).map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ════ CENÁRIOS ════ */}
        <TabsContent value="scenarios" className="mt-4 space-y-4">
          {loadingScenarios ? (
            <div className="space-y-4">
              {[...Array(2)].map((_, i) => <Card key={i}><CardContent className="p-6"><Skeleton className="h-24 w-full" /></CardContent></Card>)}
            </div>
          ) : (
            <>
              {/* Cenários do Supabase */}
              {(scenarios as Array<Record<string, unknown>>).length > 0 && (
                <div>
                  <h3 className="text-base font-semibold mb-3 flex items-center gap-2">
                    <Layers className="h-4 w-4 text-purple-600" />
                    Cenários Financeiros ({scenarios.length})
                  </h3>
                  <div className="grid gap-3 md:grid-cols-3">
                    {(scenarios as Array<Record<string, unknown>>).map((sc, i) => {
                      const type = sc.scenario_type as string
                      const colors = type === 'OPTIMISTIC'
                        ? 'border-green-200 bg-green-50'
                        : type === 'PESSIMISTIC'
                        ? 'border-red-200 bg-red-50'
                        : 'border-blue-200 bg-blue-50'
                      const metrics = sc.key_metrics as Record<string, unknown>
                      return (
                        <Card key={i} className={`border ${colors}`}>
                          <CardHeader className="pb-2">
                            <CardTitle className="text-sm">{sc.scenario_name as string}</CardTitle>
                            <CardDescription className="text-xs">{sc.scenario_type as string}</CardDescription>
                          </CardHeader>
                          <CardContent>
                            {metrics && Object.keys(metrics).length > 0 ? (
                              <div className="space-y-1 text-xs">
                                {Object.entries(metrics).slice(0, 4).map(([k, v]) => (
                                  <div key={k} className="flex justify-between">
                                    <span className="text-muted-foreground">{k}</span>
                                    <span className="font-medium">{typeof v === 'number' ? fmtM(v) : String(v)}</span>
                                  </div>
                                ))}
                              </div>
                            ) : (
                              <p className="text-xs text-muted-foreground">{sc.description as string || 'Sem métricas definidas'}</p>
                            )}
                            {typeof sc.probability === 'number' && (
                              <div className="mt-2 flex items-center gap-2">
                                <Progress value={Number(sc.probability)} className="h-1.5" />
                                <span className="text-xs text-muted-foreground">{sc.probability}%</span>
                              </div>
                            )}
                          </CardContent>
                        </Card>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Variação orçamental */}
              {(budgetVariance as Array<Record<string, unknown>>).length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Target className="h-4 w-4 text-orange-600" />
                      Variação Orçamental
                    </CardTitle>
                    <CardDescription>Comparação entre orçamento planeado e despesas reais</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-4">
                      {(budgetVariance as Array<Record<string, unknown>>).map((bv, i) => {
                        const variance = Number(bv.variance)
                        const spent    = Number(bv.spent)
                        const allocated = Number(bv.allocated)
                        const pctSpent = allocated > 0 ? Math.min(100, (spent / allocated) * 100) : 0
                        return (
                          <div key={i} className="space-y-2">
                            <div className="flex items-center justify-between text-sm">
                              <span className="font-medium">{bv.name as string}</span>
                              <div className="flex items-center gap-3">
                                <span className="text-muted-foreground">Orç: {fmtM(allocated)} AOA</span>
                                <span className="font-medium">Real: {fmtM(spent)} AOA</span>
                                <Badge className={`text-xs ${variance > 10 ? 'bg-red-100 text-red-700' : variance < -10 ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-700'}`}>
                                  {variance > 0 ? '+' : ''}{variance.toFixed(1)}%
                                </Badge>
                              </div>
                            </div>
                            <Progress
                              value={pctSpent}
                              className={`h-2 ${pctSpent > 90 ? '[&>div]:bg-red-500' : pctSpent > 75 ? '[&>div]:bg-orange-500' : '[&>div]:bg-blue-500'}`}
                            />
                            <div className="flex justify-between text-xs text-muted-foreground">
                              <span>Utilizado: {pctSpent.toFixed(1)}%</span>
                              <span>Restante: {fmtM(Number(bv.remaining))} AOA</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Projeções */}
              {(projections as Array<Record<string, unknown>>).length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <TrendingUp className="h-4 w-4 text-blue-600" />
                      Projeções Activas ({projections.length})
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {(projections as Array<Record<string, unknown>>).slice(0, 6).map((proj, i) => {
                        const dataPoints = Array.isArray(proj.data_points) ? proj.data_points : []
                        return (
                          <div key={i} className="rounded-lg border p-3">
                            <div className="flex items-center justify-between mb-2">
                              <div>
                                <p className="font-medium text-sm">{proj.projection_name as string}</p>
                                <p className="text-xs text-muted-foreground">
                                  {proj.projection_type as string} · {proj.period_type as string} · {proj.projection_method as string}
                                </p>
                              </div>
                              <div className="text-right">
                                {typeof proj.base_amount === 'number' && (
                                  <p className="text-sm font-bold">{fmtM(proj.base_amount)} AOA</p>
                                )}
                                {typeof proj.growth_rate === 'number' && (
                                  <Badge className={`text-xs ${Number(proj.growth_rate) >= 0 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                                    {Number(proj.growth_rate) >= 0 ? '+' : ''}{proj.growth_rate}% crescimento
                                  </Badge>
                                )}
                              </div>
                            </div>
                            {dataPoints.length > 0 && (
                              <div className="mt-2 h-16">
                                <ResponsiveContainer width="100%" height="100%">
                                  <LineChart data={(dataPoints as Array<Record<string, unknown>>).slice(0, 12).map((dp: Record<string, unknown>) => ({ name: dp.month, value: dp.value }))}>
                                    <Line type="monotone" dataKey="value" stroke="#6366f1" strokeWidth={2} dot={false} />
                                    <Tooltip formatter={(v: number) => [fmtM(v), 'Valor']} />
                                  </LineChart>
                                </ResponsiveContainer>
                              </div>
                            )}
                            {typeof proj.confidence_level === 'number' && (
                              <div className="flex items-center gap-2 mt-2">
                                <span className="text-xs text-muted-foreground">Confiança:</span>
                                <Progress value={Number(proj.confidence_level)} className="h-1.5 flex-1" />
                                <span className="text-xs text-muted-foreground">{proj.confidence_level}%</span>
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Estado vazio */}
              {(scenarios as unknown[]).length === 0 && (budgetVariance as unknown[]).length === 0 && (projections as unknown[]).length === 0 && (
                <Card>
                  <CardContent className="flex flex-col items-center py-16 gap-3 text-muted-foreground">
                    <Layers className="h-14 w-14 opacity-15" />
                    <p className="text-sm font-medium">Sem cenários configurados</p>
                    <p className="text-xs text-center max-w-xs">
                      Crie cenários financeiros, orçamentos e projeções na secção de Planeamento Financeiro para os ver aqui.
                    </p>
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
