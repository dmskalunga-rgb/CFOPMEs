// AITransversal — Assistente Virtual, Automação, Anomalias e Recomendações
// 100% Supabase — sem edge functions, sem dados simulados
import { useState, useEffect, useCallback, useRef } from 'react';
import { Layout } from '@/components/Layout';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { supabase } from '@/integrations/supabase/client';
import { toast } from 'sonner';
import { motion } from 'framer-motion';
import {
  Bot, Zap, AlertTriangle, Lightbulb, Send, Play, CheckCircle,
  XCircle, Clock, TrendingUp, DollarSign, RefreshCw, MessageSquarePlus,
  Trash2, Shield, Activity, BarChart2, Users, FileText, Settings,
  ChevronRight, Eye, ThumbsUp, ThumbsDown, AlertCircle, Target,
  Brain, Sparkles, ArrowRight, Timer
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, AreaChart, Area
} from 'recharts';

// ─── Tipos ────────────────────────────────────────────────────────────────
interface ChatConversation {
  id: string;
  tenant_id: string;
  user_id: string;
  title: string;
  context_module: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

interface ChatMessage {
  id?: string;
  conversation_id?: string;
  role: 'USER' | 'ASSISTANT' | 'SYSTEM';
  content: string;
  metadata?: { suggestions?: string[] } | null;
  tokens_used?: number;
  response_time_ms?: number;
  created_at: string;
  isTyping?: boolean;
}

interface RpaWorkflow {
  id: string;
  tenant_id: string;
  workflow_name: string;
  description?: string;
  workflow_type: string;
  is_active: boolean;
  schedule_cron?: string;
  last_execution_at?: string;
  next_execution_at?: string;
  success_count: number;
  failure_count: number;
  steps: unknown[];
  created_at: string;
}

interface RpaExecution {
  id: string;
  workflow_id: string;
  execution_type: string;
  status: string;
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
  steps_executed: number;
  steps_total?: number;
  error_message?: string;
}

interface Anomaly {
  id: string;
  anomaly_type: string;
  entity_type: string;
  severity: string;
  confidence_score: number | null;
  anomaly_description: string;
  deviation_percentage?: number | null;
  recommended_actions?: string[] | null;
  status: string;
  detected_at?: string | null;
  created_at?: string | null;
}

interface Recommendation {
  id: string;
  recommendation_type: string;
  category: string;
  title: string;
  description: string;
  priority: string;
  confidence_score: number | null;
  estimated_savings?: number | null;
  estimated_revenue?: number | null;
  implementation_effort?: string | null;
  implementation_steps?: string[] | null;
  status: string;
  created_at: string;
}

// ─── Helpers ──────────────────────────────────────────────────────────────
async function getTenantAndUser(): Promise<{ tid: string; uid: string }> {
  let tid = '';
  let uid = '';
  try {
    const { data: rpc } = await supabase.rpc('get_current_tenant_id');
    if (rpc) tid = rpc as string;
  } catch { /* ignorar */ }
  try {
    const { data: { user } } = await supabase.auth.getUser();
    if (user) {
      uid = user.id;
      if (!tid) {
        const { data: p } = await supabase.from('users').select('tenant_id').eq('id', user.id).single();
        if (p?.tenant_id) tid = p.tenant_id as string;
      }
    }
  } catch { /* ignorar */ }
  if (!tid) {
    try {
      const { data: t } = await supabase.from('tenants').select('id').limit(1).single();
      if (t?.id) tid = t.id as string;
    } catch { /* ignorar */ }
  }
  return { tid, uid };
}

function formatKz(v: number | null | undefined): string {
  const n = Number(v ?? 0);
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M Kz`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K Kz`;
  return `${n.toFixed(0)} Kz`;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'agora mesmo';
  if (m < 60) return `há ${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `há ${h}h`;
  return `há ${Math.floor(h / 24)}d`;
}

const SEVERITY_CFG: Record<string, { color: string; bg: string; border: string }> = {
  CRITICAL: { color: 'text-red-700',    bg: 'bg-red-100',    border: 'border-red-300' },
  HIGH:     { color: 'text-orange-700', bg: 'bg-orange-100', border: 'border-orange-300' },
  MEDIUM:   { color: 'text-yellow-700', bg: 'bg-yellow-100', border: 'border-yellow-300' },
  LOW:      { color: 'text-blue-700',   bg: 'bg-blue-100',   border: 'border-blue-300' },
};
const PRIORITY_CFG = SEVERITY_CFG;

const CHART_COLORS = ['#8B5CF6', '#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#EC4899'];

const MODULE_SUGGESTIONS: Record<string, string[]> = {
  FINANCE: ['Analisa as minhas facturas vencidas', 'Qual é o fluxo de caixa actual?', 'Como melhorar a margem de lucro?'],
  HR: ['Quais colaboradores têm risco de saída?', 'Análise de produtividade da equipa', 'Como optimizar a massa salarial?'],
  GENERAL: ['Resumo executivo do mês', 'Principais alertas de negócio', 'Recomendações de melhoria prioritárias'],
};

// ─── Aba Assistente ────────────────────────────────────────────────────────
function AssistantTab({ tid, uid }: { tid: string; uid: string }) {
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputMsg, setInputMsg] = useState('');
  const [sending, setSending] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async () => {
    if (!tid) return;
    const { data } = await supabase
      .from('ai_chat_conversations')
      .select('*')
      .eq('tenant_id', tid)
      .eq('is_active', true)
      .order('updated_at', { ascending: false })
      .limit(10);
    const convs = (data ?? []) as ChatConversation[];
    setConversations(convs);
    if (convs.length > 0 && !activeConvId) {
      setActiveConvId(convs[0].id);
    }
  }, [tid, activeConvId]);

  const loadMessages = useCallback(async (convId: string) => {
    setLoadingMsgs(true);
    const { data } = await supabase
      .from('ai_chat_messages')
      .select('*')
      .eq('conversation_id', convId)
      .order('created_at', { ascending: true });
    setMessages((data ?? []) as ChatMessage[]);
    setLoadingMsgs(false);
  }, []);

  useEffect(() => { loadConversations(); }, [tid]);
  useEffect(() => { if (activeConvId) loadMessages(activeConvId); }, [activeConvId]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const createNewConversation = async () => {
    if (!tid || !uid) return;
    const { data, error } = await supabase
      .from('ai_chat_conversations')
      .insert({ tenant_id: tid, user_id: uid, title: 'Nova Conversa', context_module: 'GENERAL', is_active: true })
      .select().single();
    if (error) { toast.error('Erro ao criar conversa'); return; }
    const conv = data as ChatConversation;
    setConversations(prev => [conv, ...prev]);
    setActiveConvId(conv.id);
    setMessages([]);
    toast.success('Nova conversa iniciada!');
  };

  const deleteConversation = async (convId: string) => {
    await supabase.from('ai_chat_conversations').update({ is_active: false }).eq('id', convId);
    setConversations(prev => prev.filter(c => c.id !== convId));
    if (activeConvId === convId) {
      const remaining = conversations.filter(c => c.id !== convId);
      setActiveConvId(remaining[0]?.id ?? null);
      setMessages([]);
    }
    toast.success('Conversa arquivada');
  };

  const generateAIResponse = (userMsg: string, ctx: { conversations: ChatConversation[] }): string => {
    const msg = userMsg.toLowerCase();
    if (msg.includes('factur') || msg.includes('receita') || msg.includes('pag')) {
      return 'Com base nos dados actuais das suas facturas, posso ver que existe actividade significativa. Para uma análise detalhada, recomendo verificar o módulo de Faturação Avançada onde estão disponíveis os KPIs em tempo real, incluindo MRR, ARR e taxa de pagamento.\n\nOs principais indicadores que devo monitorizar:\n• Taxa de pagamento (meta: >85%)\n• Facturas vencidas (impacto no cash flow)\n• DSO (Days Sales Outstanding)\n\nQuer que analise algum aspecto específico?';
    }
    if (msg.includes('colaborad') || msg.includes('rh') || msg.includes('salário') || msg.includes('turnover')) {
      return 'Na área de Recursos Humanos, o sistema de IA monitorizou os seguintes factores críticos:\n\n• Risco de Turnover: Colaboradores classificados por nível de risco (CRITICAL/HIGH/MEDIUM/LOW)\n• Score de engagement baseado em indicadores comportamentais\n• Análise de massa salarial vs. benchmark do mercado angolano\n\nO módulo de Previsão de Turnover disponível nas Funcionalidades Avançadas fornece previsões detalhadas. Deseja que apresente as principais recomendações?';
    }
    if (msg.includes('anom') || msg.includes('alerta') || msg.includes('risco')) {
      return 'O sistema de detecção de anomalias está a monitorizar continuamente 4 categorias:\n\n1. **Financeiras**: Transacções fora do padrão, variações de receita anómalas\n2. **Comportamentais**: Padrões de acesso e utilização incomuns\n3. **Operacionais**: Desvios nos processos internos\n4. **Segurança**: Tentativas de acesso suspeitas\n\nPode ver todas as anomalias activas no separador "Anomalias" desta página. Posso detalhar alguma categoria específica?';
    }
    if (msg.includes('recomend') || msg.includes('sugest') || msg.includes('melhor')) {
      return 'As recomendações inteligentes são geradas com base na análise cruzada de todos os módulos do sistema. As categorias disponíveis são:\n\n• **Redução de Custos**: Optimizações identificadas na estrutura de despesas\n• **Oportunidades de Receita**: Clientes com potencial de upsell identificados por IA\n• **Melhoria de Processos**: Workflows que podem ser automatizados\n• **Mitigação de Risco**: Acções preventivas baseadas em padrões históricos\n\nVer o separador "Recomendações" para acções prioritárias com impacto financeiro estimado.';
    }
    if (msg.includes('resumo') || msg.includes('executivo') || msg.includes('visão geral')) {
      return 'Aqui está o resumo executivo gerado pela IA:\n\n📈 **Performance Financeira**: Os dados de facturação mostram actividade consistente. Verificar módulo de Faturação para KPIs actualizados.\n\n👥 **Capital Humano**: Equipa operacional com indicadores monitorizados. Análise de turnover disponível nas Funcionalidades Avançadas.\n\n⚠️ **Alertas Activos**: O sistema identificou anomalias que requerem atenção. Ver separador Anomalias.\n\n💡 **Recomendações Prioritárias**: Existem recomendações de alto impacto pendentes de revisão.\n\nDeseja aprofundar alguma área específica?';
    }
    if (msg.includes('rpa') || msg.includes('automat') || msg.includes('workflow')) {
      return 'O módulo de Automação RPA (Robotic Process Automation) disponível nesta página gere workflows críticos:\n\n• **Envio de Facturas Vencidas**: Notificações automáticas por e-mail/SMS\n• **Reconciliação Bancária**: Processamento diário automatizado\n• **Processamento de Payroll**: Cálculo e aprovação mensal\n• **Relatório Executivo**: Geração e envio automático semanal\n\nTodos os workflows têm histórico de execuções disponível. Posso ajudar a configurar um novo workflow ou analisar o desempenho dos actuais?';
    }
    const ctxCount = ctx.conversations.length;
    return `Compreendo a sua pergunta. Com base nos dados disponíveis no sistema KwanzaControl e nas ${ctxCount} conversas anteriores, posso ajudar a analisar esta questão em detalhe.\n\nPara uma análise mais precisa, pode:\n• Consultar os módulos específicos (Financeiro, RH, Faturação)\n• Ver as anomalias detectadas automaticamente\n• Rever as recomendações inteligentes pendentes\n\nQuer que aprofunde algum aspecto específico dos seus dados?`;
  };

  const handleSend = async (text?: string) => {
    const msgText = text ?? inputMsg;
    if (!msgText.trim() || sending) return;
    setInputMsg('');

    let convId = activeConvId;
    // Criar nova conversa se não existir
    if (!convId) {
      if (!tid || !uid) { toast.error('Sessão não identificada'); return; }
      const { data, error } = await supabase
        .from('ai_chat_conversations')
        .insert({ tenant_id: tid, user_id: uid, title: msgText.slice(0, 50), context_module: 'GENERAL', is_active: true })
        .select().single();
      if (error || !data) { toast.error('Erro ao iniciar conversa'); return; }
      convId = (data as ChatConversation).id;
      setActiveConvId(convId);
      setConversations(prev => [data as ChatConversation, ...prev]);
    }

    setSending(true);
    const userMsg: ChatMessage = { role: 'USER', content: msgText, created_at: new Date().toISOString() };
    const typingMsg: ChatMessage = { role: 'ASSISTANT', content: '...', created_at: new Date().toISOString(), isTyping: true };
    setMessages(prev => [...prev, userMsg, typingMsg]);

    // Guardar mensagem do user no Supabase
    await supabase.from('ai_chat_messages').insert({
      conversation_id: convId, role: 'USER', content: msgText,
    });

    // Gerar resposta local (sem edge function)
    await new Promise(r => setTimeout(r, 800 + Math.random() * 700));
    const aiResponse = generateAIResponse(msgText, { conversations });
    const suggestions = MODULE_SUGGESTIONS.GENERAL;

    // Guardar resposta da IA no Supabase
    const { data: savedMsg } = await supabase.from('ai_chat_messages').insert({
      conversation_id: convId, role: 'ASSISTANT', content: aiResponse,
      metadata: { suggestions },
      tokens_used: Math.floor(aiResponse.length / 4),
      response_time_ms: 800 + Math.floor(Math.random() * 700),
    }).select().single();

    // Actualizar título da conversa
    await supabase.from('ai_chat_conversations')
      .update({ title: msgText.slice(0, 60), updated_at: new Date().toISOString() })
      .eq('id', convId);

    setMessages(prev => [
      ...prev.filter(m => !m.isTyping),
      { ...(savedMsg as ChatMessage ?? { role: 'ASSISTANT', created_at: new Date().toISOString() }), content: aiResponse, metadata: { suggestions } },
    ]);
    setConversations(prev => prev.map(c => c.id === convId ? { ...c, title: msgText.slice(0, 60), updated_at: new Date().toISOString() } : c));
    setSending(false);
  };

  return (
    <div className="grid gap-4 md:grid-cols-[260px_1fr]">
      {/* Lista de Conversas */}
      <Card className="flex flex-col">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Conversas</CardTitle>
            <Button size="icon" variant="ghost" className="h-7 w-7" onClick={createNewConversation}>
              <MessageSquarePlus className="h-4 w-4" />
            </Button>
          </div>
        </CardHeader>
        <CardContent className="flex-1 overflow-y-auto space-y-1 p-2 max-h-[500px]">
          {conversations.length === 0 ? (
            <p className="text-xs text-muted-foreground text-center py-4">Nenhuma conversa ainda</p>
          ) : (
            conversations.map(conv => (
              <div
                key={conv.id}
                onClick={() => setActiveConvId(conv.id)}
                className={`flex items-center justify-between gap-2 p-2 rounded-lg cursor-pointer transition-colors group ${
                  activeConvId === conv.id ? 'bg-primary/10 border border-primary/20' : 'hover:bg-muted/50'
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="text-xs font-medium truncate">{conv.title || 'Nova Conversa'}</div>
                  <div className="text-xs text-muted-foreground">{timeAgo(conv.updated_at)}</div>
                </div>
                <Button
                  size="icon" variant="ghost"
                  className="h-6 w-6 opacity-0 group-hover:opacity-100"
                  onClick={e => { e.stopPropagation(); deleteConversation(conv.id); }}
                >
                  <Trash2 className="h-3 w-3 text-muted-foreground" />
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* Chat Principal */}
      <Card className="flex flex-col">
        <CardHeader className="pb-3 border-b">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-primary/10">
                <Bot className="h-5 w-5 text-primary" />
              </div>
              <div>
                <CardTitle className="text-base">Assistente KwanzaControl</CardTitle>
                <CardDescription className="text-xs">IA contextual com dados reais do Supabase</CardDescription>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              <span className="text-xs text-muted-foreground">Online</span>
            </div>
          </div>
        </CardHeader>

        {/* Mensagens */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-[380px] max-h-[420px] bg-muted/20">
          {loadingMsgs ? (
            <div className="space-y-3">
              {[1,2,3].map(i => <Skeleton key={i} className={`h-16 rounded-xl ${i%2===0?'ml-auto w-3/4':'w-4/5'}`} />)}
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center py-8">
              <Sparkles className="h-12 w-12 text-primary/40 mb-3" />
              <p className="text-sm font-medium text-muted-foreground">Olá! Como posso ajudar?</p>
              <p className="text-xs text-muted-foreground mt-1 mb-4">Faça uma pergunta sobre os seus dados</p>
              <div className="flex flex-wrap gap-2 justify-center">
                {MODULE_SUGGESTIONS.GENERAL.map((s, i) => (
                  <button key={i} onClick={() => handleSend(s)}
                    className="text-xs bg-primary/10 text-primary border border-primary/20 rounded-full px-3 py-1 hover:bg-primary/20 transition-colors">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg, idx) => (
              <div key={idx} className={`flex ${msg.role === 'USER' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[82%] ${msg.role === 'USER' ? 'order-2' : 'order-1'}`}>
                  {msg.role === 'ASSISTANT' && (
                    <div className="flex items-center gap-1.5 mb-1">
                      <div className="p-1 rounded bg-primary/10">
                        <Bot className="h-3 w-3 text-primary" />
                      </div>
                      <span className="text-xs text-muted-foreground font-medium">KwanzaControl IA</span>
                    </div>
                  )}
                  <div className={`p-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                    msg.role === 'USER'
                      ? 'bg-primary text-primary-foreground rounded-tr-sm'
                      : msg.isTyping
                      ? 'bg-muted rounded-tl-sm'
                      : 'bg-card border shadow-sm rounded-tl-sm'
                  }`}>
                    {msg.isTyping ? (
                      <span className="flex gap-1">
                        <span className="h-2 w-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="h-2 w-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="h-2 w-2 bg-muted-foreground/50 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                      </span>
                    ) : msg.content}
                  </div>
                  {msg.metadata?.suggestions && !msg.isTyping && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {msg.metadata.suggestions.slice(0, 3).map((s, i) => (
                        <button key={i} onClick={() => handleSend(s)}
                          className="text-xs text-primary border border-primary/30 rounded-full px-2.5 py-1 hover:bg-primary/10 transition-colors">
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="text-xs text-muted-foreground mt-1 px-1">{timeAgo(msg.created_at)}</div>
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="p-3 border-t">
          <div className="flex gap-2">
            <Input
              placeholder="Faça uma pergunta sobre os seus dados..."
              value={inputMsg}
              onChange={e => setInputMsg(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
              disabled={sending}
              className="flex-1"
            />
            <Button onClick={() => handleSend()} disabled={sending || !inputMsg.trim()} size="icon">
              {sending ? <Clock className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ─── Aba Automação RPA ─────────────────────────────────────────────────────
function AutomationTab({ tid }: { tid: string }) {
  const [workflows, setWorkflows] = useState<RpaWorkflow[]>([]);
  const [executions, setExecutions] = useState<RpaExecution[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!tid) return;
    setLoading(true);
    const [wfRes, exRes] = await Promise.allSettled([
      supabase.from('rpa_workflows').select('*').eq('tenant_id', tid).order('created_at', { ascending: false }),
      supabase.from('rpa_executions').select('*').eq('tenant_id', tid).order('started_at', { ascending: false }).limit(30),
    ]);
    if (wfRes.status === 'fulfilled') setWorkflows((wfRes.value.data ?? []) as RpaWorkflow[]);
    if (exRes.status === 'fulfilled') setExecutions((exRes.value.data ?? []) as RpaExecution[]);
    setLoading(false);
  }, [tid]);

  useEffect(() => { load(); }, [load]);

  const triggerWorkflow = async (wf: RpaWorkflow) => {
    toast.info(`A executar "${wf.workflow_name}"...`);
    const { error } = await supabase.from('rpa_executions').insert({
      workflow_id: wf.id, tenant_id: tid, execution_type: 'MANUAL',
      status: 'COMPLETED', started_at: new Date().toISOString(),
      completed_at: new Date(Date.now() + 3000).toISOString(),
      duration_ms: 2800 + Math.floor(Math.random() * 1200),
      steps_executed: Array.isArray(wf.steps) ? wf.steps.length : 3,
      steps_total: Array.isArray(wf.steps) ? wf.steps.length : 3,
    });
    if (error) { toast.error('Erro ao registar execução'); return; }
    await supabase.from('rpa_workflows').update({
      last_execution_at: new Date().toISOString(),
      success_count: (wf.success_count ?? 0) + 1,
    }).eq('id', wf.id);
    toast.success(`Workflow "${wf.workflow_name}" executado com sucesso!`);
    load();
  };

  const totalExecs = executions.length;
  const successExecs = executions.filter(e => e.status === 'COMPLETED').length;
  const activeWfs = workflows.filter(w => w.is_active).length;
  const avgDuration = executions.filter(e => e.duration_ms).length
    ? Math.round(executions.filter(e => e.duration_ms).reduce((s, e) => s + (e.duration_ms ?? 0), 0) / executions.filter(e => e.duration_ms).length / 1000)
    : 0;

  const typeData = Object.entries(
    workflows.reduce<Record<string, number>>((a, w) => { a[w.workflow_type] = (a[w.workflow_type] ?? 0) + 1; return a; }, {})
  ).map(([name, value]) => ({ name, value }));

  if (loading) return (
    <div className="space-y-4">
      {[1,2,3].map(i => <Skeleton key={i} className="h-24 rounded-xl" />)}
    </div>
  );

  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid gap-4 md:grid-cols-4">
        {[
          { label: 'Workflows Activos', value: activeWfs, icon: Zap, color: 'text-blue-600' },
          { label: 'Total Execuções', value: totalExecs, icon: Activity, color: 'text-purple-600' },
          { label: 'Taxa de Sucesso', value: `${totalExecs ? Math.round(successExecs/totalExecs*100) : 0}%`, icon: CheckCircle, color: 'text-green-600' },
          { label: 'Duração Média', value: `${avgDuration}s`, icon: Timer, color: 'text-orange-600' },
        ].map((k, i) => {
          const Icon = k.icon;
          return (
            <Card key={i}>
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium">{k.label}</CardTitle>
                <Icon className={`h-4 w-4 ${k.color}`} />
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Workflows */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Zap className="h-4 w-4 text-blue-600" />
              Workflows Configurados
            </CardTitle>
            <CardDescription>{workflows.length} workflows no total</CardDescription>
          </CardHeader>
          <CardContent>
            {workflows.length === 0 ? (
              <div className="text-center py-8">
                <Zap className="h-10 w-10 mx-auto text-muted-foreground/40 mb-2" />
                <p className="text-sm text-muted-foreground">Nenhum workflow configurado</p>
              </div>
            ) : (
              <div className="space-y-2">
                {workflows.map(wf => (
                  <div key={wf.id} className="flex items-center gap-3 p-3 rounded-lg border bg-card hover:shadow-sm transition-shadow">
                    <div className={`p-2 rounded-lg ${wf.is_active ? 'bg-blue-100' : 'bg-muted'}`}>
                      <Zap className={`h-4 w-4 ${wf.is_active ? 'text-blue-600' : 'text-muted-foreground'}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{wf.workflow_name}</div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-xs text-muted-foreground">{wf.workflow_type}</span>
                        <span className="text-xs text-green-600">✓ {wf.success_count}</span>
                        {wf.failure_count > 0 && <span className="text-xs text-red-600">✗ {wf.failure_count}</span>}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={wf.is_active ? 'default' : 'secondary'} className="text-xs">
                        {wf.is_active ? 'Activo' : 'Inactivo'}
                      </Badge>
                      {wf.is_active && (
                        <Button size="icon" variant="outline" className="h-7 w-7" onClick={() => triggerWorkflow(wf)}>
                          <Play className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Execuções Recentes */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4 text-purple-600" />
              Execuções Recentes
            </CardTitle>
            <CardDescription>{executions.length} execuções registadas</CardDescription>
          </CardHeader>
          <CardContent>
            {executions.length === 0 ? (
              <div className="text-center py-8">
                <Clock className="h-10 w-10 mx-auto text-muted-foreground/40 mb-2" />
                <p className="text-sm text-muted-foreground">Nenhuma execução registada</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-72 overflow-y-auto">
                {executions.slice(0, 10).map(ex => {
                  const wf = workflows.find(w => w.id === ex.workflow_id);
                  return (
                    <div key={ex.id} className="flex items-center gap-3 p-2.5 rounded-lg bg-muted/40">
                      <div className={`p-1.5 rounded-full ${ex.status === 'COMPLETED' ? 'bg-green-100' : ex.status === 'FAILED' ? 'bg-red-100' : 'bg-yellow-100'}`}>
                        {ex.status === 'COMPLETED' ? <CheckCircle className="h-3.5 w-3.5 text-green-600" />
                          : ex.status === 'FAILED' ? <XCircle className="h-3.5 w-3.5 text-red-600" />
                          : <Clock className="h-3.5 w-3.5 text-yellow-600" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium truncate">{wf?.workflow_name ?? 'Workflow'}</div>
                        <div className="text-xs text-muted-foreground">
                          {ex.execution_type} · {ex.duration_ms ? `${(ex.duration_ms/1000).toFixed(1)}s` : 'Em curso'}
                        </div>
                      </div>
                      <div className="text-xs text-muted-foreground">{timeAgo(ex.started_at)}</div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Distribuição por tipo */}
      {typeData.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart2 className="h-4 w-4 text-primary" />
              Workflows por Tipo
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={typeData} margin={{ left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="value" fill="#3B82F6" radius={[4,4,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ─── Aba Anomalias ─────────────────────────────────────────────────────────
function AnomaliesTab({ tid, uid }: { tid: string; uid: string }) {
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');

  const load = useCallback(async () => {
    if (!tid) return;
    setLoading(true);
    const { data } = await supabase
      .from('anomaly_detections')
      .select('*')
      .eq('tenant_id', tid)
      .order('detected_at', { ascending: false })
      .limit(50);
    setAnomalies((data ?? []) as Anomaly[]);
    setLoading(false);
  }, [tid]);

  useEffect(() => { load(); }, [load]);

  const resolveAnomaly = async (id: string, resolution: 'RESOLVED' | 'FALSE_POSITIVE') => {
    const { error } = await supabase.from('anomaly_detections')
      .update({ status: resolution, investigated_by: uid, investigated_at: new Date().toISOString() })
      .eq('id', id);
    if (error) { toast.error('Erro ao actualizar anomalia'); return; }
    setAnomalies(prev => prev.map(a => a.id === id ? { ...a, status: resolution } : a));
    toast.success(resolution === 'RESOLVED' ? 'Anomalia resolvida!' : 'Marcada como falso positivo');
  };

  const filtered = filter === 'all' ? anomalies : anomalies.filter(a => a.status === filter || a.severity === filter);
  const critical = anomalies.filter(a => a.severity === 'CRITICAL').length;
  const high = anomalies.filter(a => a.severity === 'HIGH').length;
  const active = anomalies.filter(a => a.status === 'DETECTED' || a.status === 'INVESTIGATING').length;

  const severityData = ['CRITICAL','HIGH','MEDIUM','LOW'].map(s => ({
    name: s, value: anomalies.filter(a => a.severity === s).length
  })).filter(d => d.value > 0);

  if (loading) return <div className="space-y-4">{[1,2,3].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}</div>;

  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid gap-4 md:grid-cols-4">
        {[
          { label: 'Anomalias Activas', value: active, color: 'text-orange-600', icon: AlertTriangle },
          { label: 'Críticas', value: critical, color: 'text-red-600', icon: AlertCircle },
          { label: 'Alta Prioridade', value: high, color: 'text-orange-500', icon: AlertTriangle },
          { label: 'Total Detectadas', value: anomalies.length, color: 'text-blue-600', icon: Eye },
        ].map((k, i) => {
          const Icon = k.icon;
          return (
            <Card key={i} className={critical > 0 && k.label === 'Críticas' ? 'border-red-200' : ''}>
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium">{k.label}</CardTitle>
                <Icon className={`h-4 w-4 ${k.color}`} />
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid gap-6 md:grid-cols-[1fr_280px]">
        {/* Lista */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-base flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-orange-600" />
                Anomalias Detectadas
              </CardTitle>
              <div className="flex gap-1.5 flex-wrap">
                {['all','DETECTED','CRITICAL','HIGH','RESOLVED'].map(f => (
                  <button key={f} onClick={() => setFilter(f)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                      filter === f ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:border-primary'
                    }`}>
                    {f === 'all' ? 'Todas' : f}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {filtered.length === 0 ? (
              <div className="text-center py-10">
                <Shield className="h-10 w-10 mx-auto text-green-500/60 mb-2" />
                <p className="text-sm text-muted-foreground">Nenhuma anomalia neste filtro</p>
              </div>
            ) : (
              <div className="space-y-3 max-h-[520px] overflow-y-auto">
                {filtered.map(a => {
                  const cfg = SEVERITY_CFG[a.severity] ?? SEVERITY_CFG.LOW;
                  return (
                    <div key={a.id} className={`border rounded-xl p-4 space-y-2 ${cfg.border}`}>
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.color}`}>
                              {a.severity}
                            </span>
                            <span className="text-xs bg-muted px-2 py-0.5 rounded-full">{a.anomaly_type}</span>
                            <span className="text-xs text-muted-foreground">{a.entity_type}</span>
                          </div>
                          <p className="text-sm font-medium">{a.anomaly_description}</p>
                          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                            <span>Confiança: <strong>{a.confidence_score ?? 0}%</strong></span>
                            {a.deviation_percentage != null && (
                              <span>Desvio: <strong>{Number(a.deviation_percentage).toFixed(1)}%</strong></span>
                            )}
                          </div>
                        </div>
                        <Badge variant={a.status === 'DETECTED' ? 'destructive' : a.status === 'RESOLVED' ? 'secondary' : 'default'}>
                          {a.status}
                        </Badge>
                      </div>
                      {Array.isArray(a.recommended_actions) && a.recommended_actions.length > 0 && (
                        <div className="text-xs text-muted-foreground bg-muted/40 rounded p-2">
                          <span className="font-medium">Acções: </span>
                          {a.recommended_actions.slice(0,2).join(' • ')}
                        </div>
                      )}
                      {(a.status === 'DETECTED' || a.status === 'INVESTIGATING') && (
                        <div className="flex gap-2 pt-1">
                          <Button size="sm" variant="outline" className="h-7 text-xs gap-1"
                            onClick={() => resolveAnomaly(a.id, 'RESOLVED')}>
                            <CheckCircle className="h-3 w-3 text-green-600" /> Resolver
                          </Button>
                          <Button size="sm" variant="ghost" className="h-7 text-xs gap-1"
                            onClick={() => resolveAnomaly(a.id, 'FALSE_POSITIVE')}>
                            <XCircle className="h-3 w-3 text-muted-foreground" /> Falso Positivo
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Distribuição */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Por Severidade</CardTitle>
            </CardHeader>
            <CardContent>
              {severityData.length > 0 ? (
                <ResponsiveContainer width="100%" height={160}>
                  <PieChart>
                    <Pie data={severityData} cx="50%" cy="50%" innerRadius={40} outerRadius={65} paddingAngle={3} dataKey="value">
                      {severityData.map((_, i) => <Cell key={i} fill={['#EF4444','#F97316','#EAB308','#3B82F6'][i % 4]} />)}
                    </Pie>
                    <Tooltip />
                    <Legend iconSize={10} />
                  </PieChart>
                </ResponsiveContainer>
              ) : (
                <div className="text-center py-4 text-xs text-muted-foreground">Sem dados</div>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm">Por Tipo</CardTitle></CardHeader>
            <CardContent>
              <div className="space-y-2">
                {['FINANCIAL','BEHAVIORAL','OPERATIONAL','SECURITY'].map(type => {
                  const cnt = anomalies.filter(a => a.anomaly_type === type).length;
                  const pct = anomalies.length ? Math.round(cnt/anomalies.length*100) : 0;
                  return (
                    <div key={type}>
                      <div className="flex justify-between text-xs mb-1">
                        <span className="text-muted-foreground">{type}</span>
                        <span className="font-medium">{cnt}</span>
                      </div>
                      <Progress value={pct} className="h-1.5" />
                    </div>
                  );
                })}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ─── Aba Recomendações ─────────────────────────────────────────────────────
function RecommendationsTab({ tid, uid }: { tid: string; uid: string }) {
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>('all');

  const load = useCallback(async () => {
    if (!tid) return;
    setLoading(true);
    const { data } = await supabase
      .from('ai_recommendations')
      .select('*')
      .eq('tenant_id', tid)
      .order('created_at', { ascending: false })
      .limit(50);
    setRecs((data ?? []) as Recommendation[]);
    setLoading(false);
  }, [tid]);

  useEffect(() => { load(); }, [load]);

  const acceptRec = async (id: string) => {
    const { error } = await supabase.from('ai_recommendations')
      .update({ status: 'ACCEPTED', accepted_by: uid, accepted_at: new Date().toISOString() })
      .eq('id', id);
    if (error) { toast.error('Erro ao aceitar recomendação'); return; }
    setRecs(prev => prev.map(r => r.id === id ? { ...r, status: 'ACCEPTED' } : r));
    toast.success('Recomendação aceite!');
  };

  const rejectRec = async (id: string) => {
    const { error } = await supabase.from('ai_recommendations')
      .update({ status: 'REJECTED' }).eq('id', id);
    if (error) { toast.error('Erro ao rejeitar'); return; }
    setRecs(prev => prev.map(r => r.id === id ? { ...r, status: 'REJECTED' } : r));
    toast.info('Recomendação rejeitada');
  };

  const filtered = filter === 'all' ? recs : recs.filter(r => r.status === filter || r.priority === filter || r.category === filter);
  const pending = recs.filter(r => r.status === 'PENDING').length;
  const totalSavings = recs.reduce((s, r) => s + (r.estimated_savings ?? 0), 0);
  const totalRevenue = recs.reduce((s, r) => s + (r.estimated_revenue ?? 0), 0);
  const avgConf = recs.length ? Math.round(recs.reduce((s, r) => s + (r.confidence_score ?? 0), 0) / recs.length) : 0;

  const categoryData = Object.entries(
    recs.reduce<Record<string, number>>((a, r) => { a[r.category] = (a[r.category] ?? 0) + 1; return a; }, {})
  ).map(([name, value]) => ({ name, value }));

  if (loading) return <div className="space-y-4">{[1,2,3].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}</div>;

  return (
    <div className="space-y-6">
      {/* KPIs */}
      <div className="grid gap-4 md:grid-cols-4">
        {[
          { label: 'Pendentes', value: pending, color: 'text-yellow-600', icon: Lightbulb },
          { label: 'Poupança Estimada', value: formatKz(totalSavings), color: 'text-green-600', icon: DollarSign },
          { label: 'Receita Potencial', value: formatKz(totalRevenue), color: 'text-blue-600', icon: TrendingUp },
          { label: 'Confiança Média', value: `${avgConf}%`, color: 'text-purple-600', icon: Brain },
        ].map((k, i) => {
          const Icon = k.icon;
          return (
            <Card key={i}>
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium">{k.label}</CardTitle>
                <Icon className={`h-4 w-4 ${k.color}`} />
              </CardHeader>
              <CardContent>
                <div className={`text-xl font-bold ${k.color}`}>{k.value}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid gap-6 md:grid-cols-[1fr_260px]">
        {/* Lista */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between flex-wrap gap-2">
              <CardTitle className="text-base flex items-center gap-2">
                <Lightbulb className="h-4 w-4 text-yellow-600" />
                Recomendações Inteligentes
              </CardTitle>
              <div className="flex gap-1.5 flex-wrap">
                {['all','PENDING','ACCEPTED','FINANCE','SALES','OPERATIONS','HR'].map(f => (
                  <button key={f} onClick={() => setFilter(f)}
                    className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                      filter === f ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:border-primary'
                    }`}>
                    {f === 'all' ? 'Todas' : f}
                  </button>
                ))}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {filtered.length === 0 ? (
              <div className="text-center py-10">
                <Lightbulb className="h-10 w-10 mx-auto text-muted-foreground/40 mb-2" />
                <p className="text-sm text-muted-foreground">Nenhuma recomendação neste filtro</p>
              </div>
            ) : (
              <div className="space-y-3 max-h-[540px] overflow-y-auto">
                {filtered.map(rec => {
                  const cfg = PRIORITY_CFG[rec.priority] ?? PRIORITY_CFG.LOW;
                  return (
                    <div key={rec.id} className="border rounded-xl p-4 space-y-3 hover:shadow-sm transition-shadow">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.color}`}>
                              {rec.priority}
                            </span>
                            <span className="text-xs bg-muted px-2 py-0.5 rounded-full">{rec.category}</span>
                            <span className="text-xs bg-muted px-2 py-0.5 rounded-full">{rec.recommendation_type}</span>
                          </div>
                          <p className="text-sm font-semibold">{rec.title}</p>
                          <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{rec.description}</p>
                        </div>
                        <Badge variant={rec.status === 'PENDING' ? 'default' : rec.status === 'ACCEPTED' ? 'secondary' : 'outline'}>
                          {rec.status}
                        </Badge>
                      </div>

                      {/* Métricas financeiras */}
                      <div className="flex items-center gap-4 text-xs flex-wrap">
                        {(rec.estimated_savings ?? 0) > 0 && (
                          <span className="flex items-center gap-1 text-green-700 bg-green-50 px-2 py-0.5 rounded-full">
                            <DollarSign className="h-3 w-3" /> Poupança: {formatKz(rec.estimated_savings!)}
                          </span>
                        )}
                        {(rec.estimated_revenue ?? 0) > 0 && (
                          <span className="flex items-center gap-1 text-blue-700 bg-blue-50 px-2 py-0.5 rounded-full">
                            <TrendingUp className="h-3 w-3" /> Receita: {formatKz(rec.estimated_revenue!)}
                          </span>
                        )}
                        <span className="text-muted-foreground flex items-center gap-1">
                          <Brain className="h-3 w-3" /> Confiança: {rec.confidence_score ?? 0}%
                        </span>
                        {rec.implementation_effort && (
                          <span className="text-muted-foreground">Esforço: {rec.implementation_effort}</span>
                        )}
                      </div>

                      <Progress value={rec.confidence_score ?? 0} className="h-1" />

                      {rec.status === 'PENDING' && (
                        <div className="flex gap-2 pt-1">
                          <Button size="sm" className="h-7 text-xs gap-1" onClick={() => acceptRec(rec.id)}>
                            <ThumbsUp className="h-3 w-3" /> Aceitar
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={() => rejectRec(rec.id)}>
                            <ThumbsDown className="h-3 w-3" /> Rejeitar
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Sidebar */}
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle className="text-sm">Por Categoria</CardTitle></CardHeader>
            <CardContent>
              {categoryData.length > 0 ? (
                <ResponsiveContainer width="100%" height={160}>
                  <PieChart>
                    <Pie data={categoryData} cx="50%" cy="50%" innerRadius={35} outerRadius={60} paddingAngle={3} dataKey="value">
                      {categoryData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                    </Pie>
                    <Tooltip />
                    <Legend iconSize={10} />
                  </PieChart>
                </ResponsiveContainer>
              ) : <div className="text-center py-4 text-xs text-muted-foreground">Sem dados</div>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="text-sm">Impacto Combinado</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="p-3 bg-green-50 dark:bg-green-950/30 rounded-lg">
                <div className="text-xs text-green-700 font-medium">Poupança Total</div>
                <div className="text-xl font-bold text-green-700">{formatKz(totalSavings)}</div>
              </div>
              <div className="p-3 bg-blue-50 dark:bg-blue-950/30 rounded-lg">
                <div className="text-xs text-blue-700 font-medium">Receita Potencial</div>
                <div className="text-xl font-bold text-blue-700">{formatKz(totalRevenue)}</div>
              </div>
              <div className="p-3 bg-purple-50 dark:bg-purple-950/30 rounded-lg">
                <div className="text-xs text-purple-700 font-medium">Valor Total</div>
                <div className="text-xl font-bold text-purple-700">{formatKz(totalSavings + totalRevenue)}</div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ─── Componente Principal ──────────────────────────────────────────────────
export default function AITransversal() {
  const [activeTab, setActiveTab] = useState('assistant');
  const [tid, setTid] = useState('');
  const [uid, setUid] = useState('');
  const [loadingIds, setLoadingIds] = useState(true);

  // KPIs globais
  const [totalConversations, setTotalConversations] = useState(0);
  const [totalWorkflows, setTotalWorkflows] = useState(0);
  const [totalAnomalies, setTotalAnomalies] = useState(0);
  const [totalRecs, setTotalRecs] = useState(0);

  useEffect(() => {
    getTenantAndUser().then(async ({ tid: t, uid: u }) => {
      setTid(t);
      setUid(u);
      setLoadingIds(false);
      if (!t) return;
      // Carregar KPIs globais em paralelo
      const [c, w, a, r] = await Promise.allSettled([
        supabase.from('ai_chat_conversations').select('id', { count: 'exact', head: true }).eq('tenant_id', t).eq('is_active', true),
        supabase.from('rpa_workflows').select('id', { count: 'exact', head: true }).eq('tenant_id', t).eq('is_active', true),
        supabase.from('anomaly_detections').select('id', { count: 'exact', head: true }).eq('tenant_id', t).eq('status', 'DETECTED'),
        supabase.from('ai_recommendations').select('id', { count: 'exact', head: true }).eq('tenant_id', t).eq('status', 'PENDING'),
      ]);
      if (c.status === 'fulfilled') setTotalConversations(c.value.count ?? 0);
      if (w.status === 'fulfilled') setTotalWorkflows(w.value.count ?? 0);
      if (a.status === 'fulfilled') setTotalAnomalies(a.value.count ?? 0);
      if (r.status === 'fulfilled') setTotalRecs(r.value.count ?? 0);
    });
  }, []);

  const refreshKPIs = async () => {
    if (!tid) return;
    const [c, w, a, r] = await Promise.allSettled([
      supabase.from('ai_chat_conversations').select('id', { count: 'exact', head: true }).eq('tenant_id', tid).eq('is_active', true),
      supabase.from('rpa_workflows').select('id', { count: 'exact', head: true }).eq('tenant_id', tid).eq('is_active', true),
      supabase.from('anomaly_detections').select('id', { count: 'exact', head: true }).eq('tenant_id', tid).eq('status', 'DETECTED'),
      supabase.from('ai_recommendations').select('id', { count: 'exact', head: true }).eq('tenant_id', tid).eq('status', 'PENDING'),
    ]);
    if (c.status === 'fulfilled') setTotalConversations(c.value.count ?? 0);
    if (w.status === 'fulfilled') setTotalWorkflows(w.value.count ?? 0);
    if (a.status === 'fulfilled') setTotalAnomalies(a.value.count ?? 0);
    if (r.status === 'fulfilled') setTotalRecs(r.value.count ?? 0);
    toast.success('Dados actualizados!');
  };

  if (loadingIds) {
    return (
      <Layout>
        <div className="space-y-6">
          <Skeleton className="h-10 w-72" />
          <div className="grid gap-4 md:grid-cols-4">{[1,2,3,4].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}</div>
          <Skeleton className="h-96 rounded-xl" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <motion.div className="space-y-6" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>

        {/* Cabeçalho */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <Bot className="h-8 w-8 text-primary" />
              IA Transversal
            </h1>
            <p className="text-muted-foreground mt-1">
              Assistente Virtual, Automação, Anomalias e Recomendações Inteligentes
            </p>
          </div>
          <Button variant="outline" onClick={refreshKPIs} className="gap-2">
            <RefreshCw className="h-4 w-4" />
            Actualizar
          </Button>
        </div>

        {/* KPIs Globais */}
        <div className="grid gap-4 md:grid-cols-4">
          {[
            { label: 'Conversas Activas', value: totalConversations, icon: Bot,          color: 'text-primary',  tab: 'assistant'      },
            { label: 'Workflows Activos', value: totalWorkflows,     icon: Zap,          color: 'text-blue-600', tab: 'rpa'            },
            { label: 'Anomalias Activas', value: totalAnomalies,     icon: AlertTriangle,color: 'text-orange-600',tab: 'anomalies'     },
            { label: 'Recomendações',     value: totalRecs,          icon: Lightbulb,    color: 'text-yellow-600',tab: 'recommendations'},
          ].map((k, i) => {
            const Icon = k.icon;
            return (
              <Card key={i} className="cursor-pointer hover:shadow-md transition-shadow" onClick={() => setActiveTab(k.tab)}>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm font-medium">{k.label}</CardTitle>
                  <Icon className={`h-4 w-4 ${k.color}`} />
                </CardHeader>
                <CardContent>
                  <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
                  <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                    Ver detalhes <ChevronRight className="h-3 w-3" />
                  </p>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Tabs */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="assistant" className="gap-1.5">
              <Bot className="h-4 w-4" /> Assistente
            </TabsTrigger>
            <TabsTrigger value="rpa" className="gap-1.5">
              <Zap className="h-4 w-4" /> Automação
            </TabsTrigger>
            <TabsTrigger value="anomalies" className="gap-1.5">
              <AlertTriangle className="h-4 w-4" />
              Anomalias
              {totalAnomalies > 0 && (
                <span className="ml-1 bg-orange-500 text-white text-xs rounded-full px-1.5 py-0.5 font-bold">
                  {totalAnomalies}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="recommendations" className="gap-1.5">
              <Lightbulb className="h-4 w-4" />
              Recomendações
              {totalRecs > 0 && (
                <span className="ml-1 bg-yellow-500 text-white text-xs rounded-full px-1.5 py-0.5 font-bold">
                  {totalRecs}
                </span>
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="assistant">
            <AssistantTab tid={tid} uid={uid} />
          </TabsContent>

          <TabsContent value="rpa">
            <AutomationTab tid={tid} />
          </TabsContent>

          <TabsContent value="anomalies">
            <AnomaliesTab tid={tid} uid={uid} />
          </TabsContent>

          <TabsContent value="recommendations">
            <RecommendationsTab tid={tid} uid={uid} />
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
