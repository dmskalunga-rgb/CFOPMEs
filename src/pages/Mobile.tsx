// Mobile.tsx — KWANZACONTROL Mobile App Page — 100% Supabase, sem dados simulados
import { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  Smartphone, Download, Star, Shield, Zap, Users, BarChart3, FileText,
  Wallet, CheckCircle, Apple, Play, QrCode, Bell, Lock, Cloud,
  Fingerprint, Globe, RefreshCw, Loader2, AlertCircle, Wifi, WifiOff,
  CheckCircle2, XCircle, Clock, Tablet, Monitor, Settings,
  TrendingUp, TrendingDown, Package, ChevronRight, ToggleLeft, ToggleRight
} from 'lucide-react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { springPresets, staggerContainer, staggerItem } from '@/lib/motion';
import { supabase } from '@/integrations/supabase/client';
import { toast } from 'sonner';

// ── Tipos ─────────────────────────────────────────────────────────────────────
interface MobileDevice {
  id: string;
  device_name: string;
  platform: 'ios' | 'android' | 'web';
  app_version: string;
  is_active: boolean;
  last_seen_at: string;
  registered_at: string;
}

interface PushNotification {
  id: string;
  title: string;
  body: string;
  type: string;
  is_read: boolean;
  sent_at: string;
}

interface MobileSettings {
  mobile_enabled: boolean;
  mobile_notifications: boolean;
  mobile_biometric: boolean;
  mobile_offline_sync: boolean;
}

interface TenantKPIs {
  total_invoices: number;
  pending_invoices: number;
  overdue_invoices: number;
  total_revenue_month: number;
  total_transactions_month: number;
  active_employees: number;
  active_customers: number;
  currency: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function getUserContext(): Promise<{ userId: string; tenantId: string } | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;
  const { data: profile } = await supabase
    .from('users')
    .select('tenant_id')
    .eq('id', user.id)
    .maybeSingle();
  return profile?.tenant_id
    ? { userId: user.id, tenantId: profile.tenant_id as string }
    : null;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);
  if (mins < 1) return 'agora';
  if (mins < 60) return `${mins}m atrás`;
  if (hours < 24) return `${hours}h atrás`;
  return `${days}d atrás`;
}

function fmtAOA(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M Kz`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}K Kz`;
  return `${value.toLocaleString()} Kz`;
}

// ── Ícone por tipo de notificação ─────────────────────────────────────────────
const notifIcon: Record<string, React.ReactNode> = {
  payment:  <Wallet className="h-4 w-4 text-emerald-500" />,
  invoice:  <FileText className="h-4 w-4 text-blue-500" />,
  warning:  <AlertCircle className="h-4 w-4 text-yellow-500" />,
  hr:       <Users className="h-4 w-4 text-purple-500" />,
  info:     <Bell className="h-4 w-4 text-muted-foreground" />,
  error:    <XCircle className="h-4 w-4 text-destructive" />,
  success:  <CheckCircle2 className="h-4 w-4 text-emerald-500" />,
  system:   <Settings className="h-4 w-4 text-muted-foreground" />,
};

const notifBg: Record<string, string> = {
  payment: 'bg-emerald-50 dark:bg-emerald-950/20',
  invoice: 'bg-blue-50 dark:bg-blue-950/20',
  warning: 'bg-yellow-50 dark:bg-yellow-950/20',
  hr:      'bg-purple-50 dark:bg-purple-950/20',
  error:   'bg-red-50 dark:bg-red-950/20',
  success: 'bg-emerald-50 dark:bg-emerald-950/20',
  info:    'bg-muted/30',
  system:  'bg-muted/30',
};

// ── Ícone de plataforma ───────────────────────────────────────────────────────
function PlatformIcon({ platform }: { platform: string }) {
  if (platform === 'ios') return <Apple className="h-5 w-5" />;
  if (platform === 'android') return <Smartphone className="h-5 w-5" />;
  return <Monitor className="h-5 w-5" />;
}

// ── Componente principal ──────────────────────────────────────────────────────
export default function Mobile() {
  const [activeTab, setActiveTab] = useState('overview');
  const [showQRCode, setShowQRCode] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Dados Supabase
  const [kpis, setKpis] = useState<TenantKPIs | null>(null);
  const [devices, setDevices] = useState<MobileDevice[]>([]);
  const [notifications, setNotifications] = useState<PushNotification[]>([]);
  const [mobileSettings, setMobileSettings] = useState<MobileSettings>({
    mobile_enabled: true,
    mobile_notifications: true,
    mobile_biometric: true,
    mobile_offline_sync: true,
  });
  const [savingSettings, setSavingSettings] = useState(false);
  const [togglingDevice, setTogglingDevice] = useState<string | null>(null);

  // ── Carregar dados do Supabase ──────────────────────────────────────────────
  const loadData = useCallback(async () => {
    try {
      setError(null);
      setRefreshing(true);

      const ctx = await getUserContext();
      if (!ctx) {
        setError('Não autenticado. Faça login para ver os dados.');
        return;
      }

      const { userId, tenantId } = ctx;

      // Carregar em paralelo
      const [
        invoicesRes,
        transactionsRes,
        employeesRes,
        customersRes,
        settingsRes,
        devicesRes,
        notifRes,
      ] = await Promise.all([
        // KPIs de Faturas
        supabase
          .from('invoices')
          .select('status, total, currency')
          .eq('tenant_id', tenantId),

        // Transacções do mês corrente
        supabase
          .from('transactions')
          .select('amount, type, transaction_date')
          .eq('tenant_id', tenantId)
          .gte('transaction_date', new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().split('T')[0]),

        // Funcionários activos
        supabase
          .from('employees')
          .select('id', { count: 'exact', head: true })
          .eq('tenant_id', tenantId)
          .eq('status', 'ACTIVE'),

        // Clientes activos
        supabase
          .from('customers')
          .select('id', { count: 'exact', head: true })
          .eq('tenant_id', tenantId)
          .eq('is_active', true),

        // Configurações do tenant
        supabase
          .from('tenant_settings')
          .select('mobile_enabled, mobile_notifications, mobile_biometric, mobile_offline_sync, currency')
          .eq('tenant_id', tenantId)
          .maybeSingle(),

        // Dispositivos móveis
        supabase
          .from('mobile_devices')
          .select('id, device_name, platform, app_version, is_active, last_seen_at, registered_at')
          .eq('user_id', userId)
          .order('last_seen_at', { ascending: false }),

        // Notificações push
        supabase
          .from('push_notifications')
          .select('id, title, body, type, is_read, sent_at')
          .eq('user_id', userId)
          .order('sent_at', { ascending: false })
          .limit(20),
      ]);

      // Processar KPIs de facturas
      const invoices = invoicesRes.data ?? [];
      const pendingInvoices = invoices.filter(i => i.status === 'PENDING' || i.status === 'SENT').length;
      const overdueInvoices = invoices.filter(i => i.status === 'OVERDUE').length;
      const revenueMonth = (transactionsRes.data ?? [])
        .filter(t => t.type === 'INCOME' || t.type === 'income')
        .reduce((s, t) => s + (Number(t.amount) || 0), 0);

      const currency = settingsRes.data?.currency ?? 'AOA';

      setKpis({
        total_invoices: invoices.length,
        pending_invoices: pendingInvoices,
        overdue_invoices: overdueInvoices,
        total_revenue_month: revenueMonth,
        total_transactions_month: (transactionsRes.data ?? []).length,
        active_employees: employeesRes.count ?? 0,
        active_customers: customersRes.count ?? 0,
        currency,
      });

      // Configurações
      if (settingsRes.data) {
        setMobileSettings({
          mobile_enabled: settingsRes.data.mobile_enabled ?? true,
          mobile_notifications: settingsRes.data.mobile_notifications ?? true,
          mobile_biometric: settingsRes.data.mobile_biometric ?? true,
          mobile_offline_sync: settingsRes.data.mobile_offline_sync ?? true,
        });
      }

      setDevices((devicesRes.data ?? []) as MobileDevice[]);
      setNotifications((notifRes.data ?? []) as PushNotification[]);

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar dados';
      setError(msg);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // ── Marcar notificação como lida ────────────────────────────────────────────
  const markAsRead = async (notifId: string) => {
    const { error } = await supabase
      .from('push_notifications')
      .update({ is_read: true, read_at: new Date().toISOString() })
      .eq('id', notifId);
    if (!error) {
      setNotifications(prev =>
        prev.map(n => n.id === notifId ? { ...n, is_read: true } : n)
      );
    }
  };

  const markAllRead = async () => {
    const unread = notifications.filter(n => !n.is_read).map(n => n.id);
    if (unread.length === 0) return;
    const { error } = await supabase
      .from('push_notifications')
      .update({ is_read: true, read_at: new Date().toISOString() })
      .in('id', unread);
    if (!error) {
      setNotifications(prev => prev.map(n => ({ ...n, is_read: true })));
      toast.success('Todas as notificações marcadas como lidas');
    }
  };

  // ── Toggle de dispositivo ────────────────────────────────────────────────────
  const toggleDevice = async (deviceId: string, isActive: boolean) => {
    setTogglingDevice(deviceId);
    const { error } = await supabase
      .from('mobile_devices')
      .update({ is_active: !isActive })
      .eq('id', deviceId);
    if (!error) {
      setDevices(prev => prev.map(d => d.id === deviceId ? { ...d, is_active: !isActive } : d));
      toast.success(!isActive ? 'Dispositivo activado' : 'Dispositivo desactivado');
    } else {
      toast.error('Erro ao alterar estado do dispositivo');
    }
    setTogglingDevice(null);
  };

  // ── Guardar configurações mobile ─────────────────────────────────────────────
  const saveSettings = async () => {
    setSavingSettings(true);
    const ctx = await getUserContext();
    if (!ctx) { setSavingSettings(false); return; }

    const { error } = await supabase
      .from('tenant_settings')
      .upsert({
        tenant_id: ctx.tenantId,
        ...mobileSettings,
        updated_at: new Date().toISOString(),
      }, { onConflict: 'tenant_id' });

    if (!error) {
      toast.success('Configurações móveis guardadas com sucesso!');
    } else {
      toast.error('Erro ao guardar configurações: ' + error.message);
    }
    setSavingSettings(false);
  };

  // ── Remover dispositivo ──────────────────────────────────────────────────────
  const removeDevice = async (deviceId: string) => {
    if (!confirm('Remover este dispositivo? Terá de fazer login novamente no mesmo.')) return;
    const { error } = await supabase
      .from('mobile_devices')
      .delete()
      .eq('id', deviceId);
    if (!error) {
      setDevices(prev => prev.filter(d => d.id !== deviceId));
      toast.success('Dispositivo removido');
    } else {
      toast.error('Erro ao remover dispositivo');
    }
  };

  const unreadCount = notifications.filter(n => !n.is_read).length;

  // ── Features estáticas (informação) ──────────────────────────────────────────
  const features = [
    { icon: Zap, title: 'Acesso Instantâneo', description: 'Aceda aos dados financeiros em tempo real, onde estiver', color: 'text-yellow-500' },
    { icon: Shield, title: 'Segurança Avançada', description: 'Autenticação biométrica e encriptação ponto-a-ponto', color: 'text-blue-500' },
    { icon: Cloud, title: 'Sincronização Automática', description: 'Todos os dados sincronizados automaticamente com o Supabase', color: 'text-purple-500' },
    { icon: Bell, title: 'Notificações Push', description: 'Alertas em tempo real sobre facturas, pagamentos e aprovações', color: 'text-green-500' },
    { icon: Fingerprint, title: 'Biometria', description: 'Login rápido e seguro com impressão digital ou Face ID', color: 'text-red-500' },
    { icon: Globe, title: 'Offline First', description: 'Continue a trabalhar sem conexão — sincroniza ao retomar', color: 'text-indigo-500' },
  ];

  const modules = [
    { icon: BarChart3, title: 'Dashboard Mobile', description: 'KPIs e métricas em tempo real' },
    { icon: FileText, title: 'Faturação Mobile', description: 'Crie e envie facturas pelo smartphone' },
    { icon: Users, title: 'Gestão de RH', description: 'Aceda a dados de colaboradores em movimento' },
    { icon: Wallet, title: 'Financeiro Mobile', description: 'Acompanhe transacções e fluxo de caixa' },
    { icon: CheckCircle, title: 'Aprovações Rápidas', description: 'Aprove solicitações com um toque' },
    { icon: Lock, title: 'Segurança 2FA', description: 'Autenticação de dois factores integrada' },
  ];

  // ── RENDER ───────────────────────────────────────────────────────────────────
  return (
    <Layout>
      <motion.div
        className="space-y-8"
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
      >
        {/* ── Erro ── */}
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {/* ── Hero ── */}
        <motion.div
          variants={staggerItem}
          className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-primary via-primary/90 to-primary/80 p-8 md:p-12 text-primary-foreground"
        >
          <div className="relative z-10 max-w-3xl">
            <Badge className="mb-4 bg-primary-foreground/20 text-primary-foreground border-primary-foreground/30">
              Versão 2.0.1 · Abril 2026
            </Badge>
            <h1 className="text-4xl md:text-5xl font-bold mb-4 flex items-center gap-3">
              <Smartphone className="h-10 w-10" />
              KWANZACONTROL Mobile
            </h1>
            <p className="text-xl md:text-2xl mb-4 text-primary-foreground/90">
              Gerencie o seu negócio de qualquer lugar
            </p>
            <p className="text-lg mb-8 text-primary-foreground/80">
              Acesso completo ao seu CFO Digital na palma da mão. Disponível para iOS e Android.
            </p>
            <div className="flex flex-wrap gap-4">
              <Button size="lg" variant="secondary" className="gap-2"
                onClick={() => window.open('https://apps.apple.com/app/kwanzacontrol', '_blank')}>
                <Apple className="h-5 w-5" /> App Store
              </Button>
              <Button size="lg" variant="secondary" className="gap-2"
                onClick={() => window.open('https://play.google.com/store/apps/details?id=com.kwanzacontrol', '_blank')}>
                <Play className="h-5 w-5" /> Google Play
              </Button>
              <Button
                size="lg"
                variant="outline"
                className="gap-2 bg-primary-foreground/10 border-primary-foreground/30 text-primary-foreground hover:bg-primary-foreground/20"
                onClick={() => setShowQRCode(s => !s)}
              >
                <QrCode className="h-5 w-5" /> QR Code
              </Button>
            </div>

            {showQRCode && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="mt-6 p-6 bg-primary-foreground/10 rounded-lg border border-primary-foreground/20 inline-block"
              >
                <div className="flex flex-col items-center gap-3">
                  <div className="bg-white p-4 rounded-lg">
                    <div className="w-40 h-40 bg-gradient-to-br from-primary to-primary/60 rounded-lg flex items-center justify-center">
                      <QrCode className="h-24 w-24 text-white" />
                    </div>
                  </div>
                  <p className="font-semibold text-sm">Escaneie para descarregar</p>
                  <p className="text-xs text-primary-foreground/70">iOS 14+ · Android 8+</p>
                </div>
              </motion.div>
            )}
          </div>
          <div className="absolute top-0 right-0 w-1/2 h-full opacity-5 pointer-events-none">
            <Smartphone className="w-full h-full" />
          </div>
        </motion.div>

        {/* ── KPIs Reais do Tenant ── */}
        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="h-7 w-7 animate-spin text-primary mr-3" />
            <span className="text-muted-foreground">A carregar dados da empresa...</span>
          </div>
        ) : kpis && (
          <motion.div variants={staggerItem}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold flex items-center gap-2">
                <BarChart3 className="h-5 w-5 text-primary" />
                Resumo da Empresa (dados reais)
              </h2>
              <Button variant="ghost" size="sm" onClick={loadData} disabled={refreshing}>
                <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} />
                Actualizar
              </Button>
            </div>
            <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
              <Card>
                <CardContent className="pt-5">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">Total Facturas</p>
                    <FileText className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <p className="text-2xl font-bold">{kpis.total_invoices}</p>
                  <p className="text-xs text-yellow-600 mt-1">{kpis.pending_invoices} pendentes</p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-5">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">Receita do Mês</p>
                    <TrendingUp className="h-4 w-4 text-emerald-500" />
                  </div>
                  <p className="text-2xl font-bold text-emerald-600">{fmtAOA(kpis.total_revenue_month)}</p>
                  <p className="text-xs text-muted-foreground mt-1">{kpis.total_transactions_month} transacções</p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-5">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">Funcionários</p>
                    <Users className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <p className="text-2xl font-bold">{kpis.active_employees}</p>
                  <p className="text-xs text-muted-foreground mt-1">activos</p>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="pt-5">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">Clientes</p>
                    <Globe className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <p className="text-2xl font-bold">{kpis.active_customers}</p>
                  {kpis.overdue_invoices > 0 && (
                    <p className="text-xs text-destructive mt-1">
                      <TrendingDown className="h-3 w-3 inline mr-0.5" />
                      {kpis.overdue_invoices} fact. vencidas
                    </p>
                  )}
                </CardContent>
              </Card>
            </div>
          </motion.div>
        )}

        {/* ── Tabs ── */}
        <motion.div variants={staggerItem}>
          <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="overview">Visão Geral</TabsTrigger>
              <TabsTrigger value="notifications" className="relative">
                Notificações
                {unreadCount > 0 && (
                  <span className="absolute -top-1 -right-1 bg-destructive text-destructive-foreground text-[10px] rounded-full w-4 h-4 flex items-center justify-center">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                )}
              </TabsTrigger>
              <TabsTrigger value="devices">Dispositivos</TabsTrigger>
              <TabsTrigger value="settings">Configurações</TabsTrigger>
            </TabsList>

            {/* ── Overview ── */}
            <TabsContent value="overview" className="space-y-6">
              {/* Funcionalidades */}
              <Card>
                <CardHeader>
                  <CardTitle>Funcionalidades Mobile</CardTitle>
                  <CardDescription>O que pode fazer com o KWANZACONTROL Mobile</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                    {features.map((f, i) => (
                      <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.05 }}
                        className="flex items-start gap-3 p-3 rounded-lg bg-muted/30 hover:bg-muted/50 transition-colors"
                      >
                        <div className={`p-2 rounded-lg bg-background ${f.color} flex-shrink-0`}>
                          <f.icon className="h-5 w-5" />
                        </div>
                        <div>
                          <p className="font-medium text-sm">{f.title}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">{f.description}</p>
                        </div>
                      </motion.div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Módulos disponíveis */}
              <Card>
                <CardHeader>
                  <CardTitle>Módulos Disponíveis</CardTitle>
                  <CardDescription>Aceda a todos os módulos do KWANZACONTROL no seu telemóvel</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="grid gap-3 md:grid-cols-2">
                    {modules.map((m, i) => (
                      <div key={i} className="flex items-center justify-between p-3 border rounded-lg hover:bg-muted/30 transition-colors">
                        <div className="flex items-center gap-3">
                          <div className="p-2 rounded-lg bg-primary/10">
                            <m.icon className="h-4 w-4 text-primary" />
                          </div>
                          <div>
                            <p className="font-medium text-sm">{m.title}</p>
                            <p className="text-xs text-muted-foreground">{m.description}</p>
                          </div>
                        </div>
                        <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 text-xs">
                          <CheckCircle2 className="h-3 w-3 mr-1" />
                          Activo
                        </Badge>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>

              {/* Download CTA */}
              <Card className="bg-gradient-to-br from-primary/10 via-primary/5 to-transparent border-primary/20">
                <CardContent className="pt-6">
                  <div className="text-center space-y-4">
                    <h3 className="text-xl font-bold">Pronto para começar?</h3>
                    <p className="text-sm text-muted-foreground">
                      Descarregue o KWANZACONTROL Mobile e tenha o seu CFO Digital sempre à mão
                    </p>
                    <div className="flex flex-wrap justify-center gap-3 pt-2">
                      <Button className="gap-2" onClick={() => window.open('https://apps.apple.com/app/kwanzacontrol', '_blank')}>
                        <Apple className="h-4 w-4" /> iOS
                      </Button>
                      <Button variant="outline" className="gap-2" onClick={() => window.open('https://play.google.com/store/apps/details?id=com.kwanzacontrol', '_blank')}>
                        <Play className="h-4 w-4" /> Android
                      </Button>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Versão 2.0.1 · iOS 14+ · Android 8+ · Gratuito para todos os planos
                    </p>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* ── Notificações Push ── */}
            <TabsContent value="notifications" className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">Notificações Push</h3>
                  <p className="text-sm text-muted-foreground">
                    {unreadCount > 0 ? `${unreadCount} não lidas` : 'Tudo lido'}
                  </p>
                </div>
                {unreadCount > 0 && (
                  <Button variant="ghost" size="sm" onClick={markAllRead}>
                    <CheckCircle2 className="h-4 w-4 mr-1.5" />
                    Marcar todas como lidas
                  </Button>
                )}
              </div>

              {loading ? (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : notifications.length === 0 ? (
                <Card>
                  <CardContent className="py-16 text-center">
                    <Bell className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
                    <p className="text-muted-foreground">Sem notificações ainda.</p>
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-2">
                  {notifications.map(n => (
                    <motion.div
                      key={n.id}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-all
                        ${n.is_read ? 'opacity-60' : 'shadow-sm'}
                        ${notifBg[n.type] ?? 'bg-muted/30'}`}
                      onClick={() => { if (!n.is_read) markAsRead(n.id); }}
                    >
                      <div className="mt-0.5 flex-shrink-0">
                        {notifIcon[n.type] ?? <Bell className="h-4 w-4" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className={`text-sm font-medium truncate ${n.is_read ? '' : 'font-semibold'}`}>
                            {n.title}
                          </p>
                          <div className="flex items-center gap-1.5 flex-shrink-0">
                            {!n.is_read && (
                              <span className="w-2 h-2 rounded-full bg-primary flex-shrink-0" />
                            )}
                            <span className="text-xs text-muted-foreground whitespace-nowrap">
                              {timeAgo(n.sent_at)}
                            </span>
                          </div>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{n.body}</p>
                      </div>
                    </motion.div>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* ── Dispositivos ── */}
            <TabsContent value="devices" className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="font-semibold">Dispositivos Registados</h3>
                  <p className="text-sm text-muted-foreground">
                    {devices.length} dispositivo{devices.length !== 1 ? 's' : ''} ligado{devices.length !== 1 ? 's' : ''} à sua conta
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={loadData} disabled={refreshing}>
                  <RefreshCw className={`h-4 w-4 mr-1.5 ${refreshing ? 'animate-spin' : ''}`} />
                  Actualizar
                </Button>
              </div>

              {loading ? (
                <div className="flex justify-center py-10">
                  <Loader2 className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : devices.length === 0 ? (
                <Card>
                  <CardContent className="py-16 text-center space-y-3">
                    <Smartphone className="h-10 w-10 text-muted-foreground mx-auto" />
                    <p className="text-muted-foreground">Nenhum dispositivo móvel registado.</p>
                    <p className="text-xs text-muted-foreground">Faça login no app iOS ou Android para registar automaticamente.</p>
                  </CardContent>
                </Card>
              ) : (
                <div className="space-y-3">
                  {devices.map(device => (
                    <Card key={device.id} className={`transition-all ${device.is_active ? '' : 'opacity-60'}`}>
                      <CardContent className="pt-4">
                        <div className="flex items-center justify-between gap-4">
                          <div className="flex items-center gap-3">
                            <div className={`p-2.5 rounded-lg ${device.is_active ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground'}`}>
                              <PlatformIcon platform={device.platform} />
                            </div>
                            <div>
                              <div className="flex items-center gap-2">
                                <p className="font-medium text-sm">{device.device_name}</p>
                                <Badge variant="outline" className="text-xs capitalize">{device.platform}</Badge>
                              </div>
                              <div className="flex items-center gap-3 mt-0.5">
                                <span className="text-xs text-muted-foreground">v{device.app_version}</span>
                                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                  <Clock className="h-3 w-3" />
                                  {timeAgo(device.last_seen_at)}
                                </span>
                                {device.is_active ? (
                                  <span className="flex items-center gap-1 text-xs text-emerald-600">
                                    <Wifi className="h-3 w-3" /> Online
                                  </span>
                                ) : (
                                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                                    <WifiOff className="h-3 w-3" /> Inactivo
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 text-xs"
                              disabled={togglingDevice === device.id}
                              onClick={() => toggleDevice(device.id, device.is_active)}
                            >
                              {togglingDevice === device.id ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : device.is_active ? (
                                <ToggleRight className="h-4 w-4 text-primary" />
                              ) : (
                                <ToggleLeft className="h-4 w-4 text-muted-foreground" />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 text-xs text-destructive hover:text-destructive"
                              onClick={() => removeDevice(device.id)}
                            >
                              <XCircle className="h-4 w-4" />
                            </Button>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </TabsContent>

            {/* ── Configurações Mobile ── */}
            <TabsContent value="settings" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Settings className="h-5 w-5" />
                    Configurações da App Mobile
                  </CardTitle>
                  <CardDescription>
                    Controle as funcionalidades do KWANZACONTROL Mobile para a sua empresa
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Mobile App Enable */}
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div className="space-y-0.5">
                      <Label className="text-base font-medium flex items-center gap-2">
                        <Smartphone className="h-4 w-4 text-primary" />
                        App Mobile Activada
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Permitir acesso à aplicação mobile para a sua empresa
                      </p>
                    </div>
                    <Switch
                      checked={mobileSettings.mobile_enabled}
                      onCheckedChange={v => setMobileSettings(s => ({ ...s, mobile_enabled: v }))}
                    />
                  </div>

                  {/* Push Notifications */}
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div className="space-y-0.5">
                      <Label className="text-base font-medium flex items-center gap-2">
                        <Bell className="h-4 w-4 text-orange-500" />
                        Notificações Push
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Enviar alertas push para facturas, pagamentos e aprovações
                      </p>
                    </div>
                    <Switch
                      checked={mobileSettings.mobile_notifications}
                      onCheckedChange={v => setMobileSettings(s => ({ ...s, mobile_notifications: v }))}
                      disabled={!mobileSettings.mobile_enabled}
                    />
                  </div>

                  {/* Biometric */}
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div className="space-y-0.5">
                      <Label className="text-base font-medium flex items-center gap-2">
                        <Fingerprint className="h-4 w-4 text-blue-500" />
                        Autenticação Biométrica
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Permitir login com Face ID ou impressão digital
                      </p>
                    </div>
                    <Switch
                      checked={mobileSettings.mobile_biometric}
                      onCheckedChange={v => setMobileSettings(s => ({ ...s, mobile_biometric: v }))}
                      disabled={!mobileSettings.mobile_enabled}
                    />
                  </div>

                  {/* Offline Sync */}
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div className="space-y-0.5">
                      <Label className="text-base font-medium flex items-center gap-2">
                        <WifiOff className="h-4 w-4 text-purple-500" />
                        Sincronização Offline
                      </Label>
                      <p className="text-sm text-muted-foreground">
                        Guardar dados localmente e sincronizar ao retomar conexão
                      </p>
                    </div>
                    <Switch
                      checked={mobileSettings.mobile_offline_sync}
                      onCheckedChange={v => setMobileSettings(s => ({ ...s, mobile_offline_sync: v }))}
                      disabled={!mobileSettings.mobile_enabled}
                    />
                  </div>

                  <div className="pt-2">
                    <Button onClick={saveSettings} disabled={savingSettings} className="w-full md:w-auto">
                      {savingSettings ? (
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4 mr-2" />
                      )}
                      Guardar Configurações
                    </Button>
                  </div>
                </CardContent>
              </Card>

              {/* Segurança */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Shield className="h-5 w-5 text-blue-500" />
                    Segurança Mobile
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {[
                      { icon: Lock, label: 'Encriptação TLS 1.3', desc: 'Todas as comunicações são encriptadas', ok: true },
                      { icon: Shield, label: 'Autenticação JWT', desc: 'Tokens de sessão com expiração automática', ok: true },
                      { icon: Fingerprint, label: 'Biometria', desc: mobileSettings.mobile_biometric ? 'Activada' : 'Desactivada', ok: mobileSettings.mobile_biometric },
                      { icon: Bell, label: 'Notificações Seguras', desc: mobileSettings.mobile_notifications ? 'Activadas' : 'Desactivadas', ok: mobileSettings.mobile_notifications },
                    ].map((item, i) => (
                      <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/30">
                        <item.icon className={`h-4 w-4 flex-shrink-0 ${item.ok ? 'text-emerald-500' : 'text-muted-foreground'}`} />
                        <div className="flex-1">
                          <p className="text-sm font-medium">{item.label}</p>
                          <p className="text-xs text-muted-foreground">{item.desc}</p>
                        </div>
                        {item.ok ? (
                          <CheckCircle2 className="h-4 w-4 text-emerald-500 flex-shrink-0" />
                        ) : (
                          <XCircle className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                        )}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </motion.div>
      </motion.div>
    </Layout>
  );
}
