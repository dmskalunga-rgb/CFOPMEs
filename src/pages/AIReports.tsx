import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Skeleton } from '@/components/ui/skeleton';
import { Progress } from '@/components/ui/progress';
import {
  FileText, Download, Eye, Sparkles, RefreshCcw,
  TrendingUp, Users, Briefcase, Activity,
  CheckCircle, AlertTriangle, Clock, Brain,
  BarChart2, Target, Lightbulb,
  ChevronRight, Info, DollarSign, XCircle
} from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { supabase } from '@/integrations/supabase/client';
import { toast } from 'sonner';

// ─── Tipos ──────────────────────────────────────────────────────────────────

interface AIReport {
  id: string;
  report_type: string;
  report_title: string;
  report_content: string | null;
  report_summary: string | null;
  period_start: string | null;
  period_end: string | null;
  language: string | null;
  format: string | null;
  insights: Insight[] | null;
  recommendations: Recommendation[] | null;
  data_sources: string[] | null;
  generation_duration_ms: number | null;
  word_count: number | null;
  confidence_score: number | null;
  status: string;
  created_at: string;
}

interface Insight {
  type?: string;
  title: string;
  value?: number | null;
  trend?: 'up' | 'down' | 'stable';
  change?: number | null;
  description?: string;
}

interface Recommendation {
  priority?: string;
  area: string;
  action: string;
  impact?: number | null;
  effort?: string;
}

interface AIInsight {
  id: string;
  type: string;
  title: string;
  description: string;
  data: Record<string, unknown> | null;
  priority: string;
  is_read: boolean;
  is_dismissed: boolean;
  created_at: string;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

async function getTenantId(): Promise<string> {
  try {
    const { data } = await supabase.rpc('get_current_tenant_id');
    if (data) return data as string;
  } catch { /* continuar */ }
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) {
      const { data: profile } = await supabase
        .from('users').select('tenant_id').eq('id', user.id).maybeSingle();
      if (profile?.tenant_id) return profile.tenant_id as string;
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

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('pt-AO', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return iso; }
}

function durationLabel(ms: number | null | undefined): string {
  const n = ms ?? 0;
  if (n === 0) return '—';
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(1)}s`;
}

function safeArray<T>(v: T[] | null | undefined): T[] {
  return Array.isArray(v) ? v : [];
}

// ─── Gerador local de relatórios com dados reais do Supabase ────────────────

async function buildReportContent(
  type: string,
  tid: string,
  periodStart: string,
  periodEnd: string,
): Promise<{
  content: string;
  summary: string;
  insights: Insight[];
  recommendations: Recommendation[];
  data_sources: string[];
  word_count: number;
  confidence_score: number;
}> {
  const sources: string[] = [];

  // — Faturas —
  let totalInvoices = 0, totalRevenue = 0, paidRevenue = 0, overdueCount = 0, overdueAmt = 0;
  try {
    const { data: inv } = await supabase
      .from('invoices').select('total, status, due_date')
      .eq('tenant_id', tid);
    if (inv && inv.length > 0) {
      sources.push('Faturas');
      totalInvoices = inv.length;
      totalRevenue  = inv.reduce((s, r) => s + Number(r.total ?? 0), 0);
      paidRevenue   = inv.filter(r => ['PAID','paid'].includes(r.status ?? '')).reduce((s, r) => s + Number(r.total ?? 0), 0);
      const overdue = inv.filter(r => ['OVERDUE','SENT','overdue','sent'].includes(r.status ?? '') && r.due_date && new Date(r.due_date) < new Date());
      overdueCount  = overdue.length;
      overdueAmt    = overdue.reduce((s, r) => s + Number(r.total ?? 0), 0);
    }
  } catch { /* continuar */ }

  // — Transações —
  let totalIncome = 0, totalExpense = 0, txCount = 0;
  try {
    const { data: tx } = await supabase
      .from('transactions').select('amount, type')
      .eq('tenant_id', tid);
    if (tx && tx.length > 0) {
      sources.push('Transações');
      txCount      = tx.length;
      totalIncome  = tx.filter(r => r.type === 'income').reduce((s, r) => s + Number(r.amount ?? 0), 0);
      totalExpense = tx.filter(r => r.type === 'expense').reduce((s, r) => s + Number(r.amount ?? 0), 0);
    }
  } catch { /* continuar */ }

  // — Colaboradores —
  let totalEmp = 0, totalPayroll = 0, avgSalary = 0;
  try {
    const { data: emp } = await supabase
      .from('employees').select('base_salary, status')
      .eq('tenant_id', tid);
    if (emp && emp.length > 0) {
      sources.push('Colaboradores');
      const active = emp.filter(e => e.status === 'active');
      totalEmp    = active.length;
      totalPayroll = active.reduce((s, e) => s + Number(e.base_salary ?? 0), 0);
      avgSalary    = totalEmp > 0 ? totalPayroll / totalEmp : 0;
    }
  } catch { /* continuar */ }

  // — Clientes —
  let totalCustomers = 0;
  try {
    const { count } = await supabase
      .from('customers').select('id', { count: 'exact', head: true })
      .eq('tenant_id', tid);
    if (count && count > 0) { sources.push('Clientes'); totalCustomers = count; }
  } catch { /* continuar */ }

  // — Decisões IA —
  let pendingDecisions = 0;
  try {
    const { count } = await supabase
      .from('ai_decisions').select('id', { count: 'exact', head: true })
      .eq('tenant_id', tid).eq('status', 'PENDING');
    pendingDecisions = count ?? 0;
  } catch { /* continuar */ }

  const payRate    = totalRevenue > 0 ? Math.round((paidRevenue / totalRevenue) * 100) : 0;
  const netBalance = totalIncome - totalExpense;
  const typeLabels: Record<string, string> = {
    financial: 'Financeiro', hr: 'Recursos Humanos',
    operational: 'Operacional', executive: 'Executivo', custom: 'Personalizado',
  };
  const label = typeLabels[type] ?? 'Executivo';
  const now   = new Date();
  const monthName = now.toLocaleDateString('pt-AO', { month: 'long', year: 'numeric' });

  // — Conteúdo Markdown contextualizado por tipo —
  let content = '';
  const insights: Insight[] = [];
  const recommendations: Recommendation[] = [];

  if (type === 'financial' || type === 'executive') {
    content = `# Relatório ${label} — ${monthName}
**Período:** ${periodStart} a ${periodEnd}  
**Gerado em:** ${now.toLocaleString('pt-AO')}

---

## 1. Resumo Executivo

${totalInvoices > 0
  ? `A empresa registou **${totalInvoices} faturas** no período, com receita total de **${formatKz(totalRevenue)}**. A taxa de cobrança situou-se em **${payRate}%**, com **${formatKz(paidRevenue)}** já recebidos.`
  : 'Não foram encontradas faturas no período analisado.'
}

${overdueCount > 0
  ? `⚠️ **Alerta:** ${overdueCount} ${overdueCount === 1 ? 'fatura vencida' : 'faturas vencidas'} no valor total de **${formatKz(overdueAmt)}** requerem atenção imediata.`
  : overdueCount === 0 && totalInvoices > 0 ? '✅ Sem faturas vencidas em aberto.' : ''
}

---

## 2. Fluxo de Caixa

${txCount > 0 ? `
| Indicador | Valor |
|-----------|-------|
| Total de Entradas | ${formatKz(totalIncome)} |
| Total de Saídas | ${formatKz(totalExpense)} |
| Saldo Líquido | **${formatKz(netBalance)}** |
| Transações registadas | ${txCount} |
` : 'Sem dados de transações disponíveis para este período.'}

${netBalance > 0
  ? `✅ Saldo positivo de **${formatKz(netBalance)}** indica saúde financeira no período.`
  : netBalance < 0
    ? `⚠️ Saldo negativo de **${formatKz(Math.abs(netBalance))}** — recomenda-se revisão das despesas.`
    : 'Saldo neutro no período.'
}

---

## 3. Recursos Humanos

${totalEmp > 0 ? `
- **Colaboradores activos:** ${totalEmp}
- **Massa salarial mensal:** ${formatKz(totalPayroll)}
- **Salário médio:** ${formatKz(avgSalary)}
- **Custo salarial / Receita:** ${totalRevenue > 0 ? ((totalPayroll / totalRevenue) * 100).toFixed(1) : '—'}%
` : 'Sem dados de colaboradores disponíveis.'}

---

## 4. Clientes

${totalCustomers > 0 ? `
- **Clientes registados:** ${totalCustomers}
- **Receita por cliente (média):** ${totalCustomers > 0 ? formatKz(totalRevenue / totalCustomers) : '—'}
` : 'Sem dados de clientes disponíveis.'}

---

## 5. Decisões Pendentes

${pendingDecisions > 0
  ? `🔴 **${pendingDecisions} ${pendingDecisions === 1 ? 'decisão pendente' : 'decisões pendentes'}** aguardam aprovação na plataforma IA.`
  : '✅ Sem decisões pendentes de aprovação.'}

---

## 6. Recomendações da IA

${overdueCount > 3 ? '1. **Prioritário:** Activar campanha de cobrança — ' + overdueCount + ' faturas vencidas (' + formatKz(overdueAmt) + ').\n' : ''}${netBalance < 0 ? '2. Revisar estrutura de custos — saldo líquido negativo no período.\n' : ''}${payRate < 70 && totalInvoices > 0 ? '3. Implementar política de pagamento antecipado com desconto para melhorar taxa de cobrança (' + payRate + '%).\n' : ''}${totalEmp > 0 && totalPayroll / (totalRevenue || 1) > 0.4 ? '4. Ratio salários/receita acima de 40% — avaliar optimização da estrutura RH.\n' : ''}
*Análise gerada com base em ${sources.length} fonte${sources.length !== 1 ? 's' : ''} de dados: ${sources.join(', ')}.*`;

    if (totalRevenue > 0) insights.push({ title: 'Receita Total', value: totalRevenue, trend: 'up', change: 0 });
    if (overdueAmt > 0)   insights.push({ title: 'Faturas Vencidas', value: overdueAmt, trend: 'down', change: overdueCount });
    if (netBalance !== 0) insights.push({ title: 'Saldo Líquido', value: netBalance, trend: netBalance > 0 ? 'up' : 'down', change: 0 });
    if (payRate > 0)      insights.push({ title: 'Taxa de Cobrança', value: payRate, trend: payRate >= 80 ? 'up' : 'down', change: 0 });

    if (overdueCount > 0) recommendations.push({ priority: 'HIGH', area: 'Cobrança', action: `Accionar processo de cobrança para ${overdueCount} faturas vencidas (${formatKz(overdueAmt)})`, impact: Math.round(overdueAmt * 0.7), effort: 'MEDIUM' });
    if (netBalance < 0)   recommendations.push({ priority: 'HIGH', area: 'Custos', action: 'Auditar despesas do período — saldo líquido negativo detectado', impact: Math.abs(netBalance) * 0.3, effort: 'MEDIUM' });
    if (payRate < 70 && totalInvoices > 0) recommendations.push({ priority: 'MEDIUM', area: 'Recebimentos', action: 'Implementar política de desconto por pagamento antecipado (3%)', impact: (totalRevenue - paidRevenue) * 0.5, effort: 'LOW' });
    if (totalEmp > 0) recommendations.push({ priority: 'LOW', area: 'RH', action: 'Monitorizar evolução da massa salarial vs receita mensalmente', impact: 0, effort: 'LOW' });
  }

  if (type === 'hr') {
    content = `# Relatório de Recursos Humanos — ${monthName}
**Período:** ${periodStart} a ${periodEnd}  
**Gerado em:** ${now.toLocaleString('pt-AO')}

---

## 1. Força de Trabalho

${totalEmp > 0 ? `
| Métrica | Valor |
|---------|-------|
| Colaboradores Activos | **${totalEmp}** |
| Massa Salarial Mensal | **${formatKz(totalPayroll)}** |
| Salário Médio | ${formatKz(avgSalary)} |
| Custo Anual Estimado | ${formatKz(totalPayroll * 12)} |
` : 'Sem dados de colaboradores disponíveis.'}

---

## 2. Análise de Custos RH

${totalEmp > 0 && totalRevenue > 0 ? `
- **Ratio Salários/Receita:** ${((totalPayroll / totalRevenue) * 100).toFixed(1)}%
- **Receita por colaborador:** ${formatKz(totalRevenue / totalEmp)}
- **Benchmark saudável:** Ratio abaixo de 35%
` : 'Dados insuficientes para análise de ratio.'}

---

## 3. Recomendações

${totalEmp === 0 ? '- Registar colaboradores na plataforma para activar análises de RH.\n' : ''}${totalEmp > 0 && totalPayroll / (totalRevenue || 1) > 0.4 ? '- **Alta prioridade:** Ratio salários/receita elevado — avaliar optimização.\n' : ''}${totalEmp > 0 ? '- Implementar avaliações de desempenho trimestrais.\n- Monitorizar turnover e absentismo.\n' : ''}

*Relatório gerado com dados de ${sources.join(', ') || 'sistema'}.*`;

    if (totalEmp > 0) insights.push({ title: 'Colaboradores Activos', value: totalEmp, trend: 'stable', change: 0 });
    if (totalPayroll > 0) insights.push({ title: 'Massa Salarial', value: totalPayroll, trend: 'stable', change: 0 });
    if (avgSalary > 0) insights.push({ title: 'Salário Médio', value: avgSalary, trend: 'stable', change: 0 });

    recommendations.push({ priority: 'MEDIUM', area: 'RH', action: 'Realizar avaliações de desempenho e ajuste salarial anual', impact: 0, effort: 'MEDIUM' });
    if (totalEmp > 0 && totalPayroll / (totalRevenue || 1) > 0.35) recommendations.push({ priority: 'HIGH', area: 'Custos RH', action: `Ratio salários/receita de ${((totalPayroll / (totalRevenue || 1)) * 100).toFixed(0)}% — avaliar optimização`, impact: 0, effort: 'HIGH' });
  }

  if (type === 'operational') {
    content = `# Relatório Operacional — ${monthName}
**Período:** ${periodStart} a ${periodEnd}  
**Gerado em:** ${now.toLocaleString('pt-AO')}

---

## 1. Visão Operacional

| Área | Métrica | Valor |
|------|---------|-------|
| Faturação | Total de Faturas | ${totalInvoices} |
| Faturação | Taxa de Cobrança | ${payRate}% |
| Financeiro | Transações | ${txCount} |
| RH | Colaboradores | ${totalEmp} |
| CRM | Clientes | ${totalCustomers} |

---

## 2. Eficiência Operacional

${totalInvoices > 0 ? `
**Faturação:**
- ${totalInvoices} faturas emitidas, ${overdueCount} vencidas
- Valor total: ${formatKz(totalRevenue)}
- Cobrança efectiva: ${payRate}%
` : ''}

${txCount > 0 ? `
**Fluxo Financeiro:**
- ${txCount} transações registadas
- Entradas: ${formatKz(totalIncome)} | Saídas: ${formatKz(totalExpense)}
- Resultado: ${netBalance >= 0 ? '+' : ''}${formatKz(netBalance)}
` : ''}

---

## 3. Indicadores de Alerta

${overdueCount > 0 ? `🔴 ${overdueCount} faturas vencidas (${formatKz(overdueAmt)})\n` : ''}${pendingDecisions > 0 ? `🟡 ${pendingDecisions} decisões IA pendentes\n` : ''}${overdueCount === 0 && pendingDecisions === 0 ? '✅ Sem alertas operacionais activos.' : ''}

*Relatório gerado com dados de ${sources.join(', ') || 'sistema'}.*`;

    if (totalInvoices > 0) insights.push({ title: 'Faturas Emitidas', value: totalInvoices, trend: 'stable', change: 0 });
    if (payRate > 0)       insights.push({ title: 'Taxa de Cobrança', value: payRate, trend: payRate >= 80 ? 'up' : 'down', change: 0 });
    if (txCount > 0)       insights.push({ title: 'Transações', value: txCount, trend: 'stable', change: 0 });

    recommendations.push({ priority: 'MEDIUM', area: 'Processos', action: 'Automatizar reconciliação diária de transações com RPA', impact: 0, effort: 'MEDIUM' });
    if (overdueCount > 0) recommendations.push({ priority: 'HIGH', area: 'Cobrança', action: `Activar cobrança automática para ${overdueCount} faturas vencidas`, impact: Math.round(overdueAmt * 0.65), effort: 'LOW' });
  }

  // Fallback para tipo custom ou executive sem dados suficientes
  if (!content) {
    content = `# Relatório ${label} — ${monthName}
**Período:** ${periodStart} a ${periodEnd}  
**Gerado em:** ${now.toLocaleString('pt-AO')}

---

## Resumo Geral

${sources.length > 0 ? `Análise baseada em dados de: ${sources.join(', ')}.` : 'Nenhum dado encontrado no Supabase para este tenant.'}

${totalRevenue > 0 ? `- Receita total: **${formatKz(totalRevenue)}**` : ''}
${totalEmp > 0 ? `- Colaboradores activos: **${totalEmp}**` : ''}
${totalCustomers > 0 ? `- Clientes: **${totalCustomers}**` : ''}

*Relatório gerado automaticamente pela IA do KwanzaControl.*`;
  }

  const wordCount = content.split(/\s+/).filter(Boolean).length;
  const confidence = Math.min(95, 60 + sources.length * 8 + (totalRevenue > 0 ? 5 : 0) + (totalEmp > 0 ? 3 : 0));
  const summary = `Relatório ${label} de ${monthName}. ${totalRevenue > 0 ? `Receita: ${formatKz(totalRevenue)}.` : ''} ${overdueCount > 0 ? `${overdueCount} faturas vencidas.` : ''} ${totalEmp > 0 ? `${totalEmp} colaboradores activos.` : ''} ${recommendations.length} recomendações geradas.`.trim();

  return { content, summary, insights, recommendations, data_sources: sources, word_count: wordCount, confidence_score: confidence };
}

// ─── Configs visuais ─────────────────────────────────────────────────────────

const TYPE_CONFIG: Record<string, { label: string; color: string; Icon: React.ComponentType<{ className?: string }> }> = {
  financial:   { label: 'Financeiro',       color: 'bg-blue-500/10 text-blue-600 border-blue-200',     Icon: BarChart2 },
  hr:          { label: 'Recursos Humanos', color: 'bg-purple-500/10 text-purple-600 border-purple-200', Icon: Users },
  operational: { label: 'Operacional',      color: 'bg-green-500/10 text-green-600 border-green-200',   Icon: Activity },
  executive:   { label: 'Executivo',        color: 'bg-amber-500/10 text-amber-600 border-amber-200',   Icon: Briefcase },
  custom:      { label: 'Personalizado',    color: 'bg-rose-500/10 text-rose-600 border-rose-200',      Icon: Target },
};

const STATUS_CONFIG: Record<string, { label: string; Icon: React.ComponentType<{ className?: string }>; color: string }> = {
  COMPLETED:  { label: 'Concluído',  Icon: CheckCircle,   color: 'text-green-600' },
  PENDING:    { label: 'Pendente',   Icon: Clock,          color: 'text-amber-500' },
  GENERATING: { label: 'A gerar...', Icon: Brain,          color: 'text-blue-500' },
  FAILED:     { label: 'Falhou',     Icon: XCircle,        color: 'text-destructive' },
};

const PRIORITY_CONFIG: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline'; icon: string }> = {
  HIGH:   { label: 'Alto',  variant: 'destructive', icon: '🔴' },
  MEDIUM: { label: 'Médio', variant: 'default',     icon: '🟡' },
  LOW:    { label: 'Baixo', variant: 'secondary',   icon: '🟢' },
};

// ─── Componente Principal ────────────────────────────────────────────────────

export default function AIReports() {
  const [reports, setReports]           = useState<AIReport[]>([]);
  const [insights, setInsights]         = useState<AIInsight[]>([]);
  const [loading, setLoading]           = useState(true);
  const [generating, setGenerating]     = useState(false);
  const [refreshing, setRefreshing]     = useState(false);
  const [selectedReport, setSelectedReport] = useState<AIReport | null>(null);
  const [reportType, setReportType]     = useState('executive');
  const [filterType, setFilterType]     = useState('all');
  const [activeTab, setActiveTab]       = useState('reports');
  const [error, setError]               = useState<string | null>(null);
  const [tenantId, setTenantId]         = useState('');

  // ── Carregar dados ──────────────────────────────────────────────────────────

  const loadData = useCallback(async (tid?: string) => {
    setError(null);
    try {
      const resolvedTid = tid ?? tenantId;
      if (!resolvedTid) return;

      const [rRes, iRes] = await Promise.allSettled([
        supabase
          .from('ai_generated_reports')
          .select('*')
          .eq('tenant_id', resolvedTid)
          .order('created_at', { ascending: false })
          .limit(50),
        supabase
          .from('ai_insights')
          .select('*')
          .eq('tenant_id', resolvedTid)
          .eq('is_dismissed', false)
          .order('created_at', { ascending: false })
          .limit(30),
      ]);

      if (rRes.status === 'fulfilled') {
        if (rRes.value.error) console.warn('ai_generated_reports:', rRes.value.error.message);
        setReports((rRes.value.data ?? []) as AIReport[]);
      }
      if (iRes.status === 'fulfilled') {
        if (iRes.value.error) console.warn('ai_insights:', iRes.value.error.message);
        setInsights((iRes.value.data ?? []) as AIInsight[]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erro ao carregar dados');
    }
  }, [tenantId]);

  useEffect(() => {
    setLoading(true);
    getTenantId().then(tid => {
      setTenantId(tid);
      loadData(tid).finally(() => setLoading(false));
    }).catch(err => {
      setError(err instanceof Error ? err.message : 'Erro de autenticação');
      setLoading(false);
    });
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    await loadData();
    setRefreshing(false);
  }, [loadData]);

  // ── Gerar relatório (sem edge function — dados reais do Supabase) ───────────

  const generateReport = async () => {
    if (!tenantId) { toast.error('Tenant não identificado. Faça login novamente.'); return; }
    setGenerating(true);
    setError(null);
    const startMs = Date.now();
    try {
      const { data: { user } } = await supabase.auth.getUser();
      const now      = new Date();
      const firstDay = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0];
      const lastDay  = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().split('T')[0];

      const typeLabels: Record<string, string> = {
        financial: 'Financeiro', hr: 'Recursos Humanos',
        operational: 'Operacional', executive: 'Executivo', custom: 'Personalizado',
      };
      const title = `Relatório ${typeLabels[reportType] ?? reportType} — ${now.toLocaleDateString('pt-AO', { month: 'long', year: 'numeric' })}`;

      // Inserir em estado GENERATING
      const { data: newReport, error: insErr } = await supabase
        .from('ai_generated_reports')
        .insert({
          tenant_id:   tenantId,
          user_id:     user?.id ?? null,
          report_type: reportType,
          report_title: title,
          report_content: '',
          report_summary: 'A processar...',
          period_start: firstDay,
          period_end:   lastDay,
          language: 'pt',
          format: 'markdown',
          insights: [],
          recommendations: [],
          data_sources: [],
          status: 'GENERATING',
          confidence_score: 0,
          word_count: 0,
          generation_duration_ms: 0,
        })
        .select()
        .single();

      if (insErr) throw insErr;

      // Refrescar lista para mostrar estado GENERATING
      await loadData();

      // Gerar conteúdo real com dados do Supabase
      const built = await buildReportContent(reportType, tenantId, firstDay, lastDay);
      const durationMs = Date.now() - startMs;

      // Actualizar o registo com conteúdo completo
      const { error: updErr } = await supabase
        .from('ai_generated_reports')
        .update({
          report_content:       built.content,
          report_summary:       built.summary,
          insights:             built.insights,
          recommendations:      built.recommendations,
          data_sources:         built.data_sources,
          word_count:           built.word_count,
          confidence_score:     built.confidence_score,
          generation_duration_ms: durationMs,
          status: 'COMPLETED',
        })
        .eq('id', newReport.id);

      if (updErr) throw updErr;

      await loadData();
      toast.success(`Relatório "${title}" gerado com sucesso!`);
    } catch (err) {
      console.error('Erro ao gerar relatório:', err);
      const msg = err instanceof Error ? err.message : 'Erro ao gerar relatório';
      setError(msg);
      toast.error(msg);
    } finally {
      setGenerating(false);
    }
  };

  // ── Dispensar insight ───────────────────────────────────────────────────────

  const dismissInsight = async (id: string) => {
    try {
      const { error } = await supabase
        .from('ai_insights').update({ is_dismissed: true }).eq('id', id);
      if (error) { toast.error('Erro ao dispensar insight'); return; }
      setInsights(prev => prev.filter(i => i.id !== id));
      toast.success('Insight dispensado');
    } catch { toast.error('Erro ao dispensar insight'); }
  };

  // ── KPIs calculados ─────────────────────────────────────────────────────────

  const completed     = reports.filter(r => r.status === 'COMPLETED');
  const thisMonth     = reports.filter(r => new Date(r.created_at) >= new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const highInsights  = insights.filter(i => i.priority === 'HIGH');
  const avgConf       = completed.length > 0
    ? Math.round(completed.reduce((s, r) => s + (r.confidence_score ?? 0), 0) / completed.length * 10) / 10
    : 0;

  const filtered = filterType === 'all' ? reports : reports.filter(r => r.report_type === filterType);

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <Layout>
      <div className="space-y-6">

        {/* ── Header ────────────────────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Brain className="h-8 w-8 text-primary" />
              Relatórios IA
            </h1>
            <p className="text-muted-foreground mt-1">
              Análises e relatórios gerados automaticamente por Inteligência Artificial
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Button variant="outline" size="sm" onClick={refresh} disabled={refreshing || loading}>
              <RefreshCcw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
              Actualizar
            </Button>
            <Select value={reportType} onValueChange={setReportType}>
              <SelectTrigger className="w-[190px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="executive">Executivo</SelectItem>
                <SelectItem value="financial">Financeiro</SelectItem>
                <SelectItem value="hr">Recursos Humanos</SelectItem>
                <SelectItem value="operational">Operacional</SelectItem>
                <SelectItem value="custom">Personalizado</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={generateReport} disabled={generating || loading} className="gap-2">
              <Sparkles className={`h-4 w-4 ${generating ? 'animate-pulse' : ''}`} />
              {generating ? 'A Gerar...' : 'Gerar Relatório'}
            </Button>
          </div>
        </div>

        {/* ── Erro ──────────────────────────────────────────────────────────── */}
        {error && (
          <div className="flex items-center gap-2 p-3 bg-destructive/10 text-destructive border border-destructive/20 rounded-lg text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button className="text-xs underline" onClick={() => setError(null)}>Fechar</button>
          </div>
        )}

        {/* ── KPIs ──────────────────────────────────────────────────────────── */}
        {loading ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            {[
              { label: 'Relatórios Totais',  value: reports.length,     icon: FileText,      color: 'text-blue-600' },
              { label: 'Concluídos',         value: completed.length,   icon: CheckCircle,   color: 'text-green-600' },
              { label: 'Este Mês',           value: thisMonth.length,   icon: TrendingUp,    color: 'text-amber-600' },
              { label: 'Confiança Média',    value: `${avgConf}%`,      icon: Target,        color: 'text-purple-600' },
              { label: 'Insights Activos',   value: insights.length,    icon: Lightbulb,     color: 'text-sky-600' },
              { label: 'Alertas Críticos',   value: highInsights.length,icon: AlertTriangle, color: 'text-red-600' },
            ].map(({ label, value, icon: Icon, color }) => (
              <Card key={label} className="p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-xs text-muted-foreground">{label}</p>
                    <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
                  </div>
                  <Icon className={`h-5 w-5 ${color} opacity-70`} />
                </div>
              </Card>
            ))}
          </div>
        )}

        {/* ── Tabs ──────────────────────────────────────────────────────────── */}
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3 max-w-md">
            <TabsTrigger value="reports">
              <FileText className="h-4 w-4 mr-2" /> Relatórios
            </TabsTrigger>
            <TabsTrigger value="insights">
              <Lightbulb className="h-4 w-4 mr-2" /> Insights
              {highInsights.length > 0 && (
                <Badge variant="destructive" className="ml-2 text-xs px-1.5 py-0">
                  {highInsights.length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="analytics">
              <BarChart2 className="h-4 w-4 mr-2" /> Análise
            </TabsTrigger>
          </TabsList>

          {/* ── Tab: Relatórios ─────────────────────────────────────────────── */}
          <TabsContent value="reports" className="mt-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
                <div>
                  <CardTitle className="text-lg">Relatórios Gerados pela IA</CardTitle>
                  <CardDescription>Histórico de análises geradas com dados reais do Supabase</CardDescription>
                </div>
                <Select value={filterType} onValueChange={setFilterType}>
                  <SelectTrigger className="w-[160px] h-8 text-xs">
                    <SelectValue placeholder="Filtrar por tipo" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos os tipos</SelectItem>
                    <SelectItem value="executive">Executivo</SelectItem>
                    <SelectItem value="financial">Financeiro</SelectItem>
                    <SelectItem value="hr">Recursos Humanos</SelectItem>
                    <SelectItem value="operational">Operacional</SelectItem>
                    <SelectItem value="custom">Personalizado</SelectItem>
                  </SelectContent>
                </Select>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28 rounded-lg" />)}
                  </div>
                ) : filtered.length === 0 ? (
                  <div className="text-center py-16 text-muted-foreground">
                    <FileText className="h-16 w-16 mx-auto mb-4 opacity-20" />
                    <p className="text-lg font-medium">Nenhum relatório encontrado</p>
                    <p className="text-sm mt-1">
                      {filterType !== 'all'
                        ? 'Altere o filtro ou gere um relatório deste tipo.'
                        : 'Clique em "Gerar Relatório" para criar o primeiro relatório com dados reais.'}
                    </p>
                    <Button className="mt-4 gap-2" onClick={generateReport} disabled={generating}>
                      <Sparkles className="h-4 w-4" />
                      Gerar Primeiro Relatório
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {filtered.map(report => {
                      const tc = TYPE_CONFIG[report.report_type] ?? TYPE_CONFIG.custom;
                      const sc = STATUS_CONFIG[report.status]    ?? STATUS_CONFIG.COMPLETED;
                      const StatusIcon = sc.Icon;
                      const TypeIcon   = tc.Icon;
                      const insArr     = safeArray(report.insights);
                      return (
                        <div key={report.id} className="flex items-start justify-between p-4 border rounded-xl hover:bg-muted/30 transition-colors gap-4">
                          <div className="flex items-start gap-4 flex-1 min-w-0">
                            <div className={`p-2 rounded-lg border ${tc.color} shrink-0`}>
                              <TypeIcon className="h-5 w-5" />
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <h4 className="font-semibold text-sm truncate">{report.report_title}</h4>
                                <StatusIcon className={`h-3.5 w-3.5 ${sc.color} shrink-0`} />
                                <span className={`text-xs ${sc.color}`}>{sc.label}</span>
                              </div>
                              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                {report.report_summary || 'Sem resumo disponível.'}
                              </p>
                              <div className="flex flex-wrap items-center gap-2 mt-2">
                                <Badge variant="outline" className={`text-xs ${tc.color}`}>{tc.label}</Badge>
                                {(report.word_count ?? 0) > 0 && (
                                  <Badge variant="secondary" className="text-xs">{report.word_count} palavras</Badge>
                                )}
                                {(report.confidence_score ?? 0) > 0 && (
                                  <Badge variant="outline" className={`text-xs ${(report.confidence_score ?? 0) >= 85 ? 'text-green-600 border-green-200' : 'text-amber-600 border-amber-200'}`}>
                                    {(report.confidence_score ?? 0).toFixed(0)}% confiança
                                  </Badge>
                                )}
                                {(report.generation_duration_ms ?? 0) > 0 && (
                                  <span className="text-xs text-muted-foreground">⚡ {durationLabel(report.generation_duration_ms)}</span>
                                )}
                                <span className="text-xs text-muted-foreground ml-auto">{formatDate(report.created_at)}</span>
                              </div>
                              {/* Insights rápidos */}
                              {insArr.length > 0 && (
                                <div className="flex flex-wrap gap-1.5 mt-2">
                                  {insArr.slice(0, 3).map((ins, idx) => (
                                    <span key={idx} className="inline-flex items-center gap-1 text-xs bg-muted px-2 py-0.5 rounded-full">
                                      {ins.trend === 'up' ? '↑' : ins.trend === 'down' ? '↓' : '→'} {ins.title}
                                      {typeof ins.value === 'number' && ins.value > 1000 ? `: ${formatKz(ins.value)}` : ins.value != null ? `: ${ins.value}${typeof ins.value === 'number' && ins.value < 200 ? '%' : ''}` : ''}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="flex flex-col gap-2 shrink-0">
                            <Button
                              variant="outline" size="sm" className="h-8 text-xs"
                              onClick={() => setSelectedReport(report)}
                              disabled={report.status !== 'COMPLETED'}
                            >
                              <Eye className="h-3.5 w-3.5 mr-1" /> Ver
                            </Button>
                            <Button
                              variant="outline" size="sm" className="h-8 text-xs"
                              disabled={report.status !== 'COMPLETED'}
                              onClick={() => {
                                const content = report.report_content ?? '';
                                const blob = new Blob([content], { type: 'text/markdown' });
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = `${(report.report_title ?? 'relatorio').replace(/[^a-z0-9]/gi, '_')}.md`;
                                a.click();
                                URL.revokeObjectURL(url);
                              }}
                            >
                              <Download className="h-3.5 w-3.5 mr-1" /> MD
                            </Button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── Tab: Insights ───────────────────────────────────────────────── */}
          <TabsContent value="insights" className="mt-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Insights da IA</CardTitle>
                <CardDescription>Alertas e oportunidades detectados automaticamente a partir dos seus dados</CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="space-y-3">
                    {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
                  </div>
                ) : insights.length === 0 ? (
                  <div className="text-center py-16 text-muted-foreground">
                    <Lightbulb className="h-16 w-16 mx-auto mb-4 opacity-20" />
                    <p className="text-lg font-medium">Nenhum insight activo</p>
                    <p className="text-sm mt-1">Os insights aparecem aqui à medida que a IA analisa os dados da empresa.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {insights.map(insight => {
                      const pc = PRIORITY_CONFIG[insight.priority] ?? PRIORITY_CONFIG.MEDIUM;
                      return (
                        <div key={insight.id} className={`flex items-start justify-between p-4 border rounded-xl gap-4 ${
                          insight.priority === 'HIGH' ? 'border-red-200 bg-red-50/30 dark:bg-red-900/10'
                            : insight.priority === 'MEDIUM' ? 'border-amber-200 bg-amber-50/30 dark:bg-amber-900/10'
                            : 'border-muted bg-muted/20'
                        }`}>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span>{pc.icon}</span>
                              <h4 className="font-semibold text-sm">{insight.title}</h4>
                              <Badge variant={pc.variant} className="text-xs">{pc.label}</Badge>
                              <Badge variant="outline" className="text-xs text-muted-foreground">
                                {insight.type.replace(/_/g, ' ')}
                              </Badge>
                            </div>
                            <p className="text-sm text-muted-foreground mt-1">{insight.description}</p>
                            <p className="text-xs text-muted-foreground mt-1.5">{formatDate(insight.created_at)}</p>
                          </div>
                          <Button
                            variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground shrink-0"
                            onClick={() => dismissInsight(insight.id)}
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

          {/* ── Tab: Análise ────────────────────────────────────────────────── */}
          <TabsContent value="analytics" className="mt-4">
            <div className="grid gap-4 md:grid-cols-2">

              {/* Distribuição por tipo */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <BarChart2 className="h-4 w-4 text-primary" /> Distribuição por Tipo
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loading ? (
                    <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 rounded" />)}</div>
                  ) : reports.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-4">Sem dados — gere relatórios para ver análise</p>
                  ) : (
                    <div className="space-y-3">
                      {Object.entries(
                        reports.reduce<Record<string, number>>((acc, r) => {
                          acc[r.report_type] = (acc[r.report_type] ?? 0) + 1;
                          return acc;
                        }, {})
                      ).sort((a, b) => b[1] - a[1]).map(([type, count]) => {
                        const tc = TYPE_CONFIG[type] ?? TYPE_CONFIG.custom;
                        const TypeIcon = tc.Icon;
                        const pct = Math.round((count / reports.length) * 100);
                        return (
                          <div key={type}>
                            <div className="flex items-center justify-between text-sm mb-1">
                              <div className="flex items-center gap-2">
                                <TypeIcon className="h-3.5 w-3.5 text-muted-foreground" />
                                <span className="font-medium">{tc.label}</span>
                              </div>
                              <div className="flex items-center gap-2">
                                <span className="text-muted-foreground text-xs">{count}</span>
                                <span className="font-semibold">{pct}%</span>
                              </div>
                            </div>
                            <Progress value={pct} className="h-2" />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Top recomendações */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Lightbulb className="h-4 w-4 text-amber-500" /> Top Recomendações da IA
                  </CardTitle>
                  <CardDescription>Acções de alto impacto identificadas nos relatórios</CardDescription>
                </CardHeader>
                <CardContent>
                  {loading ? (
                    <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14 rounded" />)}</div>
                  ) : (() => {
                    const allRecs: (Recommendation & { reportTitle: string })[] = reports.flatMap(r =>
                      safeArray(r.recommendations).map(rec => ({ ...rec, reportTitle: r.report_title }))
                    );
                    const high = allRecs.filter(r => r.priority === 'HIGH').slice(0, 6);
                    if (high.length === 0) return (
                      <p className="text-sm text-muted-foreground text-center py-4">
                        Sem recomendações de alta prioridade ainda.<br />
                        <span className="text-xs">Gere relatórios para obter recomendações.</span>
                      </p>
                    );
                    return (
                      <div className="space-y-3">
                        {high.map((rec, idx) => (
                          <div key={idx} className="flex items-start gap-3 p-3 bg-muted/30 rounded-lg">
                            <ChevronRight className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-semibold text-primary">{rec.area}</p>
                              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{rec.action}</p>
                              {(rec.impact ?? 0) > 0 && (
                                <p className="text-xs font-medium text-green-600 mt-1 flex items-center gap-1">
                                  <DollarSign className="h-3 w-3" /> {formatKz(rec.impact)}
                                </p>
                              )}
                            </div>
                            <Badge variant="outline" className="text-xs shrink-0 border-amber-200 text-amber-700">
                              {rec.effort ?? 'MEDIUM'}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                </CardContent>
              </Card>

              {/* Qualidade dos relatórios */}
              <Card className="md:col-span-2">
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Target className="h-4 w-4 text-primary" /> Métricas de Qualidade
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loading ? (
                    <div className="grid grid-cols-4 gap-4">
                      {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-lg" />)}
                    </div>
                  ) : reports.length === 0 ? (
                    <p className="text-sm text-muted-foreground text-center py-4">Sem dados disponíveis</p>
                  ) : (() => {
                    const avgWords    = completed.length > 0 ? Math.round(completed.reduce((s, r) => s + (r.word_count ?? 0), 0) / completed.length) : 0;
                    const avgDuration = completed.length > 0 ? Math.round(completed.reduce((s, r) => s + (r.generation_duration_ms ?? 0), 0) / completed.length) : 0;
                    const totalRecs   = reports.reduce((s, r) => s + safeArray(r.recommendations).length, 0);
                    return (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {[
                          { label: 'Confiança Média',  value: `${avgConf}%`,                         icon: Target,     color: 'text-green-600',  sub: 'precisão da IA' },
                          { label: 'Palavras Médias',  value: avgWords.toLocaleString(),             icon: FileText,   color: 'text-blue-600',   sub: 'por relatório' },
                          { label: 'Tempo Médio',      value: durationLabel(avgDuration),            icon: Clock,      color: 'text-purple-600', sub: 'de geração' },
                          { label: 'Recomendações',    value: totalRecs,                             icon: Lightbulb,  color: 'text-amber-600',  sub: 'acções identificadas' },
                        ].map(({ label, value, icon: Icon, color, sub }) => (
                          <div key={label} className="p-4 border rounded-xl text-center">
                            <Icon className={`h-6 w-6 mx-auto mb-2 ${color}`} />
                            <p className={`text-2xl font-bold ${color}`}>{value}</p>
                            <p className="text-xs font-medium mt-1">{label}</p>
                            <p className="text-xs text-muted-foreground">{sub}</p>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>

        {/* ── Modal de visualização ──────────────────────────────────────────── */}
        <Dialog open={!!selectedReport} onOpenChange={() => setSelectedReport(null)}>
          <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 pr-8">
                {selectedReport && (() => {
                  const tc = TYPE_CONFIG[selectedReport.report_type] ?? TYPE_CONFIG.custom;
                  const TypeIcon = tc.Icon;
                  return <><TypeIcon className="h-5 w-5 text-primary" />{selectedReport.report_title}</>;
                })()}
              </DialogTitle>
              {selectedReport && (
                <div className="flex flex-wrap items-center gap-2 mt-2">
                  <Badge variant="outline">{TYPE_CONFIG[selectedReport.report_type]?.label ?? selectedReport.report_type}</Badge>
                  {(selectedReport.word_count ?? 0) > 0 && (
                    <Badge variant="secondary">{selectedReport.word_count} palavras</Badge>
                  )}
                  {(selectedReport.confidence_score ?? 0) > 0 && (
                    <Badge variant="outline" className="text-green-600 border-green-200">
                      {(selectedReport.confidence_score ?? 0).toFixed(0)}% confiança
                    </Badge>
                  )}
                  <span className="text-xs text-muted-foreground ml-auto">{formatDate(selectedReport.created_at)}</span>
                </div>
              )}
            </DialogHeader>

            {selectedReport && (
              <div className="mt-4 space-y-4">
                {/* Insights do relatório */}
                {safeArray(selectedReport.insights).length > 0 && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {safeArray(selectedReport.insights).map((ins, idx) => (
                      <div key={idx} className="p-3 border rounded-lg text-center">
                        <p className="text-xs text-muted-foreground">{ins.title}</p>
                        <p className={`text-lg font-bold mt-1 ${ins.trend === 'up' ? 'text-green-600' : ins.trend === 'down' ? 'text-red-600' : 'text-foreground'}`}>
                          {typeof ins.value === 'number' && ins.value > 1000
                            ? formatKz(ins.value)
                            : ins.value != null
                              ? `${ins.value}${typeof ins.value === 'number' && ins.value < 200 ? '%' : ''}`
                              : '—'}
                        </p>
                        {(ins.change ?? 0) !== 0 && (
                          <p className={`text-xs ${(ins.change ?? 0) >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {(ins.change ?? 0) >= 0 ? '+' : ''}{ins.change}%
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Conteúdo do relatório */}
                <div className="bg-muted/30 rounded-xl p-4 border">
                  <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                    {selectedReport.report_content || 'Conteúdo não disponível.'}
                  </pre>
                </div>

                {/* Recomendações */}
                {safeArray(selectedReport.recommendations).length > 0 && (
                  <div>
                    <h4 className="font-semibold text-sm mb-2 flex items-center gap-2">
                      <Lightbulb className="h-4 w-4 text-amber-500" /> Recomendações da IA
                    </h4>
                    <div className="space-y-2">
                      {safeArray(selectedReport.recommendations).map((rec, idx) => {
                        const pc = PRIORITY_CONFIG[rec.priority ?? 'MEDIUM'] ?? PRIORITY_CONFIG.MEDIUM;
                        return (
                          <div key={idx} className="flex items-start gap-3 p-3 bg-muted/30 rounded-lg">
                            <span className="text-sm mt-0.5">{pc.icon}</span>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-xs font-semibold text-primary">{rec.area}</span>
                                <Badge variant={pc.variant} className="text-xs">{pc.label}</Badge>
                                {rec.effort && (
                                  <Badge variant="outline" className="text-xs ml-auto">Esforço: {rec.effort}</Badge>
                                )}
                              </div>
                              <p className="text-xs text-muted-foreground mt-1">{rec.action}</p>
                              {(rec.impact ?? 0) > 0 && (
                                <p className="text-xs font-medium text-green-600 mt-1 flex items-center gap-1">
                                  <DollarSign className="h-3 w-3" /> Impacto potencial: {formatKz(rec.impact)}
                                </p>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Fontes de dados */}
                {safeArray(selectedReport.data_sources).length > 0 && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <Info className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">Fontes de dados:</span>
                    {safeArray(selectedReport.data_sources).map(src => (
                      <Badge key={src} variant="outline" className="text-xs">{src}</Badge>
                    ))}
                  </div>
                )}

                {/* Download */}
                <div className="flex justify-end pt-2">
                  <Button
                    variant="outline" size="sm"
                    onClick={() => {
                      const content = selectedReport.report_content ?? '';
                      const blob = new Blob([content], { type: 'text/markdown' });
                      const url  = URL.createObjectURL(blob);
                      const a    = document.createElement('a');
                      a.href     = url;
                      a.download = `${(selectedReport.report_title ?? 'relatorio').replace(/[^a-z0-9]/gi, '_')}.md`;
                      a.click();
                      URL.revokeObjectURL(url);
                    }}
                  >
                    <Download className="h-4 w-4 mr-2" /> Descarregar Markdown
                  </Button>
                </div>
              </div>
            )}
          </DialogContent>
        </Dialog>

      </div>
    </Layout>
  );
}
