/**
 * BillingDashboardPage — Planos & Billing
 * Dados 100% reais do Supabase (billing_transactions + tenant_subscriptions + tenants + commercial_plans)
 * Sem dados simulados.
 */

import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  DollarSign, CreditCard, Calendar, RefreshCw,
  TrendingUp, ArrowUpRight, ArrowDownRight,
  CheckCircle2, XCircle, Clock, AlertTriangle,
  Receipt, Zap, BarChart2, Plus, Download,
  Crown, Star,
} from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { toast } from 'sonner'
import { supabase } from '@/integrations/supabase/client'
import { useNavigate } from 'react-router-dom'
import { ROUTE_PATHS } from '@/lib/index'

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface BillingTransaction {
  id: string
  amount: number
  currency: string
  status: 'PENDING' | 'PAID' | 'FAILED' | 'REFUNDED'
  payment_method: string | null
  reference: string | null
  description: string | null
  paid_at: string | null
  due_date: string | null
  created_at: string | null
  plan_name?: string
}

interface SubscriptionInfo {
  id: string
  plan_id: string
  plan_name: string
  plan_code: string
  billing_cycle: string
  status: string
  started_at: string | null
  expires_at: string | null
  amount_paid: number | null
  price_monthly: number
  price_annual: number | null
  max_users: number | null
  max_storage_gb: number | null
  features: string[]
}

interface TenantInfo {
  id: string
  name: string
  subscription_plan: string
  subscription_status: string
  subscription_expires_at: string | null
}

interface BillingStats {
  totalPaid: number
  pendingAmount: number
  paidCount: number
  pendingCount: number
  failedCount: number
  avgMonthlyPayment: number
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtAOA(v: number): string {
  return new Intl.NumberFormat('pt-AO', {
    style: 'currency', currency: 'AOA',
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(v)
}

function fmtDate(s: string | null | undefined, full = false): string {
  if (!s) return '—'
  const opts: Intl.DateTimeFormatOptions = full
    ? { day: '2-digit', month: 'long', year: 'numeric' }
    : { day: '2-digit', month: 'short', year: 'numeric' }
  return new Date(s).toLocaleDateString('pt-AO', opts)
}

async function getTenantId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null
  const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  return data?.tenant_id ?? null
}

// ─── Status badge ─────────────────────────────────────────────────────────────

function TxStatusBadge({ status }: { status: string }) {
  if (status === 'PAID')     return <Badge className="bg-emerald-100 text-emerald-800 text-xs"><CheckCircle2 className="h-3 w-3 mr-1" />Pago</Badge>
  if (status === 'PENDING')  return <Badge className="bg-yellow-100 text-yellow-800 text-xs"><Clock className="h-3 w-3 mr-1" />Pendente</Badge>
  if (status === 'FAILED')   return <Badge variant="destructive" className="text-xs"><XCircle className="h-3 w-3 mr-1" />Falhou</Badge>
  if (status === 'REFUNDED') return <Badge variant="secondary" className="text-xs"><ArrowDownRight className="h-3 w-3 mr-1" />Reembolsado</Badge>
  return <Badge variant="outline" className="text-xs">{status}</Badge>
}

function planIcon(code: string) {
  if (code === 'ENTERPRISE') return <Crown className="h-5 w-5 text-amber-500" />
  if (code === 'PRO')        return <Zap className="h-5 w-5 text-primary" />
  return <Star className="h-5 w-5 text-muted-foreground" />
}

// ═══════════════════════════════════════════════════════════════════════════════

export default function BillingDashboardPage() {
  const navigate  = useNavigate()
  const [tab, setTab]                     = useState('overview')
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [tenant, setTenant]               = useState<TenantInfo | null>(null)
  const [subscription, setSubscription]   = useState<SubscriptionInfo | null>(null)
  const [transactions, setTransactions]   = useState<BillingTransaction[]>([])
  const [stats, setStats]                 = useState<BillingStats | null>(null)

  // ─── Carregar dados ──────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const tenantId = await getTenantId()
      if (!tenantId) throw new Error('Não autenticado ou sem tenant associado.')

      // 1. Tenant
      const { data: tenantData, error: te } = await supabase
        .from('tenants')
        .select('id,name,subscription_plan,subscription_status,subscription_expires_at')
        .eq('id', tenantId)
        .single()
      if (te) throw te
      setTenant(tenantData as TenantInfo)

      // 2. Subscrição com plano
      const { data: subRaw } = await supabase
        .from('tenant_subscriptions')
        .select(`
          id, plan_id, billing_cycle, status, started_at, expires_at, amount_paid,
          plan:commercial_plans(id, code, name, price_monthly, price_annual, max_users, max_storage_gb, features)
        `)
        .eq('tenant_id', tenantId)
        .maybeSingle()

      if (subRaw) {
        const s = subRaw as Record<string, unknown>
        const p = s.plan as Record<string, unknown> | null
        setSubscription({
          id:             String(s.id),
          plan_id:        String(s.plan_id),
          plan_name:      p ? String(p.name) : '—',
          plan_code:      p ? String(p.code) : '—',
          billing_cycle:  String(s.billing_cycle ?? 'monthly'),
          status:         String(s.status ?? 'ACTIVE'),
          started_at:     s.started_at ? String(s.started_at) : null,
          expires_at:     s.expires_at ? String(s.expires_at) : null,
          amount_paid:    s.amount_paid ? Number(s.amount_paid) : null,
          price_monthly:  p ? Number(p.price_monthly ?? 0) : 0,
          price_annual:   p && p.price_annual ? Number(p.price_annual) : null,
          max_users:      p && p.max_users ? Number(p.max_users) : null,
          max_storage_gb: p && p.max_storage_gb ? Number(p.max_storage_gb) : null,
          features:       p && Array.isArray(p.features)
                            ? (p.features as unknown[]).map(f => String(f))
                            : [],
        })
      }

      // 3. Transacções de billing
      const { data: txRaw, error: txE } = await supabase
        .from('billing_transactions')
        .select(`
          id, amount, currency, status, payment_method, reference,
          description, paid_at, due_date, created_at,
          plan:commercial_plans(name)
        `)
        .eq('tenant_id', tenantId)
        .order('created_at', { ascending: false })
        .limit(50)
      if (txE) throw txE

      const txList: BillingTransaction[] = (txRaw ?? []).map((t: Record<string, unknown>) => ({
        id:             String(t.id),
        amount:         Number(t.amount ?? 0),
        currency:       String(t.currency ?? 'AOA'),
        status:         String(t.status) as BillingTransaction['status'],
        payment_method: t.payment_method ? String(t.payment_method) : null,
        reference:      t.reference ? String(t.reference) : null,
        description:    t.description ? String(t.description) : null,
        paid_at:        t.paid_at ? String(t.paid_at) : null,
        due_date:       t.due_date ? String(t.due_date) : null,
        created_at:     t.created_at ? String(t.created_at) : null,
        plan_name:      t.plan && typeof t.plan === 'object'
                          ? String((t.plan as Record<string,unknown>).name ?? '—')
                          : undefined,
      }))
      setTransactions(txList)

      // 4. Estatísticas calculadas
      const paid     = txList.filter(t => t.status === 'PAID')
      const pending  = txList.filter(t => t.status === 'PENDING')
      const failed   = txList.filter(t => t.status === 'FAILED')
      const totalPaid = paid.reduce((s, t) => s + t.amount, 0)
      const pendingAmt = pending.reduce((s, t) => s + t.amount, 0)

      // Média mensal: agrupar pagamentos pagos por mês
      const byMonth = new Map<string, number>()
      for (const tx of paid) {
        const key = (tx.paid_at ?? tx.created_at ?? '').substring(0, 7)
        if (key) byMonth.set(key, (byMonth.get(key) ?? 0) + tx.amount)
      }
      const avgMonthly = byMonth.size > 0
        ? Array.from(byMonth.values()).reduce((a, b) => a + b, 0) / byMonth.size
        : 0

      setStats({
        totalPaid,
        pendingAmount: pendingAmt,
        paidCount:    paid.length,
        pendingCount: pending.length,
        failedCount:  failed.length,
        avgMonthlyPayment: avgMonthly,
      })

    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar dados de billing.'
      setError(msg)
      console.error('[BillingDashboard] error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Exportar CSV ─────────────────────────────────────────────────────────

  const handleExport = () => {
    if (transactions.length === 0) { toast.warning('Sem transacções para exportar'); return }
    const lines = [
      'Data,Descrição,Plano,Valor,Estado,Método,Referência',
      ...transactions.map(t =>
        [
          fmtDate(t.created_at),
          `"${t.description ?? '—'}"`,
          t.plan_name ?? '—',
          t.amount,
          t.status,
          t.payment_method ?? '—',
          t.reference ?? '—',
        ].join(',')
      )
    ].join('\n')
    const blob = new Blob(['\uFEFF' + lines], { type: 'text/csv;charset=utf-8;' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `billing_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('CSV exportado com sucesso!')
  }

  // ─── Render states ────────────────────────────────────────────────────────

  if (loading) {
    return (
      <Layout>
        <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">A carregar dados de billing…</p>
        </div>
      </Layout>
    )
  }

  if (error) {
    return (
      <Layout>
        <div className="max-w-xl mx-auto mt-12 space-y-4">
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Erro ao carregar billing</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <Button onClick={loadData} variant="outline">
            <RefreshCw className="h-4 w-4 mr-2" /> Tentar novamente
          </Button>
        </div>
      </Layout>
    )
  }

  // ─── Render principal ────────────────────────────────────────────────────

  // Histórico mensal para sparkline simples
  const txByMonth = (() => {
    const map = new Map<string, { month: string; amount: number; count: number }>()
    for (const tx of transactions.filter(t => t.status === 'PAID')) {
      const raw = (tx.paid_at ?? tx.created_at ?? '').substring(0, 7)
      if (!raw) continue
      const d = new Date(raw + '-01')
      const label = d.toLocaleDateString('pt-AO', { month: 'short', year: '2-digit' })
      const prev = map.get(raw) ?? { month: label, amount: 0, count: 0 }
      map.set(raw, { month: label, amount: prev.amount + tx.amount, count: prev.count + 1 })
    }
    return Array.from(map.values())
      .sort((a, b) => a.month.localeCompare(b.month))
      .slice(-6)
  })()

  const maxBar = txByMonth.length > 0 ? Math.max(...txByMonth.map(m => m.amount)) : 1

  return (
    <Layout>
      <div className="space-y-6">

        {/* ── Cabeçalho ── */}
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <h1 className="text-3xl font-bold">Planos & Billing</h1>
            <p className="text-muted-foreground">
              {tenant?.name ?? 'Empresa'} — Gestão de faturamento e assinaturas
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={loadData} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
              Actualizar
            </Button>
            <Button size="sm" variant="outline" onClick={handleExport} disabled={transactions.length === 0}>
              <Download className="h-4 w-4 mr-2" /> Exportar CSV
            </Button>
            <Button size="sm" onClick={() => navigate(ROUTE_PATHS.COMMERCIAL_PLANS)}>
              <Plus className="h-4 w-4 mr-2" /> Mudar de Plano
            </Button>
          </div>
        </div>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="overview"><BarChart2 className="h-4 w-4 mr-2" />Visão Geral</TabsTrigger>
            <TabsTrigger value="subscription"><Zap className="h-4 w-4 mr-2" />Subscrição</TabsTrigger>
            <TabsTrigger value="transactions"><Receipt className="h-4 w-4 mr-2" />Transacções</TabsTrigger>
          </TabsList>

          {/* ═══════ TAB: OVERVIEW ═══════ */}
          <TabsContent value="overview" className="space-y-6 mt-4">

            {/* KPI cards */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[
                {
                  label: 'Total Pago',
                  value: fmtAOA(stats?.totalPaid ?? 0),
                  sub: `${stats?.paidCount ?? 0} transacções pagas`,
                  icon: <DollarSign className="h-5 w-5 text-emerald-600" />,
                  bg: 'bg-emerald-50 dark:bg-emerald-950/20',
                  color: 'text-emerald-700',
                },
                {
                  label: 'Pagamentos Pendentes',
                  value: fmtAOA(stats?.pendingAmount ?? 0),
                  sub: `${stats?.pendingCount ?? 0} aguardam pagamento`,
                  icon: <Clock className="h-5 w-5 text-yellow-600" />,
                  bg: 'bg-yellow-50 dark:bg-yellow-950/20',
                  color: 'text-yellow-700',
                },
                {
                  label: 'Média Mensal',
                  value: fmtAOA(stats?.avgMonthlyPayment ?? 0),
                  sub: 'Baseado no histórico real',
                  icon: <TrendingUp className="h-5 w-5 text-primary" />,
                  bg: 'bg-primary/5',
                  color: 'text-primary',
                },
                {
                  label: 'Plano Actual',
                  value: subscription?.plan_name ?? (tenant?.subscription_plan ?? '—'),
                  sub: subscription?.billing_cycle === 'annual' ? 'Faturação anual' : 'Faturação mensal',
                  icon: subscription ? planIcon(subscription.plan_code) : <CreditCard className="h-5 w-5" />,
                  bg: 'bg-muted/40',
                  color: 'text-foreground',
                },
                {
                  label: 'Estado Subscrição',
                  value: tenant?.subscription_status === 'ACTIVE' ? 'Activa' : (tenant?.subscription_status ?? '—'),
                  sub: subscription?.expires_at ? `Renova em ${fmtDate(subscription.expires_at)}` : 'Sem data de renovação',
                  icon: tenant?.subscription_status === 'ACTIVE'
                    ? <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                    : <XCircle className="h-5 w-5 text-destructive" />,
                  bg: tenant?.subscription_status === 'ACTIVE' ? 'bg-emerald-50 dark:bg-emerald-950/20' : 'bg-red-50 dark:bg-red-950/20',
                  color: tenant?.subscription_status === 'ACTIVE' ? 'text-emerald-700' : 'text-destructive',
                },
                {
                  label: 'Pagamentos Falhados',
                  value: String(stats?.failedCount ?? 0),
                  sub: stats?.failedCount ? 'Requer atenção' : 'Tudo em ordem',
                  icon: stats?.failedCount
                    ? <XCircle className="h-5 w-5 text-destructive" />
                    : <CheckCircle2 className="h-5 w-5 text-emerald-600" />,
                  bg: stats?.failedCount ? 'bg-red-50 dark:bg-red-950/20' : 'bg-muted/40',
                  color: stats?.failedCount ? 'text-destructive' : 'text-muted-foreground',
                },
              ].map((kpi, i) => (
                <motion.div key={i} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }}>
                  <Card className={kpi.bg}>
                    <CardContent className="p-4">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-xs text-muted-foreground">{kpi.label}</p>
                          <p className={`text-xl font-bold mt-0.5 ${kpi.color}`}>{kpi.value}</p>
                          <p className="text-xs text-muted-foreground mt-0.5">{kpi.sub}</p>
                        </div>
                        <div className="p-2 rounded-lg bg-background/60">{kpi.icon}</div>
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              ))}
            </div>

            {/* Histórico de pagamentos — gráfico de barras simples */}
            {txByMonth.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <BarChart2 className="h-5 w-5 text-primary" />
                    Histórico de Pagamentos (últimos 6 meses)
                  </CardTitle>
                  <CardDescription>Valores pagos por mês — fonte: tabela billing_transactions</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {txByMonth.map((m, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <div className="w-16 text-right text-xs text-muted-foreground font-medium">{m.month}</div>
                      <div className="flex-1 bg-muted rounded-full h-3 overflow-hidden">
                        <motion.div
                          className="bg-primary h-3 rounded-full"
                          initial={{ width: 0 }}
                          animate={{ width: `${(m.amount / maxBar) * 100}%` }}
                          transition={{ delay: i * 0.08, duration: 0.5 }}
                        />
                      </div>
                      <div className="w-28 text-right">
                        <span className="text-sm font-semibold">{fmtAOA(m.amount)}</span>
                        <span className="text-xs text-muted-foreground ml-1">({m.count})</span>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {txByMonth.length === 0 && transactions.length === 0 && (
              <Alert>
                <Receipt className="h-4 w-4" />
                <AlertTitle>Sem histórico de pagamentos</AlertTitle>
                <AlertDescription>
                  Ainda não existem transacções de billing para este tenant.
                  <Button
                    variant="link"
                    className="px-1 h-auto"
                    onClick={() => navigate(ROUTE_PATHS.COMMERCIAL_PLANS)}
                  >
                    Escolha um plano <ArrowUpRight className="h-3 w-3 ml-0.5" />
                  </Button>
                </AlertDescription>
              </Alert>
            )}
          </TabsContent>

          {/* ═══════ TAB: SUBSCRIÇÃO ═══════ */}
          <TabsContent value="subscription" className="space-y-4 mt-4">
            {!subscription ? (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>Sem subscrição activa</AlertTitle>
                <AlertDescription>
                  Este tenant ainda não tem uma subscrição configurada.
                  <Button
                    variant="link" className="px-1 h-auto"
                    onClick={() => navigate(ROUTE_PATHS.COMMERCIAL_PLANS)}
                  >
                    Ver planos disponíveis
                  </Button>
                </AlertDescription>
              </Alert>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                {/* Detalhes da subscrição */}
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      {planIcon(subscription.plan_code)}
                      Plano {subscription.plan_name}
                    </CardTitle>
                    <CardDescription>Detalhes da subscrição actual</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {[
                      { label: 'Estado',           val: <TxStatusBadge status={subscription.status} /> },
                      { label: 'Ciclo',             val: subscription.billing_cycle === 'annual' ? 'Anual' : 'Mensal' },
                      { label: 'Início',            val: fmtDate(subscription.started_at, true) },
                      { label: 'Renovação',         val: fmtDate(subscription.expires_at, true) },
                      { label: 'Preço Mensal',      val: fmtAOA(subscription.price_monthly) },
                      { label: 'Preço Anual',       val: subscription.price_annual ? fmtAOA(subscription.price_annual) : '—' },
                      { label: 'Último Pagamento',  val: subscription.amount_paid ? fmtAOA(subscription.amount_paid) : '—' },
                      { label: 'Utilizadores',      val: subscription.max_users ? String(subscription.max_users) : 'Ilimitado' },
                      { label: 'Armazenamento',     val: subscription.max_storage_gb ? `${subscription.max_storage_gb} GB` : 'Ilimitado' },
                    ].map(r => (
                      <div key={r.label} className="flex items-center justify-between py-1 border-b last:border-0">
                        <span className="text-sm text-muted-foreground">{r.label}</span>
                        <span className="text-sm font-medium">{r.val}</span>
                      </div>
                    ))}
                  </CardContent>
                </Card>

                {/* Features incluídas */}
                <Card>
                  <CardHeader>
                    <CardTitle>Recursos Incluídos</CardTitle>
                    <CardDescription>Funcionalidades do plano {subscription.plan_name}</CardDescription>
                  </CardHeader>
                  <CardContent>
                    {subscription.features.length > 0 ? (
                      <ul className="space-y-2">
                        {subscription.features.map((f, i) => (
                          <li key={i} className="flex items-start gap-2 text-sm">
                            <CheckCircle2 className="h-4 w-4 text-emerald-600 flex-shrink-0 mt-0.5" />
                            {f}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-muted-foreground">Recursos não disponíveis.</p>
                    )}
                    <Separator className="my-4" />
                    <Button
                      className="w-full"
                      variant="outline"
                      onClick={() => navigate(ROUTE_PATHS.COMMERCIAL_PLANS)}
                    >
                      <ArrowUpRight className="h-4 w-4 mr-2" /> Ver todos os planos
                    </Button>
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          {/* ═══════ TAB: TRANSACÇÕES ═══════ */}
          <TabsContent value="transactions" className="space-y-4 mt-4">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="flex items-center gap-2">
                      <Receipt className="h-5 w-5 text-primary" />
                      Transacções de Billing
                    </CardTitle>
                    <CardDescription>
                      {transactions.length} registos · tabela billing_transactions
                    </CardDescription>
                  </div>
                  <Button size="sm" variant="outline" onClick={handleExport} disabled={transactions.length === 0}>
                    <Download className="h-3.5 w-3.5 mr-1.5" /> CSV
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {transactions.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground space-y-2">
                    <Receipt className="h-12 w-12 mx-auto opacity-20" />
                    <p>Sem transacções de billing registadas.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {transactions.map(tx => (
                      <div
                        key={tx.id}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 p-3 rounded-lg border hover:bg-muted/30 transition-colors"
                      >
                        <div className="space-y-0.5">
                          <p className="font-medium text-sm">
                            {tx.description ?? `Pagamento ${tx.plan_name ?? '—'}`}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {tx.payment_method && <span className="mr-2">📤 {tx.payment_method}</span>}
                            {tx.paid_at
                              ? <span>Pago em {fmtDate(tx.paid_at)}</span>
                              : tx.due_date
                                ? <span>Vence em {fmtDate(tx.due_date)}</span>
                                : <span>Criado em {fmtDate(tx.created_at)}</span>
                            }
                          </p>
                          {tx.reference && (
                            <p className="text-xs font-mono text-muted-foreground/70">Ref: {tx.reference}</p>
                          )}
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0">
                          <span className={`font-bold text-sm ${tx.status === 'PAID' ? 'text-emerald-700' : tx.status === 'FAILED' ? 'text-destructive' : ''}`}>
                            {fmtAOA(tx.amount)}
                          </span>
                          <TxStatusBadge status={tx.status} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

        {/* Aviso de próxima renovação */}
        {subscription?.expires_at && (() => {
          const days = Math.round((new Date(subscription.expires_at).getTime() - Date.now()) / 86400000)
          if (days <= 7 && days >= 0) return (
            <Alert>
              <Calendar className="h-4 w-4" />
              <AlertTitle>Subscrição renova em {days} dia(s)</AlertTitle>
              <AlertDescription>
                A sua subscrição do plano {subscription.plan_name} renova em {fmtDate(subscription.expires_at, true)}.
                Certifique-se de que o pagamento está processado.
              </AlertDescription>
            </Alert>
          )
          if (days < 0) return (
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Subscrição expirou</AlertTitle>
              <AlertDescription>
                A sua subscrição expirou em {fmtDate(subscription.expires_at, true)}.
                <Button variant="link" className="px-1 h-auto" onClick={() => navigate(ROUTE_PATHS.COMMERCIAL_PLANS)}>
                  Renovar agora
                </Button>
              </AlertDescription>
            </Alert>
          )
          return null
        })()}

      </div>
    </Layout>
  )
}
