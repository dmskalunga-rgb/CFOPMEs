import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import {
  TrendingUp, TrendingDown, DollarSign, Users, AlertTriangle,
  BarChart3, Target, UserCheck, Sparkles, RefreshCw,
  ArrowUpRight, ArrowDownRight, CheckCircle2, Clock,
  AlertCircle, Zap, Brain, Shield, Building2, Star,
} from 'lucide-react';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, PieChart, Pie, Cell,
} from 'recharts';
import {
  cashflowService, receivablesService, turnoverService,
  profitabilityService, collectionService, budgetService,
  leadService, churnService, customerCreditService,
} from '@/services/advancedFeaturesService';
import { supabase } from '@/integrations/supabase/client';
import { motion, AnimatePresence } from 'framer-motion';

// ─── Helpers ────────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  v >= 1_000_000
    ? `${(v / 1_000_000).toFixed(1)}M Kz`
    : v >= 1_000
    ? `${(v / 1_000).toFixed(0)}K Kz`
    : `${v.toLocaleString('pt-AO')} Kz`;

async function getTenantId(): Promise<string> {
  try {
    const { data } = await supabase.rpc('get_current_tenant_id');
    if (data) return data as string;
  } catch (_) { /* ignore */ }
  const { data: user } = await supabase.auth.getUser();
  if (user?.user) {
    const { data: profile } = await supabase
      .from('user_profiles').select('tenant_id').eq('user_id', user.user.id).single();
    if (profile?.tenant_id) return profile.tenant_id;
  }
  const { data: tenant } = await supabase.from('tenants').select('id').limit(1).single();
  return tenant?.id ?? '';
}

// ─── Risk badge colours ──────────────────────────────────────────────────────
const riskVariant = (level: string): 'destructive' | 'default' | 'secondary' | 'outline' => {
  if (level === 'CRITICAL') return 'destructive';
  if (level === 'HIGH') return 'default';
  if (level === 'MEDIUM') return 'secondary';
  return 'outline';
};

const riskColor = (level: string) => {
  if (level === 'CRITICAL') return 'text-red-600';
  if (level === 'HIGH') return 'text-orange-500';
  if (level === 'MEDIUM') return 'text-yellow-500';
  return 'text-green-500';
};

const PIE_COLORS = ['#22c55e', '#eab308', '#f97316', '#ef4444', '#dc2626'];

// ─── KPI Card ────────────────────────────────────────────────────────────────
function KpiCard({
  title, value, sub, icon: Icon, trend, color = 'text-primary',
}: {
  title: string; value: string | number; sub: string;
  icon: React.ElementType; trend?: number; color?: string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className={`h-4 w-4 ${color}`} />
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${color}`}>{value}</div>
        <div className="flex items-center gap-1 mt-1">
          {trend !== undefined && (
            trend > 0
              ? <ArrowUpRight className="h-3 w-3 text-green-500" />
              : <ArrowDownRight className="h-3 w-3 text-red-500" />
          )}
          <p className="text-xs text-muted-foreground">{sub}</p>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Section loader skeleton ─────────────────────────────────────────────────
function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-12 rounded-lg bg-muted animate-pulse" />
      ))}
    </div>
  );
}

// ─── Empty state ─────────────────────────────────────────────────────────────
function Empty({ icon: Icon, label }: { icon: React.ElementType; label: string }) {
  return (
    <div className="text-center py-12 text-muted-foreground">
      <Icon className="h-12 w-12 mx-auto mb-3 opacity-30" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════
export default function AdvancedFeatures() {
  const [activeTab, setActiveTab] = useState('overview');
  const [loading, setLoading] = useState(true);
  const [tenantId, setTenantId] = useState('');

  // Data states
  const [cashflow, setCashflow] = useState<any[]>([]);
  const [receivables, setReceivables] = useState<any>(null);
  const [turnover, setTurnover] = useState<any[]>([]);
  const [profitability, setProfitability] = useState<any[]>([]);
  const [collections, setCollections] = useState<any[]>([]);
  const [budgets, setBudgets] = useState<any[]>([]);
  const [leads, setLeads] = useState<any[]>([]);
  const [churn, setChurn] = useState<any[]>([]);
  const [credits, setCredits] = useState<any[]>([]);

  // ─── Load all data ──────────────────────────────────────────────────────
  const loadAll = useCallback(async (tid: string) => {
    setLoading(true);
    const results = await Promise.allSettled([
      cashflowService.getPredictions(tid),
      receivablesService.getLatest(tid),
      turnoverService.getPredictions(tid),
      profitabilityService.getAll(tid),
      collectionService.getWorkflows(tid),
      budgetService.getAll(tid),
      leadService.getScores(tid),
      churnService.getPredictions(tid),
      customerCreditService.getAll(tid),
    ]);
    const val = <T,>(r: PromiseSettledResult<T>, def: T) =>
      r.status === 'fulfilled' ? r.value : def;

    setCashflow(val(results[0], []) as any[]);
    setReceivables(val(results[1], null));
    setTurnover(val(results[2], []) as any[]);
    setProfitability(val(results[3], []) as any[]);
    setCollections(val(results[4], []) as any[]);
    setBudgets(val(results[5], []) as any[]);
    setLeads(val(results[6], []) as any[]);
    setChurn(val(results[7], []) as any[]);
    setCredits(val(results[8], []) as any[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    getTenantId().then((tid) => {
      setTenantId(tid);
      if (tid) loadAll(tid);
      else setLoading(false);
    });
  }, [loadAll]);

  // ─── Derived KPIs ───────────────────────────────────────────────────────
  const highRiskEmployees = turnover.filter(
    (t) => t.risk_level === 'HIGH' || t.risk_level === 'CRITICAL',
  ).length;
  const alertCashflow = cashflow.filter(
    (c) => c.risk_level === 'HIGH' || c.risk_level === 'CRITICAL',
  ).length;
  const hotLeads = leads.filter((l) => l.score_category === 'HOT').length;
  const totalReceivables = receivables?.total_receivables ?? 0;

  // Cashflow chart data
  const cfChartData = cashflow.slice(0, 7).map((c) => ({
    date: new Date(c.prediction_date).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit' }),
    entrada: Math.round(c.predicted_inflow / 1000),
    saida: Math.round(c.predicted_outflow / 1000),
    saldo: Math.round(c.predicted_balance / 1000),
  }));

  // Profitability chart
  const profitChartData = profitability.slice(0, 5).map((p) => ({
    name: p.entity_name?.split(' ').slice(0, 2).join(' ') ?? '—',
    margem: parseFloat(p.net_margin ?? 0),
    receita: Math.round((p.revenue ?? 0) / 1000),
  }));

  // Budget chart
  const budgetChartData = budgets.map((b) => ({
    name: b.department ?? '—',
    orçado: Math.round((b.budgeted_amount ?? 0) / 1000),
    real: Math.round((b.actual_amount ?? 0) / 1000),
    previsão: Math.round((b.forecast_amount ?? 0) / 1000),
  }));

  // Aging pie
  const agingData = receivables
    ? [
        { name: 'Corrente', value: receivables.current_receivables ?? 0 },
        { name: '1-30 dias', value: receivables.overdue_1_30 ?? 0 },
        { name: '31-60 dias', value: receivables.overdue_31_60 ?? 0 },
        { name: '61-90 dias', value: receivables.overdue_61_90 ?? 0 },
        { name: '+90 dias', value: receivables.overdue_90_plus ?? 0 },
      ].filter((d) => d.value > 0)
    : [];

  return (
    <Layout>
      <motion.div
        className="space-y-6"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <Sparkles className="h-8 w-8 text-primary" />
              Funcionalidades Avançadas
            </h1>
            <p className="text-muted-foreground mt-1">
              IA e automação para turbinar a sua gestão financeira
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => tenantId && loadAll(tenantId)}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            {loading ? 'Carregando...' : 'Actualizar'}
          </Button>
        </div>

        {/* ── KPI Strip ──────────────────────────────────────────────────── */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <KpiCard
            title="Previsões de Caixa"
            value={cashflow.length}
            sub={`${alertCashflow} alertas de risco`}
            icon={TrendingUp}
            color={alertCashflow > 0 ? 'text-orange-500' : 'text-primary'}
          />
          <KpiCard
            title="Funcionários em Risco"
            value={highRiskEmployees}
            sub="alto / crítico (turnover)"
            icon={AlertTriangle}
            color={highRiskEmployees > 2 ? 'text-red-600' : 'text-orange-500'}
          />
          <KpiCard
            title="Total a Receber"
            value={fmt(totalReceivables)}
            sub={`DSO: ${receivables?.dso?.toFixed(1) ?? '—'} dias`}
            icon={DollarSign}
          />
          <KpiCard
            title="Leads HOT"
            value={hotLeads}
            sub={`de ${leads.length} leads qualificados`}
            icon={Target}
            color="text-pink-500"
          />
        </div>

        {/* ── Tabs ────────────────────────────────────────────────────────── */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="overview">Visão Geral</TabsTrigger>
            <TabsTrigger value="financial">Financeiro</TabsTrigger>
            <TabsTrigger value="hr">RH</TabsTrigger>
            <TabsTrigger value="crm">CRM</TabsTrigger>
            <TabsTrigger value="budget">Orçamentos</TabsTrigger>
          </TabsList>

          {/* ════════════════════════════════════════════════════════════════
              TAB 1 — VISÃO GERAL
          ════════════════════════════════════════════════════════════════ */}
          <TabsContent value="overview" className="space-y-6">
            <AnimatePresence mode="wait">
              {loading ? (
                <Skeleton rows={4} />
              ) : (
                <motion.div
                  key="overview"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="grid gap-6 md:grid-cols-2 lg:grid-cols-3"
                >
                  {/* Cashflow Resumo */}
                  <Card className="lg:col-span-2">
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2">
                        <Brain className="h-5 w-5 text-blue-500" />
                        Previsão de Fluxo de Caixa
                      </CardTitle>
                      <CardDescription>Próximos 60 dias — entradas vs saídas (Kz 000)</CardDescription>
                    </CardHeader>
                    <CardContent>
                      {cfChartData.length > 0 ? (
                        <ResponsiveContainer width="100%" height={220}>
                          <AreaChart data={cfChartData}>
                            <defs>
                              <linearGradient id="gEnt" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
                              </linearGradient>
                              <linearGradient id="gSai" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                                <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
                              </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" className="opacity-30" />
                            <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                            <YAxis tick={{ fontSize: 11 }} />
                            <Tooltip
                              formatter={(v: number) => [`${v}K Kz`]}
                              labelStyle={{ fontWeight: 600 }}
                            />
                            <Area type="monotone" dataKey="entrada" stroke="#22c55e" fill="url(#gEnt)" name="Entrada" strokeWidth={2} />
                            <Area type="monotone" dataKey="saida" stroke="#ef4444" fill="url(#gSai)" name="Saída" strokeWidth={2} />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : (
                        <Empty icon={TrendingUp} label="Sem dados de previsão" />
                      )}
                    </CardContent>
                  </Card>

                  {/* Alertas rápidos */}
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2">
                        <Zap className="h-5 w-5 text-yellow-500" />
                        Alertas & Acções
                      </CardTitle>
                      <CardDescription>Itens que precisam de atenção</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {cashflow.filter((c) => c.risk_level === 'CRITICAL').slice(0, 2).map((c, i) => (
                        <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800">
                          <AlertCircle className="h-4 w-4 text-red-500 mt-0.5 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-red-700 dark:text-red-400">Caixa Crítico</p>
                            <p className="text-xs text-red-600 dark:text-red-500">
                              {new Date(c.prediction_date).toLocaleDateString('pt-AO')}: {fmt(c.predicted_balance)}
                            </p>
                          </div>
                        </div>
                      ))}
                      {turnover.filter((t) => t.risk_level === 'CRITICAL').slice(0, 2).map((t, i) => (
                        <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-orange-50 dark:bg-orange-950/20 border border-orange-200 dark:border-orange-800">
                          <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-orange-700 dark:text-orange-400">Turnover Crítico</p>
                            <p className="text-xs text-orange-600 dark:text-orange-500">
                              {t.employee_name}: {t.probability_of_leaving}% de saída
                            </p>
                          </div>
                        </div>
                      ))}
                      {churn.filter((c) => c.risk_level === 'CRITICAL').slice(0, 2).map((c, i) => (
                        <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-purple-50 dark:bg-purple-950/20 border border-purple-200 dark:border-purple-800">
                          <Shield className="h-4 w-4 text-purple-500 mt-0.5 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-xs font-semibold text-purple-700 dark:text-purple-400">Churn Crítico</p>
                            <p className="text-xs text-purple-600 dark:text-purple-500">
                              {c.customer_name}: {fmt(c.revenue_at_risk)} em risco
                            </p>
                          </div>
                        </div>
                      ))}
                      {cashflow.filter((c) => c.risk_level === 'CRITICAL').length === 0 &&
                        turnover.filter((t) => t.risk_level === 'CRITICAL').length === 0 &&
                        churn.filter((c) => c.risk_level === 'CRITICAL').length === 0 && (
                        <div className="flex items-center gap-3 p-3 rounded-lg bg-green-50 dark:bg-green-950/20">
                          <CheckCircle2 className="h-4 w-4 text-green-500" />
                          <p className="text-xs text-green-700 dark:text-green-400">Sem alertas críticos activos</p>
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  {/* Módulos resumo */}
                  {[
                    { icon: TrendingUp,  label: 'Previsões de Caixa',      count: cashflow.length,      color: 'text-blue-500',   bg: 'bg-blue-500/10' },
                    { icon: Users,       label: 'Análises de Turnover',     count: turnover.length,      color: 'text-orange-500', bg: 'bg-orange-500/10' },
                    { icon: DollarSign,  label: 'Workflows de Cobrança',    count: collections.length,   color: 'text-red-500',    bg: 'bg-red-500/10' },
                    { icon: BarChart3,   label: 'Análises Rentabilidade',   count: profitability.length, color: 'text-purple-500', bg: 'bg-purple-500/10' },
                    { icon: Target,      label: 'Leads Qualificados',       count: leads.length,         color: 'text-pink-500',   bg: 'bg-pink-500/10' },
                    { icon: UserCheck,   label: 'Previsões de Churn',       count: churn.length,         color: 'text-yellow-500', bg: 'bg-yellow-500/10' },
                    { icon: Building2,   label: 'Análise de Crédito',       count: credits.length,       color: 'text-teal-500',   bg: 'bg-teal-500/10' },
                    { icon: Sparkles,    label: 'Orçamentos Inteligentes',  count: budgets.length,       color: 'text-indigo-500', bg: 'bg-indigo-500/10' },
                    { icon: Star,        label: 'Módulos Activos',          count: 9,                    color: 'text-amber-500',  bg: 'bg-amber-500/10' },
                  ].map((m, idx) => (
                    <Card key={idx} className="hover:shadow-md transition-shadow">
                      <CardContent className="pt-6">
                        <div className="flex items-center gap-4">
                          <div className={`p-3 rounded-xl ${m.bg}`}>
                            <m.icon className={`h-6 w-6 ${m.color}`} />
                          </div>
                          <div>
                            <p className="text-2xl font-bold">{m.count}</p>
                            <p className="text-xs text-muted-foreground">{m.label}</p>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </TabsContent>

          {/* ════════════════════════════════════════════════════════════════
              TAB 2 — FINANCEIRO
          ════════════════════════════════════════════════════════════════ */}
          <TabsContent value="financial" className="space-y-6">
            {loading ? <Skeleton rows={5} /> : (
              <div className="space-y-6">
                {/* Cashflow + Aging */}
                <div className="grid gap-6 md:grid-cols-2">
                  {/* Cashflow detail */}
                  <Card>
                    <CardHeader>
                      <CardTitle>Previsão de Fluxo de Caixa</CardTitle>
                      <CardDescription>Próximas {cashflow.length} previsões</CardDescription>
                    </CardHeader>
                    <CardContent>
                      {cashflow.length > 0 ? (
                        <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                          {cashflow.map((c, i) => (
                            <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-muted/40 hover:bg-muted/70 transition-colors">
                              <div>
                                <p className="text-sm font-medium">
                                  {new Date(c.prediction_date).toLocaleDateString('pt-AO', { day: '2-digit', month: 'short' })}
                                </p>
                                <p className="text-xs text-muted-foreground">Confiança: {c.confidence_score}%</p>
                              </div>
                              <div className="text-right">
                                <p className={`text-sm font-bold ${c.predicted_balance >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                                  {c.predicted_balance >= 0 ? '+' : ''}{fmt(c.predicted_balance)}
                                </p>
                                <Badge variant={riskVariant(c.risk_level)} className="text-xs">
                                  {c.risk_level}
                                </Badge>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : <Empty icon={TrendingUp} label="Sem previsões disponíveis" />}
                    </CardContent>
                  </Card>

                  {/* Aging pie + detail */}
                  <Card>
                    <CardHeader>
                      <CardTitle>Análise de Recebíveis</CardTitle>
                      <CardDescription>Aging de recebíveis</CardDescription>
                    </CardHeader>
                    <CardContent>
                      {receivables ? (
                        <div className="space-y-4">
                          <div className="grid grid-cols-2 gap-4 text-center">
                            <div className="p-3 rounded-lg bg-muted/50">
                              <p className="text-xl font-bold">{fmt(receivables.total_receivables)}</p>
                              <p className="text-xs text-muted-foreground">Total a Receber</p>
                            </div>
                            <div className="p-3 rounded-lg bg-muted/50">
                              <p className="text-xl font-bold">{receivables.dso?.toFixed(1)} d</p>
                              <p className="text-xs text-muted-foreground">DSO (alvo: {receivables.dso_target})</p>
                            </div>
                          </div>
                          {agingData.length > 0 && (
                            <ResponsiveContainer width="100%" height={160}>
                              <PieChart>
                                <Pie data={agingData} cx="50%" cy="50%" innerRadius={45} outerRadius={70} dataKey="value">
                                  {agingData.map((_, idx) => (
                                    <Cell key={idx} fill={PIE_COLORS[idx]} />
                                  ))}
                                </Pie>
                                <Tooltip formatter={(v: number) => fmt(v)} />
                              </PieChart>
                            </ResponsiveContainer>
                          )}
                          <div className="space-y-1 text-sm">
                            {[
                              { label: 'Corrente (0-30d)', value: receivables.current_receivables, color: 'text-green-600' },
                              { label: '31-60 dias',       value: receivables.overdue_31_60,       color: 'text-yellow-600' },
                              { label: '61-90 dias',       value: receivables.overdue_61_90,       color: 'text-orange-600' },
                              { label: '+90 dias',         value: receivables.overdue_90_plus,     color: 'text-red-600' },
                            ].map((row, i) => (
                              <div key={i} className="flex justify-between">
                                <span className="text-muted-foreground">{row.label}</span>
                                <span className={`font-semibold ${row.color}`}>{fmt(row.value ?? 0)}</span>
                              </div>
                            ))}
                          </div>
                          <div className="pt-2 border-t">
                            <p className="text-xs text-muted-foreground">Eficácia de Cobrança</p>
                            <div className="flex items-center gap-2 mt-1">
                              <Progress value={receivables.collection_effectiveness ?? 0} className="flex-1 h-2" />
                              <span className="text-sm font-bold">{receivables.collection_effectiveness?.toFixed(1)}%</span>
                            </div>
                          </div>
                        </div>
                      ) : <Empty icon={DollarSign} label="Sem análise de recebíveis" />}
                    </CardContent>
                  </Card>
                </div>

                {/* Rentabilidade */}
                <Card>
                  <CardHeader>
                    <CardTitle>Análise de Rentabilidade</CardTitle>
                    <CardDescription>Margem líquida por cliente / departamento</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {profitability.length > 0 ? (
                      <div className="grid gap-6 md:grid-cols-2">
                        <ResponsiveContainer width="100%" height={200}>
                          <BarChart data={profitChartData} layout="vertical">
                            <CartesianGrid strokeDasharray="3 3" className="opacity-30" />
                            <XAxis type="number" tick={{ fontSize: 11 }} unit="%" />
                            <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={80} />
                            <Tooltip formatter={(v: number) => [`${v}%`, 'Margem Líquida']} />
                            <Bar dataKey="margem" fill="#8b5cf6" radius={[0, 4, 4, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                        <div className="space-y-2 max-h-52 overflow-y-auto pr-1">
                          {profitability.map((p, i) => (
                            <div key={i} className="flex items-center justify-between p-2 rounded-lg bg-muted/40">
                              <div>
                                <p className="text-sm font-medium">{p.entity_name}</p>
                                <div className="flex items-center gap-2 mt-0.5">
                                  <Badge variant="outline" className="text-xs">{p.entity_type}</Badge>
                                  <Badge
                                    variant={p.abc_classification === 'A' ? 'default' : p.abc_classification === 'B' ? 'secondary' : 'outline'}
                                    className="text-xs"
                                  >
                                    Classe {p.abc_classification}
                                  </Badge>
                                </div>
                              </div>
                              <div className="text-right">
                                <p className={`text-sm font-bold ${(p.net_margin ?? 0) > 25 ? 'text-green-600' : (p.net_margin ?? 0) > 10 ? 'text-yellow-600' : 'text-red-600'}`}>
                                  {p.net_margin?.toFixed(1)}%
                                </p>
                                <p className="text-xs text-muted-foreground">{fmt(p.revenue ?? 0)}</p>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : <Empty icon={BarChart3} label="Sem análise de rentabilidade" />}
                  </CardContent>
                </Card>

                {/* Workflows de Cobrança */}
                <Card>
                  <CardHeader>
                    <CardTitle>Workflows de Cobrança</CardTitle>
                    <CardDescription>Faturas vencidas em processo activo</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {collections.length > 0 ? (
                      <div className="space-y-2">
                        {collections.map((c, i) => (
                          <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/40 hover:bg-muted/70 transition-colors">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-medium truncate">{c.customer_id}</p>
                                <Badge variant={riskVariant(c.overdue_days > 90 ? 'CRITICAL' : c.overdue_days > 60 ? 'HIGH' : 'MEDIUM')} className="text-xs shrink-0">
                                  {c.overdue_days}d vencida
                                </Badge>
                              </div>
                              <p className="text-xs text-muted-foreground">Etapa: {c.current_stage}</p>
                            </div>
                            <div className="text-right shrink-0">
                              <p className="text-sm font-bold text-red-600">{fmt(c.invoice_amount)}</p>
                              <div className="flex items-center gap-1 justify-end mt-0.5">
                                <span className="text-xs text-muted-foreground">Sucesso:</span>
                                <span className="text-xs font-semibold">{c.success_probability}%</span>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <Empty icon={AlertTriangle} label="Sem workflows activos" />}
                  </CardContent>
                </Card>

                {/* Crédito de Clientes */}
                <Card>
                  <CardHeader>
                    <CardTitle>Análise de Crédito de Clientes</CardTitle>
                    <CardDescription>Score de crédito e exposição</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {credits.length > 0 ? (
                      <div className="space-y-2">
                        {credits.map((cr, i) => (
                          <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/40">
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate">{cr.customer_name}</p>
                              <div className="flex items-center gap-2 mt-0.5">
                                <Progress value={cr.credit_score / 10} className="w-24 h-1.5" />
                                <span className="text-xs text-muted-foreground">Score: {cr.credit_score}</span>
                              </div>
                            </div>
                            <div className="text-right shrink-0">
                              <Badge variant={riskVariant(cr.risk_level)} className="text-xs">{cr.risk_level}</Badge>
                              <p className="text-xs text-muted-foreground mt-1">
                                Saldo: {fmt(cr.outstanding_balance ?? 0)}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <Empty icon={Building2} label="Sem análises de crédito" />}
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          {/* ════════════════════════════════════════════════════════════════
              TAB 3 — RH
          ════════════════════════════════════════════════════════════════ */}
          <TabsContent value="hr" className="space-y-6">
            {loading ? <Skeleton rows={5} /> : (
              <div className="space-y-6">
                {/* KPIs turnover */}
                <div className="grid gap-4 md:grid-cols-4">
                  {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map((level) => {
                    const count = turnover.filter((t) => t.risk_level === level).length;
                    return (
                      <Card key={level}>
                        <CardContent className="pt-6">
                          <div className="text-center">
                            <p className={`text-3xl font-bold ${riskColor(level)}`}>{count}</p>
                            <p className="text-xs text-muted-foreground mt-1">Risco {level}</p>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>

                {/* Lista detalhada */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Users className="h-5 w-5 text-orange-500" />
                      Previsão de Turnover
                    </CardTitle>
                    <CardDescription>Todos os funcionários analisados, ordenados por risco</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {turnover.length > 0 ? (
                      <div className="space-y-3">
                        {turnover.map((t, i) => (
                          <motion.div
                            key={i}
                            initial={{ opacity: 0, x: -20 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: i * 0.05 }}
                            className="p-4 rounded-xl border bg-card hover:shadow-sm transition-shadow"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <p className="text-sm font-semibold">{t.employee_name}</p>
                                  <Badge variant={riskVariant(t.risk_level)} className="text-xs">{t.risk_level}</Badge>
                                  <Badge variant="outline" className="text-xs">{t.status}</Badge>
                                </div>
                                <div className="flex items-center gap-2 mt-2">
                                  <Progress value={t.turnover_risk_score ?? 0} className="flex-1 h-2" />
                                  <span className="text-xs font-bold shrink-0">{t.turnover_risk_score}/100</span>
                                </div>
                                {Array.isArray(t.key_risk_factors) && t.key_risk_factors.length > 0 && (
                                  <div className="mt-2 flex flex-wrap gap-1">
                                    {(t.key_risk_factors as string[]).slice(0, 3).map((f, fi) => (
                                      <span key={fi} className="text-xs bg-muted px-2 py-0.5 rounded-full">{f}</span>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <div className="text-right shrink-0">
                                <p className={`text-lg font-bold ${riskColor(t.risk_level)}`}>
                                  {typeof t.probability_of_leaving === 'number'
                                    ? `${parseFloat(t.probability_of_leaving.toString()).toFixed(0)}%`
                                    : '—'}
                                </p>
                                <p className="text-xs text-muted-foreground">prob. saída</p>
                                <p className="text-xs text-muted-foreground mt-1">
                                  Retenção: {fmt(t.retention_cost_estimate ?? 0)}
                                </p>
                              </div>
                            </div>
                          </motion.div>
                        ))}
                      </div>
                    ) : <Empty icon={Users} label="Sem previsões de turnover" />}
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          {/* ════════════════════════════════════════════════════════════════
              TAB 4 — CRM
          ════════════════════════════════════════════════════════════════ */}
          <TabsContent value="crm" className="space-y-6">
            {loading ? <Skeleton rows={5} /> : (
              <div className="space-y-6">
                {/* Lead Scoring */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Target className="h-5 w-5 text-pink-500" />
                      Lead Scoring
                    </CardTitle>
                    <CardDescription>Leads ordenados por pontuação de IA</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {leads.length > 0 ? (
                      <div className="space-y-3">
                        {leads.map((l, i) => (
                          <motion.div
                            key={i}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: i * 0.08 }}
                            className="p-4 rounded-xl border bg-card hover:shadow-sm transition-shadow"
                          >
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <p className="text-sm font-semibold">{l.lead_name}</p>
                                  <Badge
                                    variant={l.score_category === 'HOT' ? 'destructive' : l.score_category === 'WARM' ? 'default' : 'secondary'}
                                    className="text-xs"
                                  >
                                    {l.score_category}
                                  </Badge>
                                  <Badge variant="outline" className="text-xs">{l.status}</Badge>
                                </div>
                                <p className="text-xs text-muted-foreground mt-1">{l.lead_email}</p>
                                <div className="flex items-center gap-2 mt-2">
                                  <Progress value={l.score ?? 0} className="flex-1 h-2" />
                                  <span className="text-xs font-bold shrink-0">{l.score}/100</span>
                                </div>
                                {Array.isArray(l.recommended_actions) && (
                                  <div className="mt-2 flex flex-wrap gap-1">
                                    {(l.recommended_actions as string[]).slice(0, 2).map((a, ai) => (
                                      <span key={ai} className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">{a}</span>
                                    ))}
                                  </div>
                                )}
                              </div>
                              <div className="text-right shrink-0">
                                <p className="text-lg font-bold text-pink-500">
                                  {parseFloat((l.conversion_probability ?? 0).toString()).toFixed(0)}%
                                </p>
                                <p className="text-xs text-muted-foreground">conversão</p>
                                <p className="text-sm font-semibold mt-1">{fmt(l.estimated_value ?? 0)}</p>
                                <p className="text-xs text-muted-foreground">valor estimado</p>
                              </div>
                            </div>
                          </motion.div>
                        ))}
                      </div>
                    ) : <Empty icon={Target} label="Sem leads disponíveis" />}
                  </CardContent>
                </Card>

                {/* Churn Predictions */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <UserCheck className="h-5 w-5 text-yellow-500" />
                      Previsão de Churn
                    </CardTitle>
                    <CardDescription>Clientes em risco de abandono</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {churn.length > 0 ? (
                      <div className="space-y-3">
                        {churn.map((c, i) => (
                          <div key={i} className="flex items-start justify-between gap-4 p-4 rounded-xl border bg-card hover:shadow-sm transition-shadow">
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <p className="text-sm font-semibold">{c.customer_name}</p>
                                <Badge variant={riskVariant(c.risk_level)} className="text-xs">{c.risk_level}</Badge>
                                <Badge variant="outline" className="text-xs">{c.status}</Badge>
                              </div>
                              <div className="flex items-center gap-2 mt-2">
                                <Progress value={c.churn_risk_score ?? 0} className="flex-1 h-2" />
                                <span className="text-xs font-bold shrink-0">{c.churn_risk_score}/100</span>
                              </div>
                              {Array.isArray(c.key_risk_factors) && c.key_risk_factors.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1">
                                  {(c.key_risk_factors as string[]).slice(0, 2).map((f, fi) => (
                                    <span key={fi} className="text-xs bg-muted px-2 py-0.5 rounded-full">{f}</span>
                                  ))}
                                </div>
                              )}
                            </div>
                            <div className="text-right shrink-0">
                              <p className={`text-lg font-bold ${riskColor(c.risk_level)}`}>
                                {parseFloat((c.churn_probability ?? 0).toString()).toFixed(0)}%
                              </p>
                              <p className="text-xs text-muted-foreground">prob. churn</p>
                              <p className="text-sm font-semibold text-red-500 mt-1">{fmt(c.revenue_at_risk ?? 0)}</p>
                              <p className="text-xs text-muted-foreground">em risco</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <Empty icon={UserCheck} label="Sem previsões de churn" />}
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          {/* ════════════════════════════════════════════════════════════════
              TAB 5 — ORÇAMENTOS
          ════════════════════════════════════════════════════════════════ */}
          <TabsContent value="budget" className="space-y-6">
            {loading ? <Skeleton rows={5} /> : (
              <div className="space-y-6">
                {/* KPI totais orçamento */}
                {budgets.length > 0 && (
                  <div className="grid gap-4 md:grid-cols-3">
                    <KpiCard
                      title="Total Orçado"
                      value={fmt(budgets.reduce((s, b) => s + (b.budgeted_amount ?? 0), 0))}
                      sub="Todos os orçamentos"
                      icon={DollarSign}
                    />
                    <KpiCard
                      title="Total Real"
                      value={fmt(budgets.reduce((s, b) => s + (b.actual_amount ?? 0), 0))}
                      sub="Execução acumulada"
                      icon={BarChart3}
                      color="text-green-600"
                    />
                    <KpiCard
                      title="Variância Total"
                      value={fmt(Math.abs(budgets.reduce((s, b) => s + (b.variance ?? 0), 0)))}
                      sub={`${budgets.filter((b) => b.variance < 0).length} orçamentos em défice`}
                      icon={TrendingDown}
                      color="text-orange-500"
                    />
                  </div>
                )}

                {/* Gráfico comparativo */}
                {budgetChartData.length > 0 && (
                  <Card>
                    <CardHeader>
                      <CardTitle>Orçado vs Real vs Previsão</CardTitle>
                      <CardDescription>Por departamento (Kz 000)</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <ResponsiveContainer width="100%" height={250}>
                        <BarChart data={budgetChartData}>
                          <CartesianGrid strokeDasharray="3 3" className="opacity-30" />
                          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                          <YAxis tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(v: number) => [`${v}K Kz`]} />
                          <Bar dataKey="orçado"  fill="#6366f1" radius={[4, 4, 0, 0]} name="Orçado" />
                          <Bar dataKey="real"    fill="#22c55e" radius={[4, 4, 0, 0]} name="Real" />
                          <Bar dataKey="previsão" fill="#f59e0b" radius={[4, 4, 0, 0]} name="Previsão" />
                        </BarChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>
                )}

                {/* Lista detalhada */}
                <Card>
                  <CardHeader>
                    <CardTitle>Orçamentos Inteligentes</CardTitle>
                    <CardDescription>Sugeridos por IA com análise de variância</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {budgets.length > 0 ? (
                      <div className="space-y-3">
                        {budgets.map((b, i) => (
                          <div key={i} className="p-4 rounded-xl border bg-card hover:shadow-sm transition-shadow">
                            <div className="flex items-start justify-between gap-4">
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                  <p className="text-sm font-semibold">{b.budget_name}</p>
                                  <Badge
                                    variant={b.approval_status === 'APPROVED' ? 'secondary' : 'outline'}
                                    className="text-xs"
                                  >
                                    {b.approval_status === 'APPROVED' ? (
                                      <span className="flex items-center gap-1"><CheckCircle2 className="h-3 w-3" />{b.approval_status}</span>
                                    ) : (
                                      <span className="flex items-center gap-1"><Clock className="h-3 w-3" />{b.approval_status}</span>
                                    )}
                                  </Badge>
                                  <Badge variant="outline" className="text-xs">{b.budget_type}</Badge>
                                </div>
                                <p className="text-xs text-muted-foreground mt-1">{b.department} · {b.category}</p>
                                <div className="grid grid-cols-3 gap-3 mt-3 text-xs">
                                  <div>
                                    <p className="text-muted-foreground">Orçado</p>
                                    <p className="font-semibold">{fmt(b.budgeted_amount ?? 0)}</p>
                                  </div>
                                  <div>
                                    <p className="text-muted-foreground">Real</p>
                                    <p className="font-semibold text-green-600">{fmt(b.actual_amount ?? 0)}</p>
                                  </div>
                                  <div>
                                    <p className="text-muted-foreground">IA Sugere</p>
                                    <p className="font-semibold text-indigo-600">{fmt(b.ai_suggested_amount ?? 0)}</p>
                                  </div>
                                </div>
                                <div className="mt-2 flex items-center gap-2">
                                  <span className="text-xs text-muted-foreground">Confiança IA:</span>
                                  <Progress value={b.ai_confidence ?? 0} className="flex-1 h-1.5" />
                                  <span className="text-xs font-bold">{b.ai_confidence}%</span>
                                </div>
                              </div>
                              <div className="text-right shrink-0">
                                <p className={`text-base font-bold ${(b.variance_percentage ?? 0) < -20 ? 'text-red-600' : (b.variance_percentage ?? 0) < -10 ? 'text-orange-500' : 'text-green-600'}`}>
                                  {(b.variance_percentage ?? 0).toFixed(1)}%
                                </p>
                                <p className="text-xs text-muted-foreground">variância</p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : <Empty icon={Sparkles} label="Sem orçamentos inteligentes" />}
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
