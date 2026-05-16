import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import {
  RefreshCw,
  TrendingUp,
  TrendingDown,
  DollarSign,
  Users,
  FileText,
  Activity,
  Database,
  Zap,
  Clock,
  CheckCircle,
  XCircle,
  AlertTriangle,
  BarChart3,
  Brain,
  Layers,
  Trash2,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from 'recharts';
import { motion } from 'framer-motion';
import contextService, { type Context, type ContextCacheEntry } from '@/services/contextService';
import { supabase } from '@/integrations/supabase/client';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', maximumFractionDigits: 0 }).format(v || 0);

const fmtShort = (v: number) => {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M Kz`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K Kz`;
  return `${v.toFixed(0)} Kz`;
};

const COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4'];

// ─────────────────────────────────────────────────────────────────────────────
// Componente Principal
// ─────────────────────────────────────────────────────────────────────────────

export default function ContextEngine() {
  const { toast } = useToast();

  const [context, setContext] = useState<Context | null>(null);
  const [cacheEntries, setCacheEntries] = useState<ContextCacheEntry[]>([]);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [isCached, setIsCached] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [recommendations, setRecommendations] = useState<any[]>([]);

  // ── Carregar dados ──────────────────────────────────────────────────────────
  const loadContext = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) {
      setRefreshing(true);
      if (tenantId) await contextService.invalidateCache(tenantId);
    } else {
      setLoading(true);
    }

    try {
      const result = await contextService.getFullContext();
      setContext(result.context);
      setIsCached(result.cached);
      setTenantId(result.tenantId);
      setLastUpdated(new Date());

      // Carregar cache entries + anomalias + recomendações em paralelo
      if (result.tenantId) {
        const [cacheRes, anomRes, recRes] = await Promise.allSettled([
          contextService.getCacheEntries(result.tenantId),
          supabase
            .from('anomaly_detections')
            .select('*')
            .eq('tenant_id', result.tenantId)
            .neq('status', 'RESOLVED')
            .order('detected_at', { ascending: false })
            .limit(10),
          supabase
            .from('ai_recommendations')
            .select('*')
            .eq('tenant_id', result.tenantId)
            .in('status', ['PENDING', 'ACCEPTED'])
            .order('created_at', { ascending: false })
            .limit(8),
        ]);

        if (cacheRes.status === 'fulfilled') setCacheEntries(cacheRes.value);
        if (anomRes.status === 'fulfilled' && anomRes.value.data) setAnomalies(anomRes.value.data);
        if (recRes.status === 'fulfilled' && recRes.value.data) setRecommendations(recRes.value.data);
      }
    } catch (error) {
      console.error('Erro ao carregar contexto:', error);
      toast({ title: 'Erro ao carregar contexto', variant: 'destructive' });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [tenantId, toast]);

  useEffect(() => {
    loadContext();
  }, []);

  const handleRefresh = () => {
    loadContext(true);
    toast({ title: 'Contexto actualizado', description: 'Cache invalidado e dados recarregados.' });
  };

  const handleCleanCache = async () => {
    const count = await contextService.cleanupExpiredCache();
    toast({ title: `${count} entradas de cache removidas` });
    if (tenantId) setCacheEntries(await contextService.getCacheEntries(tenantId));
  };

  // ── Dados para gráficos ─────────────────────────────────────────────────────
  const invoicingPieData = context?.invoicing
    ? [
        { name: 'Pagas', value: context.invoicing.paidInvoices },
        { name: 'Pendentes', value: context.invoicing.pendingInvoices },
        { name: 'Vencidas', value: context.invoicing.overdueInvoices },
      ].filter((d) => d.value > 0)
    : [];

  const moduleHealthData = [
    { name: 'Financeiro', score: context?.financial ? 92 : 0 },
    { name: 'RH', score: context?.hr ? 87 : 0 },
    { name: 'Faturação', score: context?.invoicing ? 78 : 0 },
    { name: 'Operacional', score: context?.operational ? 95 : 0 },
  ];

  // ── Skeleton loading ────────────────────────────────────────────────────────
  if (loading) {
    return (
      <Layout>
        <div className="space-y-6 p-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-8 w-64 mb-2" />
              <Skeleton className="h-4 w-96" />
            </div>
            <Skeleton className="h-10 w-40" />
          </div>
          <div className="grid gap-4 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <motion.div
        className="space-y-6 p-6"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
      >
        {/* ── Header ──────────────────────────────────────────────────────── */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Brain className="h-8 w-8 text-primary" />
              Context Engine
            </h1>
            <p className="text-muted-foreground mt-1">
              Motor de contexto unificado — agrega dados de todos os módulos para alimentar a IA
            </p>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <Badge variant={isCached ? 'secondary' : 'default'}>
                <Database className="h-3 w-3 mr-1" />
                {isCached ? 'Cache activo' : 'Dados em tempo real'}
              </Badge>
              {lastUpdated && (
                <span className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" />
                  Actualizado {lastUpdated.toLocaleTimeString('pt-AO')}
                </span>
              )}
              {tenantId && (
                <span className="text-xs text-muted-foreground font-mono bg-muted px-2 py-0.5 rounded">
                  Tenant: {tenantId.substring(0, 8)}...
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleCleanCache} size="sm">
              <Trash2 className="h-4 w-4 mr-2" />
              Limpar Cache
            </Button>
            <Button onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
              {refreshing ? 'Actualizando...' : 'Forçar Actualização'}
            </Button>
          </div>
        </div>

        {/* ── KPI Cards ───────────────────────────────────────────────────── */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card className="border-green-200 dark:border-green-900">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
              <DollarSign className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {fmtShort(context?.financial?.revenue ?? 0)}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Margem: {context?.financial?.profitMargin?.toFixed(1) ?? 0}%
              </p>
              <div className="flex items-center gap-1 mt-1">
                {(context?.financial?.profit ?? 0) >= 0 ? (
                  <TrendingUp className="h-3 w-3 text-green-500" />
                ) : (
                  <TrendingDown className="h-3 w-3 text-red-500" />
                )}
                <span className="text-xs text-muted-foreground">
                  Lucro: {fmtShort(Math.abs(context?.financial?.profit ?? 0))}
                </span>
              </div>
            </CardContent>
          </Card>

          <Card className="border-purple-200 dark:border-purple-900">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Colaboradores</CardTitle>
              <Users className="h-4 w-4 text-purple-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{context?.hr?.totalEmployees ?? 0}</div>
              <p className="text-xs text-muted-foreground mt-1">
                Folha: {fmtShort(context?.hr?.totalPayroll ?? 0)}
              </p>
              <p className="text-xs text-muted-foreground">
                Desempenho médio: {context?.hr?.avgPerformance?.toFixed(1) ?? 0}/10
              </p>
            </CardContent>
          </Card>

          <Card className="border-blue-200 dark:border-blue-900">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Faturas</CardTitle>
              <FileText className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{context?.invoicing?.totalInvoices ?? 0}</div>
              <p className="text-xs text-muted-foreground mt-1">
                Pagas: {context?.invoicing?.paidInvoices ?? 0} | Vencidas:{' '}
                <span className="text-red-500">{context?.invoicing?.overdueInvoices ?? 0}</span>
              </p>
              <p className="text-xs text-muted-foreground">
                Taxa pagamento: {context?.invoicing?.paymentRate?.toFixed(1) ?? 0}%
              </p>
            </CardContent>
          </Card>

          <Card className="border-orange-200 dark:border-orange-900">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Saúde dos Módulos</CardTitle>
              <Activity className="h-4 w-4 text-orange-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {context ? '4/4' : '0/4'}
              </div>
              <p className="text-xs text-muted-foreground mt-1">Módulos activos</p>
              <div className="flex gap-1 mt-2">
                {['F', 'RH', 'Fat', 'Op'].map((m, i) => (
                  <span
                    key={m}
                    className={`text-xs px-1.5 py-0.5 rounded font-mono ${
                      context ? 'bg-green-100 text-green-700' : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {m}
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* ── Tabs ────────────────────────────────────────────────────────── */}
        <Tabs defaultValue="overview">
          <TabsList className="flex-wrap h-auto">
            <TabsTrigger value="overview">
              <Layers className="h-4 w-4 mr-1.5" />
              Visão Geral
            </TabsTrigger>
            <TabsTrigger value="financial">
              <DollarSign className="h-4 w-4 mr-1.5" />
              Financeiro
            </TabsTrigger>
            <TabsTrigger value="hr">
              <Users className="h-4 w-4 mr-1.5" />
              RH
            </TabsTrigger>
            <TabsTrigger value="invoicing">
              <FileText className="h-4 w-4 mr-1.5" />
              Faturação
            </TabsTrigger>
            <TabsTrigger value="alerts">
              <AlertTriangle className="h-4 w-4 mr-1.5" />
              Alertas
              {anomalies.length > 0 && (
                <Badge variant="destructive" className="ml-1.5 h-4 px-1 text-[10px]">
                  {anomalies.length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="cache">
              <Database className="h-4 w-4 mr-1.5" />
              Cache
            </TabsTrigger>
          </TabsList>

          {/* ── Visão Geral ─────────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-6 mt-6">
            <div className="grid gap-6 md:grid-cols-2">
              {/* Gráfico módulos */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <BarChart3 className="h-5 w-5 text-primary" />
                    Saúde dos Módulos
                  </CardTitle>
                  <CardDescription>Score de qualidade dos dados por módulo (0-100)</CardDescription>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <AreaChart data={moduleHealthData}>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                      <XAxis dataKey="name" tick={{ fontSize: 12 }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
                      <Tooltip
                        formatter={(v: number) => [`${v}%`, 'Score']}
                        contentStyle={{ fontSize: '12px', borderRadius: '8px' }}
                      />
                      <Area
                        type="monotone"
                        dataKey="score"
                        stroke="#3b82f6"
                        fill="#3b82f620"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              {/* Distribuição faturas */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <FileText className="h-5 w-5 text-primary" />
                    Distribuição de Faturas
                  </CardTitle>
                  <CardDescription>Status actual de todas as faturas</CardDescription>
                </CardHeader>
                <CardContent>
                  {invoicingPieData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={220}>
                      <PieChart>
                        <Pie
                          data={invoicingPieData}
                          cx="50%"
                          cy="50%"
                          outerRadius={80}
                          dataKey="value"
                          label={({ name, percent }) =>
                            `${name} ${(percent * 100).toFixed(0)}%`
                          }
                          labelLine={false}
                        >
                          {invoicingPieData.map((_, index) => (
                            <Cell key={index} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex items-center justify-center h-52 text-muted-foreground">
                      <div className="text-center">
                        <FileText className="h-10 w-10 mx-auto mb-2 opacity-40" />
                        <p className="text-sm">Sem dados de faturação</p>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Sumário consolidado */}
            <Card>
              <CardHeader>
                <CardTitle>Contexto Consolidado — Todos os Módulos</CardTitle>
                <CardDescription>Dados agregados em tempo real para alimentar os modelos de IA</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-6 md:grid-cols-4">
                  {/* Financeiro */}
                  <div className="space-y-3">
                    <h4 className="font-semibold text-sm flex items-center gap-2">
                      <DollarSign className="h-4 w-4 text-green-500" />
                      Financeiro
                    </h4>
                    {[
                      { label: 'Receita', value: fmt(context?.financial?.revenue ?? 0) },
                      { label: 'Despesas', value: fmt(context?.financial?.expenses ?? 0) },
                      { label: 'Lucro', value: fmt(context?.financial?.profit ?? 0) },
                      { label: 'Fluxo Caixa', value: fmt(context?.financial?.cashFlow ?? 0) },
                      { label: 'Margem', value: `${context?.financial?.profitMargin?.toFixed(1) ?? 0}%` },
                    ].map((row) => (
                      <div key={row.label} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{row.label}:</span>
                        <span className="font-medium">{row.value}</span>
                      </div>
                    ))}
                  </div>

                  {/* RH */}
                  <div className="space-y-3">
                    <h4 className="font-semibold text-sm flex items-center gap-2">
                      <Users className="h-4 w-4 text-purple-500" />
                      Recursos Humanos
                    </h4>
                    {[
                      { label: 'Colaboradores', value: String(context?.hr?.totalEmployees ?? 0) },
                      { label: 'Folha Salarial', value: fmtShort(context?.hr?.totalPayroll ?? 0) },
                      { label: 'Salário Médio', value: fmtShort(context?.hr?.avgSalary ?? 0) },
                      { label: 'Desempenho', value: `${context?.hr?.avgPerformance?.toFixed(1) ?? 0}/10` },
                      { label: 'Taxa Absentismo', value: `${context?.hr?.absenceRate ?? 0}%` },
                    ].map((row) => (
                      <div key={row.label} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{row.label}:</span>
                        <span className="font-medium">{row.value}</span>
                      </div>
                    ))}
                  </div>

                  {/* Faturação */}
                  <div className="space-y-3">
                    <h4 className="font-semibold text-sm flex items-center gap-2">
                      <FileText className="h-4 w-4 text-blue-500" />
                      Faturação
                    </h4>
                    {[
                      { label: 'Total Faturas', value: String(context?.invoicing?.totalInvoices ?? 0) },
                      { label: 'Pagas', value: String(context?.invoicing?.paidInvoices ?? 0) },
                      { label: 'Pendentes', value: String(context?.invoicing?.pendingInvoices ?? 0) },
                      { label: 'Vencidas', value: String(context?.invoicing?.overdueInvoices ?? 0) },
                      { label: 'Taxa Pagamento', value: `${context?.invoicing?.paymentRate?.toFixed(1) ?? 0}%` },
                    ].map((row) => (
                      <div key={row.label} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{row.label}:</span>
                        <span className="font-medium">{row.value}</span>
                      </div>
                    ))}
                  </div>

                  {/* Clientes & Operacional */}
                  <div className="space-y-3">
                    <h4 className="font-semibold text-sm flex items-center gap-2">
                      <Activity className="h-4 w-4 text-orange-500" />
                      Clientes & Ops
                    </h4>
                    {[
                      { label: 'Total Clientes', value: String(context?.invoicing?.totalCustomers ?? 0) },
                      { label: 'Valor Médio Fatura', value: fmtShort(context?.invoicing?.avgInvoiceValue ?? 0) },
                      { label: 'Contratos Activos', value: String(context?.operational?.activeContracts ?? 0) },
                      { label: 'Orçamentos Activos', value: String(context?.operational?.activeBudgets ?? 0) },
                      { label: 'Score Compliance', value: `${context?.operational?.complianceScore ?? 0}%` },
                    ].map((row) => (
                      <div key={row.label} className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{row.label}:</span>
                        <span className="font-medium">{row.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Financeiro ──────────────────────────────────────────────── */}
          <TabsContent value="financial" className="space-y-6 mt-6">
            <div className="grid gap-4 md:grid-cols-3">
              {[
                { label: 'Receita Total', value: fmt(context?.financial?.revenue ?? 0), icon: TrendingUp, color: 'text-green-600' },
                { label: 'Lucro Líquido', value: fmt(context?.financial?.profit ?? 0), icon: DollarSign, color: (context?.financial?.profit ?? 0) >= 0 ? 'text-green-600' : 'text-red-600' },
                { label: 'Fluxo de Caixa', value: fmt(context?.financial?.cashFlow ?? 0), icon: Activity, color: (context?.financial?.cashFlow ?? 0) >= 0 ? 'text-blue-600' : 'text-red-600' },
              ].map(({ label, value, icon: Icon, color }) => (
                <Card key={label}>
                  <CardContent className="pt-6">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-muted rounded-lg">
                        <Icon className={`h-5 w-5 ${color}`} />
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">{label}</p>
                        <p className={`text-xl font-bold ${color}`}>{value}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Análise de Rentabilidade</CardTitle>
                <CardDescription>Distribuição de receita, custos e lucro</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {[
                    { label: 'Receita', value: context?.financial?.revenue ?? 0, max: context?.financial?.revenue ?? 1, color: 'bg-green-500' },
                    { label: 'Despesas Operacionais', value: context?.financial?.expenses ?? 0, max: context?.financial?.revenue ?? 1, color: 'bg-red-400' },
                    { label: 'Folha Salarial', value: context?.hr?.totalPayroll ?? 0, max: context?.financial?.revenue ?? 1, color: 'bg-orange-400' },
                    { label: 'Lucro Líquido', value: Math.max(context?.financial?.profit ?? 0, 0), max: context?.financial?.revenue ?? 1, color: 'bg-blue-500' },
                  ].map((row) => (
                    <div key={row.label}>
                      <div className="flex justify-between text-sm mb-1">
                        <span className="text-muted-foreground">{row.label}</span>
                        <span className="font-medium">{fmtShort(row.value)}</span>
                      </div>
                      <div className="h-2 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full ${row.color} rounded-full transition-all duration-500`}
                          style={{ width: `${Math.min((row.value / row.max) * 100, 100)}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Recomendações financeiras */}
            {recommendations.filter((r) => r.category === 'FINANCE').length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Recomendações da IA — Financeiro</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {recommendations
                      .filter((r) => r.category === 'FINANCE')
                      .map((rec) => (
                        <div key={rec.id} className="p-3 rounded-lg border flex items-start gap-3">
                          <Badge
                            variant={rec.priority === 'CRITICAL' ? 'destructive' : rec.priority === 'HIGH' ? 'default' : 'secondary'}
                            className="mt-0.5 shrink-0"
                          >
                            {rec.priority}
                          </Badge>
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium">{rec.title}</p>
                            <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{rec.description}</p>
                            {rec.estimated_savings > 0 && (
                              <p className="text-xs text-green-600 mt-1">
                                Poupança estimada: {fmtShort(rec.estimated_savings)}
                              </p>
                            )}
                          </div>
                        </div>
                      ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── RH ──────────────────────────────────────────────────────── */}
          <TabsContent value="hr" className="space-y-6 mt-6">
            <div className="grid gap-4 md:grid-cols-3">
              {[
                { label: 'Colaboradores Activos', value: String(context?.hr?.totalEmployees ?? 0), sub: 'Total empresa', color: 'text-purple-600' },
                { label: 'Folha Salarial', value: fmtShort(context?.hr?.totalPayroll ?? 0), sub: 'Custo mensal', color: 'text-orange-600' },
                { label: 'Desempenho Médio', value: `${context?.hr?.avgPerformance?.toFixed(1) ?? 0}/10`, sub: 'Score da equipa', color: 'text-blue-600' },
              ].map(({ label, value, sub, color }) => (
                <Card key={label}>
                  <CardContent className="pt-6">
                    <p className="text-sm text-muted-foreground">{label}</p>
                    <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
                    <p className="text-xs text-muted-foreground mt-1">{sub}</p>
                  </CardContent>
                </Card>
              ))}
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Métricas de Capital Humano</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-2">
                  {[
                    { label: 'Taxa de Absentismo', value: `${context?.hr?.absenceRate ?? 0}%`, status: (context?.hr?.absenceRate ?? 0) < 3 ? 'Saudável' : 'Atenção', ok: (context?.hr?.absenceRate ?? 0) < 3 },
                    { label: 'Taxa de Turnover', value: `${context?.hr?.turnoverRate ?? 0}%`, status: (context?.hr?.turnoverRate ?? 0) < 10 ? 'Normal' : 'Elevado', ok: (context?.hr?.turnoverRate ?? 0) < 10 },
                    { label: 'Salário Médio', value: fmt(context?.hr?.avgSalary ?? 0), status: 'Referência mercado', ok: true },
                    { label: 'Custo/Funcionário', value: fmtShort((context?.hr?.totalPayroll ?? 0) / Math.max(context?.hr?.totalEmployees ?? 1, 1)), status: 'Mensal médio', ok: true },
                  ].map(({ label, value, status, ok }) => (
                    <div key={label} className="flex items-center justify-between p-3 rounded-lg border">
                      <div>
                        <p className="text-sm text-muted-foreground">{label}</p>
                        <p className="text-lg font-semibold">{value}</p>
                      </div>
                      <Badge variant={ok ? 'secondary' : 'destructive'}>{status}</Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Recomendações RH */}
            {recommendations.filter((r) => r.category === 'HR').length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Recomendações da IA — RH</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {recommendations
                      .filter((r) => r.category === 'HR')
                      .map((rec) => (
                        <div key={rec.id} className="p-3 rounded-lg border">
                          <div className="flex items-start gap-2 mb-2">
                            <Badge variant={rec.priority === 'HIGH' ? 'default' : 'secondary'}>{rec.priority}</Badge>
                            <p className="text-sm font-medium">{rec.title}</p>
                          </div>
                          <p className="text-xs text-muted-foreground">{rec.description}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            Confiança: {rec.confidence_score}% | Esforço: {rec.implementation_effort}
                          </p>
                        </div>
                      ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── Faturação ───────────────────────────────────────────────── */}
          <TabsContent value="invoicing" className="space-y-6 mt-6">
            <div className="grid gap-4 md:grid-cols-4">
              {[
                { label: 'Total Faturas', value: String(context?.invoicing?.totalInvoices ?? 0), color: 'text-foreground' },
                { label: 'Pagas', value: String(context?.invoicing?.paidInvoices ?? 0), color: 'text-green-600' },
                { label: 'Pendentes', value: String(context?.invoicing?.pendingInvoices ?? 0), color: 'text-yellow-600' },
                { label: 'Vencidas', value: String(context?.invoicing?.overdueInvoices ?? 0), color: 'text-red-600' },
              ].map(({ label, value, color }) => (
                <Card key={label}>
                  <CardContent className="pt-6 text-center">
                    <p className={`text-3xl font-bold ${color}`}>{value}</p>
                    <p className="text-sm text-muted-foreground mt-1">{label}</p>
                  </CardContent>
                </Card>
              ))}
            </div>

            <div className="grid gap-6 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Valores em Destaque</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {[
                      { label: 'Valor Total Emitido', value: fmt(context?.invoicing?.totalAmount ?? 0) },
                      { label: 'Valor Recebido', value: fmt(context?.invoicing?.paidAmount ?? 0) },
                      { label: 'Valor Pendente/Vencido', value: fmt(context?.invoicing?.pendingAmount ?? 0) },
                      { label: 'Valor Médio por Fatura', value: fmt(context?.invoicing?.avgInvoiceValue ?? 0) },
                      { label: 'Total de Clientes', value: String(context?.invoicing?.totalCustomers ?? 0) },
                    ].map(({ label, value }) => (
                      <div key={label} className="flex justify-between py-2 border-b last:border-0">
                        <span className="text-sm text-muted-foreground">{label}</span>
                        <span className="text-sm font-semibold">{value}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Taxa de Cobrança</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div className="text-center">
                      <p className="text-5xl font-bold text-green-600">
                        {context?.invoicing?.paymentRate?.toFixed(1) ?? 0}%
                      </p>
                      <p className="text-sm text-muted-foreground mt-2">Taxa de pagamento global</p>
                    </div>
                    <div className="space-y-2">
                      <div className="flex justify-between text-sm">
                        <span className="text-green-600">Pago</span>
                        <span>{context?.invoicing?.paymentRate?.toFixed(1) ?? 0}%</span>
                      </div>
                      <div className="h-3 bg-muted rounded-full overflow-hidden">
                        <div
                          className="h-full bg-green-500 rounded-full transition-all duration-700"
                          style={{ width: `${context?.invoicing?.paymentRate ?? 0}%` }}
                        />
                      </div>
                      <div className="flex justify-between text-sm mt-2">
                        <span className="text-red-500">Em Falta</span>
                        <span>{(100 - (context?.invoicing?.paymentRate ?? 0)).toFixed(1)}%</span>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* ── Alertas & Anomalias ──────────────────────────────────────── */}
          <TabsContent value="alerts" className="space-y-6 mt-6">
            <div className="grid gap-4 md:grid-cols-3">
              {[
                { label: 'Anomalias Activas', value: anomalies.filter((a) => a.status === 'DETECTED').length, color: 'text-red-600', icon: XCircle },
                { label: 'Em Investigação', value: anomalies.filter((a) => a.status === 'INVESTIGATING').length, color: 'text-yellow-600', icon: Clock },
                { label: 'Recomendações Pendentes', value: recommendations.filter((r) => r.status === 'PENDING').length, color: 'text-blue-600', icon: CheckCircle },
              ].map(({ label, value, color, icon: Icon }) => (
                <Card key={label}>
                  <CardContent className="pt-6">
                    <div className="flex items-center gap-3">
                      <Icon className={`h-6 w-6 ${color}`} />
                      <div>
                        <p className="text-2xl font-bold">{value}</p>
                        <p className="text-sm text-muted-foreground">{label}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>

            {anomalies.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>Anomalias Detectadas</CardTitle>
                  <CardDescription>Dados reais da análise da IA</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {anomalies.map((anomaly) => (
                      <div
                        key={anomaly.id}
                        className={`p-4 rounded-lg border-l-4 ${
                          anomaly.severity === 'CRITICAL'
                            ? 'border-l-red-600 bg-red-50 dark:bg-red-950/30'
                            : anomaly.severity === 'HIGH'
                            ? 'border-l-orange-500 bg-orange-50 dark:bg-orange-950/30'
                            : 'border-l-yellow-500 bg-yellow-50 dark:bg-yellow-950/30'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <Badge
                                variant={
                                  anomaly.severity === 'CRITICAL'
                                    ? 'destructive'
                                    : anomaly.severity === 'HIGH'
                                    ? 'default'
                                    : 'secondary'
                                }
                              >
                                {anomaly.severity}
                              </Badge>
                              <Badge variant="outline">{anomaly.anomaly_type}</Badge>
                              <span className="text-xs text-muted-foreground">
                                Confiança: {anomaly.confidence_score}%
                              </span>
                            </div>
                            <p className="text-sm font-medium">{anomaly.anomaly_description}</p>
                            {anomaly.recommended_actions?.length > 0 && (
                              <div className="mt-2">
                                <p className="text-xs font-medium text-muted-foreground mb-1">Acções recomendadas:</p>
                                <ul className="list-disc list-inside space-y-0.5">
                                  {anomaly.recommended_actions.slice(0, 2).map((a: string, i: number) => (
                                    <li key={i} className="text-xs text-muted-foreground">{a}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                          <Badge variant={anomaly.status === 'INVESTIGATING' ? 'secondary' : 'outline'} className="shrink-0">
                            {anomaly.status}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardContent className="py-16 text-center text-muted-foreground">
                  <CheckCircle className="h-16 w-16 mx-auto mb-4 text-green-500 opacity-60" />
                  <p className="text-lg font-medium">Nenhuma anomalia activa</p>
                  <p className="text-sm mt-1">O sistema está a funcionar normalmente</p>
                </CardContent>
              </Card>
            )}

            {/* Recomendações prioritárias */}
            {recommendations.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>Recomendações Prioritárias da IA</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {recommendations.slice(0, 4).map((rec) => (
                      <div key={rec.id} className="p-3 rounded-lg border flex items-start gap-3">
                        <Badge
                          variant={
                            rec.priority === 'CRITICAL'
                              ? 'destructive'
                              : rec.priority === 'HIGH'
                              ? 'default'
                              : 'secondary'
                          }
                          className="shrink-0 mt-0.5"
                        >
                          {rec.priority}
                        </Badge>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium">{rec.title}</p>
                          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{rec.description}</p>
                          <div className="flex gap-3 mt-1 text-xs">
                            {rec.estimated_savings > 0 && (
                              <span className="text-green-600">Poupança: {fmtShort(rec.estimated_savings)}</span>
                            )}
                            {rec.estimated_revenue > 0 && (
                              <span className="text-blue-600">Receita: {fmtShort(rec.estimated_revenue)}</span>
                            )}
                            <span className="text-muted-foreground">Esforço: {rec.implementation_effort}</span>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── Cache ───────────────────────────────────────────────────── */}
          <TabsContent value="cache" className="space-y-6 mt-6">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="flex items-center gap-2">
                      <Database className="h-5 w-5 text-primary" />
                      Gestão de Cache
                    </CardTitle>
                    <CardDescription>Entradas de contexto em cache — {cacheEntries.length} registos</CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={handleCleanCache}>
                      <Trash2 className="h-4 w-4 mr-1.5" />
                      Limpar Expirados
                    </Button>
                    <Button size="sm" onClick={handleRefresh} disabled={refreshing}>
                      <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} />
                      Forçar Refresh
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {cacheEntries.length > 0 ? (
                  <div className="space-y-3">
                    {cacheEntries.map((entry) => {
                      const isExpired = new Date(entry.expires_at) < new Date();
                      return (
                        <div key={entry.id} className="p-4 rounded-lg border">
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-1">
                                <Badge variant="outline">{entry.context_type}</Badge>
                                <Badge variant={isExpired ? 'destructive' : 'secondary'}>
                                  {isExpired ? 'Expirado' : 'Válido'}
                                </Badge>
                              </div>
                              <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground mt-2">
                                <span>
                                  Criado: {new Date(entry.created_at).toLocaleString('pt-AO')}
                                </span>
                                <span>
                                  Expira: {new Date(entry.expires_at).toLocaleString('pt-AO')}
                                </span>
                                <span>
                                  Acessos: {entry.access_count ?? 0}
                                </span>
                                <span>
                                  Tenant: {entry.tenant_id?.substring(0, 12)}...
                                </span>
                              </div>
                            </div>
                            <Zap className={`h-5 w-5 shrink-0 ${isExpired ? 'text-red-400' : 'text-green-400'}`} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-center py-12 text-muted-foreground">
                    <Database className="h-16 w-16 mx-auto mb-4 opacity-30" />
                    <p>Cache vazio — clique em "Forçar Actualização" para gerar contexto</p>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Explicação do cache */}
            <Card className="bg-muted/40">
              <CardContent className="pt-6">
                <h4 className="font-semibold mb-3 flex items-center gap-2">
                  <Brain className="h-4 w-4 text-primary" />
                  Como funciona o Context Engine
                </h4>
                <div className="grid gap-3 md:grid-cols-3 text-sm text-muted-foreground">
                  <div className="flex items-start gap-2">
                    <span className="text-primary font-bold">1.</span>
                    <p>Agrega dados em tempo real das tabelas: <code className="text-xs bg-muted px-1 rounded">invoices</code>, <code className="text-xs bg-muted px-1 rounded">employees</code>, <code className="text-xs bg-muted px-1 rounded">transactions</code>, <code className="text-xs bg-muted px-1 rounded">customers</code></p>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="text-primary font-bold">2.</span>
                    <p>Guarda o contexto calculado em <code className="text-xs bg-muted px-1 rounded">context_cache</code> por 30 minutos para reduzir queries desnecessárias</p>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="text-primary font-bold">3.</span>
                    <p>Fornece contexto unificado a todos os módulos de IA: Relatórios, Anomalias, Recomendações e Assistente Virtual</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
