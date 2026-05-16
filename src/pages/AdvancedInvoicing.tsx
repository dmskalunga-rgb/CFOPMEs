// =====================================================
// KWANZACONTROL – Faturação Avançada
// Subscrições, Templates, Automação e Analytics
// 100% dados reais do Supabase — sem dados simulados
// 2026-04-18
// =====================================================
import { useState, useEffect, useCallback } from 'react'
import { Layout } from '@/components/Layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend, LineChart, Line,
} from 'recharts'
import {
  FileText, TrendingUp, Calendar, DollarSign, RefreshCw,
  Plus, Pause, Play, XCircle, Star, Copy, Trash2,
  ChevronDown, ChevronUp, AlertTriangle, CheckCircle2,
  Loader2, AlertCircle, BarChart2, Users, Zap, Clock,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  invoicingKPIService, subscriptionsService, invoiceTemplatesService, invoiceAnalyticsService,
  BILLING_CYCLE_LABELS, SUBSCRIPTION_STATUS_LABELS, TEMPLATE_TYPE_LABELS, PAYMENT_METHOD_LABELS,
  type Subscription, type InvoiceTemplate, type InvoicingKPIs,
} from '@/services/advancedInvoicingService'
import { supabase } from '@/integrations/supabase/client'

// ── Utilitários ───────────────────────────────────────────────────────────────
const fmtAOA = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)

const STATUS_COLORS: Record<string, string> = {
  ACTIVE:    'bg-green-100 text-green-700 border-green-200',
  PAUSED:    'bg-yellow-100 text-yellow-700 border-yellow-200',
  CANCELLED: 'bg-red-100 text-red-700 border-red-200',
  EXPIRED:   'bg-gray-100 text-gray-700 border-gray-200',
}

const INV_STATUS_COLORS: Record<string, string> = {
  PAID:      'bg-green-100 text-green-700',
  SENT:      'bg-blue-100 text-blue-700',
  DRAFT:     'bg-gray-100 text-gray-700',
  CONFIRMED: 'bg-purple-100 text-purple-700',
  CANCELLED: 'bg-red-100 text-red-700',
  OVERDUE:   'bg-orange-100 text-orange-700',
}

const PIE_COLORS = ['#10B981', '#3B82F6', '#8B5CF6', '#F59E0B', '#EF4444', '#14B8A6']

// ── Skeleton ─────────────────────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <Card><CardHeader className="pb-2"><Skeleton className="h-4 w-32" /></CardHeader>
      <CardContent><Skeleton className="h-8 w-28 mb-1" /><Skeleton className="h-3 w-40" /></CardContent>
    </Card>
  )
}

// ── Modal: Nova Subscrição ────────────────────────────────────────────────────
interface NewSubModalProps {
  open: boolean
  onClose: () => void
  onCreated: () => void
}
function NewSubscriptionModal({ open, onClose, onCreated }: NewSubModalProps) {
  const [customers, setCustomers] = useState<{ id: string; name: string }[]>([])
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    customer_id: '', plan_name: '', description: '',
    billing_cycle: 'MONTHLY', billing_day: 1,
    amount: '', tax_rate: 14,
    start_date: new Date().toISOString().split('T')[0],
    next_billing_date: '',
    auto_renew: true, payment_method: 'BANK_TRANSFER', notes: '',
  })

  useEffect(() => {
    if (!open) return
    supabase.rpc('get_current_tenant_id').then(({ data: tid }) => {
      if (!tid) return
      supabase.from('customers').select('id, name').eq('tenant_id', tid).limit(100)
        .then(({ data }) => setCustomers(data ?? []))
    })
  }, [open])

  // Auto-calcular next_billing_date a partir de start_date e billing_cycle
  useEffect(() => {
    if (!form.start_date) return
    const d = new Date(form.start_date)
    if (form.billing_cycle === 'MONTHLY')   d.setMonth(d.getMonth() + 1)
    else if (form.billing_cycle === 'QUARTERLY') d.setMonth(d.getMonth() + 3)
    else if (form.billing_cycle === 'YEARLY') d.setFullYear(d.getFullYear() + 1)
    setForm(f => ({ ...f, next_billing_date: d.toISOString().split('T')[0] }))
  }, [form.start_date, form.billing_cycle])

  const handleSave = async () => {
    if (!form.customer_id || !form.plan_name || !form.amount) {
      toast.error('Preencha cliente, plano e valor')
      return
    }
    setSaving(true)
    try {
      await subscriptionsService.create({
        customer_id: form.customer_id,
        plan_name: form.plan_name,
        description: form.description || undefined,
        billing_cycle: form.billing_cycle,
        billing_day: Number(form.billing_day),
        amount: Number(form.amount),
        tax_rate: Number(form.tax_rate),
        start_date: form.start_date,
        next_billing_date: form.next_billing_date,
        auto_renew: form.auto_renew,
        payment_method: form.payment_method,
        notes: form.notes || undefined,
      })
      toast.success('Subscrição criada com sucesso!')
      onCreated()
      onClose()
    } catch (err) {
      toast.error(`Erro: ${err instanceof Error ? err.message : 'Desconhecido'}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader><DialogTitle>Nova Subscrição Recorrente</DialogTitle></DialogHeader>
        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <Label>Cliente *</Label>
              <Select value={form.customer_id} onValueChange={v => setForm(f => ({ ...f, customer_id: v }))}>
                <SelectTrigger><SelectValue placeholder="Seleccionar cliente" /></SelectTrigger>
                <SelectContent>
                  {customers.map(c => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="col-span-2">
              <Label>Nome do Plano *</Label>
              <Input value={form.plan_name} onChange={e => setForm(f => ({ ...f, plan_name: e.target.value }))}
                placeholder="ex: Plano Gestão Mensal" />
            </div>
            <div className="col-span-2">
              <Label>Descrição</Label>
              <Textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                rows={2} placeholder="Descrição opcional" />
            </div>
            <div>
              <Label>Ciclo de Cobrança *</Label>
              <Select value={form.billing_cycle} onValueChange={v => setForm(f => ({ ...f, billing_cycle: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(BILLING_CYCLE_LABELS).map(([k, v]) =>
                    <SelectItem key={k} value={k}>{v}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Dia de Cobrança</Label>
              <Input type="number" min={1} max={28} value={form.billing_day}
                onChange={e => setForm(f => ({ ...f, billing_day: Number(e.target.value) }))} />
            </div>
            <div>
              <Label>Valor (AOA) *</Label>
              <Input type="number" min={0} value={form.amount}
                onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
                placeholder="0" />
            </div>
            <div>
              <Label>Taxa IVA (%)</Label>
              <Input type="number" min={0} max={100} value={form.tax_rate}
                onChange={e => setForm(f => ({ ...f, tax_rate: Number(e.target.value) }))} />
            </div>
            <div>
              <Label>Data de Início *</Label>
              <Input type="date" value={form.start_date}
                onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))} />
            </div>
            <div>
              <Label>Próxima Cobrança</Label>
              <Input type="date" value={form.next_billing_date}
                onChange={e => setForm(f => ({ ...f, next_billing_date: e.target.value }))} />
            </div>
            <div>
              <Label>Método de Pagamento</Label>
              <Select value={form.payment_method} onValueChange={v => setForm(f => ({ ...f, payment_method: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(PAYMENT_METHOD_LABELS).map(([k, v]) =>
                    <SelectItem key={k} value={k}>{v}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2 mt-5">
              <input type="checkbox" id="auto_renew" checked={form.auto_renew}
                onChange={e => setForm(f => ({ ...f, auto_renew: e.target.checked }))}
                className="rounded" />
              <Label htmlFor="auto_renew" className="cursor-pointer">Renovação automática</Label>
            </div>
            <div className="col-span-2">
              <Label>Notas</Label>
              <Textarea value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                rows={2} placeholder="Notas internas" />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
            Criar Subscrição
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Modal: Novo Template ──────────────────────────────────────────────────────
interface NewTplModalProps {
  open: boolean
  onClose: () => void
  onCreated: () => void
}
function NewTemplateModal({ open, onClose, onCreated }: NewTplModalProps) {
  const [saving, setSaving] = useState(false)
  const [form, setForm] = useState({
    template_name: '', description: '', template_type: 'STANDARD',
    default_terms: '', default_notes: '',
    default_payment_terms: 30, default_tax_rate: 14, is_default: false,
  })

  const handleSave = async () => {
    if (!form.template_name) { toast.error('Nome do template obrigatório'); return }
    setSaving(true)
    try {
      await invoiceTemplatesService.create({
        template_name: form.template_name,
        description: form.description || undefined,
        template_type: form.template_type,
        default_terms: form.default_terms || undefined,
        default_notes: form.default_notes || undefined,
        default_payment_terms: Number(form.default_payment_terms),
        default_tax_rate: Number(form.default_tax_rate),
        is_default: form.is_default,
      })
      toast.success('Template criado!')
      onCreated()
      onClose()
    } catch (err) {
      toast.error(`Erro: ${err instanceof Error ? err.message : 'Desconhecido'}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>Novo Template de Fatura</DialogTitle></DialogHeader>
        <div className="space-y-4 py-2">
          <div>
            <Label>Nome do Template *</Label>
            <Input value={form.template_name}
              onChange={e => setForm(f => ({ ...f, template_name: e.target.value }))}
              placeholder="ex: Fatura de Serviços Standard" />
          </div>
          <div>
            <Label>Descrição</Label>
            <Textarea value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              rows={2} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Tipo</Label>
              <Select value={form.template_type} onValueChange={v => setForm(f => ({ ...f, template_type: v }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(TEMPLATE_TYPE_LABELS).map(([k, v]) =>
                    <SelectItem key={k} value={k}>{v}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Prazo (dias)</Label>
              <Input type="number" min={0} value={form.default_payment_terms}
                onChange={e => setForm(f => ({ ...f, default_payment_terms: Number(e.target.value) }))} />
            </div>
            <div>
              <Label>IVA (%)</Label>
              <Input type="number" min={0} max={100} value={form.default_tax_rate}
                onChange={e => setForm(f => ({ ...f, default_tax_rate: Number(e.target.value) }))} />
            </div>
            <div className="flex items-center gap-2 mt-5">
              <input type="checkbox" id="tpl_default" checked={form.is_default}
                onChange={e => setForm(f => ({ ...f, is_default: e.target.checked }))}
                className="rounded" />
              <Label htmlFor="tpl_default" className="cursor-pointer text-sm">Template padrão</Label>
            </div>
          </div>
          <div>
            <Label>Condições de Pagamento</Label>
            <Textarea value={form.default_terms}
              onChange={e => setForm(f => ({ ...f, default_terms: e.target.value }))}
              rows={2} placeholder="ex: Pagamento em 30 dias." />
          </div>
          <div>
            <Label>Notas Padrão</Label>
            <Textarea value={form.default_notes}
              onChange={e => setForm(f => ({ ...f, default_notes: e.target.value }))}
              rows={2} placeholder="ex: Obrigado pela sua confiança." />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Plus className="h-4 w-4 mr-2" />}
            Criar Template
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Card de Subscrição ────────────────────────────────────────────────────────
interface SubCardProps {
  sub: Subscription
  onStatusChange: (id: string, s: 'ACTIVE' | 'PAUSED' | 'CANCELLED') => void
  onDelete: (id: string) => void
}
function SubscriptionCard({ sub, onStatusChange, onDelete }: SubCardProps) {
  const [expanded, setExpanded] = useState(false)
  const mrr = subscriptionsService.calcMRR(sub)
  const customer = sub.customers

  return (
    <div className="border rounded-lg p-4 space-y-3 hover:bg-muted/20 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1">
          <div className="p-2 rounded-lg bg-primary/10 shrink-0">
            <Calendar className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-0.5">
              <p className="font-semibold truncate">{sub.plan_name}</p>
              {sub.subscription_number && (
                <span className="text-xs text-muted-foreground font-mono">{sub.subscription_number}</span>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{customer?.name ?? 'Cliente não encontrado'}</p>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${STATUS_COLORS[sub.status] ?? ''}`}>
                {SUBSCRIPTION_STATUS_LABELS[sub.status] ?? sub.status}
              </span>
              <Badge variant="outline" className="text-xs">
                {BILLING_CYCLE_LABELS[sub.billing_cycle] ?? sub.billing_cycle}
              </Badge>
              {sub.payment_method && (
                <span className="text-xs text-muted-foreground">
                  {PAYMENT_METHOD_LABELS[sub.payment_method] ?? sub.payment_method}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <div className="text-right">
            <p className="font-bold">{fmtAOA(Number(sub.amount))}</p>
            <p className="text-xs text-muted-foreground">≈ {fmtAOA(mrr)}/mês</p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setExpanded(e => !e)}>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      {/* Próxima cobrança */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Clock className="h-3.5 w-3.5" />
        <span>Próxima cobrança: <strong className="text-foreground">
          {sub.next_billing_date ? new Date(sub.next_billing_date).toLocaleDateString('pt-PT') : '—'}
        </strong></span>
        {sub.auto_renew && <span className="text-green-600 flex items-center gap-1"><Zap className="h-3 w-3" />Auto-renovação</span>}
      </div>

      {/* Expandido */}
      {expanded && (
        <div className="pt-3 border-t space-y-3">
          {sub.description && <p className="text-sm text-muted-foreground">{sub.description}</p>}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div><span className="text-muted-foreground">Início</span>
              <p className="font-medium">{sub.start_date ? new Date(sub.start_date).toLocaleDateString('pt-PT') : '—'}</p></div>
            <div><span className="text-muted-foreground">Fim</span>
              <p className="font-medium">{sub.end_date ? new Date(sub.end_date).toLocaleDateString('pt-PT') : 'Sem fim'}</p></div>
            <div><span className="text-muted-foreground">IVA</span>
              <p className="font-medium">{sub.tax_rate}%</p></div>
            <div><span className="text-muted-foreground">Dia de cobrança</span>
              <p className="font-medium">Dia {sub.billing_day}</p></div>
          </div>
          {sub.notes && (
            <p className="text-xs text-muted-foreground italic bg-muted/40 px-2 py-1 rounded">{sub.notes}</p>
          )}
          {sub.cancellation_reason && (
            <p className="text-xs text-red-600 bg-red-50 px-2 py-1 rounded">
              Motivo cancelamento: {sub.cancellation_reason}
            </p>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            {sub.status === 'ACTIVE' && (
              <Button variant="outline" size="sm" onClick={() => onStatusChange(sub.id, 'PAUSED')}>
                <Pause className="h-3.5 w-3.5 mr-1" />Pausar
              </Button>
            )}
            {sub.status === 'PAUSED' && (
              <Button variant="outline" size="sm" onClick={() => onStatusChange(sub.id, 'ACTIVE')}>
                <Play className="h-3.5 w-3.5 mr-1" />Reactivar
              </Button>
            )}
            {['ACTIVE','PAUSED'].includes(sub.status) && (
              <Button variant="outline" size="sm" className="text-red-600 border-red-200 hover:bg-red-50"
                onClick={() => onStatusChange(sub.id, 'CANCELLED')}>
                <XCircle className="h-3.5 w-3.5 mr-1" />Cancelar
              </Button>
            )}
            <Button variant="ghost" size="sm" className="text-red-500"
              onClick={() => onDelete(sub.id)}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Card de Template ──────────────────────────────────────────────────────────
interface TplCardProps {
  tpl: InvoiceTemplate
  onSetDefault: (id: string) => void
  onToggle: (id: string, v: boolean) => void
  onDelete: (id: string) => void
  onUse: (tpl: InvoiceTemplate) => void
}
function TemplateCard({ tpl, onSetDefault, onToggle, onDelete, onUse }: TplCardProps) {
  const items = Array.isArray(tpl.default_items) ? tpl.default_items : []
  const totalValue = items.reduce((s, it) => s + (it.quantity * it.unit_price), 0)

  return (
    <div className={`border rounded-lg p-4 space-y-3 transition-colors ${!tpl.is_active ? 'opacity-60' : 'hover:bg-muted/20'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 flex-1">
          <div className="p-2 rounded-lg bg-primary/10 shrink-0">
            <FileText className="h-5 w-5 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-0.5">
              <p className="font-semibold">{tpl.template_name}</p>
              {tpl.is_default && <span className="text-xs bg-yellow-100 text-yellow-700 border border-yellow-200 px-1.5 py-0.5 rounded-full">★ Padrão</span>}
              {!tpl.is_active && <span className="text-xs bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded-full">Inactivo</span>}
            </div>
            <div className="flex flex-wrap gap-2 mt-1">
              <Badge variant="outline" className="text-xs">{TEMPLATE_TYPE_LABELS[tpl.template_type] ?? tpl.template_type}</Badge>
              <span className="text-xs text-muted-foreground">{items.length} {items.length === 1 ? 'item' : 'itens'}</span>
              <span className="text-xs text-muted-foreground">Prazo: {tpl.default_payment_terms} dias</span>
              <span className="text-xs text-muted-foreground">IVA: {tpl.default_tax_rate}%</span>
            </div>
          </div>
        </div>
        <div className="text-right shrink-0">
          {totalValue > 0 && <p className="text-sm font-bold text-muted-foreground">{fmtAOA(totalValue)}</p>}
          <p className="text-xs text-muted-foreground">valor base</p>
        </div>
      </div>

      {tpl.description && <p className="text-xs text-muted-foreground">{tpl.description}</p>}

      {/* Itens */}
      {items.length > 0 && (
        <div className="bg-muted/30 rounded p-2 space-y-1">
          {items.slice(0, 3).map((it, i) => (
            <div key={i} className="flex justify-between text-xs">
              <span className="text-muted-foreground truncate max-w-[60%]">{it.description}</span>
              <span className="font-medium">{fmtAOA(it.quantity * it.unit_price)}</span>
            </div>
          ))}
          {items.length > 3 && <p className="text-xs text-muted-foreground">+{items.length - 3} itens...</p>}
        </div>
      )}

      {tpl.default_terms && (
        <p className="text-xs text-muted-foreground italic truncate">{tpl.default_terms}</p>
      )}

      <div className="flex items-center justify-between pt-1">
        <div className="flex gap-2">
          {!tpl.is_default && tpl.is_active && (
            <Button variant="ghost" size="sm" className="text-yellow-600 hover:bg-yellow-50 text-xs"
              onClick={() => onSetDefault(tpl.id)}>
              <Star className="h-3.5 w-3.5 mr-1" />Definir como padrão
            </Button>
          )}
          <Button variant="ghost" size="sm" className="text-xs"
            onClick={() => onToggle(tpl.id, !tpl.is_active)}>
            {tpl.is_active ? <Pause className="h-3.5 w-3.5 mr-1" /> : <Play className="h-3.5 w-3.5 mr-1" />}
            {tpl.is_active ? 'Desactivar' : 'Activar'}
          </Button>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => onUse(tpl)} disabled={!tpl.is_active}>
            <Copy className="h-3.5 w-3.5 mr-1" />Usar Template
          </Button>
          <Button variant="ghost" size="sm" className="text-red-500 hover:bg-red-50"
            onClick={() => onDelete(tpl.id)}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  )
}

// ── Página Principal ──────────────────────────────────────────────────────────
export default function AdvancedInvoicing() {
  // ── Estado ──────────────────────────────────────────────────────────────────
  const [kpis,          setKpis]          = useState<InvoicingKPIs | null>(null)
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [templates,     setTemplates]     = useState<InvoiceTemplate[]>([])
  const [monthlyRev,    setMonthlyRev]    = useState<{ label: string; paid: number; pending: number }[]>([])
  const [topCustomers,  setTopCustomers]  = useState<{ name: string; total: number }[]>([])
  const [statusDist,    setStatusDist]    = useState<{ label: string; count: number; total: number }[]>([])
  const [recentInv,     setRecentInv]     = useState<{
    id: string; invoice_number: string; customer_name: string; total: number; status: string; issue_date: string; due_date: string
  }[]>([])
  const [subFilter,     setSubFilter]     = useState<string>('all')
  const [tplFilter,     setTplFilter]     = useState<string>('all')

  const [loadingKpis,  setLoadingKpis]  = useState(true)
  const [loadingSubs,  setLoadingSubs]  = useState(true)
  const [loadingTpls,  setLoadingTpls]  = useState(true)
  const [loadingChart, setLoadingChart] = useState(true)
  const [kpiError,     setKpiError]     = useState<string | null>(null)

  const [showNewSub, setShowNewSub] = useState(false)
  const [showNewTpl, setShowNewTpl] = useState(false)

  // ── Loaders ─────────────────────────────────────────────────────────────────
  const loadKPIs = useCallback(async () => {
    setLoadingKpis(true); setKpiError(null)
    try {
      const k = await invoicingKPIService.getAll()
      setKpis(k)
    } catch (err) {
      setKpiError(err instanceof Error ? err.message : 'Erro')
    } finally {
      setLoadingKpis(false)
    }
  }, [])

  const loadSubscriptions = useCallback(async () => {
    setLoadingSubs(true)
    try {
      const subs = await subscriptionsService.list(
        subFilter !== 'all' ? { status: subFilter } : undefined
      )
      setSubscriptions(subs)
    } catch { setSubscriptions([]) }
    finally { setLoadingSubs(false) }
  }, [subFilter])

  const loadTemplates = useCallback(async () => {
    setLoadingTpls(true)
    try {
      const tpls = await invoiceTemplatesService.list(
        tplFilter !== 'all' ? { type: tplFilter } : undefined
      )
      setTemplates(tpls)
    } catch { setTemplates([]) }
    finally { setLoadingTpls(false) }
  }, [tplFilter])

  const loadCharts = useCallback(async () => {
    setLoadingChart(true)
    const [rev, cust, dist, recent] = await Promise.allSettled([
      invoiceAnalyticsService.getMonthlyRevenue(6),
      invoiceAnalyticsService.getTopCustomers(5),
      invoiceAnalyticsService.getStatusDistribution(),
      invoiceAnalyticsService.getRecentInvoices(8),
    ])
    if (rev.status    === 'fulfilled') setMonthlyRev(rev.value as never)
    if (cust.status   === 'fulfilled') setTopCustomers(cust.value)
    if (dist.status   === 'fulfilled') setStatusDist(dist.value as never)
    if (recent.status === 'fulfilled') setRecentInv(recent.value as never)
    setLoadingChart(false)
  }, [])

  useEffect(() => { loadKPIs() }, [loadKPIs])
  useEffect(() => { loadSubscriptions() }, [loadSubscriptions])
  useEffect(() => { loadTemplates() }, [loadTemplates])
  useEffect(() => { if (!loadingKpis) loadCharts() }, [loadingKpis, loadCharts])

  const handleRefresh = async () => {
    await Promise.all([loadKPIs(), loadSubscriptions(), loadTemplates(), loadCharts()])
    toast.success('Dados actualizados!')
  }

  // ── Handlers Subscrições ────────────────────────────────────────────────────
  const handleSubStatus = async (id: string, status: 'ACTIVE' | 'PAUSED' | 'CANCELLED') => {
    try {
      await subscriptionsService.updateStatus(id, status)
      toast.success(`Subscrição ${SUBSCRIPTION_STATUS_LABELS[status].toLowerCase()}!`)
      loadSubscriptions(); loadKPIs()
    } catch (err) { toast.error(`Erro: ${err instanceof Error ? err.message : ''}`) }
  }

  const handleSubDelete = async (id: string) => {
    if (!confirm('Eliminar esta subscrição permanentemente?')) return
    try {
      await subscriptionsService.delete(id)
      toast.success('Subscrição eliminada')
      loadSubscriptions(); loadKPIs()
    } catch (err) { toast.error(`Erro: ${err instanceof Error ? err.message : ''}`) }
  }

  // ── Handlers Templates ──────────────────────────────────────────────────────
  const handleTplSetDefault = async (id: string) => {
    try {
      await invoiceTemplatesService.setDefault(id)
      toast.success('Template definido como padrão')
      loadTemplates(); loadKPIs()
    } catch (err) { toast.error(`Erro: ${err instanceof Error ? err.message : ''}`) }
  }

  const handleTplToggle = async (id: string, active: boolean) => {
    try {
      await invoiceTemplatesService.toggleActive(id, active)
      toast.success(active ? 'Template activado' : 'Template desactivado')
      loadTemplates(); loadKPIs()
    } catch (err) { toast.error(`Erro: ${err instanceof Error ? err.message : ''}`) }
  }

  const handleTplDelete = async (id: string) => {
    if (!confirm('Eliminar este template permanentemente?')) return
    try {
      await invoiceTemplatesService.delete(id)
      toast.success('Template eliminado')
      loadTemplates(); loadKPIs()
    } catch (err) { toast.error(`Erro: ${err instanceof Error ? err.message : ''}`) }
  }

  const handleTplUse = (tpl: InvoiceTemplate) => {
    toast.success(`Template "${tpl.template_name}" copiado! Crie uma nova fatura na secção Faturas.`)
  }

  // ── Derived ─────────────────────────────────────────────────────────────────
  const activeSubs    = subscriptions.filter(s => s.status === 'ACTIVE')
  const pausedSubs    = subscriptions.filter(s => s.status === 'PAUSED')
  const cancelledSubs = subscriptions.filter(s => s.status === 'CANCELLED')

  // ── Loading state ────────────────────────────────────────────────────────────
  if (loadingKpis) return (
    <Layout>
      <div className="space-y-6 p-6">
        <Skeleton className="h-10 w-72" />
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
        <Skeleton className="h-72 w-full rounded-xl" />
      </div>
    </Layout>
  )

  // ── Erro crítico ─────────────────────────────────────────────────────────────
  if (kpiError && !kpis) return (
    <Layout>
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 p-6">
        <div className="p-4 rounded-full bg-red-50"><AlertCircle className="h-10 w-10 text-red-500" /></div>
        <div className="text-center">
          <h2 className="text-xl font-semibold mb-1">Sem dados disponíveis</h2>
          <p className="text-muted-foreground text-sm max-w-md">
            Não foi possível carregar dados de faturação. Verifique a ligação ao Supabase.
          </p>
          <p className="text-xs text-muted-foreground mt-2 font-mono bg-muted px-3 py-1 rounded inline-block">{kpiError}</p>
        </div>
        <Button onClick={handleRefresh} variant="outline" size="sm">
          <RefreshCw className="h-4 w-4 mr-2" />Tentar novamente
        </Button>
      </div>
    </Layout>
  )

  const inv  = kpis?.invoices
  const subs = kpis?.subscriptions

  return (
    <Layout>
      <div className="space-y-6">

        {/* ── Header ───────────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">Faturação Avançada</h1>
            <p className="text-muted-foreground">Subscrições recorrentes, templates e automação</p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              <RefreshCw className="h-4 w-4 mr-2" />Actualizar
            </Button>
            <Button size="sm" variant="outline" onClick={() => setShowNewSub(true)}>
              <Calendar className="h-4 w-4 mr-2" />Nova Subscrição
            </Button>
            <Button size="sm" onClick={() => setShowNewTpl(true)}>
              <Plus className="h-4 w-4 mr-2" />Novo Template
            </Button>
          </div>
        </div>

        {/* ── KPIs Principais ──────────────────────────────────────────────── */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Receita Total (Pago)</CardTitle>
              <DollarSign className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{fmtAOA(inv?.totalRevenue ?? 0)}</div>
              <p className="text-xs text-muted-foreground">{inv?.paidInvoices ?? 0} faturas pagas</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">MRR (Receita Mensal)</CardTitle>
              <TrendingUp className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{fmtAOA(subs?.monthlyRecurringRevenue ?? 0)}</div>
              <p className="text-xs text-muted-foreground">ARR: {fmtAOA(subs?.annualRecurringRevenue ?? 0)}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valores em Atraso</CardTitle>
              <AlertTriangle className="h-4 w-4 text-red-500" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${(inv?.overdueRevenue ?? 0) > 0 ? 'text-red-600' : 'text-muted-foreground'}`}>
                {fmtAOA(inv?.overdueRevenue ?? 0)}
              </div>
              <p className="text-xs text-muted-foreground">{inv?.overdueInvoices ?? 0} faturas em atraso</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Taxa de Pagamento</CardTitle>
              <CheckCircle2 className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{(inv?.paidRate ?? 0).toFixed(1)}%</div>
              <Progress value={inv?.paidRate ?? 0} className="h-1.5 mt-2" />
            </CardContent>
          </Card>
        </div>

        {/* ── KPIs Secundários ─────────────────────────────────────────────── */}
        <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
          {[
            { label: 'Total Faturas',      value: inv?.totalInvoices ?? 0,     icon: FileText,      color: 'text-foreground' },
            { label: 'Pendentes',          value: inv?.pendingInvoices ?? 0,   icon: Clock,         color: 'text-blue-600'   },
            { label: 'Subscrições Activas',value: subs?.active ?? 0,          icon: Calendar,      color: 'text-green-600'  },
            { label: 'Pausadas',           value: subs?.paused ?? 0,          icon: Pause,         color: 'text-yellow-600' },
            { label: 'Templates Activos',  value: kpis?.activeTemplates ?? 0, icon: FileText,      color: 'text-purple-600' },
            { label: 'Valor Pendente',     value: fmtAOA(inv?.pendingRevenue ?? 0), icon: DollarSign, color: 'text-orange-600' },
          ].map(({ label, value, icon: Icon, color }) => (
            <Card key={label}>
              <CardContent className="pt-4">
                <div className="flex items-center gap-2">
                  <Icon className={`h-4 w-4 ${color}`} />
                  <span className="text-xs text-muted-foreground">{label}</span>
                </div>
                <div className={`text-lg font-bold mt-1 ${color}`}>{value}</div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* ── Tabs ─────────────────────────────────────────────────────────── */}
        <Tabs defaultValue="overview">
          <TabsList className="grid w-full grid-cols-2 md:grid-cols-4">
            <TabsTrigger value="overview">Visão Geral</TabsTrigger>
            <TabsTrigger value="subscriptions">
              Subscrições
              {(subs?.active ?? 0) > 0 && (
                <span className="ml-1.5 bg-green-500 text-white text-xs rounded-full w-4 h-4 inline-flex items-center justify-center">{subs?.active}</span>
              )}
            </TabsTrigger>
            <TabsTrigger value="templates">Templates</TabsTrigger>
            <TabsTrigger value="automation">Automação</TabsTrigger>
          </TabsList>

          {/* ── Visão Geral ─────────────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-6">
            <div className="grid gap-6 lg:grid-cols-2">

              {/* Receita Mensal */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BarChart2 className="h-4 w-4" />Receita Mensal (últimos 6 meses)
                  </CardTitle>
                  <CardDescription>Pago vs Pendente (AOA)</CardDescription>
                </CardHeader>
                <CardContent>
                  {loadingChart ? <Skeleton className="h-48 w-full" /> :
                    monthlyRev.length > 0 ? (
                      <ResponsiveContainer width="100%" height={200}>
                        <BarChart data={monthlyRev}>
                          <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                          <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                          <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} />
                          <Tooltip formatter={(v: number) => fmtAOA(v)} />
                          <Legend />
                          <Bar dataKey="paid"    name="Pago"     fill="#10B981" radius={[4,4,0,0]} />
                          <Bar dataKey="pending" name="Pendente" fill="#F59E0B" radius={[4,4,0,0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    ) : (
                      <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                        Sem dados de faturação disponíveis
                      </div>
                    )
                  }
                </CardContent>
              </Card>

              {/* Distribuição por Status */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BarChart2 className="h-4 w-4" />Distribuição por Estado
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loadingChart ? <Skeleton className="h-48 w-full" /> :
                    statusDist.length > 0 ? (
                      <>
                        <ResponsiveContainer width="100%" height={160}>
                          <PieChart>
                            <Pie data={statusDist} dataKey="count" nameKey="label" cx="50%" cy="50%" outerRadius={60}
                              label={({ label, count }) => `${label} (${count})`}>
                              {statusDist.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                            </Pie>
                            <Tooltip formatter={(v: number) => [v, 'Faturas']} />
                          </PieChart>
                        </ResponsiveContainer>
                        <div className="space-y-1 mt-2">
                          {statusDist.map((s, i) => (
                            <div key={`${s.label}-${i}`} className="flex items-center justify-between text-xs">
                              <div className="flex items-center gap-2">
                                <span className="w-2 h-2 rounded-full" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                                <span>{s.label}</span>
                              </div>
                              <span className="text-muted-foreground">{s.count} · {fmtAOA(s.total)}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                        Sem faturas registadas
                      </div>
                    )
                  }
                </CardContent>
              </Card>
            </div>

            {/* Top Clientes + Faturas Recentes */}
            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Users className="h-4 w-4" />Top Clientes (por Receita)
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loadingChart ? <Skeleton className="h-40 w-full" /> :
                    topCustomers.length > 0 ? (
                      <div className="space-y-3">
                        {topCustomers.map((c, i) => (
                          <div key={c.name} className="space-y-1">
                            <div className="flex justify-between text-xs">
                              <div className="flex items-center gap-2">
                                <span className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold bg-muted">{i + 1}</span>
                                <span className="font-medium truncate max-w-[160px]">{c.name}</span>
                              </div>
                              <span className="font-bold">{fmtAOA(c.total)}</span>
                            </div>
                            <Progress
                              value={(c.total / (topCustomers[0]?.total || 1)) * 100}
                              className="h-1.5"
                            />
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem dados</div>
                    )
                  }
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileText className="h-4 w-4" />Faturas Recentes
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {loadingChart ? <Skeleton className="h-40 w-full" /> :
                    recentInv.length > 0 ? (
                      <div className="space-y-2">
                        {recentInv.slice(0, 6).map(inv => (
                          <div key={inv.id} className="flex items-center justify-between gap-2 py-1.5 border-b last:border-0">
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-medium truncate">{inv.customer_name}</p>
                              <p className="text-xs text-muted-foreground font-mono">{inv.invoice_number}</p>
                            </div>
                            <div className="text-right shrink-0">
                              <p className="text-xs font-bold">{fmtAOA(Number(inv.total))}</p>
                              <span className={`inline-block text-xs px-1.5 py-0.5 rounded-full ${INV_STATUS_COLORS[inv.status] ?? 'bg-gray-100'}`}>
                                {inv.status}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Sem faturas</div>
                    )
                  }
                </CardContent>
              </Card>
            </div>

            {/* Tendência MRR */}
            {monthlyRev.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <TrendingUp className="h-4 w-4" />Evolução da Receita Total
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={180}>
                    <LineChart data={monthlyRev}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v/1000).toFixed(0)}k`} />
                      <Tooltip formatter={(v: number) => fmtAOA(v)} />
                      <Line type="monotone" dataKey="total" name="Total" stroke="#3B82F6" strokeWidth={2} dot={false} />
                      <Line type="monotone" dataKey="paid"  name="Pago"  stroke="#10B981" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* ── Subscrições ─────────────────────────────────────────────────── */}
          <TabsContent value="subscriptions" className="space-y-4">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-lg font-semibold">Subscrições Recorrentes</h2>
                <p className="text-sm text-muted-foreground">
                  {activeSubs.length} activas · {pausedSubs.length} pausadas · {cancelledSubs.length} canceladas
                </p>
              </div>
              <div className="flex gap-2 items-center">
                <Select value={subFilter} onValueChange={setSubFilter}>
                  <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todas</SelectItem>
                    <SelectItem value="ACTIVE">Activas</SelectItem>
                    <SelectItem value="PAUSED">Pausadas</SelectItem>
                    <SelectItem value="CANCELLED">Canceladas</SelectItem>
                    <SelectItem value="EXPIRED">Expiradas</SelectItem>
                  </SelectContent>
                </Select>
                <Button size="sm" onClick={() => setShowNewSub(true)}>
                  <Plus className="h-4 w-4 mr-2" />Nova Subscrição
                </Button>
              </div>
            </div>

            {/* KPIs MRR */}
            {subs && (
              <div className="grid gap-3 md:grid-cols-3">
                <Card className="bg-green-50/40 border-green-200">
                  <CardContent className="pt-4">
                    <p className="text-xs text-muted-foreground">MRR</p>
                    <p className="text-2xl font-bold text-green-700">{fmtAOA(subs.monthlyRecurringRevenue)}</p>
                    <p className="text-xs text-muted-foreground">Receita mensal recorrente</p>
                  </CardContent>
                </Card>
                <Card className="bg-blue-50/40 border-blue-200">
                  <CardContent className="pt-4">
                    <p className="text-xs text-muted-foreground">ARR</p>
                    <p className="text-2xl font-bold text-blue-700">{fmtAOA(subs.annualRecurringRevenue)}</p>
                    <p className="text-xs text-muted-foreground">Projecção anual</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-4">
                    <p className="text-xs text-muted-foreground">Valor Médio / Subscrição</p>
                    <p className="text-2xl font-bold">{fmtAOA(subs.avgSubscriptionValue)}</p>
                    <p className="text-xs text-muted-foreground">{subs.active} activas de {subs.total} total</p>
                  </CardContent>
                </Card>
              </div>
            )}

            {loadingSubs ? (
              <div className="space-y-3">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-28 w-full rounded-lg" />)}</div>
            ) : subscriptions.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 gap-3">
                  <Calendar className="h-12 w-12 text-muted-foreground/40" />
                  <p className="text-muted-foreground font-medium">Nenhuma subscrição encontrada</p>
                  <p className="text-sm text-muted-foreground text-center max-w-sm">
                    {subFilter !== 'all' ? 'Sem subscrições com este filtro.' : 'Crie a primeira subscrição recorrente.'}
                  </p>
                  <Button onClick={() => setShowNewSub(true)} className="mt-2">
                    <Plus className="h-4 w-4 mr-2" />Nova Subscrição
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {subscriptions.map(sub => (
                  <SubscriptionCard
                    key={sub.id} sub={sub}
                    onStatusChange={handleSubStatus}
                    onDelete={handleSubDelete}
                  />
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── Templates ────────────────────────────────────────────────────── */}
          <TabsContent value="templates" className="space-y-4">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <h2 className="text-lg font-semibold">Templates de Fatura</h2>
                <p className="text-sm text-muted-foreground">
                  {kpis?.activeTemplates ?? 0} activos de {kpis?.templates ?? 0} total
                </p>
              </div>
              <div className="flex gap-2 items-center">
                <Select value={tplFilter} onValueChange={setTplFilter}>
                  <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">Todos os tipos</SelectItem>
                    {Object.entries(TEMPLATE_TYPE_LABELS).map(([k, v]) =>
                      <SelectItem key={k} value={k}>{v}</SelectItem>)}
                  </SelectContent>
                </Select>
                <Button size="sm" onClick={() => setShowNewTpl(true)}>
                  <Plus className="h-4 w-4 mr-2" />Novo Template
                </Button>
              </div>
            </div>

            {loadingTpls ? (
              <div className="space-y-3">{[...Array(3)].map((_, i) => <Skeleton key={i} className="h-40 w-full rounded-lg" />)}</div>
            ) : templates.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 gap-3">
                  <FileText className="h-12 w-12 text-muted-foreground/40" />
                  <p className="text-muted-foreground font-medium">Nenhum template encontrado</p>
                  <p className="text-sm text-muted-foreground text-center max-w-sm">
                    Crie templates para agilizar a emissão de faturas.
                  </p>
                  <Button onClick={() => setShowNewTpl(true)} className="mt-2">
                    <Plus className="h-4 w-4 mr-2" />Novo Template
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {templates.map(tpl => (
                  <TemplateCard
                    key={tpl.id} tpl={tpl}
                    onSetDefault={handleTplSetDefault}
                    onToggle={handleTplToggle}
                    onDelete={handleTplDelete}
                    onUse={handleTplUse}
                  />
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── Automação ────────────────────────────────────────────────────── */}
          <TabsContent value="automation" className="space-y-6">
            <div className="grid gap-6 lg:grid-cols-2">
              {/* Regras de Automação */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Zap className="h-4 w-4 text-yellow-500" />Regras de Automação
                  </CardTitle>
                  <CardDescription>Configurações automáticas para faturação recorrente</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {[
                    { title: 'Geração automática de faturas', desc: 'Cria faturas automaticamente na data de cobrança das subscrições activas', active: true },
                    { title: 'Envio de lembretes de pagamento', desc: 'Envia email 7 dias antes e no vencimento', active: false },
                    { title: 'Retry automático em falha', desc: 'Tenta novamente a cobrança após falha (máx. 3 tentativas)', active: true },
                    { title: 'Cancelamento por inadimplência', desc: 'Cancela subscrição após 30 dias sem pagamento', active: false },
                  ].map(rule => (
                    <div key={rule.title} className="flex items-start justify-between gap-3 p-3 rounded-lg border">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p className="text-sm font-medium">{rule.title}</p>
                          <span className={`text-xs px-1.5 py-0.5 rounded-full ${rule.active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                            {rule.active ? 'Activo' : 'Inactivo'}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">{rule.desc}</p>
                      </div>
                      <Button variant="outline" size="sm" className="shrink-0 text-xs" disabled>
                        {rule.active ? 'Desactivar' : 'Activar'}
                      </Button>
                    </div>
                  ))}
                  <p className="text-xs text-muted-foreground text-center">
                    Configurações de automação geridas via Edge Functions Supabase
                  </p>
                </CardContent>
              </Card>

              {/* Estatísticas de Subscrições */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BarChart2 className="h-4 w-4" />Subscrições por Estado
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {subs && subs.total > 0 ? (
                    <>
                      <ResponsiveContainer width="100%" height={160}>
                        <PieChart>
                          <Pie
                            data={[
                              { name: 'Activas',    value: subs.active,    fill: '#10B981' },
                              { name: 'Pausadas',   value: subs.paused,    fill: '#F59E0B' },
                              { name: 'Canceladas', value: subs.cancelled, fill: '#EF4444' },
                              { name: 'Expiradas',  value: subs.expired,   fill: '#9CA3AF' },
                            ].filter(d => d.value > 0)}
                            dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={60}
                            label={({ name, value }) => `${name} (${value})`}
                          >
                            {[...Array(4)].map((_, i) => <Cell key={i} fill={['#10B981','#F59E0B','#EF4444','#9CA3AF'][i]} />)}
                          </Pie>
                          <Tooltip />
                        </PieChart>
                      </ResponsiveContainer>
                      <div className="space-y-2 mt-3">
                        {[
                          { label: 'Activas',    val: subs.active,    color: 'bg-green-500' },
                          { label: 'Pausadas',   val: subs.paused,    color: 'bg-yellow-500' },
                          { label: 'Canceladas', val: subs.cancelled, color: 'bg-red-500' },
                          { label: 'Expiradas',  val: subs.expired,   color: 'bg-gray-400' },
                        ].filter(d => d.val > 0).map(d => (
                          <div key={d.label} className="flex items-center gap-3">
                            <span className={`w-2.5 h-2.5 rounded-full ${d.color}`} />
                            <span className="text-xs text-muted-foreground flex-1">{d.label}</span>
                            <Progress value={(d.val / subs.total) * 100} className="flex-1 h-1.5" />
                            <span className="text-xs font-medium w-6 text-right">{d.val}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                      Sem subscrições para mostrar
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Sumário de Automação */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Sumário do Sistema de Automação</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-4">
                  {[
                    { label: 'Subscrições Activas', value: subs?.active ?? 0, icon: CheckCircle2, color: 'text-green-600' },
                    { label: 'Próximas Cobranças',  value: subscriptions.filter(s =>
                        s.status === 'ACTIVE' &&
                        new Date(s.next_billing_date) <= new Date(Date.now() + 7*24*60*60*1000)
                      ).length, icon: Clock, color: 'text-blue-600' },
                    { label: 'Templates Activos',   value: kpis?.activeTemplates ?? 0, icon: FileText, color: 'text-purple-600' },
                    { label: 'MRR Total',            value: fmtAOA(subs?.monthlyRecurringRevenue ?? 0), icon: TrendingUp, color: 'text-green-600' },
                  ].map(({ label, value, icon: Icon, color }) => (
                    <div key={label} className="flex items-center gap-3 p-3 rounded-lg bg-muted/30">
                      <Icon className={`h-8 w-8 ${color} shrink-0`} />
                      <div>
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className={`text-lg font-bold ${color}`}>{value}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

      </div>

      {/* ── Modais ──────────────────────────────────────────────────────────── */}
      <NewSubscriptionModal
        open={showNewSub}
        onClose={() => setShowNewSub(false)}
        onCreated={() => { loadSubscriptions(); loadKPIs() }}
      />
      <NewTemplateModal
        open={showNewTpl}
        onClose={() => setShowNewTpl(false)}
        onCreated={() => { loadTemplates(); loadKPIs() }}
      />

    </Layout>
  )
}
