// =====================================================
// KWANZACONTROL — Invoicing Service Real
// Estrutura real BD:
//   customers   → tenant_id, nif, name, legal_name, email, phone, address, city,
//                 customer_type, payment_terms, is_active
//   invoices    → tenant_id, invoice_number, series, customer_id, customer_name,
//                 customer_nif, issue_date, due_date, payment_date, subtotal,
//                 iva_amount, discount_amount, total, currency, status (DRAFT/SENT/PAID/OVERDUE/CANCELLED),
//                 payment_method, notes, agt_status, created_by
//   invoice_items → invoice_id, line_number, product_code, description, quantity,
//                   unit_price, discount_percent, discount_amount, subtotal,
//                   iva_rate, iva_percent, iva_amount, total
// =====================================================
import { supabase } from '@/integrations/supabase/client'

// ─── Tipos ────────────────────────────────────────────────────────────────────

export type InvoiceStatus = 'DRAFT' | 'SENT' | 'PAID' | 'OVERDUE' | 'CANCELLED'
export type CustomerType  = 'BUSINESS' | 'INDIVIDUAL' | 'PUBLIC'
export type IvaRate       = 'normal' | 'reduced' | 'exempt'

export interface Customer {
  id: string
  tenant_id: string
  nif?: string
  name: string
  legal_name?: string
  email?: string
  phone?: string
  address?: string
  city?: string
  postal_code?: string
  country: string
  customer_type: CustomerType
  payment_terms: number
  credit_limit?: number
  notes?: string
  is_active: boolean
  created_at?: string
  updated_at?: string
}

export interface InvoiceItem {
  id?: string
  invoice_id?: string
  line_number?: number
  product_code?: string
  description: string
  quantity: number
  unit_price: number
  discount_percent?: number
  discount_amount?: number
  subtotal?: number
  iva_rate: IvaRate
  iva_percent: number
  iva_amount?: number
  total?: number
}

export interface Invoice {
  id: string
  tenant_id: string
  invoice_number: string
  series: string
  customer_id: string
  customer_name: string
  customer_nif?: string
  customer_address?: string
  issue_date: string
  due_date: string
  payment_date?: string
  subtotal: number
  iva_amount: number
  discount_amount?: number
  total: number
  currency: string
  status: InvoiceStatus
  payment_method?: string
  payment_reference?: string
  notes?: string
  internal_notes?: string
  agt_status?: string
  agt_validation_code?: string
  pdf_url?: string
  created_by?: string
  created_at?: string
  updated_at?: string
  // join
  items?: InvoiceItem[]
  customer?: Customer
}

export interface InvoiceStats {
  total: number
  draft: number
  sent: number
  paid: number
  overdue: number
  cancelled: number
  total_revenue: number       // soma PAID
  total_pending: number       // soma SENT + DRAFT
  total_overdue_amount: number
}

// ─── Taxas IVA Angola ─────────────────────────────────────────────────────────
export const IVA_RATES: Record<IvaRate, number> = {
  normal:   14,    // taxa geral
  reduced:   5,    // taxa reduzida
  exempt:    0,    // isento
}

export const IVA_LABELS: Record<IvaRate, string> = {
  normal:  'Normal (14%)',
  reduced: 'Reduzida (5%)',
  exempt:  'Isento (0%)',
}

// ─── Métodos de pagamento ─────────────────────────────────────────────────────
export const PAYMENT_METHODS = [
  { value: 'TRANSFER', label: 'Transferência Bancária' },
  { value: 'CASH',     label: 'Numerário' },
  { value: 'CHEQUE',   label: 'Cheque' },
  { value: 'CARD',     label: 'TPA / Cartão' },
  { value: 'MOBILE',   label: 'Pagamento Móvel' },
]

// ─── Calcular item ────────────────────────────────────────────────────────────
export function calcItem(item: Pick<InvoiceItem, 'quantity' | 'unit_price' | 'discount_percent' | 'iva_percent'>) {
  const gross    = item.quantity * item.unit_price
  const discAmt  = gross * ((item.discount_percent || 0) / 100)
  const subtotal = gross - discAmt
  const ivaAmt   = subtotal * ((item.iva_percent || 0) / 100)
  const total    = subtotal + ivaAmt
  return {
    subtotal:        Math.round(subtotal * 100) / 100,
    discount_amount: Math.round(discAmt  * 100) / 100,
    iva_amount:      Math.round(ivaAmt   * 100) / 100,
    total:           Math.round(total    * 100) / 100,
  }
}

// ─── Calcular totais da fatura ────────────────────────────────────────────────
export function calcInvoiceTotals(items: InvoiceItem[]) {
  let subtotal = 0, ivaAmount = 0, total = 0
  for (const it of items) {
    const c = calcItem({
      quantity:         it.quantity,
      unit_price:       it.unit_price,
      discount_percent: it.discount_percent || 0,
      iva_percent:      it.iva_percent,
    })
    subtotal  += c.subtotal
    ivaAmount += c.iva_amount
    total     += c.total
  }
  return {
    subtotal:   Math.round(subtotal  * 100) / 100,
    iva_amount: Math.round(ivaAmount * 100) / 100,
    total:      Math.round(total     * 100) / 100,
  }
}

// ─── Helper: tenant/user ──────────────────────────────────────────────────────
async function getTenantId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null
  const { data } = await supabase.from('users').select('tenant_id').eq('id', user.id).maybeSingle()
  if (data?.tenant_id) return data.tenant_id
  const { data: t } = await supabase.from('tenants').select('id').limit(1).maybeSingle()
  return t?.id || null
}

async function getUserId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  return user?.id || null
}

// ═══════════════════════════════════════════════════════════════════════════════
// CUSTOMERS SERVICE
// ═══════════════════════════════════════════════════════════════════════════════
export const customerService = {
  async getAll(activeOnly = false): Promise<Customer[]> {
    const tenantId = await getTenantId()
    if (!tenantId) return []
    let q = supabase
      .from('customers')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('name')
    if (activeOnly) q = q.eq('is_active', true)
    const { data, error } = await q
    if (error) { console.error('customers:', error); return [] }
    return data || []
  },

  async getById(id: string): Promise<Customer | null> {
    const { data, error } = await supabase
      .from('customers').select('*').eq('id', id).maybeSingle()
    if (error) { console.error(error); return null }
    return data
  },

  async create(c: Omit<Customer, 'id' | 'tenant_id' | 'created_at' | 'updated_at'>): Promise<Customer> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')
    const { data, error } = await supabase
      .from('customers').insert({ ...c, tenant_id: tenantId }).select().single()
    if (error) throw error
    return data
  },

  async update(id: string, c: Partial<Omit<Customer, 'id' | 'tenant_id'>>): Promise<Customer> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')
    const { data, error } = await supabase
      .from('customers')
      .update({ ...c, updated_at: new Date().toISOString() })
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return data
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')
    const { error } = await supabase
      .from('customers').delete()
      .eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// INVOICE SERVICE
// ═══════════════════════════════════════════════════════════════════════════════
export const invoiceService = {

  async getAll(filters?: { status?: InvoiceStatus; customerId?: string; search?: string }): Promise<Invoice[]> {
    const tenantId = await getTenantId()
    if (!tenantId) return []

    let q = supabase
      .from('invoices')
      .select('*, items:invoice_items(*)')
      .eq('tenant_id', tenantId)
      .order('issue_date', { ascending: false })
      .order('created_at', { ascending: false })

    if (filters?.status)     q = q.eq('status', filters.status)
    if (filters?.customerId) q = q.eq('customer_id', filters.customerId)
    if (filters?.search)     q = q.ilike('customer_name', `%${filters.search}%`)

    const { data, error } = await q
    if (error) { console.error('invoices:', error); return [] }
    return (data || []) as Invoice[]
  },

  async getById(id: string): Promise<Invoice | null> {
    const { data, error } = await supabase
      .from('invoices')
      .select('*, items:invoice_items(*), customer:customers(*)')
      .eq('id', id)
      .maybeSingle()
    if (error) { console.error(error); return null }
    return data as Invoice | null
  },

  async create(
    inv: {
      customer_id: string
      customer_name: string
      customer_nif?: string
      customer_address?: string
      issue_date: string
      due_date: string
      series?: string
      currency?: string
      payment_method?: string
      notes?: string
      internal_notes?: string
    },
    items: InvoiceItem[],
  ): Promise<Invoice> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    if (!tenantId) throw new Error('Não autenticado')

    const totals = calcInvoiceTotals(items)

    // Inserir fatura (número gerado por trigger)
    const { data: newInv, error: invErr } = await supabase
      .from('invoices')
      .insert({
        tenant_id:        tenantId,
        invoice_number:   '',   // preenchido pelo trigger generate_invoice_number
        series:           inv.series || 'FT',
        customer_id:      inv.customer_id,
        customer_name:    inv.customer_name,
        customer_nif:     inv.customer_nif,
        customer_address: inv.customer_address,
        issue_date:       inv.issue_date,
        due_date:         inv.due_date,
        subtotal:         totals.subtotal,
        iva_amount:       totals.iva_amount,
        total:            totals.total,
        currency:         inv.currency || 'AOA',
        status:           'DRAFT',
        payment_method:   inv.payment_method,
        notes:            inv.notes,
        internal_notes:   inv.internal_notes,
        created_by:       userId || undefined,
      })
      .select()
      .single()
    if (invErr) throw invErr

    // Inserir itens
    const itemRows = items.map((it, idx) => {
      const c = calcItem({
        quantity: it.quantity, unit_price: it.unit_price,
        discount_percent: it.discount_percent || 0, iva_percent: it.iva_percent,
      })
      return {
        invoice_id:      newInv.id,
        line_number:     idx + 1,
        product_code:    it.product_code,
        description:     it.description,
        quantity:        it.quantity,
        unit_price:      it.unit_price,
        discount_percent: it.discount_percent || 0,
        discount_amount: c.discount_amount,
        subtotal:        c.subtotal,
        iva_rate:        it.iva_rate,
        iva_percent:     it.iva_percent,
        iva_amount:      c.iva_amount,
        total:           c.total,
      }
    })
    const { error: itemErr } = await supabase.from('invoice_items').insert(itemRows)
    if (itemErr) throw itemErr

    // Retornar com itens
    return (await this.getById(newInv.id)) || newInv as Invoice
  },

  async update(
    id: string,
    inv: Partial<{
      customer_id: string; customer_name: string; customer_nif: string
      customer_address: string; issue_date: string; due_date: string
      series: string; currency: string; payment_method: string
      notes: string; internal_notes: string; status: InvoiceStatus
      payment_date: string; payment_reference: string
    }>,
    items?: InvoiceItem[],
  ): Promise<Invoice> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')

    // Recalcular totais se itens fornecidos
    let updates: Record<string, unknown> = { ...inv, updated_at: new Date().toISOString() }
    if (items && items.length > 0) {
      const totals = calcInvoiceTotals(items)
      updates = { ...updates, ...totals }
    }

    const { data, error } = await supabase
      .from('invoices').update(updates)
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error

    // Actualizar itens
    if (items) {
      await supabase.from('invoice_items').delete().eq('invoice_id', id)
      const itemRows = items.map((it, idx) => {
        const c = calcItem({
          quantity: it.quantity, unit_price: it.unit_price,
          discount_percent: it.discount_percent || 0, iva_percent: it.iva_percent,
        })
        return {
          invoice_id: id, line_number: idx + 1,
          product_code: it.product_code, description: it.description,
          quantity: it.quantity, unit_price: it.unit_price,
          discount_percent: it.discount_percent || 0,
          discount_amount: c.discount_amount, subtotal: c.subtotal,
          iva_rate: it.iva_rate, iva_percent: it.iva_percent,
          iva_amount: c.iva_amount, total: c.total,
        }
      })
      await supabase.from('invoice_items').insert(itemRows)
    }

    return data as Invoice
  },

  async updateStatus(id: string, status: InvoiceStatus, paymentDate?: string): Promise<Invoice> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')
    const upd: Record<string, unknown> = { status, updated_at: new Date().toISOString() }
    if (paymentDate) upd.payment_date = paymentDate
    const { data, error } = await supabase
      .from('invoices').update(upd)
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return data as Invoice
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    if (!tenantId) throw new Error('Não autenticado')
    // Eliminar itens primeiro
    await supabase.from('invoice_items').delete().eq('invoice_id', id)
    const { error } = await supabase
      .from('invoices').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },

  async getStats(): Promise<InvoiceStats> {
    const invoices = await this.getAll()
    return {
      total:               invoices.length,
      draft:               invoices.filter((i: Invoice) => i.status === 'DRAFT').length,
      sent:                invoices.filter((i: Invoice) => i.status === 'SENT').length,
      paid:                invoices.filter((i: Invoice) => i.status === 'PAID').length,
      overdue:             invoices.filter((i: Invoice) => i.status === 'OVERDUE').length,
      cancelled:           invoices.filter((i: Invoice) => i.status === 'CANCELLED').length,
      total_revenue:       invoices.filter((i: Invoice) => i.status === 'PAID').reduce((s: number, i: Invoice) => s + Number(i.total), 0),
      total_pending:       invoices.filter((i: Invoice) => ['SENT', 'DRAFT'].includes(i.status)).reduce((s: number, i: Invoice) => s + Number(i.total), 0),
      total_overdue_amount:invoices.filter((i: Invoice) => i.status === 'OVERDUE').reduce((s: number, i: Invoice) => s + Number(i.total), 0),
    }
  },

  /** Exportar CSV */
  exportCSV(invoices: Invoice[]): void {
    const headers = ['Nº Fatura', 'Cliente', 'NIF Cliente', 'Emissão', 'Vencimento', 'Subtotal', 'IVA', 'Total', 'Estado', 'Pagamento']
    const rows = invoices.map(i => [
      i.invoice_number,
      `"${i.customer_name}"`,
      i.customer_nif || '-',
      new Date(i.issue_date).toLocaleDateString('pt-AO'),
      new Date(i.due_date).toLocaleDateString('pt-AO'),
      Number(i.subtotal).toFixed(2),
      Number(i.iva_amount).toFixed(2),
      Number(i.total).toFixed(2),
      i.status,
      i.payment_date ? new Date(i.payment_date).toLocaleDateString('pt-AO') : '-',
    ])
    const csv = [headers.join(';'), ...rows.map(r => r.join(';'))].join('\n')
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `faturas_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(url)
  },

  /** Imprimir fatura como HTML */
  printInvoice(invoice: Invoice): void {
    const fmt = (v: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 2 }).format(v)
    const items = invoice.items || []
    const html = `
      <!DOCTYPE html><html><head><meta charset="utf-8">
      <title>${invoice.invoice_number}</title>
      <style>
        body{font-family:Arial,sans-serif;font-size:12px;margin:40px;color:#222}
        h1{font-size:20px;color:#1a3a5c}
        .header{display:flex;justify-content:space-between;margin-bottom:24px}
        .badge{display:inline-block;padding:4px 10px;border-radius:4px;font-size:11px;font-weight:bold}
        .paid{background:#dcfce7;color:#166534} .sent{background:#dbeafe;color:#1e40af}
        .overdue{background:#fee2e2;color:#991b1b} .draft{background:#f3f4f6;color:#374151}
        table{width:100%;border-collapse:collapse;margin-top:16px}
        th{background:#f3f4f6;padding:8px;text-align:left;font-size:11px;text-transform:uppercase}
        td{padding:8px;border-bottom:1px solid #e5e7eb}
        .totals{margin-top:16px;text-align:right}
        .totals td{padding:4px 8px;font-size:13px}
        .grand-total td{font-size:15px;font-weight:bold;border-top:2px solid #1a3a5c}
        @media print{button{display:none}}
      </style></head><body>
      <div class="header">
        <div>
          <h1>${invoice.invoice_number}</h1>
          <p><strong>Cliente:</strong> ${invoice.customer_name}</p>
          ${invoice.customer_nif ? `<p><strong>NIF:</strong> ${invoice.customer_nif}</p>` : ''}
          ${invoice.customer_address ? `<p><strong>Morada:</strong> ${invoice.customer_address}</p>` : ''}
        </div>
        <div style="text-align:right">
          <span class="badge ${invoice.status.toLowerCase()}">${invoice.status}</span>
          <p><strong>Emissão:</strong> ${new Date(invoice.issue_date).toLocaleDateString('pt-AO')}</p>
          <p><strong>Vencimento:</strong> ${new Date(invoice.due_date).toLocaleDateString('pt-AO')}</p>
          ${invoice.payment_date ? `<p><strong>Pago em:</strong> ${new Date(invoice.payment_date).toLocaleDateString('pt-AO')}</p>` : ''}
        </div>
      </div>
      <table>
        <thead><tr><th>#</th><th>Descrição</th><th>Qtd</th><th>Preço Unit.</th><th>Desc.%</th><th>IVA%</th><th>Total</th></tr></thead>
        <tbody>
          ${items.map((it, i) => `<tr>
            <td>${i+1}</td>
            <td>${it.description}</td>
            <td>${Number(it.quantity).toFixed(2)}</td>
            <td>${fmt(Number(it.unit_price))}</td>
            <td>${Number(it.discount_percent || 0).toFixed(0)}%</td>
            <td>${Number(it.iva_percent).toFixed(0)}%</td>
            <td>${fmt(Number(it.total))}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      <table class="totals"><tbody>
        <tr><td>Subtotal:</td><td>${fmt(Number(invoice.subtotal))}</td></tr>
        <tr><td>IVA:</td><td>${fmt(Number(invoice.iva_amount))}</td></tr>
        ${Number(invoice.discount_amount) > 0 ? `<tr><td>Desconto:</td><td>- ${fmt(Number(invoice.discount_amount))}</td></tr>` : ''}
        <tr class="grand-total"><td><strong>TOTAL A PAGAR:</strong></td><td><strong>${fmt(Number(invoice.total))}</strong></td></tr>
      </tbody></table>
      ${invoice.notes ? `<p style="margin-top:20px"><strong>Notas:</strong> ${invoice.notes}</p>` : ''}
      <p style="margin-top:24px;font-size:10px;color:#6b7280">Documento emitido por KwanzaControl — ${new Date().toLocaleDateString('pt-AO')}</p>
      </body></html>
    `
    const w = window.open('', '_blank')
    if (w) { w.document.write(html); w.document.close(); setTimeout(() => w.print(), 500) }
  },
}
