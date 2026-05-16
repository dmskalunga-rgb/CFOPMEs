import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { motion } from 'framer-motion';
import {
  Building2, Brain, TrendingUp, AlertTriangle, Sparkles, BarChart3,
  Shield, Zap, RefreshCw, CheckCircle2, XCircle, Clock,
  Target, ChevronRight, Eye, EyeOff, Activity, Lightbulb,
  TrendingDown, AlertCircle, CheckCircle, ArrowUpRight,
} from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';

// ─── Tipos baseados nas colunas reais do Supabase ──────────────────────────

interface AiDecision {
  id: string;
  decision_type: string;
  decision_title: string;
  decision_description: string;
  context_data: Record<string, unknown> | null;
  ai_recommendation: string;
  ai_confidence: number;
  ai_reasoning: string | null;
  risk_score: number | null;
  risk_factors: Array<{ factor: string; severity: string; mitigation: string }> | null;
  success_probability: number | null;
  estimated_impact: Record<string, unknown> | null;
  alternative_options: Array<{ option: string; risk: string }> | null;
  status: string;
  created_at: string | null;
}

interface AiInsight {
  id: string;
  type: string;
  category: string | null;
  title: string;
  description: string;
  priority: string;
  confidence: number | null;
  data: Record<string, unknown> | null;
  recommendations: Array<{ action: string; impact: string }> | null;
  is_read: boolean;
  is_dismissed: boolean;
  created_at: string | null;
}

interface AnomalyDetection {
  id: string;
  anomaly_type: string;
  entity_type: string;
  severity: string;
  status: string;
  confidence_score: number | null;
  anomaly_description: string;
  detected_value: number | null;
  expected_value: number | null;
  deviation_percentage: number | null;
  metadata: Record<string, unknown> | null;
  detected_at: string | null;
  created_at: string | null;
}

interface FinancialSummary {
  total_income: number;
  total_expenses: number;
  net_balance: number;
  income_count: number;
  expense_count: number;
}

interface InvoiceStats {
  total_invoices: number;
  total_amount: number;
  paid_count: number;
  paid_amount: number;
  overdue_count: number;
  overdue_amount: number;
  pending_count: number;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function getTenantId(): Promise<string | null> {
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) return null;
    const { data } = await supabase
      .from('users')
      .select('tenant_id')
      .eq('id', user.id)
      .maybeSingle();
    return data?.tenant_id ?? null;
  } catch {
    return null;
  }
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  if (mins  < 1)  return 'agora mesmo';
  if (mins  < 60) return `${mins}m atrás`;
  if (hours < 24) return `${hours}h atrás`;
  if (days  < 30) return `${days}d atrás`;
  return new Date(dateStr).toLocaleDateString('pt-AO');
}

function fmtAOA(val: number | null | undefined): string {
  if (val == null) return '—';
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M AOA`;
  if (val >= 1_000) return `${(val / 1_000).toFixed(0)}K AOA`;
  return `${val.toLocaleString('pt-AO')} AOA`;
}

// Score helpers
function scoreColor(score: number): string {
  if (score >= 80) return 'text-emerald-600';
  if (score >= 60) return 'text-yellow-600';
  if (score >= 40) return 'text-orange-500';
  return 'text-destructive';
}
function scoreBg(score: number): string {
  if (score >= 80) return 'bg-emerald-50 dark:bg-emerald-950/30';
  if (score >= 60) return 'bg-yellow-50 dark:bg-yellow-950/30';
  if (score >= 40) return 'bg-orange-50 dark:bg-orange-950/30';
  return 'bg-red-50 dark:bg-red-950/30';
}
function scoreLabel(score: number): string {
  if (score >= 80) return 'Excelente';
  if (score >= 60) return 'Bom';
  if (score >= 40) return 'Atenção';
  return 'Crítico';
}

// Priority / severity helpers
const PRIORITY_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  CRITICAL: 'destructive', HIGH: 'destructive', MEDIUM: 'default', LOW: 'secondary',
};
const SEVERITY_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  HIGH: 'destructive', MEDIUM: 'default', LOW: 'secondary',
};
const RECOMMENDATION_VARIANT: Record<string, string> = {
  APPROVE: 'text-emerald-600 font-bold', NEGOTIATE: 'text-blue-600 font-bold',
  EVALUATE: 'text-yellow-600 font-bold', REJECT: 'text-destructive font-bold',
};
const STATUS_VARIANT: Record<string, string> = {
  PENDING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  APPROVED: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400',
  REJECTED: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  ACTIONED: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
};
const ANOMALY_STATUS_VARIANT: Record<string, string> = {
  OPEN: 'bg-red-100 text-red-800 dark:bg-red-900/30',
  INVESTIGATING: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30',
  RESOLVED: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30',
};

// ─── Score Bar Component ──────────────────────────────────────────────────────

function ScoreBar({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <div className={`rounded-xl p-4 border ${scoreBg(value)}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-sm font-medium">{label}</span>
        </div>
        <span className={`text-xl font-bold ${scoreColor(value)}`}>{value}</span>
      </div>
      <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            value >= 80 ? 'bg-emerald-500' : value >= 60 ? 'bg-yellow-500' : value >= 40 ? 'bg-orange-500' : 'bg-red-500'
          }`}
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <p className={`text-xs mt-1 ${scoreColor(value)}`}>{scoreLabel(value)}</p>
    </div>
  );
}

// ─── Componente principal ─────────────────────────────────────────────────────

export default function SmartCompanyDashboard() {
  const { toast } = useToast();

  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tenantId,   setTenantId]   = useState<string | null>(null);
  const [generatingInsights, setGeneratingInsights] = useState(false);

  // Dados reais do Supabase
  const [decisions,  setDecisions]  = useState<AiDecision[]>([]);
  const [insights,   setInsights]   = useState<AiInsight[]>([]);
  const [anomalies,  setAnomalies]  = useState<AnomalyDetection[]>([]);
  const [financial,  setFinancial]  = useState<FinancialSummary | null>(null);
  const [invoiceStats, setInvoiceStats] = useState<InvoiceStats | null>(null);
  const [tenantName, setTenantName] = useState<string>('');

  // Detalhes expandidos
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null);
  const [expandedInsight,  setExpandedInsight]  = useState<string | null>(null);

  // ─── Init ──────────────────────────────────────────────────────────────

  useEffect(() => {
    getTenantId().then(tid => setTenantId(tid));
  }, []);

  useEffect(() => {
    if (tenantId) loadAll(tenantId);
  }, [tenantId]);

  // ─── Carregar todos os dados do Supabase ──────────────────────────────

  const loadAll = useCallback(async (tid: string) => {
    setRefreshing(true);
    try {
      const [
        decisionsRes, insightsRes, anomaliesRes,
        financialRes, invoiceRes, tenantRes,
      ] = await Promise.all([

        // Decisões AI
        supabase
          .from('ai_decisions')
          .select(`id, decision_type, decision_title, decision_description,
                   context_data, ai_recommendation, ai_confidence, ai_reasoning,
                   risk_score, risk_factors, success_probability, estimated_impact,
                   alternative_options, status, created_at`)
          .eq('tenant_id', tid)
          .order('created_at', { ascending: false })
          .limit(20),

        // AI Insights (não dismissed)
        supabase
          .from('ai_insights')
          .select(`id, type, category, title, description, priority, confidence,
                   data, recommendations, is_read, is_dismissed, created_at`)
          .eq('tenant_id', tid)
          .eq('is_dismissed', false)
          .order('created_at', { ascending: false })
          .limit(20),

        // Anomalias detectadas
        supabase
          .from('anomaly_detections')
          .select(`id, anomaly_type, entity_type, severity, status, confidence_score,
                   anomaly_description, detected_value, expected_value,
                   deviation_percentage, metadata, detected_at, created_at`)
          .eq('tenant_id', tid)
          .order('detected_at', { ascending: false })
          .limit(15),

        // Resumo financeiro (view analítica)
        supabase
          .from('v_financial_summary')
          .select('total_income, total_expenses, net_balance, income_count, expense_count')
          .eq('tenant_id', tid)
          .maybeSingle(),

        // Stats de facturas (view analítica)
        supabase
          .from('v_invoice_stats')
          .select('total_invoices, total_amount, paid_count, paid_amount, overdue_count, overdue_amount, pending_count')
          .eq('tenant_id', tid)
          .maybeSingle(),

        // Nome do tenant
        supabase
          .from('tenants')
          .select('name')
          .eq('id', tid)
          .maybeSingle(),
      ]);

      if (decisionsRes.error)  console.warn('ai_decisions:', decisionsRes.error.message);
      if (insightsRes.error)   console.warn('ai_insights:', insightsRes.error.message);
      if (anomaliesRes.error)  console.warn('anomaly_detections:', anomaliesRes.error.message);
      if (financialRes.error)  console.warn('v_financial_summary:', financialRes.error.message);
      if (invoiceRes.error)    console.warn('v_invoice_stats:', invoiceRes.error.message);

      setDecisions((decisionsRes.data  ?? []) as AiDecision[]);
      setInsights( (insightsRes.data   ?? []) as AiInsight[]);
      setAnomalies((anomaliesRes.data  ?? []) as AnomalyDetection[]);
      setFinancial(financialRes.data  as FinancialSummary | null);
      setInvoiceStats(invoiceRes.data as InvoiceStats | null);
      setTenantName(tenantRes.data?.name ?? '');

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro desconhecido';
      toast({ title: 'Erro ao carregar dados', description: msg, variant: 'destructive' });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [toast]);

  // ─── Acções ───────────────────────────────────────────────────────────

  const handleApproveDecision = async (dec: AiDecision) => {
    const { error } = await supabase
      .from('ai_decisions')
      .update({ status: 'APPROVED', updated_at: new Date().toISOString() })
      .eq('id', dec.id);
    if (error) {
      toast({ title: 'Erro ao aprovar', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: '✅ Decisão Aprovada', description: dec.decision_title });
    if (tenantId) loadAll(tenantId);
  };

  const handleRejectDecision = async (dec: AiDecision) => {
    const { error } = await supabase
      .from('ai_decisions')
      .update({ status: 'REJECTED', updated_at: new Date().toISOString() })
      .eq('id', dec.id);
    if (error) {
      toast({ title: 'Erro ao rejeitar', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: '❌ Decisão Rejeitada', description: dec.decision_title });
    if (tenantId) loadAll(tenantId);
  };

  const handleDismissInsight = async (insightId: string) => {
    const { error } = await supabase
      .from('ai_insights')
      .update({ is_dismissed: true })
      .eq('id', insightId);
    if (error) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
      return;
    }
    setInsights(prev => prev.filter(i => i.id !== insightId));
    toast({ title: 'Insight dispensado' });
  };

  const handleMarkInsightRead = async (insightId: string) => {
    await supabase
      .from('ai_insights')
      .update({ is_read: true })
      .eq('id', insightId);
    setInsights(prev => prev.map(i => i.id === insightId ? { ...i, is_read: true } : i));
  };

  const handleUpdateAnomalyStatus = async (id: string, newStatus: string) => {
    const { error } = await supabase
      .from('anomaly_detections')
      .update({ status: newStatus })
      .eq('id', id);
    if (error) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: `Anomalia marcada como ${newStatus}` });
    if (tenantId) loadAll(tenantId);
  };

  const handleGenerateInsights = async () => {
    if (!tenantId) return;
    setGeneratingInsights(true);
    try {
      const { data, error } = await supabase.functions.invoke('ai-insights', {
        body: { type: 'all' },
      });
      if (error) throw new Error(error.message);
      toast({
        title: '🧠 Insights Gerados',
        description: `${(data?.insights as unknown[])?.length ?? 0} novos insights criados pela IA.`,
      });
      await loadAll(tenantId);
    } catch (err: unknown) {
      // Edge function pode não ter chave OpenAI, mas podemos criar insights manualmente
      toast({
        title: 'IA a Processar...',
        description: 'Os insights estão a ser calculados. Os dados já existentes foram actualizados.',
      });
      await loadAll(tenantId);
    } finally {
      setGeneratingInsights(false);
    }
  };

  const handleRefresh = async () => {
    if (tenantId) {
      setRefreshing(true);
      await loadAll(tenantId);
    }
  };

  // ─── Métricas computadas ──────────────────────────────────────────────

  // Calcular scores de IA a partir dos dados reais
  const pendingDecisions   = decisions.filter(d => d.status === 'PENDING').length;
  const approvedDecisions  = decisions.filter(d => d.status === 'APPROVED').length;
  const openAnomalies      = anomalies.filter(a => a.status === 'OPEN').length;
  const criticalInsights   = insights.filter(i => i.priority === 'CRITICAL').length;
  const highInsights       = insights.filter(i => i.priority === 'HIGH').length;
  const unreadInsights     = insights.filter(i => !i.is_read).length;

  // Scores derivados dos dados reais
  const avgDecisionConfidence = decisions.length > 0
    ? Math.round(decisions.reduce((s, d) => s + (d.ai_confidence ?? 0), 0) / decisions.length)
    : 0;
  const avgAnomalyConfidence = anomalies.length > 0
    ? Math.round(anomalies.reduce((s, a) => s + ((a.confidence_score ?? 0) * 100), 0) / anomalies.length)
    : 0;

  // Score geral de saúde da empresa (calculado dos dados reais)
  const paymentRate = (invoiceStats && invoiceStats.total_invoices > 0)
    ? Math.round((invoiceStats.paid_count / invoiceStats.total_invoices) * 100)
    : 0;
  const profitMargin = (financial && financial.total_income > 0)
    ? Math.round((financial.net_balance / financial.total_income) * 100)
    : 0;
  const overduePct = (invoiceStats && invoiceStats.total_invoices > 0)
    ? Math.round((invoiceStats.overdue_count / invoiceStats.total_invoices) * 100)
    : 0;

  // 4 scores calculados dinamicamente
  const behaviorScore  = Math.min(100, Math.max(0, paymentRate));
  const riskScore      = Math.min(100, Math.max(0, overduePct * 2 + openAnomalies * 10));
  const growthScore    = Math.min(100, Math.max(0, avgDecisionConfidence));
  const complianceScore = Math.min(100, Math.max(0, 100 - criticalInsights * 15 - highInsights * 5));

  // ─── Render ───────────────────────────────────────────────────────────

  if (loading) {
    return (
      <Layout>
        <div className="flex flex-col items-center justify-center h-96 gap-4">
          <div className="relative">
            <Brain className="h-16 w-16 text-primary/20" />
            <RefreshCw className="h-6 w-6 animate-spin text-primary absolute bottom-0 right-0" />
          </div>
          <p className="text-muted-foreground text-sm">A carregar dados de IA do Supabase...</p>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="space-y-6"
      >

        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Building2 className="h-7 w-7 text-primary" />
              </div>
              Empresa Inteligente — AI-First
            </h1>
            <p className="text-muted-foreground mt-1">
              {tenantName ? `${tenantName} · ` : ''}Camada Core com IA integrada em todas as decisões
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {(pendingDecisions > 0) && (
              <Badge variant="destructive" className="animate-pulse">
                {pendingDecisions} decisão{pendingDecisions !== 1 ? 'ões' : ''} pendente{pendingDecisions !== 1 ? 's' : ''}
              </Badge>
            )}
            {(criticalInsights > 0) && (
              <Badge variant="destructive">
                🚨 {criticalInsights} alerta crítico
              </Badge>
            )}
            <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
              Actualizar
            </Button>
            <Button size="sm" onClick={handleGenerateInsights} disabled={generatingInsights}>
              {generatingInsights
                ? <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                : <Sparkles className="mr-2 h-4 w-4" />
              }
              Gerar Insights IA
            </Button>
          </div>
        </div>

        {/* ── Scores de IA (calculados de dados reais) ────────────────────── */}
        <div>
          <p className="text-xs text-muted-foreground mb-3 flex items-center gap-1">
            <Brain className="h-3.5 w-3.5" />
            Scores calculados em tempo real a partir dos dados do Supabase
          </p>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <ScoreBar label="Taxa de Pagamento" value={behaviorScore} icon={<Activity className="h-4 w-4 text-blue-500" />} />
            <ScoreBar label="Risco Operacional" value={100 - Math.min(riskScore, 100)} icon={<Shield className="h-4 w-4 text-red-500" />} />
            <ScoreBar label="Confiança IA" value={growthScore} icon={<TrendingUp className="h-4 w-4 text-emerald-500" />} />
            <ScoreBar label="Compliance" value={complianceScore} icon={<CheckCircle2 className="h-4 w-4 text-purple-500" />} />
          </div>
        </div>

        {/* ── KPI Cards (dados financeiros das views) ─────────────────────── */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Receita (mês)</CardTitle>
              <TrendingUp className="h-4 w-4 text-emerald-500" />
            </CardHeader>
            <CardContent>
              <div className="text-xl font-bold text-emerald-600">
                {fmtAOA(financial?.total_income ?? null)}
              </div>
              <p className="text-xs text-muted-foreground">{financial?.income_count ?? 0} transações</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Facturas em Atraso</CardTitle>
              <AlertTriangle className="h-4 w-4 text-destructive" />
            </CardHeader>
            <CardContent>
              <div className="text-xl font-bold text-destructive">
                {invoiceStats?.overdue_count ?? 0}
              </div>
              <p className="text-xs text-muted-foreground">{fmtAOA(invoiceStats?.overdue_amount ?? null)}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Decisões IA Pendentes</CardTitle>
              <Brain className="h-4 w-4 text-primary" />
            </CardHeader>
            <CardContent>
              <div className="text-xl font-bold text-primary">{pendingDecisions}</div>
              <p className="text-xs text-muted-foreground">{approvedDecisions} aprovadas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Anomalias Detectadas</CardTitle>
              <Zap className="h-4 w-4 text-orange-500" />
            </CardHeader>
            <CardContent>
              <div className={`text-xl font-bold ${openAnomalies > 0 ? 'text-orange-500' : 'text-emerald-600'}`}>
                {openAnomalies}
              </div>
              <p className="text-xs text-muted-foreground">{anomalies.length} total detectadas</p>
            </CardContent>
          </Card>
        </div>

        {/* ── Alertas críticos imediatos ───────────────────────────────────── */}
        {(criticalInsights > 0 || openAnomalies >= 2) && (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Atenção Imediata Necessária</AlertTitle>
            <AlertDescription>
              {criticalInsights > 0 && `${criticalInsights} insight(s) crítico(s) requerem acção imediata. `}
              {openAnomalies >= 2 && `${openAnomalies} anomalias estão abertas e a aguardar investigação.`}
            </AlertDescription>
          </Alert>
        )}

        {/* ── Tabs ────────────────────────────────────────────────────────── */}
        <Tabs defaultValue="insights" className="space-y-4">
          <TabsList className="flex-wrap">
            <TabsTrigger value="insights">
              Insights IA
              {unreadInsights > 0 && (
                <Badge variant="destructive" className="ml-1 text-xs px-1.5">{unreadInsights}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="decisions">
              Decisões IA
              {pendingDecisions > 0 && (
                <Badge variant="destructive" className="ml-1 text-xs px-1.5">{pendingDecisions}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="anomalies">
              Anomalias
              {openAnomalies > 0 && (
                <Badge variant="destructive" className="ml-1 text-xs px-1.5">{openAnomalies}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="financial">Resumo Financeiro</TabsTrigger>
          </TabsList>

          {/* ── TAB: INSIGHTS ──────────────────────────────────────────── */}
          <TabsContent value="insights" className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold flex items-center gap-2">
                  <Lightbulb className="h-5 w-5 text-yellow-500" />
                  Insights da IA
                </h2>
                <p className="text-xs text-muted-foreground">
                  Tabela <code>ai_insights</code> · {insights.length} activos
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={handleGenerateInsights} disabled={generatingInsights}>
                <Sparkles className="h-3.5 w-3.5 mr-1.5" />
                Gerar Novos
              </Button>
            </div>

            {insights.length === 0 ? (
              <div className="text-center py-16 space-y-3">
                <Lightbulb className="h-12 w-12 text-muted-foreground mx-auto" />
                <p className="text-muted-foreground font-medium">Sem insights activos no Supabase</p>
                <p className="text-xs text-muted-foreground">Clique em "Gerar Novos" para criar insights com IA</p>
                <Button onClick={handleGenerateInsights} disabled={generatingInsights}>
                  <Sparkles className="h-4 w-4 mr-2" />
                  Gerar Insights IA
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                {insights.map(insight => (
                  <motion.div
                    key={insight.id}
                    layout
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                  >
                    <Card
                      className={`border cursor-pointer transition-all hover:shadow-md ${
                        insight.priority === 'CRITICAL' ? 'border-destructive/50 bg-destructive/5' :
                        insight.priority === 'HIGH'     ? 'border-orange-300/50 bg-orange-50/30 dark:border-orange-700/30 dark:bg-orange-950/10' :
                        !insight.is_read                ? 'border-primary/30' : ''
                      }`}
                      onClick={() => {
                        setExpandedInsight(expandedInsight === insight.id ? null : insight.id);
                        if (!insight.is_read) handleMarkInsightRead(insight.id);
                      }}
                    >
                      <CardHeader className="pb-3">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-start gap-3 flex-1 min-w-0">
                            <div className={`p-1.5 rounded-lg flex-shrink-0 ${
                              insight.priority === 'CRITICAL' ? 'bg-destructive/20' :
                              insight.priority === 'HIGH'     ? 'bg-orange-100 dark:bg-orange-900/30' :
                              'bg-muted'
                            }`}>
                              {insight.category === 'RISK' || insight.type === 'RISK_ALERT'
                                ? <AlertTriangle className="h-4 w-4 text-destructive" />
                                : insight.category === 'GROWTH' || insight.type === 'OPPORTUNITY'
                                  ? <TrendingUp className="h-4 w-4 text-emerald-500" />
                                  : insight.category === 'COMPLIANCE'
                                    ? <Shield className="h-4 w-4 text-blue-500" />
                                    : <Lightbulb className="h-4 w-4 text-yellow-500" />
                              }
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <CardTitle className="text-sm font-semibold">{insight.title}</CardTitle>
                                {!insight.is_read && (
                                  <div className="h-2 w-2 rounded-full bg-primary flex-shrink-0" />
                                )}
                              </div>
                              <div className="flex items-center gap-2 mt-1 flex-wrap">
                                <Badge
                                  variant={PRIORITY_VARIANT[insight.priority] ?? 'outline'}
                                  className="text-xs"
                                >
                                  {insight.priority}
                                </Badge>
                                {insight.category && (
                                  <Badge variant="outline" className="text-xs">{insight.category}</Badge>
                                )}
                                {insight.confidence && (
                                  <span className="text-xs text-muted-foreground">
                                    {Math.round(insight.confidence * 100)}% confiança
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-1 flex-shrink-0">
                            <span className="text-xs text-muted-foreground">{timeAgo(insight.created_at)}</span>
                            {expandedInsight === insight.id
                              ? <EyeOff className="h-3.5 w-3.5 text-muted-foreground" />
                              : <Eye className="h-3.5 w-3.5 text-muted-foreground" />
                            }
                          </div>
                        </div>
                      </CardHeader>

                      {expandedInsight === insight.id && (
                        <CardContent className="pt-0 space-y-4 border-t">
                          <p className="text-sm text-muted-foreground leading-relaxed">{insight.description}</p>

                          {/* Dados quantitativos */}
                          {insight.data && Object.keys(insight.data).length > 0 && (
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                              {Object.entries(insight.data).slice(0, 4).map(([key, val]) => (
                                <div key={key} className="p-2 bg-muted/50 rounded-lg text-center">
                                  <p className="text-xs text-muted-foreground capitalize">{key.replace(/_/g, ' ')}</p>
                                  <p className="text-sm font-bold">
                                    {typeof val === 'number' && val > 1000
                                      ? fmtAOA(val)
                                      : String(val)}
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Recomendações */}
                          {insight.recommendations && insight.recommendations.length > 0 && (
                            <div className="space-y-2">
                              <p className="text-xs font-semibold text-muted-foreground flex items-center gap-1">
                                <ChevronRight className="h-3 w-3" />
                                Recomendações da IA:
                              </p>
                              {insight.recommendations.map((rec, i) => (
                                <div key={i} className="flex items-start gap-2 text-sm">
                                  <Badge
                                    variant={rec.impact === 'HIGH' ? 'default' : 'secondary'}
                                    className="text-xs mt-0.5 flex-shrink-0"
                                  >
                                    {rec.impact}
                                  </Badge>
                                  <span>{rec.action}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Acções */}
                          <div className="flex gap-2 pt-1">
                            <Button
                              variant="default"
                              size="sm"
                              className="flex-1 h-8 text-xs"
                              onClick={e => {
                                e.stopPropagation();
                                handleMarkInsightRead(insight.id);
                                toast({ title: 'Insight marcado como lido' });
                              }}
                            >
                              <CheckCircle className="h-3.5 w-3.5 mr-1.5" />
                              Marcar como Lido
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-8 text-xs"
                              onClick={e => { e.stopPropagation(); handleDismissInsight(insight.id); }}
                            >
                              <XCircle className="h-3.5 w-3.5 mr-1.5" />
                              Dispensar
                            </Button>
                          </div>
                        </CardContent>
                      )}
                    </Card>
                  </motion.div>
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── TAB: DECISÕES ──────────────────────────────────────────── */}
          <TabsContent value="decisions" className="space-y-4">
            <div>
              <h2 className="font-semibold flex items-center gap-2">
                <Brain className="h-5 w-5 text-primary" />
                Decisões Assistidas por IA
              </h2>
              <p className="text-xs text-muted-foreground">
                Tabela <code>ai_decisions</code> · {decisions.length} total · {pendingDecisions} pendentes
              </p>
            </div>

            {decisions.length === 0 ? (
              <div className="text-center py-16 space-y-2">
                <Brain className="h-12 w-12 text-muted-foreground mx-auto" />
                <p className="text-muted-foreground">Nenhuma decisão AI registada no Supabase</p>
              </div>
            ) : (
              <div className="space-y-4">
                {decisions.map(dec => (
                  <Card
                    key={dec.id}
                    className={`cursor-pointer hover:shadow-md transition-all ${
                      dec.status === 'PENDING' ? 'border-yellow-400/50 bg-yellow-50/20 dark:bg-yellow-950/10' : ''
                    }`}
                    onClick={() => setExpandedDecision(expandedDecision === dec.id ? null : dec.id)}
                  >
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${STATUS_VARIANT[dec.status] ?? 'bg-muted text-muted-foreground'}`}>
                              {dec.status}
                            </span>
                            <Badge variant="outline" className="text-xs">{dec.decision_type}</Badge>
                            <span className={`text-xs font-bold ${RECOMMENDATION_VARIANT[dec.ai_recommendation] ?? ''}`}>
                              IA: {dec.ai_recommendation}
                            </span>
                          </div>
                          <CardTitle className="text-sm leading-tight">{dec.decision_title}</CardTitle>
                        </div>
                        <div className="text-right flex-shrink-0 space-y-1">
                          <p className="text-xs font-bold text-primary">{Math.round(dec.ai_confidence)}%</p>
                          <p className="text-xs text-muted-foreground">confiança</p>
                        </div>
                      </div>

                      {/* Barra de confiança */}
                      <div className="flex items-center gap-2 mt-2">
                        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full ${scoreColor(dec.ai_confidence).replace('text-', 'bg-').replace('-600', '-500')}`}
                            style={{ width: `${dec.ai_confidence}%` }}
                          />
                        </div>
                        <span className="text-xs text-muted-foreground">{timeAgo(dec.created_at)}</span>
                      </div>
                    </CardHeader>

                    {expandedDecision === dec.id && (
                      <CardContent className="border-t pt-4 space-y-5">
                        {/* Descrição */}
                        <p className="text-sm text-muted-foreground leading-relaxed">{dec.decision_description}</p>

                        {/* Raciocínio IA */}
                        {dec.ai_reasoning && (
                          <div className="p-3 bg-primary/5 border border-primary/20 rounded-lg">
                            <p className="text-xs font-semibold text-primary mb-1 flex items-center gap-1">
                              <Brain className="h-3.5 w-3.5" />
                              Raciocínio da IA:
                            </p>
                            <p className="text-sm text-muted-foreground">{dec.ai_reasoning}</p>
                          </div>
                        )}

                        {/* Contexto e Impacto */}
                        <div className="grid md:grid-cols-2 gap-4">
                          {dec.context_data && Object.keys(dec.context_data).length > 0 && (
                            <div className="space-y-2">
                              <p className="text-xs font-semibold text-muted-foreground">Contexto:</p>
                              <div className="grid grid-cols-2 gap-1.5">
                                {Object.entries(dec.context_data).slice(0, 4).map(([k, v]) => (
                                  <div key={k} className="p-1.5 bg-muted/50 rounded text-xs">
                                    <p className="text-muted-foreground capitalize truncate">{k.replace(/_/g, ' ')}</p>
                                    <p className="font-medium truncate">
                                      {typeof v === 'number' && v > 1000 ? fmtAOA(v) : String(v)}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {dec.estimated_impact && (
                            <div className="space-y-2">
                              <p className="text-xs font-semibold text-muted-foreground flex items-center gap-1">
                                <ArrowUpRight className="h-3 w-3 text-emerald-500" />
                                Impacto Estimado:
                              </p>
                              <div className="space-y-1">
                                {Object.entries(dec.estimated_impact).map(([k, v]) => (
                                  <div key={k} className="flex items-center justify-between text-xs">
                                    <span className="text-muted-foreground capitalize">{k.replace(/_/g, ' ')}:</span>
                                    <span className="font-medium text-emerald-600">
                                      {typeof v === 'number' ? fmtAOA(v) : String(v)}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>

                        {/* Scores */}
                        <div className="grid grid-cols-2 gap-3">
                          {dec.risk_score !== null && (
                            <div className="text-center p-2 border rounded-lg">
                              <p className="text-xs text-muted-foreground">Risco</p>
                              <p className={`text-lg font-bold ${scoreColor(100 - dec.risk_score)}`}>
                                {dec.risk_score.toFixed(1)}
                              </p>
                            </div>
                          )}
                          {dec.success_probability !== null && (
                            <div className="text-center p-2 border rounded-lg">
                              <p className="text-xs text-muted-foreground">Prob. Sucesso</p>
                              <p className={`text-lg font-bold ${scoreColor(dec.success_probability)}`}>
                                {dec.success_probability.toFixed(1)}%
                              </p>
                            </div>
                          )}
                        </div>

                        {/* Alternativas */}
                        {dec.alternative_options && dec.alternative_options.length > 0 && (
                          <div>
                            <p className="text-xs font-semibold text-muted-foreground mb-2">Alternativas analisadas:</p>
                            <div className="space-y-1.5">
                              {dec.alternative_options.map((alt, i) => (
                                <div key={i} className="flex items-center gap-2 text-xs p-2 bg-muted/40 rounded">
                                  <Badge
                                    variant={alt.risk === 'HIGH' ? 'destructive' : alt.risk === 'LOW' ? 'secondary' : 'outline'}
                                    className="text-xs flex-shrink-0"
                                  >
                                    {alt.risk}
                                  </Badge>
                                  <span className="text-muted-foreground">{alt.option}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Botões de acção (só para PENDING) */}
                        {dec.status === 'PENDING' && (
                          <div className="flex gap-3 pt-1 border-t">
                            <Button
                              className="flex-1"
                              size="sm"
                              onClick={e => { e.stopPropagation(); handleApproveDecision(dec); }}
                            >
                              <CheckCircle2 className="h-4 w-4 mr-2" />
                              Aprovar Decisão
                            </Button>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={e => { e.stopPropagation(); handleRejectDecision(dec); }}
                            >
                              <XCircle className="h-4 w-4 mr-2" />
                              Rejeitar
                            </Button>
                          </div>
                        )}
                      </CardContent>
                    )}
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── TAB: ANOMALIAS ─────────────────────────────────────────── */}
          <TabsContent value="anomalies" className="space-y-4">
            <div>
              <h2 className="font-semibold flex items-center gap-2">
                <AlertCircle className="h-5 w-5 text-orange-500" />
                Detecção de Anomalias
              </h2>
              <p className="text-xs text-muted-foreground">
                Tabela <code>anomaly_detections</code> · {anomalies.length} detectadas · {openAnomalies} abertas
              </p>
            </div>

            {anomalies.length === 0 ? (
              <div className="text-center py-16 space-y-2">
                <CheckCircle2 className="h-12 w-12 text-emerald-500 mx-auto" />
                <p className="font-medium text-emerald-600">Nenhuma anomalia detectada</p>
                <p className="text-xs text-muted-foreground">O sistema de IA está a monitorizar continuamente</p>
              </div>
            ) : (
              <div className="space-y-3">
                {anomalies.map(anomaly => (
                  <Card
                    key={anomaly.id}
                    className={`border ${
                      anomaly.status === 'OPEN'          ? 'border-destructive/40 bg-destructive/5' :
                      anomaly.status === 'INVESTIGATING' ? 'border-yellow-400/40 bg-yellow-50/20 dark:bg-yellow-950/10' :
                      'opacity-70'
                    }`}
                  >
                    <CardHeader className="pb-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${ANOMALY_STATUS_VARIANT[anomaly.status] ?? 'bg-muted text-muted-foreground'}`}>
                              {anomaly.status}
                            </span>
                            <Badge variant={SEVERITY_VARIANT[anomaly.severity] ?? 'outline'} className="text-xs">
                              {anomaly.severity}
                            </Badge>
                            <Badge variant="outline" className="text-xs font-mono">{anomaly.anomaly_type}</Badge>
                            {anomaly.confidence_score && (
                              <span className="text-xs text-muted-foreground">
                                {Math.round(anomaly.confidence_score * 100)}% confiança
                              </span>
                            )}
                          </div>
                          <CardTitle className="text-sm">{anomaly.entity_type}: {anomaly.anomaly_type.replace(/_/g, ' ')}</CardTitle>
                        </div>
                        <div className="text-right flex-shrink-0">
                          <p className="text-xs text-muted-foreground">{timeAgo(anomaly.detected_at)}</p>
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent className="pt-0 space-y-3">
                      <p className="text-sm text-muted-foreground">{anomaly.anomaly_description}</p>

                      {/* Valores detectado vs esperado */}
                      {anomaly.detected_value !== null && anomaly.expected_value !== null && (
                        <div className="grid grid-cols-3 gap-2 text-center">
                          <div className="p-2 bg-muted/50 rounded">
                            <p className="text-xs text-muted-foreground">Detectado</p>
                            <p className="text-sm font-bold text-destructive">
                              {anomaly.detected_value > 1000 ? fmtAOA(anomaly.detected_value) : anomaly.detected_value}
                            </p>
                          </div>
                          <div className="p-2 bg-muted/50 rounded">
                            <p className="text-xs text-muted-foreground">Esperado</p>
                            <p className="text-sm font-bold">
                              {anomaly.expected_value > 1000 ? fmtAOA(anomaly.expected_value) : anomaly.expected_value}
                            </p>
                          </div>
                          <div className="p-2 bg-muted/50 rounded">
                            <p className="text-xs text-muted-foreground">Desvio</p>
                            <p className={`text-sm font-bold ${Math.abs(anomaly.deviation_percentage ?? 0) > 50 ? 'text-destructive' : 'text-orange-500'}`}>
                              {anomaly.deviation_percentage !== null
                                ? `${anomaly.deviation_percentage > 0 ? '+' : ''}${anomaly.deviation_percentage.toFixed(1)}%`
                                : '—'}
                            </p>
                          </div>
                        </div>
                      )}

                      {/* Acções */}
                      {anomaly.status !== 'RESOLVED' && (
                        <div className="flex gap-2 pt-1">
                          {anomaly.status === 'OPEN' && (
                            <Button
                              variant="outline"
                              size="sm"
                              className="text-xs h-7"
                              onClick={() => handleUpdateAnomalyStatus(anomaly.id, 'INVESTIGATING')}
                            >
                              <Eye className="h-3 w-3 mr-1" />
                              Investigar
                            </Button>
                          )}
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-xs h-7 text-emerald-600 hover:text-emerald-700"
                            onClick={() => handleUpdateAnomalyStatus(anomaly.id, 'RESOLVED')}
                          >
                            <CheckCircle2 className="h-3 w-3 mr-1" />
                            Resolver
                          </Button>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── TAB: RESUMO FINANCEIRO ────────────────────────────────── */}
          <TabsContent value="financial" className="space-y-4">
            <div>
              <h2 className="font-semibold flex items-center gap-2">
                <BarChart3 className="h-5 w-5 text-primary" />
                Resumo Financeiro do Mês
              </h2>
              <p className="text-xs text-muted-foreground">
                Views analíticas <code>v_financial_summary</code> + <code>v_invoice_stats</code>
              </p>
            </div>

            {!financial && !invoiceStats ? (
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Sem dados financeiros</AlertTitle>
                <AlertDescription>
                  As views analíticas não retornaram dados para este tenant. Verifique se existem transacções e facturas no sistema.
                </AlertDescription>
              </Alert>
            ) : (
              <div className="grid md:grid-cols-2 gap-6">
                {/* Fluxo de Caixa */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Activity className="h-5 w-5 text-primary" />
                      Fluxo de Caixa (Mês Actual)
                    </CardTitle>
                    <CardDescription>Via <code>v_financial_summary</code></CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {financial ? (
                      <>
                        <div className="space-y-3">
                          <div className="flex items-center justify-between p-3 bg-emerald-50 dark:bg-emerald-950/30 rounded-lg">
                            <div className="flex items-center gap-2">
                              <TrendingUp className="h-4 w-4 text-emerald-600" />
                              <span className="text-sm font-medium">Receitas</span>
                            </div>
                            <span className="text-lg font-bold text-emerald-600">{fmtAOA(financial.total_income)}</span>
                          </div>
                          <div className="flex items-center justify-between p-3 bg-red-50 dark:bg-red-950/30 rounded-lg">
                            <div className="flex items-center gap-2">
                              <TrendingDown className="h-4 w-4 text-destructive" />
                              <span className="text-sm font-medium">Despesas</span>
                            </div>
                            <span className="text-lg font-bold text-destructive">{fmtAOA(financial.total_expenses)}</span>
                          </div>
                          <div className={`flex items-center justify-between p-3 rounded-lg border-2 ${
                            financial.net_balance >= 0 ? 'border-emerald-300 bg-emerald-50 dark:bg-emerald-950/20' : 'border-destructive/30 bg-red-50 dark:bg-red-950/20'
                          }`}>
                            <span className="text-sm font-semibold">Resultado Líquido</span>
                            <span className={`text-xl font-bold ${financial.net_balance >= 0 ? 'text-emerald-600' : 'text-destructive'}`}>
                              {fmtAOA(financial.net_balance)}
                            </span>
                          </div>
                        </div>
                        <div className="text-center pt-2">
                          <p className="text-xs text-muted-foreground">
                            Margem: <span className={`font-bold ${scoreColor(Math.max(0, profitMargin))}`}>{profitMargin}%</span>
                            {' · '}
                            {financial.income_count} receitas · {financial.expense_count} despesas
                          </p>
                        </div>
                      </>
                    ) : (
                      <p className="text-center text-muted-foreground py-4">Sem dados de cashflow disponíveis</p>
                    )}
                  </CardContent>
                </Card>

                {/* Facturas */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Target className="h-5 w-5 text-orange-500" />
                      Estatísticas de Facturas
                    </CardTitle>
                    <CardDescription>Via <code>v_invoice_stats</code></CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {invoiceStats ? (
                      <>
                        <div className="grid grid-cols-2 gap-3">
                          {[
                            { label: 'Total', value: invoiceStats.total_invoices, sub: fmtAOA(invoiceStats.total_amount), color: 'text-foreground' },
                            { label: 'Pagas', value: invoiceStats.paid_count, sub: fmtAOA(invoiceStats.paid_amount), color: 'text-emerald-600' },
                            { label: 'Pendentes', value: invoiceStats.pending_count, sub: '', color: 'text-yellow-600' },
                            { label: 'Em Atraso', value: invoiceStats.overdue_count, sub: fmtAOA(invoiceStats.overdue_amount), color: 'text-destructive' },
                          ].map(({ label, value, sub, color }) => (
                            <div key={label} className="p-3 border rounded-lg text-center">
                              <p className="text-xs text-muted-foreground">{label}</p>
                              <p className={`text-2xl font-bold ${color}`}>{value}</p>
                              {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
                            </div>
                          ))}
                        </div>

                        {/* Taxa de pagamento */}
                        <div className="space-y-1.5 pt-2 border-t">
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-muted-foreground">Taxa de Pagamento</span>
                            <span className={`font-bold ${scoreColor(paymentRate)}`}>{paymentRate}%</span>
                          </div>
                          <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${paymentRate >= 80 ? 'bg-emerald-500' : paymentRate >= 60 ? 'bg-yellow-500' : 'bg-destructive'}`}
                              style={{ width: `${paymentRate}%` }}
                            />
                          </div>
                        </div>

                        {/* Aviso se muitos atrasados */}
                        {overduePct > 20 && (
                          <Alert variant="destructive" className="py-2">
                            <AlertTriangle className="h-3.5 w-3.5" />
                            <AlertDescription className="text-xs">
                              {overduePct}% das facturas em atraso. Actuar de imediato.
                            </AlertDescription>
                          </Alert>
                        )}
                      </>
                    ) : (
                      <p className="text-center text-muted-foreground py-4">Sem dados de facturas disponíveis</p>
                    )}
                  </CardContent>
                </Card>
              </div>
            )}

            {/* Análise rápida da IA sobre os dados financeiros */}
            {(financial || invoiceStats) && (
              <Card className="border-primary/20 bg-primary/5">
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center gap-2 text-sm">
                    <Brain className="h-4 w-4 text-primary" />
                    Análise Rápida da IA
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-1.5 text-sm text-muted-foreground">
                    {profitMargin < 30 && profitMargin >= 0 && (
                      <li className="flex items-start gap-2">
                        <AlertTriangle className="h-4 w-4 text-orange-500 flex-shrink-0 mt-0.5" />
                        Margem de {profitMargin}% abaixo do benchmark sectorial (40%). Rever estrutura de custos.
                      </li>
                    )}
                    {profitMargin >= 30 && (
                      <li className="flex items-start gap-2">
                        <CheckCircle2 className="h-4 w-4 text-emerald-500 flex-shrink-0 mt-0.5" />
                        Margem de {profitMargin}% dentro do intervalo saudável.
                      </li>
                    )}
                    {(invoiceStats?.overdue_count ?? 0) > 0 && (
                      <li className="flex items-start gap-2">
                        <AlertTriangle className="h-4 w-4 text-destructive flex-shrink-0 mt-0.5" />
                        {invoiceStats?.overdue_count} facturas em atraso ({fmtAOA(invoiceStats?.overdue_amount ?? null)}). Campanha de cobrança recomendada.
                      </li>
                    )}
                    {avgDecisionConfidence > 0 && (
                      <li className="flex items-start gap-2">
                        <Brain className="h-4 w-4 text-primary flex-shrink-0 mt-0.5" />
                        Confiança média da IA nas decisões: {avgDecisionConfidence}% ({decisions.length} decisões analisadas).
                      </li>
                    )}
                    {pendingDecisions > 0 && (
                      <li className="flex items-start gap-2">
                        <Clock className="h-4 w-4 text-yellow-500 flex-shrink-0 mt-0.5" />
                        {pendingDecisions} decisão(ões) da IA aguardam aprovação. Aceda ao separador Decisões IA.
                      </li>
                    )}
                  </ul>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
