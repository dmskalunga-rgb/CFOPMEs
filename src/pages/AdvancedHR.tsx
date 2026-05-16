// ============================================================
// KWANZACONTROL – RH Avançado
// Analytics e IA para Gestão de Pessoas
// 100% dados reais do Supabase – sem dados simulados
// 2026-04-18
// ============================================================
import { useState, useEffect, useCallback } from 'react'
import { Layout }   from '@/components/Layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button }   from '@/components/ui/button'
import { Badge }    from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, LineChart, Line, Legend, RadarChart, Radar,
  PolarGrid, PolarAngleAxis, PolarRadiusAxis,
} from 'recharts'
import {
  Users, TrendingUp, TrendingDown, Award, Brain, RefreshCw,
  AlertTriangle, CheckCircle2, Star, UserMinus, Flame,
  ChevronDown, ChevronUp, DollarSign, BarChart2, UserCheck,
  Loader2, AlertCircle,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  hrAnalyticsService, hrInsightsService, performanceAnalyticsService,
  payrollAnalyticsService, turnoverService,
  INSIGHT_TYPE_LABELS, INSIGHT_TYPE_COLORS,
  type HRKPIs, type HRAIInsight, type DeptStats,
  type PerfDistribution, type AbsenceStats, type SalaryRange,
  type HRAnalyticsSnapshot, type InsightType,
} from '@/services/advancedHRService'

// ── Utilitários ───────────────────────────────────────────────────────────────
const fmt   = (v: number, d = 0) => v.toLocaleString('pt-PT', { minimumFractionDigits: d, maximumFractionDigits: d })
const fmtAOA = (v: number) => `AOA ${fmt(v, 0)}`

const DEPT_COLORS  = ['#3B82F6','#10B981','#F59E0B','#EF4444','#8B5CF6','#EC4899','#14B8A6','#F97316']
const PERF_COLORS  = ['#EF4444','#F97316','#F59E0B','#10B981','#3B82F6']

// ── Sub-componentes ───────────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <Card>
      <CardHeader className="pb-2"><Skeleton className="h-4 w-32" /></CardHeader>
      <CardContent><Skeleton className="h-8 w-24 mb-1" /><Skeleton className="h-3 w-40" /></CardContent>
    </Card>
  )
}

function InsightIcon({ type }: { type: InsightType }) {
  const icons: Record<InsightType, React.ReactNode> = {
    FLIGHT_RISK:         <UserMinus className="h-5 w-5 text-red-600" />,
    HIGH_POTENTIAL:      <Star      className="h-5 w-5 text-green-600" />,
    BURNOUT_RISK:        <Flame     className="h-5 w-5 text-orange-600" />,
    PERFORMANCE_DECLINE: <TrendingDown className="h-5 w-5 text-yellow-600" />,
    PROMOTION_READY:     <Award    className="h-5 w-5 text-blue-600" />,
  }
  return <>{icons[type] ?? <Brain className="h-5 w-5 text-purple-600" />}</>
}

function RiskBadge({ level }: { level: 'low' | 'medium' | 'high' }) {
  const variants = {
    high:   { label: 'Alto',   cls: 'bg-red-100 text-red-700 border-red-200'    },
    medium: { label: 'Médio',  cls: 'bg-orange-100 text-orange-700 border-orange-200' },
    low:    { label: 'Baixo',  cls: 'bg-green-100 text-green-700 border-green-200'   },
  }
  const v = variants[level]
  return <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${v.cls}`}>{v.label}</span>
}

interface InsightCardProps {
  insight: HRAIInsight
  onResolve: (id: string) => void
}
function InsightCard({ insight, onResolve }: InsightCardProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border rounded-lg p-4 space-y-3 hover:bg-muted/30 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1">
          <div className="p-2 rounded-lg bg-muted shrink-0">
            <InsightIcon type={insight.insight_type} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-1">
              <span className={`text-sm font-semibold ${INSIGHT_TYPE_COLORS[insight.insight_type]}`}>
                {INSIGHT_TYPE_LABELS[insight.insight_type]}
              </span>
              <RiskBadge level={insight.risk_level} />
              <span className="text-xs text-muted-foreground">{insight.probability.toFixed(0)}% probabilidade</span>
            </div>
            <p className="font-medium text-sm">{insight.title}</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              {insight.employee?.department ?? ''}{insight.employee?.position ? ` · ${insight.employee.position}` : ''}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="ghost" size="sm" onClick={() => setExpanded(e => !e)}>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {/* Barra de probabilidade */}
      <Progress value={insight.probability} className="h-1.5" />

      {/* Expandido */}
      {expanded && (
        <div className="space-y-3 pt-2 border-t">
          <p className="text-sm text-muted-foreground">{insight.description}</p>

          {insight.factors && insight.factors.length > 0 && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">FACTORES</p>
              <div className="flex flex-wrap gap-2">
                {insight.factors.map((f, i) => (
                  <span key={i} className="text-xs bg-muted px-2 py-1 rounded">
                    <span className="text-muted-foreground">{f.factor}:</span> <strong>{f.value}</strong>
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="bg-blue-50 border border-blue-100 rounded-lg p-3">
            <p className="text-xs font-medium text-blue-700 mb-1">💡 Recomendação</p>
            <p className="text-sm text-blue-900">{insight.recommendation}</p>
          </div>

          {insight.estimated_impact && (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <TrendingUp className="h-3.5 w-3.5" />
              <span>{insight.estimated_impact}</span>
            </div>
          )}

          <div className="flex justify-end">
            <Button
              variant="outline" size="sm"
              className="text-green-700 border-green-200 hover:bg-green-50"
              onClick={() => onResolve(insight.id)}
            >
              <CheckCircle2 className="h-4 w-4 mr-1" /> Marcar como Resolvido
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Página Principal ──────────────────────────────────────────────────────────
export default function AdvancedHR() {
  // ─ Estado ──────────────────────────────────────────────────────────────────
  const [kpis,          setKpis]          = useState<HRKPIs | null>(null)
  const [insights,      setInsights]      = useState<HRAIInsight[]>([])
  const [deptStats,     setDeptStats]     = useState<DeptStats[]>([])
  const [perfDist,      setPerfDist]      = useState<PerfDistribution[]>([])
  const [absStats,      setAbsStats]      = useState<AbsenceStats[]>([])
  const [salaryRanges,  setSalaryRanges]  = useState<SalaryRange[]>([])
  const [snapshots,     setSnapshots]     = useState<HRAnalyticsSnapshot[]>([])
  const [topPerformers, setTopPerformers] = useState<ReturnType<typeof Array<{id:string;full_name:string;position:string;department:string;performance_score:number}>>>([])
  const [recentEvals,   setRecentEvals]   = useState<ReturnType<typeof Array<Record<string,unknown>>>>([])
  const [payrollData,   setPayrollData]   = useState<ReturnType<typeof Array<Record<string,unknown>>>>([])
  const [turnoverDept,  setTurnoverDept]  = useState<ReturnType<typeof Array<Record<string,unknown>>>>([])

  const [loadingMain,     setLoadingMain]     = useState(true)
  const [loadingInsights, setLoadingInsights] = useState(true)
  const [regenerating,    setRegenerating]    = useState(false)
  const [loadError,       setLoadError]       = useState<string | null>(null)

  // ─ Loaders ─────────────────────────────────────────────────────────────────
  const loadMain = useCallback(async () => {
    setLoadingMain(true)
    setLoadError(null)
    try {
      const [k, d, p, a, s, snap] = await Promise.all([
        hrAnalyticsService.getKPIs(),
        hrAnalyticsService.getDeptStats(),
        hrAnalyticsService.getPerfDistribution(),
        hrAnalyticsService.getAbsenceStats(),
        hrAnalyticsService.getSalaryRanges(),
        hrAnalyticsService.getMonthlySnapshots(6),
      ])
      setKpis(k); setDeptStats(d); setPerfDist(p)
      setAbsStats(a); setSalaryRanges(s); setSnapshots(snap)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro desconhecido'
      setLoadError(msg)
      toast.error('Erro ao carregar dados de RH')
    } finally {
      setLoadingMain(false)
    }
  }, [])

  const loadInsights = useCallback(async () => {
    setLoadingInsights(true)
    try {
      const ins = await hrInsightsService.getAll()
      setInsights(ins)
    } catch {
      setInsights([])
    } finally {
      setLoadingInsights(false)
    }
  }, [])

  const loadSecondary = useCallback(async () => {
    const [tp, re, pay, td] = await Promise.allSettled([
      performanceAnalyticsService.getTopPerformers(5),
      performanceAnalyticsService.getRecentEvaluations(8),
      payrollAnalyticsService.getMonthlyPayrollSummary(6),
      turnoverService.getTurnoverByDept(),
    ])
    if (tp.status  === 'fulfilled') setTopPerformers(tp.value as never)
    if (re.status  === 'fulfilled') setRecentEvals(re.value as never)
    if (pay.status === 'fulfilled') setPayrollData(pay.value as never)
    if (td.status  === 'fulfilled') setTurnoverDept(td.value as never)
  }, [])

  useEffect(() => { loadMain() }, [loadMain])
  useEffect(() => { loadInsights() }, [loadInsights])
  useEffect(() => {
    if (!loadingMain) loadSecondary()
  }, [loadingMain, loadSecondary])

  // ─ Handlers ────────────────────────────────────────────────────────────────
  const handleRefresh = async () => {
    await Promise.all([loadMain(), loadInsights()])
    await loadSecondary()
    toast.success('Dados actualizados!')
  }

  const handleRegenerateInsights = async () => {
    setRegenerating(true)
    toast.info('A gerar insights com dados actualizados…')
    try {
      const ins = await hrInsightsService.regenerate()
      setInsights(ins)
      toast.success(`${ins.length} insights gerados!`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro'
      toast.error(`Erro ao gerar insights: ${msg}`)
    } finally {
      setRegenerating(false)
    }
  }

  const handleResolveInsight = async (id: string) => {
    try {
      await hrInsightsService.resolve(id)
      setInsights(prev => prev.filter(i => i.id !== id))
      toast.success('Insight marcado como resolvido')
    } catch {
      toast.error('Erro ao resolver insight')
    }
  }

  // ─ Loading ─────────────────────────────────────────────────────────────────
  if (loadingMain) return (
    <Layout>
      <div className="space-y-6 p-6">
        <Skeleton className="h-10 w-80" />
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    </Layout>
  )

  // ─ Erro crítico ────────────────────────────────────────────────────────────
  if (loadError && !kpis) return (
    <Layout>
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 p-6">
        <div className="p-4 rounded-full bg-red-50"><AlertCircle className="h-10 w-10 text-red-500" /></div>
        <div className="text-center">
          <h2 className="text-xl font-semibold mb-1">Sem dados disponíveis</h2>
          <p className="text-muted-foreground text-sm max-w-md">
            Não foi possível carregar dados de RH. Adicione funcionários na secção Gestão de RH para ver analytics aqui.
          </p>
          <p className="text-xs text-muted-foreground mt-2 font-mono bg-muted px-3 py-1 rounded inline-block">{loadError}</p>
        </div>
        <Button onClick={loadMain} variant="outline" size="sm">
          <RefreshCw className="h-4 w-4 mr-2" />Tentar novamente
        </Button>
      </div>
    </Layout>
  )

  const highRiskCount   = insights.filter(i => i.risk_level === 'high').length
  const flightRiskCount = insights.filter(i => i.insight_type === 'FLIGHT_RISK').length

  // Dados para gráfico de tendências (snapshots)
  const trendData = snapshots.map(s => ({
    month:      s.snapshot_month,
    Retenção:   Number(s.retention_rate?.toFixed(1) ?? 0),
    Absenteísmo: Number(s.absenteeism_rate?.toFixed(1) ?? 0),
    Performance: Number(s.avg_performance?.toFixed(2) ?? 0),
    Funcionários: s.active_employees,
  }))

  // Dados para radar de métricas
  const radarData = kpis ? [
    { metric: 'Retenção',     value: Math.min(kpis.retentionRate, 100) },
    { metric: 'Desempenho',   value: kpis.avgPerformance * 20 },
    { metric: 'Estabilidade', value: 100 - kpis.turnoverRate },
    { metric: 'Presença',     value: 100 - kpis.absenteeismRate },
    { metric: 'Insights OK',  value: kpis.activeInsights > 0 ? Math.max(0, 100 - kpis.highRiskInsights * 10) : 100 },
  ] : []

  return (
    <Layout>
      <div className="space-y-6">

        {/* ── Header ─────────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">RH Avançado</h1>
            <p className="text-muted-foreground">Analytics e IA para gestão de pessoas</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              <RefreshCw className="h-4 w-4 mr-2" />Actualizar
            </Button>
            <Button size="sm" onClick={handleRegenerateInsights} disabled={regenerating}>
              {regenerating ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Brain className="h-4 w-4 mr-2" />}
              {regenerating ? 'A gerar…' : 'Gerar Insights IA'}
            </Button>
          </div>
        </div>

        {/* ── KPIs principais ────────────────────────────────────────────────── */}
        {kpis && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Total Funcionários</CardTitle>
                <Users className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{kpis.totalEmployees}</div>
                <div className="flex gap-3 mt-1">
                  <span className="text-xs text-green-600">{kpis.activeEmployees} activos</span>
                  <span className="text-xs text-blue-600">{kpis.onLeave} licença</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Taxa de Retenção</CardTitle>
                <TrendingUp className="h-4 w-4 text-green-600" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-green-600">{kpis.retentionRate.toFixed(1)}%</div>
                <Progress value={kpis.retentionRate} className="h-1.5 mt-2" />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Desempenho Médio</CardTitle>
                <Award className="h-4 w-4 text-yellow-500" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {kpis.avgPerformance > 0 ? kpis.avgPerformance.toFixed(1) : '—'}<span className="text-sm text-muted-foreground">/5</span>
                </div>
                <Progress value={kpis.avgPerformance * 20} className="h-1.5 mt-2" />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Insights IA</CardTitle>
                <Brain className="h-4 w-4 text-purple-600" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-purple-600">{kpis.activeInsights}</div>
                {highRiskCount > 0 && (
                  <p className="text-xs text-red-600 mt-1 flex items-center gap-1">
                    <AlertTriangle className="h-3 w-3" />{highRiskCount} de alto risco
                  </p>
                )}
                {highRiskCount === 0 && <p className="text-xs text-muted-foreground mt-1">Sem riscos críticos</p>}
              </CardContent>
            </Card>
          </div>
        )}

        {/* ── KPIs secundários ───────────────────────────────────────────────── */}
        {kpis && (
          <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
            {[
              { label: 'Massa Salarial', value: fmtAOA(kpis.totalSalaryMass), icon: DollarSign, color: 'text-blue-600' },
              { label: 'Salário Médio',  value: fmtAOA(kpis.avgSalary),       icon: DollarSign, color: 'text-blue-400' },
              { label: 'Absenteísmo',    value: `${kpis.absenteeismRate.toFixed(1)}%`, icon: UserMinus, color: kpis.absenteeismRate > 5 ? 'text-red-600' : 'text-muted-foreground' },
              { label: 'Turnover',       value: `${kpis.turnoverRate.toFixed(1)}%`, icon: UserMinus, color: kpis.turnoverRate > 10 ? 'text-red-600' : 'text-muted-foreground' },
              { label: 'Aus. Pendentes', value: kpis.pendingAbsences.toString(), icon: AlertTriangle, color: kpis.pendingAbsences > 0 ? 'text-orange-600' : 'text-muted-foreground' },
              { label: 'Risco Saída',    value: flightRiskCount.toString(), icon: UserMinus, color: flightRiskCount > 0 ? 'text-red-600' : 'text-green-600' },
            ].map(({ label, value, icon: Icon, color }) => (
              <Card key={label}>
                <CardContent className="pt-4">
                  <div className="flex items-center gap-2">
                    <Icon className={`h-4 w-4 ${color}`} />
                    <span className="text-xs text-muted-foreground">{label}</span>
                  </div>
                  <div className={`text-lg font-bold mt-1 ${color}`}>{value}</div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* ── Tabs ───────────────────────────────────────────────────────────── */}
        <Tabs defaultValue="overview">
          <TabsList className="grid w-full grid-cols-2 md:grid-cols-5">
            <TabsTrigger value="overview">Visão Geral</TabsTrigger>
            <TabsTrigger value="insights">
              Insights IA
              {highRiskCount > 0 && <span className="ml-1.5 bg-red-500 text-white text-xs rounded-full w-4 h-4 inline-flex items-center justify-center">{highRiskCount}</span>}
            </TabsTrigger>
            <TabsTrigger value="performance">Desempenho</TabsTrigger>
            <TabsTrigger value="payroll">Payroll</TabsTrigger>
            <TabsTrigger value="turnover">Rotatividade</TabsTrigger>
          </TabsList>

          {/* ── Visão Geral ──────────────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-6">
            <div className="grid gap-6 lg:grid-cols-2">

              {/* Distribuição por Departamento */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BarChart2 className="h-4 w-4" />Distribuição por Departamento
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {deptStats.length > 0 ? (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={deptStats} margin={{ left: 0, right: 8, top: 4, bottom: 30 }}>
                        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                        <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(v: number) => [v, 'Funcionários']} />
                        <Bar dataKey="count" fill="#3B82F6" radius={[4,4,0,0]}>
                          {deptStats.map((_, i) => <Cell key={i} fill={DEPT_COLORS[i % DEPT_COLORS.length]} />)}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">Sem dados de departamentos</div>
                  )}
                </CardContent>
              </Card>

              {/* Radar de Saúde Organizacional */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BarChart2 className="h-4 w-4" />Saúde Organizacional
                  </CardTitle>
                  <CardDescription>Métricas chave em percentagem</CardDescription>
                </CardHeader>
                <CardContent>
                  {radarData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={220}>
                      <RadarChart data={radarData}>
                        <PolarGrid />
                        <PolarAngleAxis dataKey="metric" tick={{ fontSize: 11 }} />
                        <PolarRadiusAxis domain={[0, 100]} tick={{ fontSize: 9 }} />
                        <Radar dataKey="value" stroke="#8B5CF6" fill="#8B5CF6" fillOpacity={0.25} />
                        <Tooltip formatter={(v: number) => [`${v.toFixed(1)}%`]} />
                      </RadarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">Sem dados suficientes</div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Tendências mensais */}
            {trendData.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <TrendingUp className="h-4 w-4" />Tendências de RH (últimos 6 meses)
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={trendData}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Legend />
                      <Line type="monotone" dataKey="Retenção"    stroke="#10B981" strokeWidth={2} dot={false} />
                      <Line type="monotone" dataKey="Absenteísmo" stroke="#F59E0B" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}

            {/* Ausências e Salários lado a lado */}
            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Distribuição de Ausências</CardTitle>
                  <CardDescription>Aprovadas nos últimos registos</CardDescription>
                </CardHeader>
                <CardContent>
                  {absStats.length > 0 ? (
                    <>
                      <ResponsiveContainer width="100%" height={160}>
                        <PieChart>
                          <Pie data={absStats} dataKey="count" nameKey="label" cx="50%" cy="50%" outerRadius={60} label={({ label, pct }) => `${label} (${pct.toFixed(0)}%)`}>
                            {absStats.map((_, i) => <Cell key={i} fill={DEPT_COLORS[i % DEPT_COLORS.length]} />)}
                          </Pie>
                          <Tooltip formatter={(v: number) => [v, 'Ausências']} />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="space-y-1 mt-2">
                        {absStats.slice(0,4).map((a, i) => (
                          <div key={a.type} className="flex items-center justify-between text-xs">
                            <div className="flex items-center gap-2">
                              <span className="w-2 h-2 rounded-full" style={{ background: DEPT_COLORS[i % DEPT_COLORS.length] }} />
                              <span>{a.label}</span>
                            </div>
                            <span className="text-muted-foreground">{a.count} ({a.days} dias)</span>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem ausências registadas</div>}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Distribuição Salarial (AOA)</CardTitle>
                  <CardDescription>Funcionários activos por faixa</CardDescription>
                </CardHeader>
                <CardContent>
                  {salaryRanges.length > 0 ? (
                    <div className="space-y-3 mt-2">
                      {salaryRanges.map((r, i) => (
                        <div key={r.range} className="space-y-1">
                          <div className="flex justify-between text-xs">
                            <span className="font-medium">{r.range}</span>
                            <span className="text-muted-foreground">{r.count} ({r.pct.toFixed(0)}%)</span>
                          </div>
                          <Progress value={r.pct} className="h-2" style={{ '--progress-color': DEPT_COLORS[i % DEPT_COLORS.length] } as React.CSSProperties} />
                        </div>
                      ))}
                    </div>
                  ) : <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem dados salariais</div>}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* ── Insights IA ──────────────────────────────────────────────────── */}
          <TabsContent value="insights" className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">Insights e Previsões IA</h2>
                <p className="text-sm text-muted-foreground">Gerados a partir dos dados reais de desempenho e ausências</p>
              </div>
              {insights.length > 0 && (
                <div className="flex gap-2">
                  <Badge variant="outline">{insights.filter(i => i.risk_level === 'high').length} alto risco</Badge>
                  <Badge variant="outline">{insights.filter(i => i.risk_level === 'medium').length} médio</Badge>
                  <Badge variant="outline">{insights.filter(i => i.risk_level === 'low').length} baixo</Badge>
                </div>
              )}
            </div>

            {loadingInsights ? (
              <div className="space-y-3">
                {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 w-full rounded-lg" />)}
              </div>
            ) : insights.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 gap-3">
                  <Brain className="h-12 w-12 text-muted-foreground/40" />
                  <p className="text-muted-foreground font-medium">Nenhum insight gerado ainda</p>
                  <p className="text-sm text-muted-foreground text-center max-w-sm">
                    Clique em "Gerar Insights IA" para analisar os dados actuais dos seus funcionários e obter recomendações.
                  </p>
                  <Button onClick={handleRegenerateInsights} disabled={regenerating} className="mt-2">
                    {regenerating ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Brain className="h-4 w-4 mr-2" />}
                    Gerar Insights IA
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <>
                {/* Insights de alto risco primeiro */}
                {['high', 'medium', 'low'].map(level => {
                  const group = insights.filter(i => i.risk_level === level)
                  if (group.length === 0) return null
                  return (
                    <div key={level} className="space-y-3">
                      <h3 className="text-sm font-medium text-muted-foreground uppercase tracking-wide flex items-center gap-2">
                        {level === 'high'   && <AlertTriangle className="h-3.5 w-3.5 text-red-500" />}
                        {level === 'medium' && <AlertTriangle className="h-3.5 w-3.5 text-orange-500" />}
                        {level === 'low'    && <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />}
                        {level === 'high' ? 'Alto Risco' : level === 'medium' ? 'Risco Médio' : 'Baixo Risco'}
                        <span className="text-xs bg-muted px-1.5 py-0.5 rounded">{group.length}</span>
                      </h3>
                      {group.map(ins => (
                        <InsightCard key={ins.id} insight={ins} onResolve={handleResolveInsight} />
                      ))}
                    </div>
                  )
                })}
              </>
            )}
          </TabsContent>

          {/* ── Desempenho ───────────────────────────────────────────────────── */}
          <TabsContent value="performance" className="space-y-6">
            <div className="grid gap-6 lg:grid-cols-2">

              {/* Distribuição de performance */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Distribuição de Desempenho</CardTitle>
                  <CardDescription>Funcionários activos por faixa de score (0–5)</CardDescription>
                </CardHeader>
                <CardContent>
                  {perfDist.filter(p => p.count > 0).length > 0 ? (
                    <ResponsiveContainer width="100%" height={200}>
                      <BarChart data={perfDist.filter(p => p.count > 0)}>
                        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                        <XAxis dataKey="range" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 12 }} />
                        <Tooltip formatter={(v: number, n: string) => [v, n === 'count' ? 'Funcionários' : n]} />
                        <Bar dataKey="count" radius={[4,4,0,0]}>
                          {perfDist.map((_, i) => <Cell key={i} fill={PERF_COLORS[i % PERF_COLORS.length]} />)}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  ) : <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">Sem avaliações registadas</div>}
                </CardContent>
              </Card>

              {/* Top performers */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Star className="h-4 w-4 text-yellow-500" />Top Performers
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {topPerformers.length > 0 ? (
                    <div className="space-y-3">
                      {topPerformers.map((e: {id:string;full_name:string;position:string;department:string;performance_score:number}, i) => (
                        <div key={e.id} className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-3">
                            <span className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${i === 0 ? 'bg-yellow-100 text-yellow-700' : i === 1 ? 'bg-gray-100 text-gray-700' : 'bg-orange-50 text-orange-700'}`}>
                              {i + 1}
                            </span>
                            <div>
                              <p className="text-sm font-medium">{e.full_name}</p>
                              <p className="text-xs text-muted-foreground">{e.position} · {e.department}</p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="text-sm font-bold text-green-600">{Number(e.performance_score).toFixed(1)}</p>
                            <p className="text-xs text-muted-foreground">/ 5.0</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem avaliações disponíveis</div>}
                </CardContent>
              </Card>
            </div>

            {/* Avaliações recentes */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <UserCheck className="h-4 w-4" />Avaliações Recentes
                </CardTitle>
              </CardHeader>
              <CardContent>
                {recentEvals.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-xs text-muted-foreground">
                          <th className="text-left pb-2 font-medium">Funcionário</th>
                          <th className="text-left pb-2 font-medium">Período</th>
                          <th className="text-left pb-2 font-medium">Data</th>
                          <th className="text-center pb-2 font-medium">Global</th>
                          <th className="text-center pb-2 font-medium">Produt.</th>
                          <th className="text-center pb-2 font-medium">Qualidade</th>
                          <th className="text-center pb-2 font-medium">Equipa</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(recentEvals as Array<{id:string; overall_score:number; evaluation_period:string; evaluation_date:string; productivity_score:number; quality_score:number; teamwork_score:number; employee:{full_name:string}}>).map(ev => (
                          <tr key={ev.id} className="border-b last:border-0 hover:bg-muted/30">
                            <td className="py-2">{ev.employee?.full_name ?? '—'}</td>
                            <td className="py-2 text-muted-foreground">{ev.evaluation_period}</td>
                            <td className="py-2 text-muted-foreground">{ev.evaluation_date}</td>
                            {[ev.overall_score, ev.productivity_score, ev.quality_score, ev.teamwork_score].map((s, j) => (
                              <td key={j} className="py-2 text-center">
                                <span className={`font-medium ${Number(s) >= 4 ? 'text-green-600' : Number(s) >= 3 ? 'text-yellow-600' : 'text-red-600'}`}>
                                  {s != null ? Number(s).toFixed(1) : '—'}
                                </span>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem avaliações registadas</div>}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Payroll ──────────────────────────────────────────────────────── */}
          <TabsContent value="payroll" className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <DollarSign className="h-4 w-4" />Evolução do Processamento Salarial
                </CardTitle>
                <CardDescription>Bruto vs Líquido vs Encargos (AOA)</CardDescription>
              </CardHeader>
              <CardContent>
                {payrollData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={payrollData as Array<{label:string;totalGross:number;totalNet:number;totalInss:number;totalIrt:number}>}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} />
                      <Tooltip formatter={(v: number) => fmtAOA(v)} />
                      <Legend />
                      <Bar dataKey="totalGross" name="Bruto"  fill="#3B82F6" radius={[4,4,0,0]} />
                      <Bar dataKey="totalNet"   name="Líquido" fill="#10B981" radius={[4,4,0,0]} />
                      <Bar dataKey="totalInss"  name="INSS"   fill="#F59E0B" radius={[4,4,0,0]} />
                      <Bar dataKey="totalIrt"   name="IRT"    fill="#EF4444" radius={[4,4,0,0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="flex flex-col items-center justify-center h-48 gap-3 text-muted-foreground">
                    <DollarSign className="h-10 w-10 opacity-40" />
                    <p className="text-sm">Nenhum recibo de salário processado ainda</p>
                    <p className="text-xs">Processe salários na secção Payroll para ver análises aqui</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Resumo de deduções */}
            {payrollData.length > 0 && (
              <div className="grid gap-4 md:grid-cols-3">
                {(() => {
                  const pd = payrollData as Array<{totalGross:number;totalNet:number;totalInss:number;totalIrt:number;count:number}>
                  const totG = pd.reduce((s, m) => s + m.totalGross, 0)
                  const totN = pd.reduce((s, m) => s + m.totalNet, 0)
                  const totI = pd.reduce((s, m) => s + m.totalInss, 0)
                  const totT = pd.reduce((s, m) => s + m.totalIrt, 0)
                  return [
                    { label: 'Total Bruto Processado', value: fmtAOA(totG), icon: DollarSign, color: 'text-blue-600' },
                    { label: 'Total Líquido Pago',      value: fmtAOA(totN), icon: CheckCircle2, color: 'text-green-600' },
                    { label: 'Total Encargos (INSS+IRT)',value: fmtAOA(totI + totT), icon: TrendingDown, color: 'text-red-600' },
                  ].map(({ label, value, icon: Icon, color }) => (
                    <Card key={label}>
                      <CardContent className="pt-5">
                        <div className="flex items-center gap-2 mb-1">
                          <Icon className={`h-4 w-4 ${color}`} />
                          <span className="text-xs text-muted-foreground">{label}</span>
                        </div>
                        <p className={`text-xl font-bold ${color}`}>{value}</p>
                      </CardContent>
                    </Card>
                  ))
                })()}
              </div>
            )}
          </TabsContent>

          {/* ── Rotatividade ─────────────────────────────────────────────────── */}
          <TabsContent value="turnover" className="space-y-6">
            <div className="grid gap-6 lg:grid-cols-2">

              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <UserMinus className="h-4 w-4" />Rotatividade por Departamento
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {turnoverDept.length > 0 ? (
                    <>
                      <ResponsiveContainer width="100%" height={200}>
                        <BarChart data={turnoverDept as Array<{dept:string;rate:number;total:number;left:number}>}>
                          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                          <XAxis dataKey="dept" tick={{ fontSize: 11 }} angle={-20} textAnchor="end" />
                          <YAxis tick={{ fontSize: 11 }} unit="%" />
                          <Tooltip formatter={(v: number) => [`${v.toFixed(1)}%`, 'Taxa Turnover']} />
                          <Bar dataKey="rate" radius={[4,4,0,0]}>
                            {(turnoverDept as Array<{rate:number}>).map((d, i) => (
                              <Cell key={i} fill={d.rate > 20 ? '#EF4444' : d.rate > 10 ? '#F59E0B' : '#10B981'} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                      <div className="space-y-2 mt-4">
                        {(turnoverDept as Array<{dept:string;total:number;left:number;rate:number}>).slice(0,5).map(d => (
                          <div key={d.dept} className="flex items-center gap-3">
                            <span className="text-xs text-muted-foreground w-32 truncate">{d.dept}</span>
                            <Progress value={d.rate} className="flex-1 h-2" />
                            <span className={`text-xs font-medium w-12 text-right ${d.rate > 20 ? 'text-red-600' : d.rate > 10 ? 'text-orange-600' : 'text-green-600'}`}>
                              {d.rate.toFixed(1)}%
                            </span>
                            <span className="text-xs text-muted-foreground w-16 text-right">{d.left}/{d.total}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">Sem dados de rotatividade</div>}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Departamentos por Salário Médio</CardTitle>
                </CardHeader>
                <CardContent>
                  {deptStats.length > 0 ? (
                    <div className="space-y-3">
                      {[...deptStats].sort((a, b) => b.avg_salary - a.avg_salary).slice(0,6).map((d, i) => (
                        <div key={d.name} className="space-y-1">
                          <div className="flex justify-between text-xs">
                            <div className="flex items-center gap-2">
                              <span className="w-2.5 h-2.5 rounded-full" style={{ background: DEPT_COLORS[i % DEPT_COLORS.length] }} />
                              <span className="font-medium">{d.name}</span>
                              <span className="text-muted-foreground">({d.count})</span>
                            </div>
                            <span className="text-muted-foreground">{fmtAOA(d.avg_salary)}</span>
                          </div>
                          <Progress
                            value={deptStats.length > 0 ? (d.avg_salary / Math.max(...deptStats.map(x => x.avg_salary))) * 100 : 0}
                            className="h-1.5"
                          />
                        </div>
                      ))}
                    </div>
                  ) : <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem dados salariais</div>}
                </CardContent>
              </Card>
            </div>

            {/* KPIs de turnover */}
            {kpis && (
              <div className="grid gap-4 md:grid-cols-3">
                <Card className={kpis.turnoverRate > 15 ? 'border-red-200 bg-red-50/30' : ''}>
                  <CardContent className="pt-5">
                    <p className="text-sm text-muted-foreground">Taxa de Turnover Global</p>
                    <p className={`text-3xl font-bold mt-1 ${kpis.turnoverRate > 15 ? 'text-red-600' : kpis.turnoverRate > 8 ? 'text-orange-600' : 'text-green-600'}`}>
                      {kpis.turnoverRate.toFixed(1)}%
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {kpis.turnoverRate <= 8 ? 'Saudável (< 8% recomendado)' : kpis.turnoverRate <= 15 ? 'Atenção (8-15%)' : 'Crítico (> 15%)'}
                    </p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <p className="text-sm text-muted-foreground">Funcionários Activos</p>
                    <p className="text-3xl font-bold mt-1 text-green-600">{kpis.activeEmployees}</p>
                    <p className="text-xs text-muted-foreground mt-1">{kpis.retentionRate.toFixed(1)}% de retenção</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <p className="text-sm text-muted-foreground">Desligamentos</p>
                    <p className="text-3xl font-bold mt-1">{kpis.terminated}</p>
                    <p className="text-xs text-muted-foreground mt-1">Inactivos ou desligados</p>
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>
        </Tabs>

      </div>
    </Layout>
  )
}
