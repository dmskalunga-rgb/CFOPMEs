// AIUeba — UEBA: User and Entity Behavior Analytics
// 100% Supabase — sem dados simulados, sem edge functions
import { useState, useEffect, useCallback } from 'react';
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
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle, Shield, TrendingUp, Activity, RefreshCw,
  Search, Filter, Eye, CheckCircle, XCircle, Clock,
  User, FileText, CreditCard, Users, AlertCircle,
  ChevronDown, ChevronUp, BarChart2, Target, Lock,
  Brain, Zap, Database, ArrowUpRight, ArrowDownRight,
  ScanLine, ShieldAlert, ShieldCheck, Info
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, RadarChart, Radar, PolarGrid,
  PolarAngleAxis, PolarRadiusAxis, AreaChart, Area, LineChart, Line
} from 'recharts';

// ─── Tipos ────────────────────────────────────────────────────────────────
interface UebaAlert {
  id: string;
  tenant_id: string;
  user_id?: string;
  entity_type: string;
  entity_id: string;
  anomaly_type: string;
  anomaly_score: number;
  severity: string;
  title: string;
  description: string;
  details?: Record<string, unknown>;
  recommendations?: string[];
  baseline_value?: number;
  actual_value?: number;
  deviation_percentage?: number;
  status: string;
  assigned_to?: string;
  resolved_at?: string;
  resolved_by?: string;
  resolution_notes?: string;
  false_positive_reason?: string;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

interface UebaBaseline {
  id: string;
  tenant_id: string;
  entity_type: string;
  entity_id: string;
  metric_name: string;
  baseline_value: number;
  std_deviation: number;
  min_value?: number;
  max_value?: number;
  sample_size: number;
  confidence_level: number;
  metadata?: Record<string, unknown>;
  last_updated_at?: string;
}

// ─── Config de severidade ──────────────────────────────────────────────────
const SEV_CFG: Record<string, {
  bg: string; border: string; text: string; badge: string; icon: React.ReactNode; label: string;
}> = {
  critical: {
    bg: 'bg-red-50 dark:bg-red-950/20',
    border: 'border-red-300 dark:border-red-800',
    text: 'text-red-700 dark:text-red-400',
    badge: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
    icon: <AlertCircle className="h-4 w-4" />,
    label: 'CRÍTICO',
  },
  high: {
    bg: 'bg-orange-50 dark:bg-orange-950/20',
    border: 'border-orange-300 dark:border-orange-800',
    text: 'text-orange-700 dark:text-orange-400',
    badge: 'bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300',
    icon: <AlertTriangle className="h-4 w-4" />,
    label: 'ALTA',
  },
  medium: {
    bg: 'bg-yellow-50 dark:bg-yellow-950/20',
    border: 'border-yellow-300 dark:border-yellow-800',
    text: 'text-yellow-700 dark:text-yellow-400',
    badge: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/50 dark:text-yellow-300',
    icon: <AlertTriangle className="h-4 w-4" />,
    label: 'MÉDIA',
  },
  low: {
    bg: 'bg-blue-50 dark:bg-blue-950/20',
    border: 'border-blue-300 dark:border-blue-800',
    text: 'text-blue-700 dark:text-blue-400',
    badge: 'bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300',
    icon: <Info className="h-4 w-4" />,
    label: 'BAIXA',
  },
};

const ENTITY_ICONS: Record<string, React.ReactNode> = {
  user:        <User className="h-4 w-4" />,
  transaction: <CreditCard className="h-4 w-4" />,
  invoice:     <FileText className="h-4 w-4" />,
  employee:    <Users className="h-4 w-4" />,
  system:      <Database className="h-4 w-4" />,
};

const ANOMALY_TYPE_LABELS: Record<string, string> = {
  fraud:             'Fraude',
  unusual_pattern:   'Padrão Incomum',
  policy_violation:  'Violação de Política',
  security_breach:   'Segurança',
};

const CHART_COLORS = ['#EF4444', '#F97316', '#EAB308', '#3B82F6', '#8B5CF6', '#10B981'];

// ─── Helpers ──────────────────────────────────────────────────────────────
async function getTenantAndUser(): Promise<{ tid: string; uid: string }> {
  let tid = '';
  let uid = '';
  try {
    const { data } = await supabase.rpc('get_current_tenant_id');
    if (data) tid = data as string;
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

function formatKz(v: number): string {
  if (!v || isNaN(v)) return '—';
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M Kz`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)}K Kz`;
  return `${v.toFixed(0)} Kz`;
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

// ─── Card de Alerta expandível ─────────────────────────────────────────────
function AlertCard({ alert, uid, onUpdate }: {
  alert: UebaAlert;
  uid: string;
  onUpdate: (id: string, updates: Partial<UebaAlert>) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [notes, setNotes] = useState('');
  const cfg = SEV_CFG[alert.severity] ?? SEV_CFG.low;

  const updateStatus = async (status: string) => {
    setUpdating(true);
    const updates: Partial<UebaAlert> = { status };
    if (status === 'RESOLVED') {
      updates.resolved_at = new Date().toISOString();
      updates.resolved_by = uid || undefined;
      if (notes) updates.resolution_notes = notes;
    }
    if (status === 'FALSE_POSITIVE') {
      updates.false_positive_reason = notes || 'Verificado manualmente — não é anomalia real';
    }
    const { error } = await supabase.from('ueba_alerts').update(updates).eq('id', alert.id);
    if (error) { toast.error('Erro ao actualizar alerta'); setUpdating(false); return; }
    onUpdate(alert.id, updates);
    toast.success(
      status === 'RESOLVED' ? '✅ Alerta resolvido!' :
      status === 'INVESTIGATING' ? '🔍 Em investigação' :
      status === 'FALSE_POSITIVE' ? '🚫 Marcado como falso positivo' :
      'Alerta actualizado'
    );
    setUpdating(false);
  };

  const recs = Array.isArray(alert.recommendations) ? alert.recommendations as string[] : [];
  const details = alert.details as Record<string, unknown> ?? {};

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className={`rounded-xl border-2 ${cfg.border} ${cfg.bg} transition-all`}
    >
      {/* Header sempre visível */}
      <div className="p-4 cursor-pointer" onClick={() => setExpanded(e => !e)}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 flex-1 min-w-0">
            {/* Score circular */}
            <div className="relative flex-shrink-0">
              <div className={`w-12 h-12 rounded-full border-2 ${cfg.border} flex items-center justify-center font-bold text-sm ${cfg.text}`}>
                {alert.anomaly_score.toFixed(0)}
              </div>
              <div className={`absolute -top-1 -right-1 p-0.5 rounded-full ${cfg.badge}`}>
                {cfg.icon}
              </div>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${cfg.badge}`}>
                  {cfg.label}
                </span>
                <span className="text-xs bg-muted px-2 py-0.5 rounded-full flex items-center gap-1">
                  {ENTITY_ICONS[alert.entity_type] ?? <Database className="h-3 w-3" />}
                  {alert.entity_type.toUpperCase()}
                </span>
                <span className="text-xs bg-muted px-2 py-0.5 rounded-full">
                  {ANOMALY_TYPE_LABELS[alert.anomaly_type] ?? alert.anomaly_type}
                </span>
              </div>
              <h4 className={`font-semibold text-sm ${cfg.text} leading-snug`}>{alert.title}</h4>
              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{alert.description}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Badge variant={
              alert.status === 'OPEN' ? 'destructive' :
              alert.status === 'INVESTIGATING' ? 'default' :
              alert.status === 'RESOLVED' ? 'secondary' : 'outline'
            } className="text-xs">
              {alert.status === 'OPEN' ? 'Aberto' :
               alert.status === 'INVESTIGATING' ? 'Em Investigação' :
               alert.status === 'RESOLVED' ? 'Resolvido' :
               alert.status === 'FALSE_POSITIVE' ? 'Falso Positivo' : alert.status}
            </Badge>
            <div className="text-xs text-muted-foreground whitespace-nowrap">{timeAgo(alert.created_at)}</div>
            {expanded ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
          </div>
        </div>

        {/* Barra de score */}
        <div className="mt-3 flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-20">Score:</span>
          <Progress value={alert.anomaly_score} className="flex-1 h-2" />
          <span className={`text-xs font-bold ${cfg.text}`}>{alert.anomaly_score.toFixed(1)}/100</span>
        </div>
      </div>

      {/* Conteúdo expandido */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 space-y-4 border-t border-muted/50 pt-3">

              {/* Descrição completa */}
              <div>
                <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">Descrição Detalhada</h5>
                <p className="text-sm leading-relaxed">{alert.description}</p>
              </div>

              {/* Valores baseline vs actual */}
              {(alert.baseline_value !== undefined || alert.actual_value !== undefined) && (
                <div className="grid grid-cols-3 gap-3">
                  <div className="bg-muted/40 rounded-lg p-3 text-center">
                    <div className="text-xs text-muted-foreground mb-0.5">Valor Baseline</div>
                    <div className="text-sm font-bold">
                      {alert.baseline_value !== undefined && alert.baseline_value !== null
                        ? (alert.baseline_value > 1000 ? formatKz(alert.baseline_value) : alert.baseline_value.toFixed(1))
                        : '—'}
                    </div>
                  </div>
                  <div className={`rounded-lg p-3 text-center ${cfg.bg} border ${cfg.border}`}>
                    <div className={`text-xs font-medium mb-0.5 ${cfg.text}`}>Valor Actual</div>
                    <div className={`text-sm font-bold ${cfg.text}`}>
                      {alert.actual_value !== undefined && alert.actual_value !== null
                        ? (alert.actual_value > 1000 ? formatKz(alert.actual_value) : alert.actual_value.toFixed(1))
                        : '—'}
                    </div>
                  </div>
                  <div className="bg-muted/40 rounded-lg p-3 text-center">
                    <div className="text-xs text-muted-foreground mb-0.5">Desvio</div>
                    <div className={`text-sm font-bold ${
                      (alert.deviation_percentage ?? 0) > 0 ? 'text-red-600' : 'text-green-600'
                    }`}>
                      {alert.deviation_percentage !== undefined && alert.deviation_percentage !== null
                        ? `${alert.deviation_percentage > 0 ? '+' : ''}${alert.deviation_percentage.toFixed(1)}%`
                        : '—'}
                    </div>
                  </div>
                </div>
              )}

              {/* Detalhes técnicos */}
              {Object.keys(details).length > 0 && (
                <div>
                  <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Detalhes Técnicos</h5>
                  <div className="grid grid-cols-2 gap-2">
                    {Object.entries(details).slice(0, 8).map(([k, v]) => (
                      <div key={k} className="flex items-start gap-2 text-xs bg-muted/30 rounded p-2">
                        <span className="text-muted-foreground capitalize font-medium">{k.replace(/_/g, ' ')}:</span>
                        <span className="font-medium break-all">
                          {Array.isArray(v) ? v.join(', ') : String(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Recomendações */}
              {recs.length > 0 && (
                <div>
                  <h5 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">
                    Acções Recomendadas ({recs.length})
                  </h5>
                  <div className="space-y-1.5">
                    {recs.map((rec, i) => (
                      <div key={i} className="flex items-start gap-2 text-sm">
                        <span className={`flex-shrink-0 w-5 h-5 rounded-full ${cfg.badge} flex items-center justify-center text-xs font-bold mt-0.5`}>
                          {i + 1}
                        </span>
                        <span>{rec}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Entidade */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  {ENTITY_ICONS[alert.entity_type] ?? <Database className="h-3 w-3" />}
                  Entidade: <strong className="text-foreground">{alert.entity_id}</strong>
                </span>
                <span>Detectado: <strong className="text-foreground">{new Date(alert.created_at).toLocaleString('pt-AO')}</strong></span>
              </div>

              {/* Acções */}
              {(alert.status === 'OPEN' || alert.status === 'INVESTIGATING') && (
                <div className="space-y-3 pt-2 border-t border-muted/50">
                  <Input
                    placeholder="Notas de resolução (opcional)..."
                    value={notes}
                    onChange={e => setNotes(e.target.value)}
                    className="h-8 text-sm"
                  />
                  <div className="flex flex-wrap gap-2">
                    {alert.status === 'OPEN' && (
                      <Button size="sm" variant="outline" className="h-8 text-xs gap-1"
                        disabled={updating} onClick={() => updateStatus('INVESTIGATING')}>
                        <Eye className="h-3 w-3" /> Iniciar Investigação
                      </Button>
                    )}
                    <Button size="sm" className="h-8 text-xs gap-1 bg-green-600 hover:bg-green-700"
                      disabled={updating} onClick={() => updateStatus('RESOLVED')}>
                      <CheckCircle className="h-3 w-3" /> Resolver
                    </Button>
                    <Button size="sm" variant="outline" className="h-8 text-xs gap-1"
                      disabled={updating} onClick={() => updateStatus('FALSE_POSITIVE')}>
                      <XCircle className="h-3 w-3" /> Falso Positivo
                    </Button>
                  </div>
                </div>
              )}
              {alert.status === 'RESOLVED' && alert.resolution_notes && (
                <div className="text-xs bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg p-3">
                  <span className="font-medium text-green-700">Nota de resolução: </span>
                  <span className="text-green-700">{alert.resolution_notes}</span>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ─── Componente Principal ──────────────────────────────────────────────────
export default function AIUeba() {
  const [alerts, setAlerts] = useState<UebaAlert[]>([]);
  const [baselines, setBaselines] = useState<UebaBaseline[]>([]);
  const [loading, setLoading] = useState(true);
  const [tid, setTid] = useState('');
  const [uid, setUid] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterSeverity, setFilterSeverity] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [filterType, setFilterType] = useState('all');
  const [activeTab, setActiveTab] = useState('overview');

  const loadData = useCallback(async (tenantId: string) => {
    if (!tenantId) return;
    setLoading(true);
    const [alertsRes, baselinesRes] = await Promise.allSettled([
      supabase
        .from('ueba_alerts')
        .select('*')
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false })
        .limit(100),
      supabase
        .from('ueba_baselines')
        .select('*')
        .eq('tenant_id', tenantId)
        .order('last_updated_at', { ascending: false }),
    ]);
    if (alertsRes.status === 'fulfilled' && !alertsRes.value.error) {
      setAlerts((alertsRes.value.data ?? []) as UebaAlert[]);
    } else if (alertsRes.status === 'rejected' || alertsRes.value.error) {
      toast.error('Erro ao carregar alertas UEBA');
    }
    if (baselinesRes.status === 'fulfilled' && !baselinesRes.value.error) {
      setBaselines((baselinesRes.value.data ?? []) as UebaBaseline[]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    getTenantAndUser().then(({ tid: t, uid: u }) => {
      setTid(t);
      setUid(u);
      if (t) loadData(t);
    });
  }, []);

  const handleRefresh = async () => {
    if (!tid) return;
    toast.info('A actualizar dados UEBA...');
    await loadData(tid);
    toast.success('Dados actualizados!');
  };

  const handleAlertUpdate = (id: string, updates: Partial<UebaAlert>) => {
    setAlerts(prev => prev.map(a => a.id === id ? { ...a, ...updates } : a));
  };

  // ── Filtros ──
  const filteredAlerts = alerts.filter(a => {
    const q = searchQuery.toLowerCase();
    if (q && !a.title.toLowerCase().includes(q) && !a.description.toLowerCase().includes(q) && !a.entity_id.toLowerCase().includes(q)) return false;
    if (filterSeverity !== 'all' && a.severity !== filterSeverity) return false;
    if (filterStatus !== 'all' && a.status !== filterStatus) return false;
    if (filterType !== 'all' && a.anomaly_type !== filterType) return false;
    return true;
  });

  // ── KPIs ──
  const kpis = {
    total: alerts.length,
    critical: alerts.filter(a => a.severity === 'critical').length,
    high: alerts.filter(a => a.severity === 'high').length,
    medium: alerts.filter(a => a.severity === 'medium').length,
    low: alerts.filter(a => a.severity === 'low').length,
    open: alerts.filter(a => a.status === 'OPEN').length,
    investigating: alerts.filter(a => a.status === 'INVESTIGATING').length,
    resolved: alerts.filter(a => a.status === 'RESOLVED').length,
    falsePosive: alerts.filter(a => a.status === 'FALSE_POSITIVE').length,
    avgScore: alerts.length ? Math.round(alerts.reduce((s, a) => s + a.anomaly_score, 0) / alerts.length) : 0,
  };

  // ── Dados para gráficos ──
  const severityData = [
    { name: 'Crítico', value: kpis.critical, color: '#EF4444' },
    { name: 'Alta',    value: kpis.high,     color: '#F97316' },
    { name: 'Média',   value: kpis.medium,   color: '#EAB308' },
    { name: 'Baixa',   value: kpis.low,      color: '#3B82F6' },
  ].filter(d => d.value > 0);

  const statusData = [
    { name: 'Abertos',        value: kpis.open,        color: '#EF4444' },
    { name: 'Investigando',   value: kpis.investigating,color: '#F97316' },
    { name: 'Resolvidos',     value: kpis.resolved,    color: '#10B981' },
    { name: 'Falso Positivo', value: kpis.falsePosive, color: '#6B7280' },
  ].filter(d => d.value > 0);

  const typeData = Object.entries(
    alerts.reduce<Record<string, number>>((acc, a) => {
      const label = ANOMALY_TYPE_LABELS[a.anomaly_type] ?? a.anomaly_type;
      acc[label] = (acc[label] ?? 0) + 1;
      return acc;
    }, {})
  ).map(([name, value]) => ({ name, value }));

  const entityData = Object.entries(
    alerts.reduce<Record<string, number>>((acc, a) => {
      acc[a.entity_type] = (acc[a.entity_type] ?? 0) + 1;
      return acc;
    }, {})
  ).map(([name, value]) => ({ name: name.toUpperCase(), value }));

  const scoreDistribution = [
    { range: '0-20',  count: alerts.filter(a => a.anomaly_score < 20).length },
    { range: '20-40', count: alerts.filter(a => a.anomaly_score >= 20 && a.anomaly_score < 40).length },
    { range: '40-60', count: alerts.filter(a => a.anomaly_score >= 40 && a.anomaly_score < 60).length },
    { range: '60-80', count: alerts.filter(a => a.anomaly_score >= 60 && a.anomaly_score < 80).length },
    { range: '80-100',count: alerts.filter(a => a.anomaly_score >= 80).length },
  ];

  // Score de risco global (0-100)
  const globalRiskScore = kpis.total > 0
    ? Math.min(100, Math.round(
        (kpis.critical * 25 + kpis.high * 15 + kpis.medium * 8 + kpis.low * 3) / Math.max(kpis.total, 1)
        + (kpis.open / Math.max(kpis.total, 1)) * 20
      ))
    : 0;

  if (loading) {
    return (
      <Layout>
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <Skeleton className="h-10 w-72" />
            <Skeleton className="h-9 w-36" />
          </div>
          <div className="grid gap-4 md:grid-cols-4">
            {[1,2,3,4].map(i => <Skeleton key={i} className="h-28 rounded-xl" />)}
          </div>
          <Skeleton className="h-96 rounded-xl" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <motion.div className="space-y-6" initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>

        {/* ── Cabeçalho ── */}
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="p-2 bg-orange-100 dark:bg-orange-950/50 rounded-xl">
                <ScanLine className="h-7 w-7 text-orange-600" />
              </div>
              UEBA — Detecção de Anomalias
            </h1>
            <p className="text-muted-foreground mt-1">
              User and Entity Behavior Analytics · Monitorização comportamental em tempo real
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground bg-muted rounded-full px-3 py-1.5">
              <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
              Tenant: <code className="font-mono">{tid ? tid.slice(0, 8) + '...' : '—'}</code>
            </div>
            <Button onClick={handleRefresh} variant="outline" className="gap-2">
              <RefreshCw className="h-4 w-4" />
              Actualizar
            </Button>
          </div>
        </div>

        {/* ── Score de Risco Global ── */}
        <Card className={`border-2 ${
          globalRiskScore >= 70 ? 'border-red-300 bg-red-50 dark:bg-red-950/20' :
          globalRiskScore >= 40 ? 'border-orange-300 bg-orange-50 dark:bg-orange-950/20' :
          'border-green-300 bg-green-50 dark:bg-green-950/20'
        }`}>
          <CardContent className="p-5">
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="flex items-center gap-4">
                <div className={`relative w-16 h-16 rounded-full border-4 flex items-center justify-center font-bold text-lg ${
                  globalRiskScore >= 70 ? 'border-red-500 text-red-700' :
                  globalRiskScore >= 40 ? 'border-orange-500 text-orange-700' :
                  'border-green-500 text-green-700'
                }`}>
                  {globalRiskScore}
                </div>
                <div>
                  <div className="text-sm font-medium text-muted-foreground">Score de Risco Global</div>
                  <div className={`text-lg font-bold ${
                    globalRiskScore >= 70 ? 'text-red-700' :
                    globalRiskScore >= 40 ? 'text-orange-700' :
                    'text-green-700'
                  }`}>
                    {globalRiskScore >= 70 ? '🔴 RISCO ELEVADO — Atenção Imediata Necessária' :
                     globalRiskScore >= 40 ? '🟠 RISCO MODERADO — Monitorização Activa' :
                     '🟢 RISCO BAIXO — Sistema Normal'}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {kpis.critical} críticos · {kpis.high} alta · {kpis.medium} média · {kpis.low} baixa · {kpis.open} abertos
                  </div>
                </div>
              </div>
              <Progress value={globalRiskScore} className="w-48 h-3" />
            </div>
          </CardContent>
        </Card>

        {/* ── KPIs ── */}
        <div className="grid gap-4 md:grid-cols-4 lg:grid-cols-8">
          {[
            { label: 'Críticos',       value: kpis.critical,     color: 'text-red-600',    bg: 'bg-red-100',    icon: ShieldAlert  },
            { label: 'Alta Prioridade',value: kpis.high,         color: 'text-orange-600', bg: 'bg-orange-100', icon: AlertTriangle },
            { label: 'Média Prioridade',value: kpis.medium,      color: 'text-yellow-600', bg: 'bg-yellow-100', icon: AlertTriangle },
            { label: 'Baixa Prioridade',value: kpis.low,         color: 'text-blue-600',   bg: 'bg-blue-100',   icon: Info          },
            { label: 'Abertos',        value: kpis.open,         color: 'text-red-600',    bg: 'bg-red-50',     icon: AlertCircle   },
            { label: 'Investigando',   value: kpis.investigating, color: 'text-purple-600', bg: 'bg-purple-50',  icon: Eye           },
            { label: 'Resolvidos',     value: kpis.resolved,     color: 'text-green-600',  bg: 'bg-green-50',   icon: ShieldCheck   },
            { label: 'Score Médio',    value: `${kpis.avgScore}`, color: 'text-primary',   bg: 'bg-primary/10', icon: Brain         },
          ].map((k, i) => {
            const Icon = k.icon;
            return (
              <Card key={i} className="md:col-span-2">
                <CardHeader className="flex flex-row items-center justify-between pb-2 space-y-0">
                  <CardTitle className="text-xs font-medium">{k.label}</CardTitle>
                  <div className={`p-1.5 rounded-lg ${k.bg}`}>
                    <Icon className={`h-3.5 w-3.5 ${k.color}`} />
                  </div>
                </CardHeader>
                <CardContent>
                  <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* ── Tabs ── */}
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="overview" className="gap-1.5">
              <BarChart2 className="h-4 w-4" /> Visão Geral
            </TabsTrigger>
            <TabsTrigger value="alerts" className="gap-1.5">
              <AlertTriangle className="h-4 w-4" />
              Alertas
              {kpis.open + kpis.investigating > 0 && (
                <span className="ml-1 bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 font-bold">
                  {kpis.open + kpis.investigating}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="baselines" className="gap-1.5">
              <Target className="h-4 w-4" /> Baselines
            </TabsTrigger>
            <TabsTrigger value="analytics" className="gap-1.5">
              <Brain className="h-4 w-4" /> Análise
            </TabsTrigger>
          </TabsList>

          {/* ── Tab: Visão Geral ── */}
          <TabsContent value="overview" className="space-y-6">
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">

              {/* Distribuição por severidade */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm flex items-center gap-2">
                    <ShieldAlert className="h-4 w-4 text-red-600" />
                    Por Severidade
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {severityData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={180}>
                      <PieChart>
                        <Pie data={severityData} cx="50%" cy="50%" innerRadius={45} outerRadius={70} paddingAngle={3} dataKey="value">
                          {severityData.map((d, i) => <Cell key={i} fill={d.color} />)}
                        </Pie>
                        <Tooltip />
                        <Legend iconSize={10} />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : <div className="text-center py-10 text-muted-foreground text-sm">Sem dados</div>}
                </CardContent>
              </Card>

              {/* Distribuição por status */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Activity className="h-4 w-4 text-blue-600" />
                    Por Estado
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {statusData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={180}>
                      <PieChart>
                        <Pie data={statusData} cx="50%" cy="50%" innerRadius={45} outerRadius={70} paddingAngle={3} dataKey="value">
                          {statusData.map((d, i) => <Cell key={i} fill={d.color} />)}
                        </Pie>
                        <Tooltip />
                        <Legend iconSize={10} />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : <div className="text-center py-10 text-muted-foreground text-sm">Sem dados</div>}
                </CardContent>
              </Card>

              {/* Por tipo de entidade */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Database className="h-4 w-4 text-purple-600" />
                    Por Entidade
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {entityData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={180}>
                      <BarChart data={entityData} margin={{ left: -20 }}>
                        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                        <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                        <YAxis tick={{ fontSize: 10 }} />
                        <Tooltip />
                        <Bar dataKey="value" radius={[4,4,0,0]}>
                          {entityData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  ) : <div className="text-center py-10 text-muted-foreground text-sm">Sem dados</div>}
                </CardContent>
              </Card>
            </div>

            {/* Distribuição por tipo de anomalia */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Zap className="h-4 w-4 text-yellow-600" />
                  Distribuição por Tipo de Anomalia
                </CardTitle>
              </CardHeader>
              <CardContent>
                {typeData.length > 0 ? (
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={typeData} margin={{ left: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip />
                      <Bar dataKey="value" fill="#8B5CF6" radius={[4,4,0,0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : <div className="text-center py-10 text-muted-foreground text-sm">Sem dados</div>}
              </CardContent>
            </Card>

            {/* Alertas críticos recentes */}
            {kpis.critical + kpis.high > 0 && (
              <Card className="border-red-200 dark:border-red-800">
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2 text-red-700">
                    <AlertCircle className="h-4 w-4" />
                    Alertas Críticos e de Alta Prioridade
                  </CardTitle>
                  <CardDescription>Requerem atenção imediata</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {alerts
                      .filter(a => (a.severity === 'critical' || a.severity === 'high') && a.status !== 'RESOLVED' && a.status !== 'FALSE_POSITIVE')
                      .slice(0, 4)
                      .map(a => {
                        const cfg = SEV_CFG[a.severity];
                        return (
                          <div key={a.id} className={`flex items-start gap-3 p-3 rounded-lg border ${cfg.border} ${cfg.bg}`}>
                            <div className={`p-1.5 rounded-full ${cfg.badge} flex-shrink-0`}>
                              {cfg.icon}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className={`text-xs font-bold ${cfg.text}`}>{cfg.label}</span>
                                <span className="text-xs text-muted-foreground">{a.entity_type.toUpperCase()}</span>
                                <span className="text-xs text-muted-foreground">{timeAgo(a.created_at)}</span>
                              </div>
                              <p className="text-sm font-medium mt-0.5">{a.title}</p>
                              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{a.description}</p>
                            </div>
                            <Badge variant="outline" className={`${cfg.text} border-current text-xs flex-shrink-0`}>
                              Score: {a.anomaly_score.toFixed(0)}
                            </Badge>
                          </div>
                        );
                      })}
                    <Button variant="outline" className="w-full text-sm gap-2" onClick={() => setActiveTab('alerts')}>
                      Ver Todos os Alertas <ArrowUpRight className="h-4 w-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── Tab: Alertas ── */}
          <TabsContent value="alerts" className="space-y-4">
            {/* Filtros */}
            <Card>
              <CardContent className="p-4">
                <div className="flex flex-wrap gap-3 items-center">
                  <div className="flex items-center gap-2 flex-1 min-w-[200px]">
                    <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                    <Input
                      placeholder="Pesquisar alertas..."
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      className="h-8 text-sm"
                    />
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                    {/* Severidade */}
                    <div className="flex gap-1">
                      {['all','critical','high','medium','low'].map(s => (
                        <button key={s} onClick={() => setFilterSeverity(s)}
                          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                            filterSeverity === s ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:border-primary'
                          }`}>
                          {s === 'all' ? 'Todas' : s === 'critical' ? '🔴 Crítico' : s === 'high' ? '🟠 Alta' : s === 'medium' ? '🟡 Média' : '🔵 Baixa'}
                        </button>
                      ))}
                    </div>
                    {/* Estado */}
                    <div className="flex gap-1">
                      {['all','OPEN','INVESTIGATING','RESOLVED','FALSE_POSITIVE'].map(s => (
                        <button key={s} onClick={() => setFilterStatus(s)}
                          className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${
                            filterStatus === s ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:border-primary'
                          }`}>
                          {s === 'all' ? 'Todos' : s === 'OPEN' ? 'Abertos' : s === 'INVESTIGATING' ? 'Investigando' : s === 'RESOLVED' ? 'Resolvidos' : 'Falso +'}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  Mostrando {filteredAlerts.length} de {alerts.length} alertas
                </div>
              </CardContent>
            </Card>

            {/* Lista de alertas */}
            {filteredAlerts.length === 0 ? (
              <div className="text-center py-16">
                <ShieldCheck className="h-14 w-14 mx-auto text-green-500/50 mb-3" />
                <p className="text-sm font-medium text-muted-foreground">Nenhum alerta neste filtro</p>
                <p className="text-xs text-muted-foreground mt-1">Tente ajustar os filtros ou actualizar os dados</p>
              </div>
            ) : (
              <div className="space-y-3">
                {filteredAlerts.map(alert => (
                  <AlertCard key={alert.id} alert={alert} uid={uid} onUpdate={handleAlertUpdate} />
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── Tab: Baselines ── */}
          <TabsContent value="baselines" className="space-y-4">
            {baselines.length === 0 ? (
              <div className="text-center py-16">
                <Target className="h-14 w-14 mx-auto text-muted-foreground/40 mb-3" />
                <p className="text-sm text-muted-foreground">Nenhum baseline configurado</p>
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {baselines.map(b => {
                  const meta = b.metadata as Record<string, unknown> ?? {};
                  return (
                    <Card key={b.id} className="hover:shadow-md transition-shadow">
                      <CardHeader className="pb-2">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="flex items-center gap-2 mb-1">
                              <span className="flex items-center gap-1 text-xs bg-muted px-2 py-0.5 rounded-full">
                                {ENTITY_ICONS[b.entity_type] ?? <Database className="h-3 w-3" />}
                                {b.entity_type.toUpperCase()}
                              </span>
                              <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
                                {b.metric_name.replace(/_/g, ' ')}
                              </span>
                            </div>
                            <CardTitle className="text-sm">
                              {String(meta.description ?? b.metric_name.replace(/_/g, ' '))}
                            </CardTitle>
                          </div>
                          <Badge variant="outline" className="text-xs">
                            {b.confidence_level}% conf.
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-3 gap-3 mb-3">
                          <div className="text-center bg-muted/40 rounded-lg p-2.5">
                            <div className="text-xs text-muted-foreground mb-0.5">Baseline</div>
                            <div className="text-sm font-bold">
                              {b.baseline_value > 10000 ? formatKz(b.baseline_value)
                               : b.baseline_value > 100 ? b.baseline_value.toFixed(0)
                               : b.baseline_value.toFixed(2)}
                            </div>
                          </div>
                          <div className="text-center bg-muted/40 rounded-lg p-2.5">
                            <div className="text-xs text-muted-foreground mb-0.5">Desvio Padrão</div>
                            <div className="text-sm font-bold">
                              {b.std_deviation > 10000 ? formatKz(b.std_deviation)
                               : b.std_deviation > 100 ? b.std_deviation.toFixed(0)
                               : b.std_deviation.toFixed(2)}
                            </div>
                          </div>
                          <div className="text-center bg-muted/40 rounded-lg p-2.5">
                            <div className="text-xs text-muted-foreground mb-0.5">Amostras</div>
                            <div className="text-sm font-bold">{b.sample_size}</div>
                          </div>
                        </div>
                        {b.min_value !== undefined && b.max_value !== undefined && (
                          <div className="text-xs text-muted-foreground flex items-center gap-3">
                            <span>Min: <strong>{b.min_value > 1000 ? formatKz(b.min_value) : b.min_value.toFixed(0)}</strong></span>
                            <span>Máx: <strong>{b.max_value > 1000 ? formatKz(b.max_value) : b.max_value.toFixed(0)}</strong></span>
                            <span>Unidade: <strong>{String(meta.unit ?? '—')}</strong></span>
                          </div>
                        )}
                        <Progress
                          value={b.confidence_level}
                          className="mt-2 h-1.5"
                        />
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </TabsContent>

          {/* ── Tab: Análise ── */}
          <TabsContent value="analytics" className="space-y-6">
            <div className="grid gap-6 md:grid-cols-2">

              {/* Distribuição de scores */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <BarChart2 className="h-4 w-4 text-primary" />
                    Distribuição de Scores de Anomalia
                  </CardTitle>
                  <CardDescription>Frequência por intervalo de score (0–100)</CardDescription>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={scoreDistribution} margin={{ left: -20 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="range" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip formatter={(v) => [`${v} alertas`, 'Contagem']} />
                      <Bar dataKey="count" radius={[4,4,0,0]}>
                        {scoreDistribution.map((d, i) => (
                          <Cell key={i} fill={
                            d.range === '80-100' ? '#EF4444' :
                            d.range === '60-80' ? '#F97316' :
                            d.range === '40-60' ? '#EAB308' :
                            d.range === '20-40' ? '#3B82F6' : '#10B981'
                          } />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              {/* Resumo de Qualidade do Sistema */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <Shield className="h-4 w-4 text-green-600" />
                    Métricas de Qualidade UEBA
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  {[
                    {
                      label: 'Taxa de Resolução',
                      value: kpis.total > 0 ? Math.round(kpis.resolved / kpis.total * 100) : 0,
                      color: 'bg-green-500',
                      hint: `${kpis.resolved} de ${kpis.total} alertas resolvidos`,
                    },
                    {
                      label: 'Taxa de Falsos Positivos',
                      value: kpis.total > 0 ? Math.round(kpis.falsePosive / kpis.total * 100) : 0,
                      color: 'bg-gray-400',
                      hint: `${kpis.falsePosive} marcados como falso positivo`,
                    },
                    {
                      label: 'Alertas Críticos/Total',
                      value: kpis.total > 0 ? Math.round(kpis.critical / kpis.total * 100) : 0,
                      color: 'bg-red-500',
                      hint: `${kpis.critical} alertas críticos activos`,
                    },
                    {
                      label: 'Em Investigação',
                      value: kpis.total > 0 ? Math.round(kpis.investigating / kpis.total * 100) : 0,
                      color: 'bg-purple-500',
                      hint: `${kpis.investigating} alertas em análise`,
                    },
                  ].map((m, i) => (
                    <div key={i}>
                      <div className="flex items-center justify-between text-sm mb-1">
                        <span>{m.label}</span>
                        <span className="font-bold">{m.value}%</span>
                      </div>
                      <Progress value={m.value} className="h-2" />
                      <div className="text-xs text-muted-foreground mt-0.5">{m.hint}</div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>

            {/* Top 5 alertas por score */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-red-600" />
                  Top 5 Alertas por Score de Anomalia
                </CardTitle>
                <CardDescription>Os alertas com maior probabilidade de anomalia real</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[...alerts].sort((a, b) => b.anomaly_score - a.anomaly_score).slice(0, 5).map((a, idx) => {
                    const cfg = SEV_CFG[a.severity] ?? SEV_CFG.low;
                    return (
                      <div key={a.id} className="flex items-center gap-3">
                        <div className="text-lg font-bold text-muted-foreground w-6 text-center">{idx + 1}</div>
                        <div className={`w-2 h-10 rounded-full flex-shrink-0 ${
                          a.severity === 'critical' ? 'bg-red-500' :
                          a.severity === 'high' ? 'bg-orange-500' :
                          a.severity === 'medium' ? 'bg-yellow-500' : 'bg-blue-500'
                        }`} />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium truncate">{a.title}</div>
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span className={`px-1.5 py-0.5 rounded-full ${cfg.badge}`}>{cfg.label}</span>
                            <span>{a.entity_type.toUpperCase()}</span>
                            <span>{ANOMALY_TYPE_LABELS[a.anomaly_type] ?? a.anomaly_type}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <Progress value={a.anomaly_score} className="w-24 h-2" />
                          <span className={`text-sm font-bold ${cfg.text} w-10 text-right`}>
                            {a.anomaly_score.toFixed(0)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>

            {/* Modelos de detecção utilizados */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Brain className="h-4 w-4 text-purple-600" />
                  Modelos de Detecção Activos
                </CardTitle>
                <CardDescription>Algoritmos de IA em uso no sistema UEBA</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 md:grid-cols-3">
                  {[
                    { name: 'Statistical Z-Score', desc: 'Detecção de outliers estatísticos com desvio padrão', type: 'Transacções', active: true },
                    { name: 'Behavioral Baseline', desc: 'Comparação de comportamento actual vs. histórico', type: 'Utilizadores', active: true },
                    { name: 'Trend Analysis', desc: 'Análise de tendências temporais e padrões sazonais', type: 'Facturas', active: true },
                    { name: 'Peer Comparison', desc: 'Comparação com grupos de pares similares', type: 'Colaboradores', active: true },
                    { name: 'Duplicate Detection', desc: 'Identificação de transacções duplicadas suspeitas', type: 'Pagamentos', active: true },
                    { name: 'Data Exfiltration', desc: 'Monitorização de exportações de dados anómalas', type: 'Sistema', active: true },
                  ].map((m, i) => (
                    <div key={i} className="p-3 border rounded-lg bg-card hover:shadow-sm transition-shadow">
                      <div className="flex items-center gap-2 mb-1.5">
                        <div className="w-2 h-2 rounded-full bg-green-500" />
                        <span className="text-xs font-bold text-green-700">ACTIVO</span>
                        <span className="text-xs text-muted-foreground ml-auto">{m.type}</span>
                      </div>
                      <div className="text-sm font-semibold mb-1">{m.name}</div>
                      <div className="text-xs text-muted-foreground">{m.desc}</div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
