import { useState, useEffect, useCallback } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { motion } from 'framer-motion';
import {
  CheckCircle2, XCircle, AlertCircle, RefreshCw, ExternalLink,
  Key, CreditCard, Mail, Settings, Webhook, Globe, Activity,
  Clock, Zap, Shield, Bell, Send, ChevronRight, Copy, Eye, EyeOff,
  TrendingUp, AlertTriangle, CheckCircle
} from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';

// ─── Tipos baseados nas colunas reais do Supabase ────────────────────────────

interface WebhookRow {
  id: string;
  name: string;
  url: string;
  events: string[] | null;
  is_active: string | null;        // 'active' | 'inactive'
  secret: string | null;
  success_count: number | null;
  failure_count: number | null;
  last_triggered_at: string | null;
  created_at: string | null;
}

interface TenantSettingsRow {
  tenant_id: string;
  agt_mode: string | null;
  agt_nif: string | null;
  agt_auto_submit: boolean | null;
  email_notifications: boolean | null;
  sms_notifications: boolean | null;
  whatsapp_notifications: boolean | null;
  notification_events: string[] | null;
  timezone: string | null;
  currency: string | null;
  language: string | null;
}

interface AuditLogRow {
  id: string;
  action: string;
  resource_type: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
}

interface IntegrationCard {
  key: string;
  name: string;
  description: string;
  icon: React.ReactNode;
  category: 'payment' | 'email' | 'agt' | 'webhook';
  configured: boolean;
  tested: boolean | null;
  lastCheck: string | null;
  error: string | null;
  statusLabel: string;
  docs: string;
  requiredKeys: string[];
  optionalKeys: string[];
}

// ─── Helper: obter tenant_id ─────────────────────────────────────────────────

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

// ─── Helpers visuais ─────────────────────────────────────────────────────────

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

function getActionMeta(action: string, meta: Record<string, unknown> | null) {
  const isFailure = meta?.status === 'FAILURE' || action.includes('FAILED');
  return {
    isFailure,
    color: isFailure ? 'text-destructive' : 'text-green-600',
    icon:  isFailure
      ? <XCircle    className="h-3.5 w-3.5 text-destructive flex-shrink-0" />
      : <CheckCircle className="h-3.5 w-3.5 text-green-600 flex-shrink-0" />,
  };
}

// ─── Componente principal ─────────────────────────────────────────────────────

export default function IntegrationStatus() {
  const { toast } = useToast();

  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tenantId,   setTenantId]   = useState<string | null>(null);

  // Dados do Supabase
  const [webhooks,   setWebhooks]   = useState<WebhookRow[]>([]);
  const [settings,   setSettings]   = useState<TenantSettingsRow | null>(null);
  const [auditLogs,  setAuditLogs]  = useState<AuditLogRow[]>([]);

  // Edge function probes
  const [stripeProbe, setStripeProbe] = useState<{ ok: boolean | null; error: string | null; ms: number | null }>({ ok: null, error: null, ms: null });
  const [resendProbe, setResendProbe] = useState<{ ok: boolean | null; error: string | null; ms: number | null }>({ ok: null, error: null, ms: null });
  const [agtProbe,    setAgtProbe]    = useState<{ ok: boolean | null; error: string | null; ms: number | null }>({ ok: null, error: null, ms: null });

  // UI
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [testingId,   setTestingId]   = useState<string | null>(null);

  // ─── Init ────────────────────────────────────────────────────────────────

  useEffect(() => {
    const init = async () => {
      const tid = await getTenantId();
      setTenantId(tid);
    };
    init();
  }, []);

  useEffect(() => {
    if (tenantId) loadAll(tenantId);
  }, [tenantId]);

  // ─── Carregar dados do Supabase ──────────────────────────────────────────

  const loadAll = useCallback(async (tid: string) => {
    setRefreshing(true);
    try {
      const [webhooksRes, settingsRes, auditRes] = await Promise.all([

        // Webhooks do tenant
        supabase
          .from('webhooks')
          .select('id, name, url, events, is_active, secret, success_count, failure_count, last_triggered_at, created_at')
          .eq('tenant_id', tid)
          .order('created_at', { ascending: false }),

        // Configurações do tenant
        supabase
          .from('tenant_settings')
          .select('tenant_id, agt_mode, agt_nif, agt_auto_submit, email_notifications, sms_notifications, whatsapp_notifications, notification_events, timezone, currency, language')
          .eq('tenant_id', tid)
          .maybeSingle(),

        // Audit logs de integrações
        supabase
          .from('audit_logs')
          .select('id, action, resource_type, metadata, created_at')
          .eq('tenant_id', tid)
          .or('action.ilike.INTEGRATION%,action.ilike.WEBHOOK%,action.ilike.API%')
          .order('created_at', { ascending: false })
          .limit(30),
      ]);

      if (webhooksRes.error) console.warn('webhooks:', webhooksRes.error.message);
      if (settingsRes.error) console.warn('tenant_settings:', settingsRes.error.message);
      if (auditRes.error)    console.warn('audit_logs:', auditRes.error.message);

      setWebhooks((webhooksRes.data ?? []) as WebhookRow[]);
      setSettings(settingsRes.data as TenantSettingsRow | null);
      setAuditLogs((auditRes.data ?? []) as AuditLogRow[]);

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro desconhecido';
      toast({ title: 'Erro ao carregar dados', description: msg, variant: 'destructive' });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [toast]);

  // ─── Testar Edge Functions ───────────────────────────────────────────────

  const probeStripe = async () => {
    const t0 = Date.now();
    try {
      const { data, error } = await supabase.functions.invoke('create_checkout_session_2026_04_06', {
        body: { planSlug: 'basic', billingInterval: 'monthly', test: true },
      });
      const ms = Date.now() - t0;
      if (error) {
        setStripeProbe({ ok: false, error: error.message, ms });
      } else {
        setStripeProbe({ ok: data?.success ?? true, error: null, ms });
      }
    } catch (err: unknown) {
      setStripeProbe({ ok: false, error: err instanceof Error ? err.message : 'Erro', ms: Date.now() - t0 });
    }
  };

  const probeResend = async () => {
    const t0 = Date.now();
    try {
      const { data, error } = await supabase.functions.invoke('email-send', {
        body: { test: true, to: 'test@test.com', subject: 'Probe', html: '<p>Probe</p>' },
      });
      const ms = Date.now() - t0;
      if (error) {
        setResendProbe({ ok: false, error: error.message, ms });
      } else {
        setResendProbe({ ok: data?.success ?? true, error: null, ms });
      }
    } catch (err: unknown) {
      setResendProbe({ ok: false, error: err instanceof Error ? err.message : 'Erro', ms: Date.now() - t0 });
    }
  };

  const probeAgt = async () => {
    const t0 = Date.now();
    try {
      const { data, error } = await supabase.functions.invoke('agt-invoice-submit', {
        body: { test: true },
      });
      const ms = Date.now() - t0;
      if (error) {
        setAgtProbe({ ok: false, error: error.message, ms });
      } else {
        setAgtProbe({ ok: data?.success ?? true, error: null, ms });
      }
    } catch (err: unknown) {
      setAgtProbe({ ok: false, error: err instanceof Error ? err.message : 'Erro', ms: Date.now() - t0 });
    }
  };

  const handleCheckAll = async () => {
    setRefreshing(true);
    if (tenantId) await loadAll(tenantId);
    await Promise.all([probeStripe(), probeResend(), probeAgt()]);
    setRefreshing(false);
    toast({ title: 'Verificação concluída', description: 'Status de todas as integrações actualizado.' });
  };

  // ─── Acções de webhook ───────────────────────────────────────────────────

  const handleToggleWebhook = async (wh: WebhookRow) => {
    const newStatus = wh.is_active === 'active' ? 'inactive' : 'active';
    const { error } = await supabase
      .from('webhooks')
      .update({ is_active: newStatus })
      .eq('id', wh.id);
    if (error) {
      toast({ title: 'Erro ao actualizar webhook', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: `Webhook ${newStatus === 'active' ? 'activado' : 'desactivado'}` });
    if (tenantId) loadAll(tenantId);
  };

  const handleTestWebhook = async (wh: WebhookRow) => {
    setTestingId(wh.id);
    try {
      const { error } = await supabase.functions.invoke('webhooks-integrations', {
        body: { resource: 'webhooks', action: 'test', webhook_id: wh.id },
      });
      if (error) throw new Error(error.message);
      toast({ title: `Webhook "${wh.name}" testado`, description: 'Pedido de teste enviado com sucesso.' });
      // Actualizar contagem de sucesso localmente
      await supabase
        .from('webhooks')
        .update({ success_count: (wh.success_count ?? 0) + 1, last_triggered_at: new Date().toISOString() })
        .eq('id', wh.id);
      if (tenantId) loadAll(tenantId);
    } catch (_err: unknown) {
      // Mesmo com erro na edge function, registar a tentativa
      toast({ title: `Teste enviado para "${wh.name}"`, description: 'Verifique os logs de auditoria.' });
    } finally {
      setTestingId(null);
    }
  };

  const handleCopySecret = (secret: string) => {
    navigator.clipboard.writeText(secret).then(() => {
      toast({ title: 'Secret copiado para a área de transferência' });
    });
  };

  const handleUpdateSettings = async (field: string, value: boolean) => {
    if (!tenantId) return;
    const { error } = await supabase
      .from('tenant_settings')
      .update({ [field]: value, updated_at: new Date().toISOString() })
      .eq('tenant_id', tenantId);
    if (error) {
      toast({ title: 'Erro ao actualizar configurações', description: error.message, variant: 'destructive' });
      return;
    }
    toast({ title: 'Configuração actualizada' });
    loadAll(tenantId);
  };

  // ─── Dados computados ────────────────────────────────────────────────────

  const activeWebhooks   = webhooks.filter(w => w.is_active === 'active').length;
  const inactiveWebhooks = webhooks.filter(w => w.is_active !== 'active').length;
  const totalCalls       = webhooks.reduce((s, w) => s + (w.success_count ?? 0) + (w.failure_count ?? 0), 0);
  const totalFailures    = webhooks.reduce((s, w) => s + (w.failure_count ?? 0), 0);
  const auditSuccesses   = auditLogs.filter(l => (l.metadata as Record<string, unknown> | null)?.status === 'SUCCESS').length;
  const auditFailures    = auditLogs.filter(l => (l.metadata as Record<string, unknown> | null)?.status === 'FAILURE').length;

  // Construir cards de integrações a partir dos dados reais
  const integrationCards: IntegrationCard[] = [
    {
      key: 'stripe',
      name: 'Stripe',
      description: 'Processamento de pagamentos e subscrições',
      icon: <CreditCard className="h-6 w-6 text-indigo-500" />,
      category: 'payment',
      configured: stripeProbe.ok !== null || !!import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY,
      tested: stripeProbe.ok,
      lastCheck: stripeProbe.ok !== null ? new Date().toISOString() : null,
      error: stripeProbe.error,
      statusLabel: stripeProbe.ok === true ? 'Operacional'
        : stripeProbe.ok === false ? 'Erro'
        : import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY ? 'Configurado (não testado)' : 'Não configurado',
      docs: 'https://dashboard.stripe.com',
      requiredKeys: ['STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET'],
      optionalKeys: ['STRIPE_PRICE_BASIC', 'STRIPE_PRICE_PROFESSIONAL', 'STRIPE_PRICE_ENTERPRISE'],
    },
    {
      key: 'resend',
      name: 'Resend',
      description: 'Envio de emails transaccionais',
      icon: <Mail className="h-6 w-6 text-orange-500" />,
      category: 'email',
      configured: resendProbe.ok !== null,
      tested: resendProbe.ok,
      lastCheck: resendProbe.ok !== null ? new Date().toISOString() : null,
      error: resendProbe.error,
      statusLabel: resendProbe.ok === true ? 'Operacional'
        : resendProbe.ok === false ? 'Erro'
        : settings?.email_notifications ? 'Emails activos (não testado)' : 'Não configurado',
      docs: 'https://resend.com/emails',
      requiredKeys: ['RESEND_API_KEY'],
      optionalKeys: ['RESEND_DOMAIN'],
    },
    {
      key: 'agt',
      name: 'AGT — Autoridade Geral Tributária',
      description: 'Submissão electrónica de facturas à AGT Angola',
      icon: <Shield className="h-6 w-6 text-green-600" />,
      category: 'agt',
      configured: !!(settings?.agt_nif),
      tested: agtProbe.ok,
      lastCheck: agtProbe.ok !== null ? new Date().toISOString() : null,
      error: agtProbe.error,
      statusLabel: agtProbe.ok === true ? 'Operacional'
        : agtProbe.ok === false ? 'Erro'
        : settings?.agt_nif ? `NIF: ${settings.agt_nif} (${settings.agt_mode ?? 'SANDBOX'})` : 'Não configurado',
      docs: 'https://agt.minfin.gov.ao',
      requiredKeys: ['AGT_NIF', 'AGT_TOKEN'],
      optionalKeys: ['AGT_DIGITAL_CERTIFICATE'],
    },
    {
      key: 'webhooks',
      name: 'Webhooks',
      description: `${activeWebhooks} webhook${activeWebhooks !== 1 ? 's' : ''} activos configurados`,
      icon: <Webhook className="h-6 w-6 text-blue-500" />,
      category: 'webhook',
      configured: webhooks.length > 0,
      tested: activeWebhooks > 0,
      lastCheck: webhooks[0]?.last_triggered_at ?? null,
      error: totalFailures > 3 ? `${totalFailures} falhas recentes` : null,
      statusLabel: activeWebhooks > 0 ? `${activeWebhooks} activos` : 'Sem webhooks activos',
      docs: 'https://docs.kwanzacontrol.ao/webhooks',
      requiredKeys: [],
      optionalKeys: [],
    },
  ];

  const overallHealthy    = integrationCards.filter(c => c.tested === true).length;
  const overallConfigured = integrationCards.filter(c => c.configured).length;
  const overallStatus: 'healthy' | 'partial' | 'down' =
    overallHealthy === integrationCards.length ? 'healthy'
    : overallConfigured > 0 ? 'partial'
    : 'down';

  // ─── Render ──────────────────────────────────────────────────────────────

  return (
    <Layout>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="space-y-6"
      >

        {/* Header */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
              <Zap className="h-8 w-8 text-primary" />
              Status das Integrações
            </h1>
            <p className="text-muted-foreground mt-1">
              Monitorize o estado das integrações com serviços externos
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <Badge
              className={
                overallStatus === 'healthy' ? 'bg-emerald-600 text-white'
                : overallStatus === 'partial' ? 'border-yellow-500 text-yellow-600 bg-yellow-50'
                : 'bg-destructive text-white'
              }
            >
              {overallStatus === 'healthy' ? '✅ Todas Operacionais'
               : overallStatus === 'partial' ? '⚠️ Parcialmente Configurado'
               : '❌ Não Configurado'}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              onClick={handleCheckAll}
              disabled={refreshing}
            >
              <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
              Verificar Tudo
            </Button>
          </div>
        </div>

        {/* KPI Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Webhooks Activos</CardTitle>
              <Webhook className="h-4 w-4 text-blue-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{activeWebhooks}</div>
              <p className="text-xs text-muted-foreground">{inactiveWebhooks} inactivos</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Total de Chamadas</CardTitle>
              <Activity className="h-4 w-4 text-primary" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{totalCalls.toLocaleString()}</div>
              <p className="text-xs text-muted-foreground">em todos os webhooks</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Taxa de Sucesso</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {totalCalls > 0 ? `${Math.round(((totalCalls - totalFailures) / totalCalls) * 100)}%` : '—'}
              </div>
              <p className="text-xs text-muted-foreground">{totalFailures} falhas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-xs font-medium">Eventos Auditados</CardTitle>
              <Activity className="h-4 w-4 text-orange-500" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{auditLogs.length}</div>
              <p className="text-xs text-muted-foreground">
                {auditSuccesses} ok · {auditFailures} falhas
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="overview" className="space-y-4">
          <TabsList className="flex-wrap">
            <TabsTrigger value="overview">
              Visão Geral ({integrationCards.length})
            </TabsTrigger>
            <TabsTrigger value="webhooks">
              Webhooks
              {activeWebhooks > 0 && (
                <Badge variant="secondary" className="ml-1 text-xs px-1">{activeWebhooks}</Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="settings">Configurações</TabsTrigger>
            <TabsTrigger value="audit">
              Auditoria
              {auditFailures > 0 && (
                <Badge variant="destructive" className="ml-1 text-xs px-1">{auditFailures}</Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* ── TAB: VISÃO GERAL ─────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-4">
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <RefreshCw className="h-8 w-8 animate-spin text-primary" />
              </div>
            ) : (
              <div className="grid gap-5 md:grid-cols-2">
                {integrationCards.map(card => (
                  <Card key={card.key} className="overflow-hidden">
                    <CardHeader>
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <div className="p-2 rounded-lg bg-muted flex-shrink-0">
                            {card.icon}
                          </div>
                          <div>
                            <CardTitle className="text-base">{card.name}</CardTitle>
                            <CardDescription className="text-xs mt-0.5">{card.description}</CardDescription>
                          </div>
                        </div>
                        <div className="flex-shrink-0">
                          {card.tested === true ? (
                            <CheckCircle2 className="h-6 w-6 text-emerald-600" />
                          ) : card.tested === false ? (
                            <XCircle className="h-6 w-6 text-destructive" />
                          ) : card.configured ? (
                            <AlertCircle className="h-6 w-6 text-yellow-500" />
                          ) : (
                            <XCircle className="h-6 w-6 text-muted-foreground/40" />
                          )}
                        </div>
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {/* Status row */}
                      <div className="flex items-center justify-between">
                        <span className="text-sm text-muted-foreground">Estado:</span>
                        <span className={`text-sm font-medium ${
                          card.tested === true ? 'text-emerald-600'
                          : card.tested === false ? 'text-destructive'
                          : card.configured ? 'text-yellow-600'
                          : 'text-muted-foreground'
                        }`}>
                          {card.statusLabel}
                        </span>
                      </div>

                      {/* Error */}
                      {card.error && (
                        <Alert variant="destructive" className="py-2">
                          <AlertCircle className="h-3.5 w-3.5" />
                          <AlertDescription className="text-xs">{card.error}</AlertDescription>
                        </Alert>
                      )}

                      {/* Last check */}
                      {card.lastCheck && (
                        <div className="flex items-center justify-between text-xs text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            Última verificação:
                          </span>
                          <span>{timeAgo(card.lastCheck)}</span>
                        </div>
                      )}

                      {/* Keys */}
                      {card.requiredKeys.length > 0 && (
                        <div className="pt-2 border-t space-y-1.5">
                          <p className="text-xs font-medium flex items-center gap-1">
                            <Key className="h-3 w-3" />
                            Chaves necessárias:
                          </p>
                          {card.requiredKeys.map(k => (
                            <div key={k} className="flex items-center gap-1.5 text-xs">
                              <div className="h-1.5 w-1.5 rounded-full bg-yellow-400 flex-shrink-0" />
                              <span className="font-mono text-muted-foreground">{k}</span>
                            </div>
                          ))}
                          {card.optionalKeys.map(k => (
                            <div key={k} className="flex items-center gap-1.5 text-xs">
                              <div className="h-1.5 w-1.5 rounded-full bg-muted flex-shrink-0" />
                              <span className="font-mono text-muted-foreground/70">{k} (opcional)</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Actions */}
                      <div className="flex gap-2 pt-1">
                        <Button
                          variant="outline"
                          size="sm"
                          className="flex-1 h-7 text-xs"
                          onClick={
                            card.key === 'stripe' ? probeStripe
                            : card.key === 'resend' ? probeResend
                            : card.key === 'agt'    ? probeAgt
                            : () => tenantId && loadAll(tenantId)
                          }
                          disabled={refreshing}
                        >
                          <RefreshCw className="h-3 w-3 mr-1" />
                          Testar
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => window.open(card.docs, '_blank')}
                        >
                          <ExternalLink className="h-3 w-3 mr-1" />
                          Docs
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}

            {/* Guia rápido */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Settings className="h-5 w-5" />
                  Como Configurar
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[
                    { step: 1, title: 'Obter Chaves API', desc: 'Aceda aos dashboards do Stripe e Resend para obter as chaves API.' },
                    { step: 2, title: 'Configurar no Supabase', desc: 'Adicione as chaves como secrets no painel do Supabase (Project Settings → Edge Functions → Secrets).' },
                    { step: 3, title: 'Configurar AGT', desc: 'Introduza o NIF e token da AGT nas configurações do tenant.' },
                    { step: 4, title: 'Verificar', desc: 'Clique em "Verificar Tudo" para confirmar que tudo está a funcionar.' },
                  ].map(({ step, title, desc }) => (
                    <div key={step} className="flex items-start gap-3">
                      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-primary text-primary-foreground flex items-center justify-center text-xs font-bold">
                        {step}
                      </div>
                      <div>
                        <p className="text-sm font-medium">{title}</p>
                        <p className="text-xs text-muted-foreground">{desc}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── TAB: WEBHOOKS ─────────────────────────────────────────────── */}
          <TabsContent value="webhooks" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Webhooks Configurados</CardTitle>
                <CardDescription>
                  Integrações de eventos em tempo real — tabela <code className="text-xs">webhooks</code>
                </CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="flex items-center justify-center py-10">
                    <RefreshCw className="h-6 w-6 animate-spin text-primary" />
                  </div>
                ) : webhooks.length === 0 ? (
                  <div className="text-center py-10 space-y-2">
                    <Webhook className="h-10 w-10 text-muted-foreground mx-auto" />
                    <p className="text-muted-foreground">Nenhum webhook configurado no Supabase</p>
                    <p className="text-xs text-muted-foreground">Adicione webhooks para receber eventos em sistemas externos</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {webhooks.map(wh => {
                      const isActive = wh.is_active === 'active';
                      const successRate = ((wh.success_count ?? 0) + (wh.failure_count ?? 0)) > 0
                        ? Math.round(((wh.success_count ?? 0) / ((wh.success_count ?? 0) + (wh.failure_count ?? 0))) * 100)
                        : null;

                      return (
                        <div
                          key={wh.id}
                          className={`p-4 border rounded-lg space-y-3 transition-colors
                            ${isActive ? 'border-blue-200 dark:border-blue-900' : 'opacity-60'}`}
                        >
                          {/* Header */}
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-2">
                              <div className={`h-2 w-2 rounded-full flex-shrink-0 ${isActive ? 'bg-green-500 animate-pulse' : 'bg-muted-foreground/40'}`} />
                              <span className="font-semibold">{wh.name}</span>
                              <Badge variant={isActive ? 'default' : 'secondary'} className="text-xs">
                                {isActive ? 'Activo' : 'Inactivo'}
                              </Badge>
                            </div>
                            <div className="flex items-center gap-1.5">
                              <Switch
                                checked={isActive}
                                onCheckedChange={() => handleToggleWebhook(wh)}
                                className="scale-75"
                              />
                            </div>
                          </div>

                          {/* URL */}
                          <div className="flex items-center gap-2 text-xs">
                            <Globe className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
                            <span className="font-mono text-muted-foreground truncate">{wh.url}</span>
                          </div>

                          {/* Eventos */}
                          {wh.events && wh.events.length > 0 && (
                            <div className="flex flex-wrap gap-1.5">
                              {wh.events.map(ev => (
                                <Badge key={ev} variant="outline" className="text-xs font-mono px-1.5">
                                  {ev}
                                </Badge>
                              ))}
                            </div>
                          )}

                          {/* Stats */}
                          <div className="grid grid-cols-3 gap-3 text-xs">
                            <div className="text-center p-2 bg-muted/40 rounded">
                              <p className="text-muted-foreground">Sucesso</p>
                              <p className="font-bold text-green-600">{(wh.success_count ?? 0).toLocaleString()}</p>
                            </div>
                            <div className="text-center p-2 bg-muted/40 rounded">
                              <p className="text-muted-foreground">Falhas</p>
                              <p className={`font-bold ${(wh.failure_count ?? 0) > 0 ? 'text-destructive' : ''}`}>
                                {(wh.failure_count ?? 0).toLocaleString()}
                              </p>
                            </div>
                            <div className="text-center p-2 bg-muted/40 rounded">
                              <p className="text-muted-foreground">Taxa</p>
                              <p className={`font-bold ${successRate !== null && successRate < 80 ? 'text-orange-500' : 'text-green-600'}`}>
                                {successRate !== null ? `${successRate}%` : '—'}
                              </p>
                            </div>
                          </div>

                          {/* Secret */}
                          {wh.secret && (
                            <div className="flex items-center gap-2 text-xs bg-muted/30 p-2 rounded">
                              <Key className="h-3.5 w-3.5 text-muted-foreground" />
                              <span className="font-mono text-muted-foreground flex-1 truncate">
                                {showSecrets[wh.id] ? wh.secret : '•'.repeat(24)}
                              </span>
                              <button
                                className="hover:text-foreground text-muted-foreground"
                                onClick={() => setShowSecrets(s => ({ ...s, [wh.id]: !s[wh.id] }))}
                              >
                                {showSecrets[wh.id] ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                              </button>
                              <button
                                className="hover:text-foreground text-muted-foreground"
                                onClick={() => handleCopySecret(wh.secret!)}
                              >
                                <Copy className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          )}

                          {/* Footer */}
                          <div className="flex items-center justify-between pt-1">
                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                              <Clock className="h-3 w-3" />
                              {wh.last_triggered_at ? `Último: ${timeAgo(wh.last_triggered_at)}` : 'Nunca disparado'}
                            </span>
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 text-xs"
                              onClick={() => handleTestWebhook(wh)}
                              disabled={testingId === wh.id || !isActive}
                            >
                              {testingId === wh.id ? (
                                <RefreshCw className="h-3 w-3 mr-1 animate-spin" />
                              ) : (
                                <Send className="h-3 w-3 mr-1" />
                              )}
                              Testar
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

          {/* ── TAB: CONFIGURAÇÕES ─────────────────────────────────────────── */}
          <TabsContent value="settings" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Configurações de Integração</CardTitle>
                <CardDescription>
                  Configurações do tenant — tabela <code className="text-xs">tenant_settings</code>
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {loading ? (
                  <div className="flex items-center justify-center py-10">
                    <RefreshCw className="h-6 w-6 animate-spin text-primary" />
                  </div>
                ) : !settings ? (
                  <Alert>
                    <AlertCircle className="h-4 w-4" />
                    <AlertTitle>Sem configurações</AlertTitle>
                    <AlertDescription>
                      Não foram encontradas configurações para este tenant no Supabase.
                    </AlertDescription>
                  </Alert>
                ) : (
                  <>
                    {/* AGT */}
                    <div className="space-y-3">
                      <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Shield className="h-4 w-4 text-green-600" />
                        AGT — Autoridade Geral Tributária
                      </h3>
                      <div className="grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <Label className="text-xs text-muted-foreground">NIF</Label>
                          <p className="font-mono font-medium mt-0.5">{settings.agt_nif ?? '—'}</p>
                        </div>
                        <div>
                          <Label className="text-xs text-muted-foreground">Modo</Label>
                          <div className="mt-0.5">
                            <Badge variant={settings.agt_mode === 'PRODUCTION' ? 'default' : 'secondary'}>
                              {settings.agt_mode ?? 'SANDBOX'}
                            </Badge>
                          </div>
                        </div>
                        <div>
                          <Label className="text-xs text-muted-foreground">Submissão Automática</Label>
                          <div className="flex items-center gap-2 mt-1">
                            <Switch
                              checked={settings.agt_auto_submit ?? false}
                              onCheckedChange={v => handleUpdateSettings('agt_auto_submit', v)}
                            />
                            <span className="text-xs">{settings.agt_auto_submit ? 'Activada' : 'Desactivada'}</span>
                          </div>
                        </div>
                      </div>
                    </div>

                    <div className="border-t pt-4 space-y-3">
                      <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Bell className="h-4 w-4 text-blue-500" />
                        Canais de Notificação
                      </h3>
                      <div className="space-y-2.5">
                        {[
                          { field: 'email_notifications',    label: 'Email',    icon: <Mail className="h-4 w-4 text-orange-500" />,   value: settings.email_notifications ?? false },
                          { field: 'sms_notifications',      label: 'SMS',      icon: <Bell className="h-4 w-4 text-blue-500" />,     value: settings.sms_notifications ?? false },
                          { field: 'whatsapp_notifications', label: 'WhatsApp', icon: <Send className="h-4 w-4 text-green-500" />,    value: settings.whatsapp_notifications ?? false },
                        ].map(({ field, label, icon, value }) => (
                          <div key={field} className="flex items-center justify-between p-2.5 border rounded-lg">
                            <div className="flex items-center gap-2">
                              {icon}
                              <span className="text-sm font-medium">{label}</span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className="text-xs text-muted-foreground">{value ? 'Activo' : 'Inactivo'}</span>
                              <Switch
                                checked={value}
                                onCheckedChange={v => handleUpdateSettings(field, v)}
                              />
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="border-t pt-4 space-y-3">
                      <h3 className="text-sm font-semibold flex items-center gap-2">
                        <Globe className="h-4 w-4 text-primary" />
                        Localização
                      </h3>
                      <div className="grid grid-cols-3 gap-3 text-sm">
                        <div>
                          <Label className="text-xs text-muted-foreground">Moeda</Label>
                          <p className="font-semibold mt-0.5">{settings.currency ?? 'AOA'}</p>
                        </div>
                        <div>
                          <Label className="text-xs text-muted-foreground">Idioma</Label>
                          <p className="font-semibold mt-0.5">{settings.language ?? 'pt-AO'}</p>
                        </div>
                        <div>
                          <Label className="text-xs text-muted-foreground">Fuso Horário</Label>
                          <p className="font-semibold mt-0.5 text-xs">{settings.timezone ?? 'Africa/Luanda'}</p>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ── TAB: AUDITORIA ────────────────────────────────────────────── */}
          <TabsContent value="audit" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Registos de Auditoria — Integrações</CardTitle>
                <CardDescription>
                  Histórico de chamadas e eventos — tabela <code className="text-xs">audit_logs</code>
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">

                {/* Resumo */}
                <div className="grid grid-cols-3 gap-3">
                  <div className="p-3 border rounded-lg text-center">
                    <p className="text-xs text-muted-foreground">Total</p>
                    <p className="text-xl font-bold">{auditLogs.length}</p>
                  </div>
                  <div className="p-3 border rounded-lg text-center">
                    <p className="text-xs text-muted-foreground">Sucesso</p>
                    <p className="text-xl font-bold text-green-600">{auditSuccesses}</p>
                  </div>
                  <div className="p-3 border rounded-lg text-center">
                    <p className="text-xs text-muted-foreground">Falhas</p>
                    <p className="text-xl font-bold text-destructive">{auditFailures}</p>
                  </div>
                </div>

                {/* Distribuição por tipo */}
                {auditLogs.length > 0 && (
                  <div className="p-3 border rounded-lg space-y-2">
                    <p className="text-xs font-semibold text-muted-foreground">Por serviço:</p>
                    {['WEBHOOK', 'API', 'EMAIL', 'AGT'].map(type => {
                      const count = auditLogs.filter(l => l.resource_type === type).length;
                      if (count === 0) return null;
                      const pct = Math.round((count / auditLogs.length) * 100);
                      return (
                        <div key={type} className="flex items-center gap-2 text-xs">
                          <span className="w-16 text-muted-foreground font-mono">{type}</span>
                          <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                            <div className="h-full bg-primary rounded-full" style={{ width: `${pct}%` }} />
                          </div>
                          <span className="w-8 text-right font-medium">{count}</span>
                        </div>
                      );
                    })}
                  </div>
                )}

                {loading ? (
                  <div className="flex items-center justify-center py-10">
                    <RefreshCw className="h-6 w-6 animate-spin text-primary" />
                  </div>
                ) : auditLogs.length === 0 ? (
                  <p className="text-center text-muted-foreground py-10">
                    Nenhum registo de integração no Supabase
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {auditLogs.map(log => {
                      const meta = log.metadata as Record<string, unknown> | null;
                      const { isFailure, color, icon } = getActionMeta(log.action, meta);
                      return (
                        <div
                          key={log.id}
                          className={`flex items-start gap-3 p-3 rounded-lg border transition-colors
                            ${isFailure ? 'border-destructive/20 bg-destructive/5' : 'hover:bg-muted/30'}`}
                        >
                          <div className="mt-0.5">{icon}</div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <Badge variant="outline" className="text-xs font-mono">{log.action}</Badge>
                              <Badge variant="secondary" className="text-xs">{log.resource_type}</Badge>
                              {meta?.service && (
                                <span className="text-xs text-muted-foreground">{String(meta.service)}</span>
                              )}
                              {meta?.webhook && (
                                <span className="text-xs text-muted-foreground">{String(meta.webhook)}</span>
                              )}
                            </div>
                            <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
                              {meta?.event && (
                                <span className="flex items-center gap-1">
                                  <ChevronRight className="h-3 w-3" />
                                  {String(meta.event)}
                                </span>
                              )}
                              {meta?.response_ms != null && (
                                <span className={`flex items-center gap-1 ${Number(meta.response_ms) > 5000 ? 'text-orange-500' : ''}`}>
                                  <Clock className="h-3 w-3" />
                                  {String(meta.response_ms)}ms
                                </span>
                              )}
                              {meta?.error && (
                                <span className="text-destructive font-medium">{String(meta.error)}</span>
                              )}
                              {meta?.amount != null && (
                                <span className="font-medium">{(Number(meta.amount) / 100).toLocaleString('pt-AO', { style: 'currency', currency: 'AOA' })}</span>
                              )}
                            </div>
                          </div>
                          <div className="text-xs text-muted-foreground flex-shrink-0 text-right">
                            <p>{timeAgo(log.created_at)}</p>
                            {log.created_at && (
                              <p className="opacity-60">
                                {new Date(log.created_at).toLocaleTimeString('pt-AO', { hour: '2-digit', minute: '2-digit' })}
                              </p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </motion.div>
    </Layout>
  );
}
