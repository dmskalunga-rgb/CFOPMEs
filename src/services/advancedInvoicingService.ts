// =====================================================
// KWANZACONTROL – Advanced Invoicing Service
// Faturação Avançada: Subscrições, Templates, KPIs
// 100% dados reais do Supabase — sem dados simulados
// 2026-04-18
// =====================================================

import { supabase } from '@/integrations/supabase/client'

// ── Helper: tenant_id robusto ─────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  const { data: rpcData, error: rpcErr } = await supabase.rpc('get_current_tenant_id')
  if (!rpcErr && rpcData) return rpcData as string
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  const { data: u } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  if (u?.tenant_id) return u.tenant_id
  throw new Error('Tenant não encontrado')
}

// ── Tipos ─────────────────────────────────────────────────────────────────────

export interface Subscription {
  id: string
  tenant_id: string
  customer_id: string
  subscription_number: string | null
  plan_name: string
  description?: string | null
  billing_cycle: 'MONTHLY' | 'QUARTERLY' | 'YEARLY' | 'CUSTOM'
  billing_day: number
  amount: number
  currency: string
  tax_rate: number
  start_date: string
  end_date?: string | null
  next_billing_date: string
  last_billing_date?: string | null
  status: 'ACTIVE' | 'PAUSED' | 'CANCELLED' | 'EXPIRED'
  auto_renew: boolean
  retry_count: number
  max_retries: number
  retry_interval_days: number
  payment_method?: string | null
  notes?: string | null
  cancelled_at?: string | null
  cancellation_reason?: string | null
  created_at: string
  updated_at: string
  // join
  customers?: { id: string; name: string; email: string | null; nif: string | null } | null
}

export interface SubscriptionInvoice {
  id: string
  subscription_id: string
  invoice_id?: string | null
  billing_date: string
  due_date: string
  amount: number
  tax_amount: number
  total_amount: number
  status: 'PENDING' | 'PAID' | 'FAILED' | 'RETRYING' | 'CANCELLED'
  retry_count: number
  last_retry_date?: string | null
  next_retry_date?: string | null
  paid_at?: string | null
  paid_amount?: number | null
  payment_method?: string | null
  payment_reference?: string | null
  failed_reason?: string | null
  notes?: string | null
  created_at: string
  updated_at: string
}

export interface InvoiceTemplate {
  id: string
  tenant_id: string
  template_name: string
  description?: string | null
  template_type: 'STANDARD' | 'RECURRING' | 'PROFORMA' | 'CREDIT_NOTE'
  default_items?: TemplateItem[] | null
  default_terms?: string | null
  default_notes?: string | null
  default_payment_terms: number
  default_tax_rate: number
  is_default: boolean
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface TemplateItem {
  description: string
  quantity: number
  unit_price: number
  iva_rate?: string
  iva_percent?: number
}

export interface InvoiceKPIs {
  totalInvoices: number
  paidInvoices: number
  pendingInvoices: number
  overdueInvoices: number
  draftInvoices: number
  totalRevenue: number
  pendingRevenue: number
  overdueRevenue: number
  avgInvoiceValue: number
  paidRate: number
}

export interface SubscriptionKPIs {
  total: number
  active: number
  paused: number
  cancelled: number
  expired: number
  monthlyRecurringRevenue: number
  annualRecurringRevenue: number
  avgSubscriptionValue: number
}

export interface InvoicingKPIs {
  invoices: InvoiceKPIs
  subscriptions: SubscriptionKPIs
  templates: number
  activeTemplates: number
}

// ── Labels ────────────────────────────────────────────────────────────────────
export const BILLING_CYCLE_LABELS: Record<string, string> = {
  MONTHLY:   'Mensal',
  QUARTERLY: 'Trimestral',
  YEARLY:    'Anual',
  CUSTOM:    'Personalizado',
}

export const SUBSCRIPTION_STATUS_LABELS: Record<string, string> = {
  ACTIVE:    'Activa',
  PAUSED:    'Pausada',
  CANCELLED: 'Cancelada',
  EXPIRED:   'Expirada',
}

export const TEMPLATE_TYPE_LABELS: Record<string, string> = {
  STANDARD:    'Standard',
  RECURRING:   'Recorrente',
  PROFORMA:    'Proforma',
  CREDIT_NOTE: 'Nota de Crédito',
}

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  BANK_TRANSFER: 'Transferência Bancária',
  CARD:  'Cartão',
  CASH:  'Numerário',
  OTHER: 'Outro',
}

// ── 1. KPIs Gerais ────────────────────────────────────────────────────────────
export const invoicingKPIService = {

  async getAll(): Promise<InvoicingKPIs> {
    const tenantId = await getTenantId()
    const today = new Date().toISOString().split('T')[0]

    const [invRes, subRes, tplRes] = await Promise.allSettled([
      supabase.from('invoices')
        .select('status, total, due_date')
        .eq('tenant_id', tenantId),
      supabase.from('subscriptions')
        .select('status, amount, billing_cycle')
        .eq('tenant_id', tenantId),
      supabase.from('invoice_templates')
        .select('is_active')
        .eq('tenant_id', tenantId),
    ])

    // ── Invoices KPIs ──
    const invRows = invRes.status === 'fulfilled' ? (invRes.value.data ?? []) : []
    const paid     = invRows.filter(i => i.status === 'PAID')
    const pending  = invRows.filter(i => ['SENT','CONFIRMED'].includes(i.status))
    const overdue  = invRows.filter(i => ['SENT','CONFIRMED'].includes(i.status) && i.due_date < today)
    const draft    = invRows.filter(i => i.status === 'DRAFT')
    const totalRev = paid.reduce((s, i) => s + Number(i.total || 0), 0)
    const pendRev  = pending.reduce((s, i) => s + Number(i.total || 0), 0)
    const ovdRev   = overdue.reduce((s, i) => s + Number(i.total || 0), 0)
    const nonDraft = invRows.filter(i => i.status !== 'DRAFT')

    const invoices: InvoiceKPIs = {
      totalInvoices:   invRows.length,
      paidInvoices:    paid.length,
      pendingInvoices: pending.length,
      overdueInvoices: overdue.length,
      draftInvoices:   draft.length,
      totalRevenue:    totalRev,
      pendingRevenue:  pendRev,
      overdueRevenue:  ovdRev,
      avgInvoiceValue: nonDraft.length > 0
        ? nonDraft.reduce((s, i) => s + Number(i.total || 0), 0) / nonDraft.length : 0,
      paidRate: invRows.length > 0 ? (paid.length / invRows.length) * 100 : 0,
    }

    // ── Subscriptions KPIs ──
    const subRows  = subRes.status === 'fulfilled' ? (subRes.value.data ?? []) : []
    const activeSubs = subRows.filter(s => s.status === 'ACTIVE')
    const mrr = activeSubs.reduce((sum, s) => {
      const amt = Number(s.amount || 0)
      if (s.billing_cycle === 'MONTHLY')   return sum + amt
      if (s.billing_cycle === 'QUARTERLY') return sum + amt / 3
      if (s.billing_cycle === 'YEARLY')    return sum + amt / 12
      return sum + amt
    }, 0)

    const subscriptions: SubscriptionKPIs = {
      total:     subRows.length,
      active:    activeSubs.length,
      paused:    subRows.filter(s => s.status === 'PAUSED').length,
      cancelled: subRows.filter(s => s.status === 'CANCELLED').length,
      expired:   subRows.filter(s => s.status === 'EXPIRED').length,
      monthlyRecurringRevenue: mrr,
      annualRecurringRevenue:  mrr * 12,
      avgSubscriptionValue: activeSubs.length > 0
        ? activeSubs.reduce((s, sub) => s + Number(sub.amount || 0), 0) / activeSubs.length : 0,
    }

    // ── Templates KPIs ──
    const tplRows = tplRes.status === 'fulfilled' ? (tplRes.value.data ?? []) : []
    const activeTpl = tplRows.filter(t => t.is_active).length

    return { invoices, subscriptions, templates: tplRows.length, activeTemplates: activeTpl }
  },
}

// ── 2. Subscriptions Service ──────────────────────────────────────────────────
export const subscriptionsService = {

  async list(filters?: { status?: string; search?: string }): Promise<Subscription[]> {
    const tenantId = await getTenantId()
    let query = supabase
      .from('subscriptions')
      .select('*, customers(id, name, email, nif)')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })
    if (filters?.status) query = query.eq('status', filters.status)
    const { data, error } = await query
    if (error) throw error
    return (data ?? []) as Subscription[]
  },

  async create(sub: {
    customer_id: string
    plan_name: string
    description?: string
    billing_cycle: string
    billing_day?: number
    amount: number
    tax_rate?: number
    start_date: string
    next_billing_date: string
    auto_renew?: boolean
    payment_method?: string
    notes?: string
  }): Promise<Subscription> {
    const tenantId = await getTenantId()
    const subNum = `SUB-${new Date().getFullYear()}-${String(Date.now()).slice(-6)}`
    const { data, error } = await supabase
      .from('subscriptions')
      .insert({ ...sub, tenant_id: tenantId, subscription_number: subNum, status: 'ACTIVE', currency: 'AOA' })
      .select('*, customers(id, name, email, nif)')
      .single()
    if (error) throw error
    return data as Subscription
  },

  async updateStatus(id: string, status: 'ACTIVE' | 'PAUSED' | 'CANCELLED' | 'EXPIRED', reason?: string): Promise<void> {
    const tenantId = await getTenantId()
    const updates: Record<string, unknown> = { status }
    if (status === 'CANCELLED') {
      updates.cancelled_at = new Date().toISOString()
      updates.cancellation_reason = reason ?? null
    }
    const { error } = await supabase
      .from('subscriptions')
      .update(updates)
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('subscriptions')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },

  async getBillingHistory(subscriptionId: string): Promise<SubscriptionInvoice[]> {
    const { data, error } = await supabase
      .from('subscription_invoices')
      .select('*')
      .eq('subscription_id', subscriptionId)
      .order('billing_date', { ascending: false })
    if (error) throw error
    return (data ?? []) as SubscriptionInvoice[]
  },

  // Calcular MRR de uma subscrição
  calcMRR(sub: Subscription): number {
    const amt = Number(sub.amount || 0)
    if (sub.billing_cycle === 'MONTHLY')   return amt
    if (sub.billing_cycle === 'QUARTERLY') return amt / 3
    if (sub.billing_cycle === 'YEARLY')    return amt / 12
    return amt
  },
}

// ── 3. Invoice Templates Service ─────────────────────────────────────────────
export const invoiceTemplatesService = {

  async list(filters?: { type?: string; active?: boolean }): Promise<InvoiceTemplate[]> {
    const tenantId = await getTenantId()
    let query = supabase
      .from('invoice_templates')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('is_default', { ascending: false })
    if (filters?.type) query = query.eq('template_type', filters.type)
    if (filters?.active !== undefined) query = query.eq('is_active', filters.active)
    const { data, error } = await query
    if (error) throw error
    return (data ?? []).map(t => ({
      ...t,
      default_items: Array.isArray(t.default_items) ? t.default_items : [],
    })) as InvoiceTemplate[]
  },

  async create(tpl: {
    template_name: string
    description?: string
    template_type: string
    default_items?: TemplateItem[]
    default_terms?: string
    default_notes?: string
    default_payment_terms?: number
    default_tax_rate?: number
    is_default?: boolean
  }): Promise<InvoiceTemplate> {
    const tenantId = await getTenantId()
    // Se é default, desactivar os outros
    if (tpl.is_default) {
      await supabase.from('invoice_templates')
        .update({ is_default: false })
        .eq('tenant_id', tenantId)
    }
    const { data, error } = await supabase
      .from('invoice_templates')
      .insert({ ...tpl, tenant_id: tenantId, is_active: true })
      .select()
      .single()
    if (error) throw error
    return { ...data, default_items: Array.isArray(data.default_items) ? data.default_items : [] } as InvoiceTemplate
  },

  async setDefault(id: string): Promise<void> {
    const tenantId = await getTenantId()
    await supabase.from('invoice_templates')
      .update({ is_default: false })
      .eq('tenant_id', tenantId)
    const { error } = await supabase
      .from('invoice_templates')
      .update({ is_default: true })
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },

  async toggleActive(id: string, isActive: boolean): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('invoice_templates')
      .update({ is_active: isActive })
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('invoice_templates')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ── 4. Invoice Analytics ──────────────────────────────────────────────────────
export const invoiceAnalyticsService = {

  async getMonthlyRevenue(nMonths = 6) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('invoices')
      .select('issue_date, total, status')
      .eq('tenant_id', tenantId)
      .in('status', ['PAID', 'SENT', 'CONFIRMED'])
      .order('issue_date', { ascending: true })
    if (error || !data) return []

    // Agrupar por mês
    const map = new Map<string, { month: string; paid: number; pending: number; count: number }>()
    data.forEach(inv => {
      const m = (inv.issue_date || '').slice(0, 7)
      const prev = map.get(m) ?? { month: m, paid: 0, pending: 0, count: 0 }
      const amt = Number(inv.total || 0)
      if (inv.status === 'PAID') prev.paid += amt
      else prev.pending += amt
      prev.count++
      map.set(m, prev)
    })

    const months = Array.from(map.values())
      .sort((a, b) => a.month.localeCompare(b.month))
      .slice(-nMonths)
      .map(m => ({
        ...m,
        label: new Date(m.month + '-01').toLocaleDateString('pt-PT', { month: 'short', year: '2-digit' }),
        total: m.paid + m.pending,
      }))
    return months
  },

  async getTopCustomers(limit = 5) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('invoices')
      .select('customer_name, total, status')
      .eq('tenant_id', tenantId)
      .eq('status', 'PAID')
    if (error || !data) return []
    const map = new Map<string, number>()
    data.forEach(inv => {
      const prev = map.get(inv.customer_name) ?? 0
      map.set(inv.customer_name, prev + Number(inv.total || 0))
    })
    return Array.from(map.entries())
      .map(([name, total]) => ({ name, total }))
      .sort((a, b) => b.total - a.total)
      .slice(0, limit)
  },

  async getStatusDistribution() {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('invoices')
      .select('status, total')
      .eq('tenant_id', tenantId)
    if (error || !data) return []
    const map = new Map<string, { count: number; total: number }>()
    data.forEach(inv => {
      const prev = map.get(inv.status) ?? { count: 0, total: 0 }
      map.set(inv.status, { count: prev.count + 1, total: prev.total + Number(inv.total || 0) })
    })
    const labels: Record<string, string> = {
      PAID: 'Pago', SENT: 'Enviado', DRAFT: 'Rascunho',
      CONFIRMED: 'Confirmado', CANCELLED: 'Cancelado', OVERDUE: 'Em Atraso',
    }
    return Array.from(map.entries()).map(([status, { count, total }]) => ({
      status, label: labels[status] ?? status, count, total,
    })).sort((a, b) => b.count - a.count)
  },

  async getRecentInvoices(limit = 8) {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('invoices')
      .select('id, invoice_number, customer_name, total, status, issue_date, due_date')
      .eq('tenant_id', tenantId)
      .order('issue_date', { ascending: false })
      .limit(limit)
    if (error || !data) return []
    return data
  },
}

export default {
  kpis:       invoicingKPIService,
  subs:       subscriptionsService,
  templates:  invoiceTemplatesService,
  analytics:  invoiceAnalyticsService,
}
