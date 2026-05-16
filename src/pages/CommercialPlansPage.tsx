/**
 * CommercialPlansPage — Planos Comerciais
 * Dados 100% reais do Supabase (tabelas commercial_plans + tenant_subscriptions + tenants)
 * Sem dados simulados.
 */

import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  Check, Star, Zap, RefreshCw, Crown, Sparkles,
  Users, HardDrive, Headphones, AlertTriangle,
  Calendar, TrendingUp, ArrowRight, Info,
} from 'lucide-react'
import { Layout } from '@/components/Layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { toast } from 'sonner'
import { supabase } from '@/integrations/supabase/client'

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface CommercialPlan {
  id: string
  code: string
  name: string
  description: string | null
  price_monthly: number
  price_annual: number | null
  currency: string
  max_users: number | null
  max_storage_gb: number | null
  features: string[]
  is_active: boolean
  is_popular: boolean
  sort_order: number
}

interface TenantSubscription {
  id: string
  plan_id: string
  billing_cycle: string
  status: string
  started_at: string | null
  expires_at: string | null
  amount_paid: number | null
  plan: CommercialPlan | null
}

interface TenantInfo {
  id: string
  name: string
  subscription_plan: string
  subscription_status: string
  subscription_expires_at: string | null
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtAOA(v: number): string {
  return new Intl.NumberFormat('pt-AO', {
    style: 'currency', currency: 'AOA',
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(v)
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('pt-AO', { day: '2-digit', month: 'long', year: 'numeric' })
}

async function getTenantId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null
  const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  return data?.tenant_id ?? null
}

// ─── Ícone por feature ────────────────────────────────────────────────────────

function featureIcon(f: string) {
  if (f.toLowerCase().includes('utilizador')) return <Users className="h-3.5 w-3.5 text-primary flex-shrink-0" />
  if (f.toLowerCase().includes('armazenamento') || f.toLowerCase().includes('gb')) return <HardDrive className="h-3.5 w-3.5 text-primary flex-shrink-0" />
  if (f.toLowerCase().includes('suporte') || f.toLowerCase().includes('sla')) return <Headphones className="h-3.5 w-3.5 text-primary flex-shrink-0" />
  if (f.toLowerCase().includes('ia') || f.toLowerCase().includes('smart')) return <Sparkles className="h-3.5 w-3.5 text-primary flex-shrink-0" />
  return <Check className="h-3.5 w-3.5 text-emerald-600 flex-shrink-0" />
}

// ─── Plano → ícone de destaque ────────────────────────────────────────────────

function planIcon(code: string) {
  if (code === 'ENTERPRISE') return <Crown className="h-6 w-6 text-amber-500" />
  if (code === 'PRO') return <Zap className="h-6 w-6 text-primary" />
  return <Star className="h-6 w-6 text-muted-foreground" />
}

// ═══════════════════════════════════════════════════════════════════════════════

export default function CommercialPlansPage() {
  const [plans, setPlans]         = useState<CommercialPlan[]>([])
  const [subscription, setSubscription] = useState<TenantSubscription | null>(null)
  const [tenant, setTenant]       = useState<TenantInfo | null>(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [annualBilling, setAnnualBilling] = useState(false)
  const [changingPlan, setChangingPlan] = useState<string | null>(null)

  // ─── Carregar dados ────────────────────────────────────────────────────────

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const tenantId = await getTenantId()
      if (!tenantId) throw new Error('Não autenticado ou utilizador sem tenant associado.')

      // Carregar tenant
      const { data: tenantData, error: tenantErr } = await supabase
        .from('tenants')
        .select('id,name,subscription_plan,subscription_status,subscription_expires_at')
        .eq('id', tenantId)
        .single()
      if (tenantErr) throw tenantErr
      setTenant(tenantData as TenantInfo)

      // Carregar planos activos
      const { data: plansData, error: plansErr } = await supabase
        .from('commercial_plans')
        .select('*')
        .eq('is_active', true)
        .order('sort_order', { ascending: true })
      if (plansErr) throw plansErr

      const parsedPlans: CommercialPlan[] = (plansData ?? []).map(p => ({
        ...p,
        features: Array.isArray(p.features)
          ? (p.features as unknown[]).map(f => String(f))
          : [],
      }))
      setPlans(parsedPlans)

      // Carregar subscrição do tenant (com join ao plano)
      const { data: subData } = await supabase
        .from('tenant_subscriptions')
        .select(`
          id, plan_id, billing_cycle, status, started_at, expires_at, amount_paid,
          plan:commercial_plans(*)
        `)
        .eq('tenant_id', tenantId)
        .maybeSingle()

      if (subData) {
        const sub = subData as Record<string, unknown>
        const rawPlan = sub.plan as Record<string, unknown> | null
        setSubscription({
          id:            String(sub.id),
          plan_id:       String(sub.plan_id),
          billing_cycle: String(sub.billing_cycle ?? 'monthly'),
          status:        String(sub.status ?? 'ACTIVE'),
          started_at:    sub.started_at ? String(sub.started_at) : null,
          expires_at:    sub.expires_at ? String(sub.expires_at) : null,
          amount_paid:   sub.amount_paid ? Number(sub.amount_paid) : null,
          plan: rawPlan ? {
            ...rawPlan,
            features: Array.isArray(rawPlan.features)
              ? (rawPlan.features as unknown[]).map(f => String(f))
              : [],
          } as CommercialPlan : null,
        })
        setAnnualBilling(String(sub.billing_cycle) === 'annual')
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao carregar dados dos planos.'
      setError(msg)
      console.error('[CommercialPlans] load error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Seleccionar plano ─────────────────────────────────────────────────────

  const handleSelectPlan = async (plan: CommercialPlan) => {
    if (subscription?.plan_id === plan.id) {
      toast.info('Já está no plano ' + plan.name)
      return
    }
    setChangingPlan(plan.id)
    try {
      const tenantId = await getTenantId()
      if (!tenantId) throw new Error('Não autenticado')

      const cycle      = annualBilling ? 'annual' : 'monthly'
      const amount     = annualBilling ? (plan.price_annual ?? plan.price_monthly * 12) : plan.price_monthly
      const expiresAt  = new Date()
      expiresAt.setMonth(expiresAt.getMonth() + (annualBilling ? 12 : 1))

      if (subscription?.id) {
        // Actualizar subscrição existente
        const { error: updErr } = await supabase
          .from('tenant_subscriptions')
          .update({
            plan_id:       plan.id,
            billing_cycle: cycle,
            status:        'ACTIVE',
            expires_at:    expiresAt.toISOString(),
            amount_paid:   amount,
            updated_at:    new Date().toISOString(),
          })
          .eq('id', subscription.id)
        if (updErr) throw updErr
      } else {
        // Criar nova subscrição
        const { error: insErr } = await supabase
          .from('tenant_subscriptions')
          .insert({
            tenant_id:     tenantId,
            plan_id:       plan.id,
            billing_cycle: cycle,
            status:        'ACTIVE',
            started_at:    new Date().toISOString(),
            expires_at:    expiresAt.toISOString(),
            amount_paid:   amount,
          })
        if (insErr) throw insErr
      }

      // Actualizar campo subscription_plan no tenant
      await supabase
        .from('tenants')
        .update({
          subscription_plan:      plan.code,
          subscription_status:    'ACTIVE',
          subscription_expires_at: expiresAt.toISOString(),
          updated_at:             new Date().toISOString(),
        })
        .eq('id', tenantId)

      // Registar transacção de billing
      await supabase.from('billing_transactions').insert({
        tenant_id:      tenantId,
        plan_id:        plan.id,
        subscription_id: subscription?.id ?? null,
        amount,
        currency:       'AOA',
        status:         'PENDING',
        payment_method: 'TRANSFER',
        description:    `Mudança para Plano ${plan.name} — ${annualBilling ? 'Anual' : 'Mensal'}`,
        due_date:       new Date().toISOString().split('T')[0],
      })

      toast.success(`✅ Plano ${plan.name} activado com sucesso!`)
      loadData()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Erro ao mudar de plano.')
    } finally {
      setChangingPlan(null)
    }
  }

  // ─── Estado de loading ─────────────────────────────────────────────────────

  if (loading) {
    return (
      <Layout>
        <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
          <RefreshCw className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">A carregar planos comerciais…</p>
        </div>
      </Layout>
    )
  }

  // ─── Erro ──────────────────────────────────────────────────────────────────

  if (error) {
    return (
      <Layout>
        <div className="max-w-xl mx-auto mt-12 space-y-4">
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Erro ao carregar planos</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <Button onClick={loadData} variant="outline">
            <RefreshCw className="h-4 w-4 mr-2" /> Tentar novamente
          </Button>
        </div>
      </Layout>
    )
  }

  const currentPlanCode = tenant?.subscription_plan ?? 'BASIC'
  const currentPlan     = plans.find(p => p.code === currentPlanCode)

  // Desconto anual (%)
  const annualDiscount = (plan: CommercialPlan) => {
    if (!plan.price_annual) return 0
    const full = plan.price_monthly * 12
    return Math.round(((full - plan.price_annual) / full) * 100)
  }

  const displayPrice = (plan: CommercialPlan) =>
    annualBilling && plan.price_annual
      ? plan.price_annual / 12
      : plan.price_monthly

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <Layout>
      <div className="space-y-8">

        {/* ── Cabeçalho ── */}
        <div className="text-center space-y-3">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-sm font-medium">
            <Sparkles className="h-4 w-4" />
            KwanzaControl — Planos para empresas angolanas
          </div>
          <h1 className="text-3xl font-bold">Planos Comerciais</h1>
          <p className="text-muted-foreground max-w-xl mx-auto">
            Escolha o plano ideal para o seu negócio. Todos os planos incluem conformidade AGT
            e suporte em português angolano.
          </p>

          {/* Toggle anual/mensal */}
          <div className="flex items-center justify-center gap-3 mt-4">
            <Label htmlFor="annual-toggle" className={!annualBilling ? 'font-semibold' : 'text-muted-foreground'}>
              Mensal
            </Label>
            <Switch
              id="annual-toggle"
              checked={annualBilling}
              onCheckedChange={setAnnualBilling}
            />
            <Label htmlFor="annual-toggle" className={annualBilling ? 'font-semibold' : 'text-muted-foreground'}>
              Anual
              <Badge className="ml-2 bg-emerald-100 text-emerald-800 text-xs py-0">Poupe até 20%</Badge>
            </Label>
          </div>
        </div>

        {/* ── Plano actual do tenant ── */}
        {tenant && currentPlan && (
          <Card className="border-primary/30 bg-primary/5 max-w-2xl mx-auto">
            <CardContent className="p-4">
              <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 justify-between">
                <div className="flex items-center gap-3">
                  {planIcon(currentPlanCode)}
                  <div>
                    <p className="font-semibold">{tenant.name}</p>
                    <p className="text-sm text-muted-foreground">
                      Plano actual: <span className="font-medium text-primary">{currentPlan.name}</span>
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className={
                    tenant.subscription_status === 'ACTIVE'
                      ? 'bg-emerald-100 text-emerald-800'
                      : 'bg-red-100 text-red-800'
                  }>
                    {tenant.subscription_status === 'ACTIVE' ? 'Activo' : tenant.subscription_status}
                  </Badge>
                  {subscription?.expires_at && (
                    <div className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Calendar className="h-3.5 w-3.5" />
                      Renova em {fmtDate(subscription.expires_at)}
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* ── Cards de planos ── */}
        {plans.length === 0 ? (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertTitle>Sem planos disponíveis</AlertTitle>
            <AlertDescription>
              Não foram encontrados planos activos na base de dados. Contacte o suporte.
            </AlertDescription>
          </Alert>
        ) : (
          <div className="grid gap-6 md:grid-cols-3">
            {plans.map((plan, idx) => {
              const isCurrent   = plan.code === currentPlanCode
              const isChanging  = changingPlan === plan.id
              const discount    = annualDiscount(plan)
              const price       = displayPrice(plan)

              return (
                <motion.div
                  key={plan.id}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: idx * 0.1 }}
                  className="relative"
                >
                  {plan.is_popular && (
                    <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 z-10">
                      <Badge className="bg-primary text-primary-foreground shadow-sm px-3">
                        <Star className="h-3 w-3 mr-1" /> Mais Popular
                      </Badge>
                    </div>
                  )}
                  {isCurrent && (
                    <div className="absolute -top-3.5 right-4 z-10">
                      <Badge variant="secondary" className="shadow-sm">
                        Plano Actual
                      </Badge>
                    </div>
                  )}

                  <Card className={`
                    h-full flex flex-col transition-all
                    ${plan.is_popular ? 'border-primary shadow-lg ring-1 ring-primary/20' : ''}
                    ${isCurrent ? 'border-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/10' : ''}
                    ${plan.code === 'ENTERPRISE' ? 'border-amber-300/60 bg-amber-50/30 dark:bg-amber-950/10' : ''}
                  `}>
                    <CardHeader className="text-center pb-4 pt-6">
                      <div className="flex justify-center mb-2">
                        {planIcon(plan.code)}
                      </div>
                      <CardTitle className="text-xl">{plan.name}</CardTitle>
                      {plan.description && (
                        <CardDescription className="text-xs">{plan.description}</CardDescription>
                      )}
                      <div className="mt-4 space-y-1">
                        <div className="flex items-end justify-center gap-1">
                          <span className="text-4xl font-bold">{fmtAOA(price)}</span>
                          <span className="text-muted-foreground pb-1">/mês</span>
                        </div>
                        {annualBilling && plan.price_annual && (
                          <div className="space-y-0.5">
                            <p className="text-xs text-muted-foreground">
                              Faturado {fmtAOA(plan.price_annual)}/ano
                            </p>
                            {discount > 0 && (
                              <Badge className="bg-emerald-100 text-emerald-800 text-xs py-0">
                                <TrendingUp className="h-2.5 w-2.5 mr-1" /> Poupe {discount}%
                              </Badge>
                            )}
                          </div>
                        )}
                      </div>

                      {/* Limites resumidos */}
                      <div className="flex justify-center gap-4 mt-3 text-xs text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Users className="h-3 w-3" />
                          {plan.max_users ?? '∞'} utilizadores
                        </span>
                        <span className="flex items-center gap-1">
                          <HardDrive className="h-3 w-3" />
                          {plan.max_storage_gb ? `${plan.max_storage_gb} GB` : '∞'}
                        </span>
                      </div>
                    </CardHeader>

                    <CardContent className="flex-1 flex flex-col space-y-4">
                      <Separator />

                      <ul className="space-y-2 flex-1">
                        {plan.features.map((feature, i) => (
                          <li key={i} className="flex items-start gap-2 text-sm">
                            {featureIcon(feature)}
                            <span>{feature}</span>
                          </li>
                        ))}
                      </ul>

                      <Button
                        className="w-full mt-auto"
                        variant={plan.is_popular && !isCurrent ? 'default' : 'outline'}
                        disabled={isCurrent || isChanging}
                        onClick={() => handleSelectPlan(plan)}
                      >
                        {isChanging
                          ? <><RefreshCw className="h-4 w-4 mr-2 animate-spin" />A activar…</>
                          : isCurrent
                            ? 'Plano Actual'
                            : <><ArrowRight className="h-4 w-4 mr-2" />Escolher {plan.name}</>
                        }
                      </Button>
                    </CardContent>
                  </Card>
                </motion.div>
              )
            })}
          </div>
        )}

        {/* ── Tabela de comparação ── */}
        {plans.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Info className="h-5 w-5 text-primary" />
                Comparação de Planos
              </CardTitle>
              <CardDescription>Visão detalhada de todos os recursos incluídos</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-3 px-4 font-semibold text-muted-foreground">Recurso</th>
                      {plans.map(p => (
                        <th key={p.id} className="text-center py-3 px-4 font-semibold">
                          <span className={p.code === currentPlanCode ? 'text-primary' : ''}>{p.name}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { label: 'Utilizadores',     key: (p: CommercialPlan) => p.max_users ? String(p.max_users) : 'Ilimitado' },
                      { label: 'Armazenamento',    key: (p: CommercialPlan) => p.max_storage_gb ? `${p.max_storage_gb} GB` : 'Ilimitado' },
                      { label: 'Preço Mensal',     key: (p: CommercialPlan) => fmtAOA(p.price_monthly) },
                      { label: 'Preço Anual',      key: (p: CommercialPlan) => p.price_annual ? fmtAOA(p.price_annual) : '—' },
                    ].map(row => (
                      <tr key={row.label} className="border-b hover:bg-muted/20">
                        <td className="py-3 px-4 text-muted-foreground font-medium">{row.label}</td>
                        {plans.map(p => (
                          <td key={p.id} className="text-center py-3 px-4">
                            <span className={p.code === currentPlanCode ? 'font-semibold text-primary' : ''}>
                              {row.key(p)}
                            </span>
                          </td>
                        ))}
                      </tr>
                    ))}
                    {/* Features dinâmicas: union de todas as features */}
                    {Array.from(new Set(plans.flatMap(p => p.features))).map(feat => (
                      <tr key={feat} className="border-b hover:bg-muted/20">
                        <td className="py-3 px-4 text-muted-foreground">{feat}</td>
                        {plans.map(p => (
                          <td key={p.id} className="text-center py-3 px-4">
                            {p.features.includes(feat)
                              ? <Check className="h-4 w-4 mx-auto text-emerald-600" />
                              : <span className="text-muted-foreground/40">—</span>
                            }
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {/* ── Nota de contacto para Enterprise ── */}
        <Card className="border-amber-200 dark:border-amber-800/40 bg-amber-50/50 dark:bg-amber-950/20">
          <CardContent className="p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <Crown className="h-6 w-6 text-amber-500 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Precisa de uma solução personalizada?</p>
                <p className="text-sm text-muted-foreground">
                  O plano Enterprise pode ser totalmente adaptado à sua empresa.
                  Contacte-nos para uma proposta customizada com SLA garantido.
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              className="border-amber-400 text-amber-700 hover:bg-amber-100 flex-shrink-0"
              onClick={() => toast.info('Contacto comercial: comercial@kwanzacontrol.ao')}
            >
              Falar com vendas
            </Button>
          </CardContent>
        </Card>

        {/* ── Botão actualizar ── */}
        <div className="flex justify-center">
          <Button variant="ghost" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Actualizar dados
          </Button>
        </div>
      </div>
    </Layout>
  )
}
