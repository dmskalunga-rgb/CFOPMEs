// AIDecisions — Decisões com IA (dados reais Supabase)
import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Brain, TrendingUp, AlertTriangle, CheckCircle, RefreshCw,
  Clock, Target, DollarSign, Users, Zap, ChevronDown, ChevronUp,
  X, ThumbsUp, ThumbsDown, Eye, BarChart2, Shield, Activity,
  FileText, Settings, ArrowRight, AlertCircle
} from 'lucide-react';
import { toast } from 'sonner';
import { supabase } from '@/integrations/supabase/client';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar,
  PieChart, Pie, Cell, Legend
} from 'recharts';

// ─── Tipos ─────────────────────────────────────────────────────────────────
interface AIDecision {
  id: string;
  tenant_id: string;
  user_id: string;
  decision_type: string;
  decision_title: string;
  decision_description: string | null;
  context_data: Record<string, unknown> | null;
  ai_recommendation: 'APPROVE' | 'REJECT' | 'REVIEW' | 'DEFER';
  ai_confidence: number | null;
  ai_reasoning: string | null;
  risk_score: number | null;
  risk_factors: Array<{ factor: string; severity: string; mitigation?: string; amount?: number }> | null;
  success_probability: number | null;
  estimated_impact: Record<string, unknown> | null;
  alternative_options: Array<Record<string, unknown>> | null;
  user_decision: string | null;
  user_notes: string | null;
  status: 'PENDING' | 'DECIDED' | 'IMPLEMENTED' | 'EVALUATED';
  created_at: string;
  updated_at: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  try {
    const { data: rpcData } = await supabase.rpc('get_current_tenant_id');
    if (rpcData) return rpcData as string;
  } catch { /* continuar */ }
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) {
      const { data: profile } = await supabase
        .from('users').select('tenant_id').eq('id', user.id).single();
      if (profile?.tenant_id) return profile.tenant_id as string;
    }
  } catch { /* continuar */ }
  try {
    const { data: t } = await supabase.from('tenants').select('id').limit(1).single();
    if (t?.id) return t.id as string;
  } catch { /* continuar */ }
  return '';
}

function formatKz(value: number | null | undefined): string {
  const n = Number(value ?? 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M Kz`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K Kz`;
  return `${n.toFixed(0)} Kz`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('pt-AO', {
    day: '2-digit', month: 'short', year: 'numeric',
  });
}

const DECISION_TYPE_LABELS: Record<string, { label: string; icon: React.ComponentType<{ className?: string }> }> = {
  approve_expense:  { label: 'Despesa',     icon: DollarSign },
  hire_employee:    { label: 'RH',          icon: Users },
  invest:           { label: 'Investimento',icon: TrendingUp },
  approve_invoice:  { label: 'Fatura',      icon: FileText },
  custom:           { label: 'Operações',   icon: Settings },
};

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  PENDING:     { label: 'Pendente',    color: 'text-yellow-700', bg: 'bg-yellow-100' },
  DECIDED:     { label: 'Decidido',    color: 'text-blue-700',   bg: 'bg-blue-100'   },
  IMPLEMENTED: { label: 'Implementado',color: 'text-green-700',  bg: 'bg-green-100'  },
  EVALUATED:   { label: 'Avaliado',    color: 'text-purple-700', bg: 'bg-purple-100' },
};

const REC_CONFIG: Record<string, { label: string; color: string; icon: React.ComponentType<{ className?: string }> }> = {
  APPROVE: { label: 'Aprovar',   color: 'text-green-700 bg-green-100',  icon: CheckCircle },
  REJECT:  { label: 'Rejeitar',  color: 'text-red-700   bg-red-100',    icon: X },
  REVIEW:  { label: 'Rever',     color: 'text-yellow-700 bg-yellow-100',icon: Eye },
  DEFER:   { label: 'Adiar',     color: 'text-gray-700  bg-gray-100',   icon: Clock },
};

const RISK_COLORS: Record<string, string> = {
  VERY_LOW: 'text-green-600',
  LOW:      'text-blue-600',
  MEDIUM:   'text-yellow-600',
  HIGH:     'text-orange-600',
  CRITICAL: 'text-red-600',
};

const CHART_COLORS = ['#8B5CF6','#3B82F6','#10B981','#F59E0B','#EF4444','#EC4899'];

// ─── Componente Card de Decisão ─────────────────────────────────────────
function DecisionCard({
  decision,
  onDecide,
}: {
  decision: AIDecision;
  onDecide: (id: string, action: 'APPROVED' | 'REJECTED' | 'DEFERRED', notes?: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const typeInfo = DECISION_TYPE_LABELS[decision.decision_type] ?? { label: 'Outro', icon: Brain };
  const TypeIcon = typeInfo.icon;
  const recInfo = REC_CONFIG[decision.ai_recommendation] ?? REC_CONFIG.REVIEW;
  const RecIcon = recInfo.icon;
  const statusInfo = STATUS_CONFIG[decision.status] ?? STATUS_CONFIG.PENDING;

  const impactValue: number = (() => {
    const imp = decision.estimated_impact ?? {};
    const v = imp?.financial ?? imp?.revenue_increase_pct ?? imp?.monthly_savings;
    return typeof v === 'number' ? v : 0;
  })();

  return (
    <div className="border rounded-xl p-5 space-y-4 bg-card hover:shadow-md transition-shadow">
      {/* Cabeçalho */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1 min-w-0">
          <div className="p-2 rounded-lg bg-primary/10 shrink-0">
            <TypeIcon className="h-5 w-5 text-primary" />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-base leading-tight">{decision.decision_title}</h3>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${statusInfo.bg} ${statusInfo.color}`}>
                {statusInfo.label}
              </span>
              <span className="text-xs text-muted-foreground">
                {typeInfo.label}
              </span>
              <span className="text-xs text-muted-foreground">
                {formatDate(decision.created_at)}
              </span>
            </div>
          </div>
        </div>
        {/* Recomendação IA */}
        <div className={`flex items-center gap-1 text-xs font-semibold px-3 py-1 rounded-full shrink-0 ${recInfo.color}`}>
          <RecIcon className="h-3.5 w-3.5" />
          <span>{recInfo.label}</span>
        </div>
      </div>

      {/* Métricas em linha */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-muted/40 rounded-lg p-3 text-center">
          <div className="text-xs text-muted-foreground mb-1">Confiança IA</div>
          <div className="text-xl font-bold text-primary">{(decision.ai_confidence ?? 0).toFixed(0)}%</div>
          <Progress value={decision.ai_confidence ?? 0} className="mt-1 h-1" />
        </div>
        <div className="bg-muted/40 rounded-lg p-3 text-center">
          <div className="text-xs text-muted-foreground mb-1">Risco</div>
          <div className={`text-xl font-bold ${
            (decision.risk_score ?? 50) < 20 ? 'text-green-600' :
            (decision.risk_score ?? 50) < 40 ? 'text-blue-600' :
            (decision.risk_score ?? 50) < 60 ? 'text-yellow-600' : 'text-red-600'
          }`}>{(decision.risk_score ?? 0).toFixed(0)}%</div>
          <Progress value={decision.risk_score ?? 0} className="mt-1 h-1" />
        </div>
        <div className="bg-muted/40 rounded-lg p-3 text-center">
          <div className="text-xs text-muted-foreground mb-1">Sucesso</div>
          <div className="text-xl font-bold text-green-600">{(decision.success_probability ?? 0).toFixed(0)}%</div>
          <Progress value={decision.success_probability ?? 0} className="mt-1 h-1" />
        </div>
      </div>

      {/* Impacto estimado */}
      {impactValue > 0 && (
        <div className="flex items-center gap-2 text-sm">
          <TrendingUp className="h-4 w-4 text-green-600 shrink-0" />
          <span className="font-medium text-green-700">
            Impacto financeiro estimado: {formatKz(impactValue)}
          </span>
        </div>
      )}

      {/* Reasoning da IA */}
      <div className="bg-purple-50 dark:bg-purple-950/20 rounded-lg p-3 border border-purple-200 dark:border-purple-800">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-purple-700 dark:text-purple-400 mb-1">
          <Brain className="h-3.5 w-3.5" />
          Raciocínio da IA
        </div>
        <p className="text-xs text-purple-800 dark:text-purple-300 leading-relaxed line-clamp-3">
          {decision.ai_reasoning ?? 'Análise em processamento...'}
        </p>
      </div>

      {/* Expansão — Detalhes */}
      {expanded && (
        <div className="space-y-3 border-t pt-3">
          {/* Descrição */}
          {decision.decision_description && (
            <p className="text-sm text-muted-foreground leading-relaxed">
              {decision.decision_description}
            </p>
          )}

          {/* Factores de risco */}
          {Array.isArray(decision.risk_factors) && (decision.risk_factors?.length ?? 0) > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1">
                <Shield className="h-3.5 w-3.5" /> Factores de Risco
              </div>
              <div className="space-y-1.5">
                {decision.risk_factors.map((rf, i) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className={`font-medium shrink-0 ${RISK_COLORS[rf.severity] ?? 'text-gray-600'}`}>
                      [{rf.severity}]
                    </span>
                    <span className="text-foreground">{rf.factor}</span>
                    {rf.mitigation && (
                      <span className="text-muted-foreground">→ {rf.mitigation}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Alternativas */}
          {Array.isArray(decision.alternative_options) && (decision.alternative_options?.length ?? 0) > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1">
                <ArrowRight className="h-3.5 w-3.5" /> Alternativas
              </div>
              <div className="space-y-1.5">
                {decision.alternative_options.map((alt, i) => {
                  const altStr = typeof alt.option === 'string' ? alt.option : JSON.stringify(alt);
                  return (
                    <div key={i} className="text-xs bg-muted/50 rounded px-2 py-1.5">
                      {altStr}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Acções */}
      <div className="flex items-center gap-2 pt-1">
        {decision.status === 'PENDING' && (
          <>
            <Button size="sm" onClick={() => onDecide(decision.id, 'APPROVED')} className="gap-1">
              <ThumbsUp className="h-3.5 w-3.5" /> Aprovar
            </Button>
            <Button size="sm" variant="destructive" onClick={() => onDecide(decision.id, 'REJECTED')} className="gap-1">
              <ThumbsDown className="h-3.5 w-3.5" /> Rejeitar
            </Button>
            <Button size="sm" variant="outline" onClick={() => onDecide(decision.id, 'DEFERRED')} className="gap-1">
              <Clock className="h-3.5 w-3.5" /> Adiar
            </Button>
          </>
        )}
        <Button
          size="sm" variant="ghost"
          className="ml-auto gap-1"
          onClick={() => setExpanded(v => !v)}
        >
          {expanded ? <><ChevronUp className="h-3.5 w-3.5" /> Menos</> : <><ChevronDown className="h-3.5 w-3.5" /> Detalhes</>}
        </Button>
      </div>
    </div>
  );
}

// ─── Componente Principal ──────────────────────────────────────────────────
export default function AIDecisions() {
  const [decisions, setDecisions] = useState<AIDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [tenantId, setTenantId] = useState('');
  const [activeTab, setActiveTab] = useState('pending');
  const [filterType, setFilterType] = useState<string>('all');

  // ── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async (tid?: string) => {
    setLoading(true);
    try {
      const resolvedTid = tid ?? tenantId;
      if (!resolvedTid) return;

      const { data, error } = await supabase
        .from('ai_decisions')
        .select('*')
        .eq('tenant_id', resolvedTid)
        .order('created_at', { ascending: false });

      if (error) throw error;
      setDecisions((data ?? []) as AIDecision[]);
    } catch (err) {
      console.error('Erro ao carregar decisões:', err);
      toast.error('Erro ao carregar decisões. Tente novamente.');
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => {
    getTenantId().then(tid => {
      setTenantId(tid);
      loadData(tid);
    });
  }, []);

  // ── Acção do utilizador numa decisão ───────────────────────────────────
  const handleDecide = async (id: string, action: 'APPROVED' | 'REJECTED' | 'DEFERRED', notes?: string) => {
    try {
      const { error } = await supabase
        .from('ai_decisions')
        .update({
          user_decision: action,
          user_notes: notes ?? null,
          status: 'DECIDED',
          decision_made_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })
        .eq('id', id);

      if (error) throw error;

      setDecisions(prev => prev.map(d =>
        d.id === id ? { ...d, user_decision: action, status: 'DECIDED' } : d
      ));

      const labels = { APPROVED: 'aprovada', REJECTED: 'rejeitada', DEFERRED: 'adiada' };
      toast.success(`Decisão ${labels[action]} com sucesso!`);
    } catch (err) {
      console.error('Erro ao actualizar decisão:', err);
      toast.error('Erro ao actualizar decisão.');
    }
  };

  // ── KPIs calculados ─────────────────────────────────────────────────────
  const pending   = decisions.filter(d => d.status === 'PENDING');
  const decided   = decisions.filter(d => d.status === 'DECIDED');
  const implemented = decisions.filter(d => d.status === 'IMPLEMENTED');
  const totalImpact = decisions.reduce((sum, d) => {
    const imp = d.estimated_impact ?? {};
    const v = imp?.financial ?? imp?.monthly_savings;
    return sum + (typeof v === 'number' ? v : 0);
  }, 0);
  const avgConfidence = decisions.length
    ? decisions.reduce((s, d) => s + (d.ai_confidence ?? 0), 0) / decisions.length
    : 0;
  const highRisk = decisions.filter(d => (d.risk_score ?? 0) >= 50).length;

  // ── Dados para gráficos ─────────────────────────────────────────────────
  const typeDistribution = Object.entries(
    decisions.reduce<Record<string, number>>((acc, d) => {
      const label = DECISION_TYPE_LABELS[d.decision_type]?.label ?? 'Outro';
      acc[label] = (acc[label] ?? 0) + 1;
      return acc;
    }, {})
  ).map(([name, value]) => ({ name, value }));

  const statusDistribution = Object.entries(
    decisions.reduce<Record<string, number>>((acc, d) => {
      const label = STATUS_CONFIG[d.status]?.label ?? d.status;
      acc[label] = (acc[label] ?? 0) + 1;
      return acc;
    }, {})
  ).map(([name, value]) => ({ name, value }));

  const confidenceByType = Object.entries(
    decisions.reduce<Record<string, { sum: number; count: number }>>((acc, d) => {
      const label = DECISION_TYPE_LABELS[d.decision_type]?.label ?? 'Outro';
      if (!acc[label]) acc[label] = { sum: 0, count: 0 };
      acc[label].sum   += (d.ai_confidence ?? 0);
      acc[label].count += 1;
      return acc;
    }, {})
  ).map(([name, { sum, count }]) => ({ name, confianca: Math.round(sum / count) }));

  // ── Filtragem ────────────────────────────────────────────────────────────
  const filtered = decisions.filter(d => {
    const matchTab = activeTab === 'pending'
      ? d.status === 'PENDING'
      : activeTab === 'decided'
      ? d.status !== 'PENDING'
      : true;
    const matchType = filterType === 'all' || d.decision_type === filterType;
    return matchTab && matchType;
  });

  // ── Loading skeleton ─────────────────────────────────────────────────────
  if (loading) {
    return (
      <Layout>
        <div className="space-y-6">
          <Skeleton className="h-10 w-64" />
          <div className="grid gap-4 md:grid-cols-4">
            {[1,2,3,4].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}
          </div>
          <div className="space-y-4">
            {[1,2,3].map(i => <Skeleton key={i} className="h-48 rounded-xl" />)}
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">

        {/* ── Cabeçalho ─────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Decisões com IA</h1>
            <p className="text-muted-foreground">Recomendações inteligentes baseadas em dados reais</p>
          </div>
          <Button variant="outline" onClick={() => loadData()} disabled={loading} className="gap-2">
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
        </div>

        {/* ── KPIs ───────────────────────────────────────────────────────── */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card className="border-yellow-200 dark:border-yellow-800">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
              <Clock className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">{pending.length}</div>
              <p className="text-xs text-muted-foreground">aguardam decisão</p>
            </CardContent>
          </Card>

          <Card className="border-green-200 dark:border-green-800">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Impacto Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatKz(totalImpact)}</div>
              <p className="text-xs text-muted-foreground">potencial combinado</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Confiança Média</CardTitle>
              <Brain className="h-4 w-4 text-purple-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-purple-600">{avgConfidence.toFixed(0)}%</div>
              <Progress value={avgConfidence} className="mt-2 h-1.5" />
            </CardContent>
          </Card>

          <Card className="border-red-200 dark:border-red-800">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Alto Risco</CardTitle>
              <AlertTriangle className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{highRisk}</div>
              <p className="text-xs text-muted-foreground">requerem atenção</p>
            </CardContent>
          </Card>
        </div>

        {/* ── Tabs ───────────────────────────────────────────────────────── */}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <div className="flex items-center justify-between flex-wrap gap-3">
            <TabsList>
              <TabsTrigger value="pending" className="gap-1.5">
                <Clock className="h-3.5 w-3.5" />
                Pendentes
                {pending.length > 0 && (
                  <span className="ml-1 bg-yellow-500 text-white text-xs rounded-full px-1.5 py-0.5 font-bold">
                    {pending.length}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="decided" className="gap-1.5">
                <CheckCircle className="h-3.5 w-3.5" />
                Histórico ({decided.length + implemented.length})
              </TabsTrigger>
              <TabsTrigger value="analytics" className="gap-1.5">
                <BarChart2 className="h-3.5 w-3.5" />
                Análise
              </TabsTrigger>
            </TabsList>

            {/* Filtro por tipo */}
            <div className="flex items-center gap-2 flex-wrap">
              {['all', ...Object.keys(DECISION_TYPE_LABELS)].map(type => (
                <button
                  key={type}
                  onClick={() => setFilterType(type)}
                  className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                    filterType === type
                      ? 'bg-primary text-primary-foreground border-primary'
                      : 'border-border text-muted-foreground hover:border-primary hover:text-primary'
                  }`}
                >
                  {type === 'all' ? 'Todos' : DECISION_TYPE_LABELS[type].label}
                </button>
              ))}
            </div>
          </div>

          {/* ── Tab: Pendentes ─────────────────────────────────────────── */}
          <TabsContent value="pending" className="space-y-4 mt-4">
            {filtered.length === 0 ? (
              <Card>
                <CardContent className="py-16 text-center">
                  <CheckCircle className="h-12 w-12 text-green-500 mx-auto mb-3" />
                  <h3 className="font-semibold text-lg">Tudo em dia!</h3>
                  <p className="text-muted-foreground mt-1">Não há decisões pendentes de aprovação.</p>
                </CardContent>
              </Card>
            ) : (
              filtered.map(d => (
                <DecisionCard key={d.id} decision={d} onDecide={handleDecide} />
              ))
            )}
          </TabsContent>

          {/* ── Tab: Histórico ─────────────────────────────────────────── */}
          <TabsContent value="decided" className="space-y-4 mt-4">
            {filtered.length === 0 ? (
              <Card>
                <CardContent className="py-16 text-center">
                  <Activity className="h-12 w-12 text-muted-foreground mx-auto mb-3" />
                  <p className="text-muted-foreground">Nenhuma decisão no histórico ainda.</p>
                </CardContent>
              </Card>
            ) : (
              filtered.map(d => (
                <DecisionCard key={d.id} decision={d} onDecide={handleDecide} />
              ))
            )}
          </TabsContent>

          {/* ── Tab: Análise ───────────────────────────────────────────── */}
          <TabsContent value="analytics" className="space-y-6 mt-4">
            {decisions.length === 0 ? (
              <Card>
                <CardContent className="py-16 text-center">
                  <BarChart2 className="h-12 w-12 text-muted-foreground mx-auto mb-3" />
                  <p className="text-muted-foreground">Sem dados suficientes para análise.</p>
                </CardContent>
              </Card>
            ) : (
              <>
                <div className="grid gap-6 md:grid-cols-2">
                  {/* Distribuição por tipo */}
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base flex items-center gap-2">
                        <Target className="h-4 w-4 text-primary" />
                        Decisões por Categoria
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <ResponsiveContainer width="100%" height={220}>
                        <PieChart>
                          <Pie
                            data={typeDistribution}
                            cx="50%" cy="50%"
                            innerRadius={55} outerRadius={85}
                            paddingAngle={3}
                            dataKey="value"
                          >
                            {typeDistribution.map((_, i) => (
                              <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                            ))}
                          </Pie>
                          <Tooltip formatter={(v) => [`${v} decisões`]} />
                          <Legend />
                        </PieChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>

                  {/* Confiança por categoria */}
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base flex items-center gap-2">
                        <Brain className="h-4 w-4 text-purple-600" />
                        Confiança IA por Categoria
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={confidenceByType} margin={{ left: -20 }}>
                          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                          <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} />
                          <Tooltip formatter={(v) => [`${v}%`, 'Confiança']} />
                          <Bar dataKey="confianca" fill="#8B5CF6" radius={[4,4,0,0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>
                </div>

                {/* Estado das decisões */}
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Activity className="h-4 w-4 text-blue-600" />
                      Estado das Decisões
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      {statusDistribution.map((item, i) => (
                        <div key={i} className="text-center p-4 bg-muted/40 rounded-xl">
                          <div className="text-3xl font-bold" style={{ color: CHART_COLORS[i % CHART_COLORS.length] }}>
                            {item.value}
                          </div>
                          <div className="text-sm text-muted-foreground mt-1">{item.name}</div>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>

                {/* Top decisões por impacto */}
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Zap className="h-4 w-4 text-yellow-600" />
                      Top Decisões por Impacto Financeiro
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-3">
                      {[...decisions]
                        .filter(d => {
                          const v = d.estimated_impact?.financial ?? d.estimated_impact?.monthly_savings;
                          return typeof v === 'number' && v > 0;
                        })
                        .sort((a, b) => {
                          const av = (a.estimated_impact?.financial ?? a.estimated_impact?.monthly_savings ?? 0) as number;
                          const bv = (b.estimated_impact?.financial ?? b.estimated_impact?.monthly_savings ?? 0) as number;
                          return bv - av;
                        })
                        .slice(0, 5)
                        .map((d, i) => {
                          const v = (d.estimated_impact?.financial ?? d.estimated_impact?.monthly_savings ?? 0) as number;
                          const typeInfo = DECISION_TYPE_LABELS[d.decision_type] ?? { label: 'Outro', icon: Brain };
                          const TypeIcon = typeInfo.icon;
                          return (
                            <div key={d.id} className="flex items-center gap-3">
                              <span className="text-sm font-bold text-muted-foreground w-5">{i+1}</span>
                              <div className="p-1.5 rounded bg-primary/10">
                                <TypeIcon className="h-3.5 w-3.5 text-primary" />
                              </div>
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium truncate">{d.decision_title}</div>
                                <div className="text-xs text-muted-foreground">{typeInfo.label}</div>
                              </div>
                              <div className="text-sm font-bold text-green-600 shrink-0">{formatKz(v)}</div>
                            </div>
                          );
                        })}
                    </div>
                  </CardContent>
                </Card>

                {/* Métricas de qualidade da IA */}
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Shield className="h-4 w-4 text-green-600" />
                      Métricas de Qualidade da IA
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                      {[
                        { label: 'Confiança Média', value: `${avgConfidence.toFixed(1)}%`, color: 'text-purple-600', icon: Brain },
                        { label: 'Taxa de Aprovação IA', value: `${decisions.length ? Math.round(decisions.filter(d => d.ai_recommendation === 'APPROVE').length / decisions.length * 100) : 0}%`, color: 'text-green-600', icon: CheckCircle },
                        { label: 'Risco Médio', value: `${decisions.length ? (decisions.reduce((s,d) => s + (d.risk_score ?? 0), 0) / decisions.length).toFixed(1) : 0}%`, color: 'text-orange-600', icon: AlertCircle },
                        { label: 'Prob. Sucesso Média', value: `${decisions.length ? (decisions.reduce((s,d) => s + (d.success_probability ?? 0), 0) / decisions.length).toFixed(1) : 0}%`, color: 'text-blue-600', icon: Target },
                      ].map((m, i) => {
                        const MIcon = m.icon;
                        return (
                          <div key={i} className="text-center">
                            <MIcon className={`h-8 w-8 mx-auto mb-2 ${m.color}`} />
                            <div className={`text-2xl font-bold ${m.color}`}>{m.value}</div>
                            <div className="text-xs text-muted-foreground mt-1">{m.label}</div>
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              </>
            )}
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
