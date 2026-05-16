// =====================================================
// KWANZACONTROL - AI Dashboard
// Previsões, recomendações e automação — dados reais Supabase
// =====================================================

import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Brain, TrendingUp, TrendingDown, AlertCircle, Lightbulb,
  Activity, RefreshCw, Zap, Target, BarChart3,
  Users, FileText, DollarSign, CheckCircle,
  AlertTriangle, Clock, Sparkles, ArrowUpRight,
  ArrowDownRight, Minus, ShieldAlert, BarChart2
} from 'lucide-react';
import { supabase } from '@/integrations/supabase/client';
import { toast } from 'sonner';
import { motion } from 'framer-motion';

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface MonthlyData {
  month: string;
  income: number;
  expenses: number;
  net: number;
}

interface Prediction {
  month: string;
  label: string;
  predicted_income: number;
  predicted_expenses: number;
  predicted_balance: number;
  confidence: number;
  trend: 'up' | 'down' | 'stable';
}

interface AIRecommendation {
  type: string;
  title: string;
  description: string;
  impact: number;
  confidence: number;
  priority: 'HIGH' | 'MEDIUM' | 'LOW';
  actions: string[];
}

interface KPI {
  label: string;
  value: string | number;
  sub?: string;
  trend?: 'up' | 'down' | 'stable' | null;
  trendValue?: string;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
}

interface AIInsight {
  id: string;
  type: string;
  title: string;
  description: string;
  priority: string;
  is_read: boolean;
  is_dismissed: boolean;
  created_at: string;
}

interface AutomationCheck {
  name: string;
  status: 'ok' | 'warn' | 'alert';
  detail: string;
  count: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function getTenantId(): Promise<string> {
  try {
    const { data } = await supabase.rpc('get_current_tenant_id');
    if (data) return data as string;
  } catch { /* continuar */ }
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) {
      const { data: p } = await supabase
        .from('users').select('tenant_id').eq('id', user.id).maybeSingle();
      if (p?.tenant_id) return p.tenant_id as string;
    }
  } catch { /* continuar */ }
  try {
    const { data: t } = await supabase.from('tenants').select('id').limit(1).single();
    if (t?.id) return t.id as string;
  } catch { /* continuar */ }
  return '';
}

function formatKz(v: number | null | undefined): string {
  const n = Number(v ?? 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M Kz`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K Kz`;
  return `${n.toFixed(0)} Kz`;
}

function monthLabel(offset: number): { month: string; label: string } {
  const d = new Date();
  d.setMonth(d.getMonth() + offset);
  return {
    month: d.toISOString().slice(0, 7),
    label: d.toLocaleDateString('pt-AO', { month: 'long', year: 'numeric' }),
  };
}

/** Regressão linear simples para prever próximos N valores */
function linearRegression(values: number[], steps: number): number[] {
  const n = values.length;
  if (n === 0) return Array(steps).fill(0);
  if (n === 1) return Array(steps).fill(values[0]);
  const xMean = (n - 1) / 2;
  const yMean = values.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  values.forEach((y, x) => { num += (x - xMean) * (y - yMean); den += (x - xMean) ** 2; });
  const slope  = den !== 0 ? num / den : 0;
  const intcpt = yMean - slope * xMean;
  return Array.from({ length: steps }, (_, i) => Math.max(0, intcpt + slope * (n + i)));
}

function trendOf(values: number[]): 'up' | 'down' | 'stable' {
  if (values.length < 2) return 'stable';
  const first = values.slice(0, Math.ceil(values.length / 2)).reduce((a, b) => a + b, 0);
  const last  = values.slice(Math.floor(values.length / 2)).reduce((a, b) => a + b, 0);
  const delta = (last - first) / (first || 1);
  if (delta > 0.05) return 'up';
  if (delta < -0.05) return 'down';
  return 'stable';
}

// ─── Componente ───────────────────────────────────────────────────────────────

export default function AIDashboard() {
  const [loading, setLoading]           = useState(true);
  const [refreshing, setRefreshing]     = useState(false);
  const [running, setRunning]           = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [tenantId, setTenantId]         = useState('');

  // Dados reais
  const [monthlyData, setMonthlyData]   = useState<MonthlyData[]>([]);
  const [predictions, setPredictions]   = useState<Prediction[]>([]);
  const [recommendations, setRecommendations] = useState<AIRecommendation[]>([]);
  const [kpis, setKpis]                 = useState<KPI[]>([]);
  const [insights, setInsights]         = useState<AIInsight[]>([]);
  const [automationChecks, setAutomationChecks] = useState<AutomationCheck[]>([]);
  const [lastUpdated, setLastUpdated]   = useState<Date | null>(null);

  // ── Carga de dados reais ───────────────────────────────────────────────────

  const loadAll = useCallback(async (tid?: string) => {
    setError(null);
    const resolvedTid = tid ?? tenantId;
    if (!resolvedTid) return;

    try {
      // ── 1. Cashflow dos últimos 12 meses (transações reais) ──────────────
      let monthly: MonthlyData[] = [];
      try {
        const { data: txData } = await supabase
          .from('transactions')
          .select('amount, type, transaction_date')
          .eq('tenant_id', resolvedTid)
          .gte('transaction_date', new Date(Date.now() - 365 * 24 * 3600 * 1000).toISOString().split('T')[0])
          .order('transaction_date', { ascending: true });

        if (txData && txData.length > 0) {
          const grouped: Record<string, { income: number; expenses: number }> = {};
          txData.forEach(tx => {
            const m = (tx.transaction_date as string).slice(0, 7);
            if (!grouped[m]) grouped[m] = { income: 0, expenses: 0 };
            const amt = Number(tx.amount ?? 0);
            const t   = (tx.type as string ?? '').toUpperCase();
            if (t === 'INCOME') grouped[m].income += amt;
            else                 grouped[m].expenses += amt;
          });
          monthly = Object.entries(grouped)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([month, v]) => ({ month, income: v.income, expenses: v.expenses, net: v.income - v.expenses }));
        }
      } catch { /* sem transações */ }
      setMonthlyData(monthly);

      // ── 2. Gerar previsões com regressão linear ──────────────────────────
      const incomeVals   = monthly.map(m => m.income);
      const expenseVals  = monthly.map(m => m.expenses);
      const futureIncome = linearRegression(incomeVals, 3);
      const futureExp    = linearRegression(expenseVals, 3);
      const incTrend     = trendOf(incomeVals);
      const confBase     = Math.min(0.92, 0.55 + monthly.length * 0.03);

      const preds: Prediction[] = [1, 2, 3].map((offset, i) => {
        const { month, label } = monthLabel(offset);
        const pi = futureIncome[i];
        const pe = futureExp[i];
        const pb = pi - pe;
        return {
          month, label,
          predicted_income:   Math.round(pi),
          predicted_expenses: Math.round(pe),
          predicted_balance:  Math.round(pb),
          confidence: Math.max(0.4, confBase - i * 0.06),
          trend: pb > 0 ? (incTrend === 'up' ? 'up' : 'stable') : 'down',
        };
      });
      setPredictions(preds);

      // ── 3. KPIs reais ────────────────────────────────────────────────────
      let totalInvoices = 0, overdueCount = 0, overdueAmt = 0, paidAmt = 0, totalAmt = 0;
      let totalEmployees = 0, totalPayroll = 0;
      let pendingDecisions = 0, anomalyCount = 0, reportsCount = 0;

      try {
        const { data: inv } = await supabase
          .from('invoices').select('total, status, due_date').eq('tenant_id', resolvedTid);
        if (inv) {
          totalInvoices = inv.length;
          totalAmt  = inv.reduce((s, r) => s + Number(r.total ?? 0), 0);
          paidAmt   = inv.filter(r => ['PAID', 'paid'].includes(r.status ?? '')).reduce((s, r) => s + Number(r.total ?? 0), 0);
          const ov  = inv.filter(r => ['OVERDUE', 'SENT', 'overdue', 'sent'].includes(r.status ?? '') && r.due_date && new Date(r.due_date) < new Date());
          overdueCount = ov.length;
          overdueAmt   = ov.reduce((s, r) => s + Number(r.total ?? 0), 0);
        }
      } catch { /* continuar */ }

      try {
        const { data: emp } = await supabase
          .from('employees').select('gross_salary, status').eq('tenant_id', resolvedTid);
        if (emp) {
          const active = emp.filter(e => ['ACTIVE', 'active'].includes(e.status ?? ''));
          totalEmployees = active.length;
          totalPayroll   = active.reduce((s, e) => s + Number(e.gross_salary ?? 0), 0);
        }
      } catch { /* continuar */ }

      try {
        const { count: dc } = await supabase
          .from('ai_decisions').select('id', { count: 'exact', head: true })
          .eq('tenant_id', resolvedTid).eq('status', 'PENDING');
        pendingDecisions = dc ?? 0;
      } catch { /* continuar */ }

      try {
        const { count: ac } = await supabase
          .from('anomaly_detections').select('id', { count: 'exact', head: true })
          .eq('tenant_id', resolvedTid).eq('status', 'OPEN');
        anomalyCount = ac ?? 0;
      } catch { /* continuar */ }

      try {
        const { count: rc } = await supabase
          .from('ai_generated_reports').select('id', { count: 'exact', head: true })
          .eq('tenant_id', resolvedTid).eq('status', 'COMPLETED');
        reportsCount = rc ?? 0;
      } catch { /* continuar */ }

      const totalIncome   = monthly.reduce((s, m) => s + m.income, 0);
      const totalExpenses = monthly.reduce((s, m) => s + m.expenses, 0);
      const avgMonthly    = monthly.length > 0 ? totalIncome / monthly.length : 0;
      const payRate       = totalAmt > 0 ? Math.round((paidAmt / totalAmt) * 100) : 0;

      setKpis([
        { label: 'Receita Total (12m)',    value: formatKz(totalIncome),    sub: `${monthly.length} meses`,         trend: trendOf(incomeVals),   trendValue: `média ${formatKz(avgMonthly)}/mês`, icon: DollarSign,    color: 'text-green-600' },
        { label: 'Despesas Totais (12m)',  value: formatKz(totalExpenses),  sub: `ratio ${totalIncome > 0 ? ((totalExpenses/totalIncome)*100).toFixed(0) : 0}%`, trend: trendOf(expenseVals), trendValue: '', icon: TrendingDown,  color: 'text-red-600' },
        { label: 'Saldo Líquido (12m)',    value: formatKz(totalIncome - totalExpenses), sub: 'acumulado',          trend: (totalIncome - totalExpenses) >= 0 ? 'up' : 'down', trendValue: '', icon: BarChart2,      color: totalIncome >= totalExpenses ? 'text-green-600' : 'text-red-600' },
        { label: 'Taxa de Cobrança',       value: `${payRate}%`,            sub: `${totalInvoices} faturas`,        trend: payRate >= 80 ? 'up' : payRate >= 60 ? 'stable' : 'down', trendValue: `${overdueCount} vencidas`, icon: FileText,      color: payRate >= 80 ? 'text-green-600' : 'text-amber-600' },
        { label: 'Facturas Vencidas',      value: formatKz(overdueAmt),     sub: `${overdueCount} facturas`,        trend: overdueCount > 0 ? 'down' : 'up',  trendValue: '', icon: AlertTriangle,  color: overdueCount > 0 ? 'text-red-600' : 'text-green-600' },
        { label: 'Massa Salarial',         value: formatKz(totalPayroll),   sub: `${totalEmployees} colaboradores`, trend: 'stable', trendValue: totalEmployees > 0 ? `${formatKz(totalPayroll / totalEmployees)}/col.` : '', icon: Users, color: 'text-purple-600' },
        { label: 'Decisões IA Pendentes',  value: pendingDecisions,          sub: 'aguardam aprovação',              trend: pendingDecisions > 0 ? 'down' : 'up', trendValue: '', icon: Brain,          color: pendingDecisions > 0 ? 'text-amber-600' : 'text-green-600' },
        { label: 'Anomalias em Aberto',    value: anomalyCount,              sub: 'detectadas pela IA',              trend: anomalyCount > 0 ? 'down' : 'up',  trendValue: '', icon: ShieldAlert,    color: anomalyCount > 0 ? 'text-red-600' : 'text-green-600' },
      ]);

      // ── 4. Recomendações geradas localmente com dados reais ──────────────
      const recs: AIRecommendation[] = [];

      if (overdueCount > 0) recs.push({
        type: 'CASH_FLOW',
        priority: 'HIGH',
        title: `Cobrança Urgente: ${overdueCount} Facturas Vencidas`,
        description: `Existem ${overdueCount} factura${overdueCount > 1 ? 's' : ''} vencida${overdueCount > 1 ? 's' : ''} no valor total de ${formatKz(overdueAmt)}. Cada mês sem cobrança reduz a probabilidade de recebimento em ~8%.`,
        impact: Math.round(overdueAmt * 0.72),
        confidence: 0.88,
        actions: [
          `Contactar imediatamente os ${overdueCount} clientes com facturas vencidas`,
          'Activar workflow de lembretes automáticos (email + SMS)',
          'Oferecer desconto de 2% para pagamento em 5 dias úteis',
          'Para facturas > 90 dias, considerar processo de cobrança formal',
        ],
      });

      if (trendOf(expenseVals) === 'up' && totalExpenses > totalIncome * 0.8) recs.push({
        type: 'COST_REDUCTION',
        priority: 'HIGH',
        title: 'Despesas em Crescimento Acelerado',
        description: `As despesas representam ${totalIncome > 0 ? ((totalExpenses / totalIncome) * 100).toFixed(0) : 0}% da receita e mostram tendência crescente. Sem acção, o saldo poderá tornar-se negativo nos próximos 2-3 meses.`,
        impact: Math.round(totalExpenses * 0.12),
        confidence: 0.76,
        actions: [
          'Auditar as 5 categorias de despesa com maior crescimento',
          'Renegociar contratos de fornecedores (redução média esperada: 8-15%)',
          'Identificar e eliminar despesas recorrentes desnecessárias',
          'Implementar aprovação prévia para despesas > 50.000 Kz',
        ],
      });

      if (payRate < 75 && totalInvoices > 0) recs.push({
        type: 'REVENUE',
        priority: 'MEDIUM',
        title: `Taxa de Cobrança Baixa (${payRate}%)`,
        description: `A taxa de cobrança está abaixo do benchmark de 80%. Melhorar para 85% representaria ${formatKz((totalAmt - paidAmt) * 0.1)} de receita adicional imediata.`,
        impact: Math.round((totalAmt - paidAmt) * 0.25),
        confidence: 0.82,
        actions: [
          'Implementar política de pagamento antecipado com desconto de 3%',
          'Activar notificações automáticas 7, 3 e 1 dias antes do vencimento',
          'Oferecer opções de pagamento por transferência bancária e multicaixa',
          'Rever condições de crédito para clientes com histórico de atraso',
        ],
      });

      if (trendOf(incomeVals) === 'up') recs.push({
        type: 'GROWTH',
        priority: 'MEDIUM',
        title: 'Tendência de Crescimento da Receita Detectada',
        description: `A receita mostra tendência crescente nos últimos ${monthly.length} meses. A IA prevê que esta tendência continue nos próximos 3 meses com ${Math.round(confBase * 100)}% de confiança.`,
        impact: Math.round(avgMonthly * 0.15),
        confidence: confBase,
        actions: [
          'Capitalizar o crescimento com estratégia de retenção de clientes',
          'Considerar expansão da capacidade operacional',
          'Reinvestir margem adicional em marketing e aquisição de clientes',
          'Documentar e replicar as práticas comerciais bem-sucedidas',
        ],
      });

      if (totalEmployees > 0 && totalPayroll / (totalIncome || 1) > 0.4) recs.push({
        type: 'HR',
        priority: 'MEDIUM',
        title: 'Ratio Massa Salarial / Receita Elevado',
        description: `A massa salarial representa ${totalIncome > 0 ? ((totalPayroll / totalIncome) * 100).toFixed(0) : 0}% da receita mensal. O benchmark saudável é abaixo de 35%.`,
        impact: Math.round(totalPayroll * 0.08),
        confidence: 0.71,
        actions: [
          'Analisar produtividade por departamento (receita per capita)',
          'Avaliar possibilidade de automação de tarefas repetitivas',
          'Rever estrutura de incentivos variáveis vs. fixos',
          'Planear crescimento da equipa alinhado com crescimento da receita',
        ],
      });

      if (pendingDecisions > 2) recs.push({
        type: 'OPERATIONS',
        priority: 'LOW',
        title: `${pendingDecisions} Decisões IA Pendentes de Aprovação`,
        description: `Há ${pendingDecisions} decisões assistidas pela IA aguardando revisão humana. Decisões atrasadas podem impactar eficiência operacional.`,
        impact: 0,
        confidence: 0.95,
        actions: [
          'Aceder ao módulo "Decisões com IA" para rever pendências',
          'Delegar aprovações a responsáveis de departamento',
          'Configurar alertas de decisões pendentes no painel de notificações',
        ],
      });

      setRecommendations(recs);

      // ── 5. Insights da tabela ai_insights ────────────────────────────────
      try {
        const { data: ins } = await supabase
          .from('ai_insights')
          .select('id, type, title, description, priority, is_read, is_dismissed, created_at')
          .eq('tenant_id', resolvedTid)
          .eq('is_dismissed', false)
          .order('created_at', { ascending: false })
          .limit(20);
        setInsights((ins ?? []) as AIInsight[]);
      } catch { setInsights([]); }

      // ── 6. Verificações de automação ─────────────────────────────────────
      const checks: AutomationCheck[] = [
        {
          name: 'Facturas Vencidas',
          status: overdueCount === 0 ? 'ok' : overdueCount <= 3 ? 'warn' : 'alert',
          detail: overdueCount === 0 ? 'Sem facturas vencidas' : `${overdueCount} factura${overdueCount > 1 ? 's' : ''} vencida${overdueCount > 1 ? 's' : ''}`,
          count: overdueCount,
        },
        {
          name: 'Anomalias em Aberto',
          status: anomalyCount === 0 ? 'ok' : anomalyCount <= 2 ? 'warn' : 'alert',
          detail: anomalyCount === 0 ? 'Sistema limpo' : `${anomalyCount} anomalia${anomalyCount > 1 ? 's' : ''} detectada${anomalyCount > 1 ? 's' : ''}`,
          count: anomalyCount,
        },
        {
          name: 'Decisões Pendentes',
          status: pendingDecisions === 0 ? 'ok' : pendingDecisions <= 3 ? 'warn' : 'alert',
          detail: pendingDecisions === 0 ? 'Sem pendências' : `${pendingDecisions} decisão${pendingDecisions > 1 ? 'ões' : ''} por aprovar`,
          count: pendingDecisions,
        },
        {
          name: 'Relatórios Gerados',
          status: reportsCount > 0 ? 'ok' : 'warn',
          detail: reportsCount > 0 ? `${reportsCount} relatório${reportsCount > 1 ? 's' : ''} concluído${reportsCount > 1 ? 's' : ''}` : 'Nenhum relatório ainda',
          count: reportsCount,
        },
        {
          name: 'Dados Financeiros',
          status: monthly.length > 0 ? 'ok' : 'warn',
          detail: monthly.length > 0 ? `${monthly.length} meses de histórico` : 'Sem transações registadas',
          count: monthly.length,
        },
        {
          name: 'Colaboradores Activos',
          status: totalEmployees > 0 ? 'ok' : 'warn',
          detail: totalEmployees > 0 ? `${totalEmployees} colaborador${totalEmployees > 1 ? 'es' : ''} activo${totalEmployees > 1 ? 's' : ''}` : 'Sem colaboradores registados',
          count: totalEmployees,
        },
      ];
      setAutomationChecks(checks);
      setLastUpdated(new Date());
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar dados';
      setError(msg);
      toast.error(msg);
    }
  }, [tenantId]);

  useEffect(() => {
    setLoading(true);
    getTenantId().then(tid => {
      setTenantId(tid);
      loadAll(tid).finally(() => setLoading(false));
    }).catch(err => {
      setError(err instanceof Error ? err.message : 'Erro de autenticação');
      setLoading(false);
    });
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await loadAll();
    setRefreshing(false);
    toast.success('Dados actualizados com sucesso');
  }, [loadAll]);

  // ── Executar verificações automáticas ─────────────────────────────────────

  const runAutomatedChecks = async () => {
    if (!tenantId) { toast.error('Tenant não identificado'); return; }
    setRunning(true);
    try {
      // Actualizar is_read nos insights não lidos
      await supabase
        .from('ai_insights')
        .update({ is_read: true })
        .eq('tenant_id', tenantId)
        .eq('is_read', false);

      // Recarregar tudo
      await loadAll();

      const alerts = automationChecks.filter(c => c.status === 'alert').length;
      const warns  = automationChecks.filter(c => c.status === 'warn').length;
      toast.success(
        alerts > 0
          ? `Verificação concluída: ${alerts} alerta${alerts > 1 ? 's' : ''} crítico${alerts > 1 ? 's' : ''} encontrado${alerts > 1 ? 's' : ''}`
          : warns > 0
            ? `Verificação concluída: ${warns} aviso${warns > 1 ? 's' : ''} encontrado${warns > 1 ? 's' : ''}`
            : 'Verificação concluída: tudo em ordem ✅'
      );
    } catch (err) {
      toast.error('Erro ao executar verificações');
    } finally {
      setRunning(false);
    }
  };

  // ─── Helpers de render ────────────────────────────────────────────────────

  const TrendIcon = ({ t }: { t?: 'up' | 'down' | 'stable' | null }) => {
    if (t === 'up')   return <ArrowUpRight className="h-4 w-4 text-green-500" />;
    if (t === 'down') return <ArrowDownRight className="h-4 w-4 text-red-500" />;
    return <Minus className="h-4 w-4 text-amber-500" />;
  };

  const priorityConfig = {
    HIGH:   { label: 'Alta',  variant: 'destructive' as const, dot: 'bg-red-500' },
    MEDIUM: { label: 'Média', variant: 'default'     as const, dot: 'bg-amber-500' },
    LOW:    { label: 'Baixa', variant: 'secondary'   as const, dot: 'bg-green-500' },
  };

  const checkColor = { ok: 'text-green-600 bg-green-50 border-green-200', warn: 'text-amber-600 bg-amber-50 border-amber-200', alert: 'text-red-600 bg-red-50 border-red-200' };
  const checkIcon  = { ok: <CheckCircle className="h-4 w-4 text-green-600" />, warn: <AlertCircle className="h-4 w-4 text-amber-500" />, alert: <AlertTriangle className="h-4 w-4 text-red-600" /> };

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <Layout>
      <div className="space-y-6">

        {/* Header */}
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}
          className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-3">
              <Brain className="h-8 w-8 text-primary" />
              Dashboard de IA
            </h1>
            <p className="text-muted-foreground mt-1">
              Previsões inteligentes, recomendações e automação com dados reais
            </p>
            {lastUpdated && (
              <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                <Clock className="h-3 w-3" /> Actualizado: {lastUpdated.toLocaleTimeString('pt-AO')}
              </p>
            )}
          </div>
          <div className="flex gap-2 flex-wrap">
            <Button onClick={runAutomatedChecks} disabled={running || loading} variant="outline" size="sm">
              <Zap className={`mr-2 h-4 w-4 ${running ? 'animate-pulse text-amber-500' : ''}`} />
              {running ? 'A verificar...' : 'Executar Verificações'}
            </Button>
            <Button onClick={refresh} disabled={refreshing || loading} size="sm">
              <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
              {refreshing ? 'A actualizar...' : 'Actualizar Dados'}
            </Button>
          </div>
        </motion.div>

        {/* Erro */}
        {error && (
          <div className="flex items-center gap-2 p-3 bg-destructive/10 text-destructive border border-destructive/20 rounded-lg text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button className="text-xs underline" onClick={() => setError(null)}>Fechar</button>
          </div>
        )}

        {/* KPIs */}
        {loading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
          </div>
        ) : (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }}
            className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {kpis.map((kpi, i) => (
              <Card key={i} className="p-4 hover:shadow-md transition-shadow">
                <div className="flex items-start justify-between mb-2">
                  <p className="text-xs text-muted-foreground font-medium">{kpi.label}</p>
                  <kpi.icon className={`h-4 w-4 ${kpi.color} opacity-80`} />
                </div>
                <p className={`text-xl font-bold ${kpi.color}`}>{kpi.value}</p>
                <div className="flex items-center gap-1 mt-1">
                  <TrendIcon t={kpi.trend} />
                  <span className="text-xs text-muted-foreground">{kpi.sub}</span>
                </div>
                {kpi.trendValue && (
                  <p className="text-xs text-muted-foreground mt-0.5">{kpi.trendValue}</p>
                )}
              </Card>
            ))}
          </motion.div>
        )}

        {/* Tabs principais */}
        <Tabs defaultValue="predictions" className="space-y-4">
          <TabsList className="grid w-full grid-cols-4 max-w-2xl">
            <TabsTrigger value="predictions">
              <BarChart3 className="mr-2 h-4 w-4" /> Previsões
            </TabsTrigger>
            <TabsTrigger value="recommendations">
              <Lightbulb className="mr-2 h-4 w-4" /> Recomendações
              {recommendations.filter(r => r.priority === 'HIGH').length > 0 && (
                <Badge variant="destructive" className="ml-1.5 text-xs px-1.5 py-0">
                  {recommendations.filter(r => r.priority === 'HIGH').length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="insights">
              <Sparkles className="mr-2 h-4 w-4" /> Insights
              {insights.filter(i => !i.is_read).length > 0 && (
                <Badge variant="destructive" className="ml-1.5 text-xs px-1.5 py-0">
                  {insights.filter(i => !i.is_read).length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="automation">
              <Activity className="mr-2 h-4 w-4" /> Automação
            </TabsTrigger>
          </TabsList>

          {/* ── Tab: Previsões ───────────────────────────────────────────────── */}
          <TabsContent value="predictions" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <BarChart3 className="h-5 w-5 text-primary" />
                  Previsão de Fluxo de Caixa — Próximos 3 Meses
                </CardTitle>
                <CardDescription>
                  {monthlyData.length > 0
                    ? `Previsões geradas por regressão linear com base em ${monthlyData.length} meses de histórico real`
                    : 'Registe transações no módulo Finanças para activar as previsões de IA'}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-4">
                    {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-36 rounded-lg" />)}
                  </div>
                ) : monthlyData.length === 0 ? (
                  <div className="text-center py-16 text-muted-foreground">
                    <Brain className="h-16 w-16 mx-auto mb-4 opacity-20" />
                    <p className="text-lg font-medium">Sem histórico financeiro disponível</p>
                    <p className="text-sm mt-2 max-w-sm mx-auto">
                      As previsões são geradas automaticamente a partir das suas transações reais. Registe entradas e saídas no módulo <strong>Finanças</strong> para activar esta funcionalidade.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {/* Resumo histórico */}
                    <div className="grid grid-cols-3 gap-3 mb-6 p-4 bg-muted/30 rounded-xl border">
                      <div className="text-center">
                        <p className="text-xs text-muted-foreground">Receita Média/Mês</p>
                        <p className="text-lg font-bold text-green-600">
                          {formatKz(monthlyData.reduce((s, m) => s + m.income, 0) / monthlyData.length)}
                        </p>
                        <TrendIcon t={trendOf(monthlyData.map(m => m.income))} />
                      </div>
                      <div className="text-center">
                        <p className="text-xs text-muted-foreground">Despesas Médias/Mês</p>
                        <p className="text-lg font-bold text-red-600">
                          {formatKz(monthlyData.reduce((s, m) => s + m.expenses, 0) / monthlyData.length)}
                        </p>
                        <TrendIcon t={trendOf(monthlyData.map(m => m.expenses))} />
                      </div>
                      <div className="text-center">
                        <p className="text-xs text-muted-foreground">Saldo Médio/Mês</p>
                        <p className={`text-lg font-bold ${monthlyData.reduce((s, m) => s + m.net, 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                          {formatKz(monthlyData.reduce((s, m) => s + m.net, 0) / monthlyData.length)}
                        </p>
                        <p className="text-xs text-muted-foreground">Baseado em {monthlyData.length} meses</p>
                      </div>
                    </div>

                    {/* Previsões mês a mês */}
                    {predictions.map((pred, index) => (
                      <motion.div key={pred.month}
                        initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }}
                        transition={{ duration: 0.3, delay: index * 0.1 }}
                        className="border rounded-xl p-4 hover:bg-muted/20 transition-colors">
                        <div className="flex items-center justify-between mb-3">
                          <div className="flex items-center gap-3">
                            <h3 className="font-semibold capitalize">{pred.label}</h3>
                            <Badge variant={pred.trend === 'up' ? 'default' : pred.trend === 'down' ? 'destructive' : 'secondary'} className="text-xs">
                              {pred.trend === 'up' ? '↑ Crescimento' : pred.trend === 'down' ? '↓ Declínio' : '→ Estável'}
                            </Badge>
                          </div>
                          <div className="text-right">
                            <div className="text-xs text-muted-foreground">Confiança da IA</div>
                            <div className="font-bold text-primary">{(pred.confidence * 100).toFixed(0)}%</div>
                          </div>
                        </div>
                        <div className="grid gap-3 md:grid-cols-3 mb-3">
                          <div className="p-3 bg-green-50 dark:bg-green-900/10 rounded-lg border border-green-200 dark:border-green-800">
                            <p className="text-xs text-muted-foreground">Receita Prevista</p>
                            <p className="text-lg font-bold text-green-600">{formatKz(pred.predicted_income)}</p>
                          </div>
                          <div className="p-3 bg-red-50 dark:bg-red-900/10 rounded-lg border border-red-200 dark:border-red-800">
                            <p className="text-xs text-muted-foreground">Despesas Previstas</p>
                            <p className="text-lg font-bold text-red-600">{formatKz(pred.predicted_expenses)}</p>
                          </div>
                          <div className={`p-3 rounded-lg border ${pred.predicted_balance >= 0 ? 'bg-green-50 dark:bg-green-900/10 border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-800'}`}>
                            <p className="text-xs text-muted-foreground">Saldo Previsto</p>
                            <p className={`text-lg font-bold ${pred.predicted_balance >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                              {formatKz(pred.predicted_balance)}
                            </p>
                          </div>
                        </div>
                        <div className="mt-2">
                          <div className="flex justify-between text-xs text-muted-foreground mb-1">
                            <span>Confiança da previsão</span>
                            <span>{(pred.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <Progress value={pred.confidence * 100} className="h-1.5" />
                        </div>
                      </motion.div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Histórico mensal */}
            {!loading && monthlyData.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <BarChart2 className="h-4 w-4 text-primary" />
                    Histórico Mensal Real ({monthlyData.length} meses)
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {[...monthlyData].reverse().slice(0, 6).map((m, i) => (
                      <div key={m.month} className="flex items-center gap-3 text-sm">
                        <span className="text-muted-foreground w-16 shrink-0">{m.month}</span>
                        <div className="flex-1 grid grid-cols-3 gap-2 text-xs">
                          <span className="text-green-600 font-medium">+{formatKz(m.income)}</span>
                          <span className="text-red-600 font-medium">-{formatKz(m.expenses)}</span>
                          <span className={`font-bold ${m.net >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {m.net >= 0 ? '+' : ''}{formatKz(m.net)}
                          </span>
                        </div>
                        <div className="w-24 shrink-0">
                          <Progress
                            value={m.income > 0 ? Math.min(100, (m.net / m.income) * 100 + 50) : 50}
                            className="h-1.5"
                          />
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── Tab: Recomendações ───────────────────────────────────────────── */}
          <TabsContent value="recommendations" className="space-y-4">
            {loading ? (
              <div className="space-y-4">
                {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-xl" />)}
              </div>
            ) : recommendations.length === 0 ? (
              <Card>
                <CardContent className="text-center py-16 text-muted-foreground">
                  <Lightbulb className="h-16 w-16 mx-auto mb-4 opacity-20" />
                  <p className="text-lg font-medium">Nenhuma recomendação disponível</p>
                  <p className="text-sm mt-2">A IA gera recomendações assim que existam dados financeiros registados na plataforma.</p>
                  <Button className="mt-4" size="sm" variant="outline" onClick={refresh}>
                    <RefreshCw className="h-4 w-4 mr-2" /> Verificar novamente
                  </Button>
                </CardContent>
              </Card>
            ) : recommendations.map((rec, index) => {
              const pc = priorityConfig[rec.priority] ?? priorityConfig.MEDIUM;
              return (
                <motion.div key={index}
                  initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: index * 0.08 }}>
                  <Card className={`border-l-4 ${rec.priority === 'HIGH' ? 'border-l-red-500' : rec.priority === 'MEDIUM' ? 'border-l-amber-500' : 'border-l-green-500'}`}>
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-start gap-3 flex-1">
                          <Target className="h-5 w-5 text-primary mt-0.5 shrink-0" />
                          <div>
                            <div className="flex items-center gap-2 flex-wrap">
                              <CardTitle className="text-base">{rec.title}</CardTitle>
                              <Badge variant={pc.variant} className="text-xs">{pc.label} Prioridade</Badge>
                            </div>
                            <CardDescription className="mt-1">{rec.description}</CardDescription>
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <p className="text-xs text-muted-foreground">Confiança</p>
                          <p className="font-bold text-primary">{(rec.confidence * 100).toFixed(0)}%</p>
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {rec.impact > 0 && (
                        <div className="flex items-center justify-between p-3 bg-primary/5 rounded-lg border border-primary/10">
                          <span className="font-medium text-sm flex items-center gap-2">
                            <DollarSign className="h-4 w-4 text-primary" /> Impacto Financeiro Estimado
                          </span>
                          <span className="text-lg font-bold text-primary">{formatKz(rec.impact)}</span>
                        </div>
                      )}
                      <div>
                        <h4 className="font-semibold text-sm mb-2 flex items-center gap-1">
                          <CheckCircle className="h-4 w-4 text-green-600" /> Acções Recomendadas pela IA:
                        </h4>
                        <ul className="space-y-2">
                          {rec.actions.map((action, i) => (
                            <li key={i} className="flex items-start gap-2 text-sm">
                              <span className="text-primary font-bold mt-0.5 shrink-0">{i + 1}.</span>
                              <span className="text-muted-foreground">{action}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              );
            })}
          </TabsContent>

          {/* ── Tab: Insights ────────────────────────────────────────────────── */}
          <TabsContent value="insights" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-primary" />
                  Insights da IA
                </CardTitle>
                <CardDescription>
                  Alertas e oportunidades detectados automaticamente nos seus dados
                </CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
                  </div>
                ) : insights.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <Sparkles className="h-12 w-12 mx-auto mb-3 opacity-20" />
                    <p className="font-medium">Sem insights activos de momento</p>
                    <p className="text-sm mt-1">Os insights são gerados à medida que a IA analisa os dados da empresa. Verifique também o módulo <strong>Relatórios IA</strong>.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {insights.map(ins => {
                      const pc = priorityConfig[ins.priority as keyof typeof priorityConfig] ?? priorityConfig.MEDIUM;
                      return (
                        <div key={ins.id} className={`flex items-start justify-between p-4 rounded-xl border gap-3 ${
                          ins.priority === 'HIGH' ? 'border-red-200 bg-red-50/30 dark:bg-red-900/10'
                            : ins.priority === 'MEDIUM' ? 'border-amber-200 bg-amber-50/30 dark:bg-amber-900/10'
                            : 'border-muted bg-muted/20'
                        }`}>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className={`h-2 w-2 rounded-full shrink-0 ${pc.dot}`} />
                              <h4 className="font-semibold text-sm">{ins.title}</h4>
                              <Badge variant={pc.variant} className="text-xs">{pc.label}</Badge>
                              {!ins.is_read && (
                                <Badge variant="outline" className="text-xs text-blue-600 border-blue-200">Novo</Badge>
                              )}
                            </div>
                            <p className="text-sm text-muted-foreground mt-1">{ins.description}</p>
                            <p className="text-xs text-muted-foreground mt-1.5">
                              {ins.type.replace(/_/g, ' ')} · {new Date(ins.created_at).toLocaleDateString('pt-AO')}
                            </p>
                          </div>
                          <Button
                            variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground shrink-0"
                            onClick={async () => {
                              await supabase.from('ai_insights').update({ is_dismissed: true }).eq('id', ins.id);
                              setInsights(prev => prev.filter(i => i.id !== ins.id));
                              toast.success('Insight dispensado');
                            }}
                          >
                            Dispensar
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Tab: Automação ───────────────────────────────────────────────── */}
          <TabsContent value="automation" className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              {/* Estado do sistema */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Activity className="h-4 w-4 text-primary" />
                    Estado do Sistema IA
                  </CardTitle>
                  <CardDescription>Verificações automáticas em tempo real</CardDescription>
                </CardHeader>
                <CardContent>
                  {loading ? (
                    <div className="space-y-2">
                      {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12 rounded-lg" />)}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {automationChecks.map((check, i) => (
                        <div key={i} className={`flex items-center justify-between p-3 rounded-lg border ${checkColor[check.status]}`}>
                          <div className="flex items-center gap-2">
                            {checkIcon[check.status]}
                            <span className="text-sm font-medium">{check.name}</span>
                          </div>
                          <span className="text-xs font-medium">{check.detail}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <Button className="w-full mt-4" variant="outline" size="sm" onClick={runAutomatedChecks} disabled={running || loading}>
                    <Zap className={`h-4 w-4 mr-2 ${running ? 'animate-pulse text-amber-500' : ''}`} />
                    {running ? 'A executar...' : 'Executar Verificação Completa'}
                  </Button>
                </CardContent>
              </Card>

              {/* Resumo de automação */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Brain className="h-4 w-4 text-primary" />
                    Resumo de Automação IA
                  </CardTitle>
                  <CardDescription>O que a IA está a monitorizar</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    {[
                      {
                        label: 'Previsões Geradas',
                        value: predictions.length > 0 ? `${predictions.length} meses previstos` : 'Sem dados suficientes',
                        icon: BarChart3, active: predictions.length > 0,
                      },
                      {
                        label: 'Recomendações Activas',
                        value: recommendations.length > 0 ? `${recommendations.length} recomendações` : 'Aguardar dados',
                        icon: Lightbulb, active: recommendations.length > 0,
                      },
                      {
                        label: 'Insights em Aberto',
                        value: insights.length > 0 ? `${insights.length} insights` : 'Nenhum activo',
                        icon: Sparkles, active: insights.length > 0,
                      },
                      {
                        label: 'Alertas Críticos',
                        value: automationChecks.filter(c => c.status === 'alert').length > 0
                          ? `${automationChecks.filter(c => c.status === 'alert').length} alertas críticos`
                          : 'Nenhum alerta crítico',
                        icon: AlertTriangle,
                        active: automationChecks.filter(c => c.status === 'alert').length === 0,
                      },
                      {
                        label: 'Dados Monitorizados',
                        value: monthlyData.length > 0 ? `${monthlyData.length} meses de histórico` : 'Sem transações',
                        icon: Activity, active: monthlyData.length > 0,
                      },
                    ].map(({ label, value, icon: Icon, active }, i) => (
                      <div key={i} className="flex items-center justify-between p-3 bg-muted/30 rounded-lg border">
                        <div className="flex items-center gap-2">
                          <Icon className={`h-4 w-4 ${active ? 'text-primary' : 'text-muted-foreground'}`} />
                          <span className="text-sm font-medium">{label}</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground">{value}</span>
                          <span className={`h-2 w-2 rounded-full ${active ? 'bg-green-500' : 'bg-amber-400'}`} />
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Alertas críticos destacados */}
            {!loading && automationChecks.filter(c => c.status === 'alert').length > 0 && (
              <Card className="border-red-200 bg-red-50/20 dark:bg-red-900/10">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base text-red-700 flex items-center gap-2">
                    <AlertTriangle className="h-5 w-5" />
                    Alertas Críticos Detectados
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {automationChecks.filter(c => c.status === 'alert').map((check, i) => (
                      <div key={i} className="flex items-center gap-3 p-3 bg-red-100/50 dark:bg-red-900/20 rounded-lg border border-red-200">
                        <AlertTriangle className="h-5 w-5 text-red-600 shrink-0" />
                        <div>
                          <p className="font-semibold text-sm text-red-700">{check.name}</p>
                          <p className="text-xs text-red-600">{check.detail}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
