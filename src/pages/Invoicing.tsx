import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Plus, FileText, Download, Send, Trash2, RefreshCw, Search,
  Loader2, Eye, Edit, CheckCircle2, Clock, XCircle, AlertTriangle,
  Printer, Filter, X, Users,
  MoreVertical, UserPlus, Building2, Upload,
} from 'lucide-react'
import { InvoiceImport } from '@/components/InvoiceImport'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toast } from 'sonner'
import {
  customerService, invoiceService,
  calcItem, calcInvoiceTotals,
  IVA_RATES, IVA_LABELS, PAYMENT_METHODS,
  type Customer, type Invoice, type InvoiceItem,
  type InvoiceStatus, type IvaRate, type InvoiceStats,
} from '@/services/invoicingServiceReal'

// ─── Formatadores ─────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)

const fmtDate = (d: string) =>
  new Date(d).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric' })

// ─── Status config ────────────────────────────────────────────────────────────
const STATUS_CONFIG: Record<InvoiceStatus, {
  label: string; icon: React.ReactNode
  bg: string; text: string; border: string
}> = {
  DRAFT:     { label: 'Rascunho', icon: <FileText className="h-3 w-3" />,     bg: 'bg-gray-100',   text: 'text-gray-600',   border: 'border-gray-200' },
  SENT:      { label: 'Enviada',  icon: <Send className="h-3 w-3" />,          bg: 'bg-blue-100',   text: 'text-blue-700',   border: 'border-blue-200' },
  PAID:      { label: 'Paga',     icon: <CheckCircle2 className="h-3 w-3" />, bg: 'bg-green-100',  text: 'text-green-700',  border: 'border-green-200' },
  OVERDUE:   { label: 'Vencida',  icon: <AlertTriangle className="h-3 w-3" />,bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-200' },
  CANCELLED: { label: 'Anulada',  icon: <XCircle className="h-3 w-3" />,      bg: 'bg-amber-100',  text: 'text-amber-700',  border: 'border-amber-200' },
}

function StatusBadge({ status }: { status: InvoiceStatus }) {
  const c = STATUS_CONFIG[status] || STATUS_CONFIG.DRAFT
  return (
    <Badge className={`${c.bg} ${c.text} ${c.border} gap-1 text-xs border`}>
      {c.icon} {c.label}
    </Badge>
  )
}

// ─── Item linha vazio ─────────────────────────────────────────────────────────
const EMPTY_ITEM: InvoiceItem = {
  description: '', quantity: 1, unit_price: 0,
  discount_percent: 0, iva_rate: 'normal', iva_percent: 14,
}

// ─── Formulário de fatura vazio ───────────────────────────────────────────────
const EMPTY_INV_FORM = {
  customer_id: '',
  series: 'FT',
  issue_date: new Date().toISOString().split('T')[0],
  due_date: new Date(Date.now() + 30 * 86400000).toISOString().split('T')[0],
  currency: 'AOA',
  payment_method: '',
  notes: '',
  internal_notes: '',
}

// ─── Formulário de cliente vazio ──────────────────────────────────────────────
const EMPTY_CUST_FORM = {
  name: '', legal_name: '', nif: '', email: '', phone: '',
  address: '', city: '', country: 'AO',
  customer_type: 'BUSINESS' as 'BUSINESS' | 'INDIVIDUAL' | 'PUBLIC',
  payment_terms: 30, is_active: true, notes: '',
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function Invoicing() {
  const [invoices,   setInvoices]   = useState<Invoice[]>([])
  const [customers,  setCustomers]  = useState<Customer[]>([])
  const [stats,      setStats]      = useState<InvoiceStats | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [activeTab,  setActiveTab]  = useState('invoices')

  // Filtros
  const [filterStatus,   setFilterStatus]   = useState<InvoiceStatus | 'all'>('all')
  const [searchTerm,     setSearchTerm]     = useState('')
  const [showFilters,    setShowFilters]    = useState(false)

  // Diálogos
  const [isInvDialogOpen,  setIsInvDialogOpen]  = useState(false)
  const [isCustDialogOpen, setIsCustDialogOpen] = useState(false)
  const [isDetailOpen,     setIsDetailOpen]     = useState(false)
  const [editingInv,  setEditingInv]  = useState<Invoice | null>(null)
  const [editingCust, setEditingCust] = useState<Customer | null>(null)
  const [detailInv,   setDetailInv]   = useState<Invoice | null>(null)
  const [submitting,  setSubmitting]  = useState(false)

  // Form fatura
  const [invForm,  setInvForm]  = useState(EMPTY_INV_FORM)
  const [invItems, setInvItems] = useState<InvoiceItem[]>([{ ...EMPTY_ITEM }])

  // Form cliente
  const [custForm, setCustForm] = useState(EMPTY_CUST_FORM)

  // ─── Carregar dados ────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [invData, custData, statsData] = await Promise.all([
        invoiceService.getAll(),
        customerService.getAll(),
        invoiceService.getStats(),
      ])
      setInvoices(invData)
      setCustomers(custData)
      setStats(statsData)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados de faturação')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Filtros reactivos ─────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    let result = [...invoices]
    if (filterStatus !== 'all') result = result.filter(i => i.status === filterStatus)
    if (searchTerm) result = result.filter(i =>
      i.customer_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      i.invoice_number.toLowerCase().includes(searchTerm.toLowerCase())
    )
    return result
  }, [invoices, filterStatus, searchTerm])

  // ─── Totais calculados dos itens ───────────────────────────────────────────
  const invTotals = useMemo(() => calcInvoiceTotals(invItems), [invItems])

  // ─── Gestão de itens da fatura ─────────────────────────────────────────────
  const updateItem = (idx: number, field: keyof InvoiceItem, value: string | number) => {
    setInvItems(prev => {
      const next = [...prev]
      const item = { ...next[idx], [field]: value }
      if (field === 'iva_rate') {
        item.iva_percent = IVA_RATES[value as IvaRate]
      }
      next[idx] = item
      return next
    })
  }

  const addItem = () => setInvItems(prev => [...prev, { ...EMPTY_ITEM }])
  const removeItem = (idx: number) => {
    if (invItems.length === 1) return
    setInvItems(prev => prev.filter((_, i) => i !== idx))
  }

  // ─── Abrir diálogo fatura ──────────────────────────────────────────────────
  const openInvDialog = (inv?: Invoice) => {
    if (inv) {
      setEditingInv(inv)
      setInvForm({
        customer_id:    inv.customer_id,
        series:         inv.series || 'FT',
        issue_date:     inv.issue_date,
        due_date:       inv.due_date,
        currency:       inv.currency || 'AOA',
        payment_method: inv.payment_method || '',
        notes:          inv.notes || '',
        internal_notes: inv.internal_notes || '',
      })
      setInvItems((inv.items && inv.items.length > 0)
        ? inv.items.map(it => ({
            id: it.id, invoice_id: it.invoice_id,
            description: it.description, quantity: Number(it.quantity),
            unit_price: Number(it.unit_price), discount_percent: Number(it.discount_percent || 0),
            iva_rate: it.iva_rate as IvaRate, iva_percent: Number(it.iva_percent),
          }))
        : [{ ...EMPTY_ITEM }]
      )
    } else {
      setEditingInv(null)
      setInvForm(EMPTY_INV_FORM)
      setInvItems([{ ...EMPTY_ITEM }])
    }
    setIsInvDialogOpen(true)
  }

  const closeInvDialog = () => {
    setIsInvDialogOpen(false)
    setEditingInv(null)
    setInvForm(EMPTY_INV_FORM)
    setInvItems([{ ...EMPTY_ITEM }])
  }

  // ─── Guardar fatura ────────────────────────────────────────────────────────
  const handleSaveInvoice = async () => {
    if (!invForm.customer_id) { toast.error('Selecione um cliente'); return }
    if (invItems.some(it => !it.description.trim())) { toast.error('Preencha a descrição de todos os itens'); return }
    if (invItems.some(it => Number(it.unit_price) <= 0)) { toast.error('O preço unitário deve ser > 0'); return }

    const cust = customers.find(c => c.id === invForm.customer_id)
    if (!cust) { toast.error('Cliente não encontrado'); return }

    setSubmitting(true)
    try {
      const invPayload = {
        customer_id:      invForm.customer_id,
        customer_name:    cust.name,
        customer_nif:     cust.nif,
        customer_address: cust.address,
        issue_date:       invForm.issue_date,
        due_date:         invForm.due_date,
        series:           invForm.series,
        currency:         invForm.currency,
        payment_method:   invForm.payment_method || undefined,
        notes:            invForm.notes || undefined,
        internal_notes:   invForm.internal_notes || undefined,
      }

      if (editingInv) {
        await invoiceService.update(editingInv.id, invPayload, invItems)
        toast.success('Fatura actualizada com sucesso!')
      } else {
        await invoiceService.create(invPayload, invItems)
        toast.success('Fatura criada com sucesso!')
      }
      await loadData()
      closeInvDialog()
    } catch (err) {
      console.error(err)
      toast.error('Erro ao guardar fatura')
    } finally {
      setSubmitting(false)
    }
  }

  // ─── Acções rápidas de fatura ──────────────────────────────────────────────
  const handleStatusChange = async (id: string, status: InvoiceStatus, label: string) => {
    try {
      const paymentDate = status === 'PAID' ? new Date().toISOString().split('T')[0] : undefined
      await invoiceService.updateStatus(id, status, paymentDate)
      toast.success(`Fatura marcada como ${label}`)
      await loadData()
    } catch { toast.error('Erro ao actualizar estado') }
  }

  const handleDelete = async (id: string, num: string) => {
    if (!confirm(`Eliminar fatura ${num}? Esta acção é irreversível.`)) return
    try {
      await invoiceService.delete(id)
      toast.success('Fatura eliminada')
      await loadData()
    } catch { toast.error('Erro ao eliminar fatura') }
  }

  const handleViewDetail = async (inv: Invoice) => {
    const full = await invoiceService.getById(inv.id)
    setDetailInv(full || inv)
    setIsDetailOpen(true)
  }

  // ─── Cliente CRUD ──────────────────────────────────────────────────────────
  const openCustDialog = (cust?: Customer) => {
    if (cust) {
      setEditingCust(cust)
      setCustForm({
        name:          cust.name,
        legal_name:    cust.legal_name || '',
        nif:           cust.nif || '',
        email:         cust.email || '',
        phone:         cust.phone || '',
        address:       cust.address || '',
        city:          cust.city || '',
        country:       cust.country || 'AO',
        customer_type: cust.customer_type || 'BUSINESS',
        payment_terms: cust.payment_terms || 30,
        is_active:     cust.is_active,
        notes:         cust.notes || '',
      })
    } else {
      setEditingCust(null)
      setCustForm(EMPTY_CUST_FORM)
    }
    setIsCustDialogOpen(true)
  }

  const handleSaveCustomer = async () => {
    if (!custForm.name.trim()) { toast.error('Nome do cliente obrigatório'); return }
    setSubmitting(true)
    try {
      if (editingCust) {
        await customerService.update(editingCust.id, custForm)
        toast.success('Cliente actualizado!')
      } else {
        await customerService.create(custForm)
        toast.success('Cliente criado!')
      }
      setIsCustDialogOpen(false)
      setEditingCust(null)
      await loadData()
    } catch (err) {
      console.error(err)
      toast.error('Erro ao guardar cliente')
    } finally { setSubmitting(false) }
  }

  const handleDeleteCustomer = async (id: string, name: string) => {
    if (!confirm(`Eliminar cliente "${name}"?`)) return
    try {
      await customerService.delete(id)
      toast.success('Cliente eliminado')
      await loadData()
    } catch { toast.error('Erro ao eliminar cliente (pode ter faturas associadas)') }
  }

  // ─── Skeleton ──────────────────────────────────────────────────────────────
  if (loading) return (
    <div className="space-y-6 p-6">
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_, i) => (
          <Card key={i}><CardContent className="p-6"><Skeleton className="h-20 w-full" /></CardContent></Card>
        ))}
      </div>
      <Card><CardContent className="p-6"><Skeleton className="h-72 w-full" /></CardContent></Card>
    </div>
  )

  const hasFilters = filterStatus !== 'all' || searchTerm

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Faturação</h1>
          <p className="text-muted-foreground">Gestão de faturas e documentos fiscais</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
          <Button variant="outline" size="sm" onClick={() => invoiceService.exportCSV(filtered)} disabled={filtered.length === 0}>
            <Download className="h-4 w-4 mr-2" /> Exportar CSV
          </Button>
          <Button variant="outline" size="sm" onClick={() => openCustDialog()}>
            <UserPlus className="h-4 w-4 mr-2" /> Novo Cliente
          </Button>
          <Button variant="outline" size="sm" onClick={() => setActiveTab('import')}>
            <Upload className="h-4 w-4 mr-2" /> Importar
          </Button>
          <Button size="sm" onClick={() => openInvDialog()}>
            <Plus className="h-4 w-4 mr-2" /> Nova Fatura
          </Button>
        </div>
      </div>

      {/* ── Cards de métricas ── */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: 'Total Faturado',
              value: fmt(stats.total_revenue + stats.total_pending + stats.total_overdue_amount),
              icon: <FileText className="h-5 w-5 text-blue-600" />,
              bg: 'bg-blue-50', color: 'text-blue-600',
              sub: `${stats.total} faturas`,
            },
            {
              title: 'Cobrado (Pagas)',
              value: fmt(stats.total_revenue),
              icon: <CheckCircle2 className="h-5 w-5 text-green-600" />,
              bg: 'bg-green-50', color: 'text-green-600',
              sub: `${stats.paid} faturas pagas`,
            },
            {
              title: 'A Receber',
              value: fmt(stats.total_pending),
              icon: <Clock className="h-5 w-5 text-blue-500" />,
              bg: 'bg-sky-50', color: 'text-sky-600',
              sub: `${stats.sent + stats.draft} em aberto`,
            },
            {
              title: 'Vencidas',
              value: fmt(stats.total_overdue_amount),
              icon: <AlertTriangle className="h-5 w-5 text-red-600" />,
              bg: 'bg-red-50', color: 'text-red-600',
              sub: `${stats.overdue} faturas vencidas`,
            },
          ].map((m, i) => (
            <motion.div key={i} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}>
              <Card>
                <CardContent className="p-6">
                  <div className="flex items-center justify-between mb-3">
                    <p className="text-sm font-medium text-muted-foreground">{m.title}</p>
                    <div className={`p-2 rounded-lg ${m.bg}`}>{m.icon}</div>
                  </div>
                  <p className={`text-2xl font-bold ${m.color}`}>{m.value}</p>
                  <p className="text-xs text-muted-foreground mt-1">{m.sub}</p>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      )}

      {/* ── Tabs ── */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="flex flex-wrap">
          <TabsTrigger value="invoices"><FileText className="h-4 w-4 mr-2" /> Faturas</TabsTrigger>
          <TabsTrigger value="customers"><Users className="h-4 w-4 mr-2" /> Clientes</TabsTrigger>
          <TabsTrigger value="import"><Upload className="h-4 w-4 mr-2" /> Importar</TabsTrigger>
        </TabsList>

        {/* ════════════ TAB: FATURAS ════════════ */}
        <TabsContent value="invoices" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Faturas ({filtered.length})</CardTitle>
                  <CardDescription>
                    {hasFilters ? `${filtered.length} de ${invoices.length} faturas` : `${invoices.length} faturas no total`}
                  </CardDescription>
                </div>
                <div className="flex gap-2">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      placeholder="Cliente ou nº fatura..."
                      className="pl-9 w-52"
                      value={searchTerm}
                      onChange={e => setSearchTerm(e.target.value)}
                    />
                    {searchTerm && <button onClick={() => setSearchTerm('')} className="absolute right-3 top-1/2 -translate-y-1/2"><X className="h-3 w-3" /></button>}
                  </div>
                  <Button
                    variant={showFilters ? 'default' : 'outline'} size="sm"
                    onClick={() => setShowFilters(!showFilters)}
                  >
                    <Filter className="h-4 w-4 mr-2" /> Filtrar
                  </Button>
                </div>
              </div>

              {/* Filtros rápidos de estado */}
              <AnimatePresence>
                {showFilters && (
                  <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
                    <Separator className="my-3" />
                    <div className="flex flex-wrap gap-2">
                      {(['all', 'DRAFT', 'SENT', 'PAID', 'OVERDUE', 'CANCELLED'] as const).map(s => (
                        <button
                          key={s}
                          onClick={() => setFilterStatus(s)}
                          className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                            filterStatus === s
                              ? 'bg-primary text-primary-foreground border-primary'
                              : 'bg-background border-border hover:bg-muted'
                          }`}
                        >
                          {s === 'all' ? `Todas (${invoices.length})` : `${STATUS_CONFIG[s].label} (${invoices.filter(i => i.status === s).length})`}
                        </button>
                      ))}
                      {hasFilters && (
                        <button
                          onClick={() => { setFilterStatus('all'); setSearchTerm('') }}
                          className="px-3 py-1.5 rounded-full text-xs font-medium text-muted-foreground hover:text-foreground flex items-center gap-1"
                        >
                          <X className="h-3 w-3" /> Limpar
                        </button>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </CardHeader>

            <CardContent>
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                  <FileText className="h-14 w-14 opacity-15" />
                  <p className="text-sm font-medium">Nenhuma fatura encontrada</p>
                  <Button size="sm" onClick={() => openInvDialog()}>
                    <Plus className="h-4 w-4 mr-2" /> Criar Fatura
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {/* Cabeçalho tabela */}
                  <div className="hidden lg:grid lg:grid-cols-[2fr_1fr_1fr_1fr_1fr_auto] gap-4 text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 pb-1">
                    <span>Fatura / Cliente</span>
                    <span>Emissão</span>
                    <span>Vencimento</span>
                    <span className="text-right">Total</span>
                    <span className="text-center">Estado</span>
                    <span className="text-center">Acções</span>
                  </div>
                  <Separator />

                  <AnimatePresence>
                    {filtered.map((inv, idx) => {
                      const daysOverdue = inv.status === 'OVERDUE'
                        ? Math.floor((Date.now() - new Date(inv.due_date).getTime()) / 86400000)
                        : 0

                      return (
                        <motion.div
                          key={inv.id}
                          initial={{ opacity: 0, y: 6 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -6 }}
                          transition={{ delay: idx * 0.025 }}
                          className="grid grid-cols-1 lg:grid-cols-[2fr_1fr_1fr_1fr_1fr_auto] gap-4 items-center rounded-lg border px-3 py-3 hover:bg-muted/30 transition-colors"
                        >
                          {/* Fatura + Cliente */}
                          <div>
                            <div className="flex items-center gap-2">
                              <p className="font-semibold text-sm">{inv.invoice_number}</p>
                              {inv.series && inv.series !== 'FT' && (
                                <Badge variant="outline" className="text-xs py-0">{inv.series}</Badge>
                              )}
                            </div>
                            <p className="text-sm text-muted-foreground truncate">{inv.customer_name}</p>
                            {inv.customer_nif && <p className="text-xs text-muted-foreground">NIF: {inv.customer_nif}</p>}
                            {daysOverdue > 0 && (
                              <p className="text-xs text-red-600 font-medium">{daysOverdue} dias em atraso</p>
                            )}
                          </div>

                          {/* Datas */}
                          <div>
                            <span className="text-xs text-muted-foreground lg:hidden">Emissão: </span>
                            <span className="text-sm">{fmtDate(inv.issue_date)}</span>
                          </div>
                          <div>
                            <span className="text-xs text-muted-foreground lg:hidden">Vencimento: </span>
                            <span className={`text-sm ${inv.status === 'OVERDUE' ? 'text-red-600 font-medium' : ''}`}>
                              {fmtDate(inv.due_date)}
                            </span>
                          </div>

                          {/* Total */}
                          <div className="lg:text-right">
                            <p className="font-bold text-sm">{fmt(Number(inv.total))}</p>
                            {Number(inv.iva_amount) > 0 && (
                              <p className="text-xs text-muted-foreground">IVA: {fmt(Number(inv.iva_amount))}</p>
                            )}
                          </div>

                          {/* Estado */}
                          <div className="lg:flex lg:justify-center">
                            <StatusBadge status={inv.status} />
                          </div>

                          {/* Acções */}
                          <div className="flex items-center gap-1 lg:justify-center">
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleViewDetail(inv)}>
                              <Eye className="h-3.5 w-3.5" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openInvDialog(inv)}>
                              <Edit className="h-3.5 w-3.5" />
                            </Button>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button variant="ghost" size="icon" className="h-8 w-8">
                                  <MoreVertical className="h-3.5 w-3.5" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-44">
                                {inv.status === 'DRAFT' && (
                                  <DropdownMenuItem onClick={() => handleStatusChange(inv.id, 'SENT', 'Enviada')}>
                                    <Send className="h-3.5 w-3.5 mr-2 text-blue-600" /> Marcar Enviada
                                  </DropdownMenuItem>
                                )}
                                {['DRAFT','SENT','OVERDUE'].includes(inv.status) && (
                                  <DropdownMenuItem onClick={() => handleStatusChange(inv.id, 'PAID', 'Paga')}>
                                    <CheckCircle2 className="h-3.5 w-3.5 mr-2 text-green-600" /> Marcar Paga
                                  </DropdownMenuItem>
                                )}
                                {['DRAFT','SENT'].includes(inv.status) && (
                                  <DropdownMenuItem onClick={() => handleStatusChange(inv.id, 'CANCELLED', 'Anulada')}>
                                    <XCircle className="h-3.5 w-3.5 mr-2 text-amber-600" /> Anular
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem onClick={() => invoiceService.printInvoice(inv)}>
                                  <Printer className="h-3.5 w-3.5 mr-2" /> Imprimir
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  className="text-destructive focus:text-destructive"
                                  onClick={() => handleDelete(inv.id, inv.invoice_number)}
                                >
                                  <Trash2 className="h-3.5 w-3.5 mr-2" /> Eliminar
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </div>
                        </motion.div>
                      )
                    })}
                  </AnimatePresence>

                  {/* Rodapé totais */}
                  {filtered.length > 0 && (
                    <div className="pt-4 border-t flex flex-wrap gap-4 justify-end text-sm">
                      <span className="text-muted-foreground">
                        Subtotal: <strong>{fmt(filtered.reduce((s, i) => s + Number(i.subtotal), 0))}</strong>
                      </span>
                      <span className="text-muted-foreground">
                        IVA: <strong>{fmt(filtered.reduce((s, i) => s + Number(i.iva_amount), 0))}</strong>
                      </span>
                      <span className="font-bold">
                        Total: {fmt(filtered.reduce((s, i) => s + Number(i.total), 0))}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════════════ TAB: CLIENTES ════════════ */}
        <TabsContent value="customers" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Clientes ({customers.length})</CardTitle>
                  <CardDescription>{customers.filter(c => c.is_active).length} activos</CardDescription>
                </div>
                <Button size="sm" onClick={() => openCustDialog()}>
                  <UserPlus className="h-4 w-4 mr-2" /> Novo Cliente
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {customers.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                  <Users className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Sem clientes cadastrados</p>
                  <Button size="sm" onClick={() => openCustDialog()}><UserPlus className="h-4 w-4 mr-2" /> Adicionar Cliente</Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {customers.map((cust, idx) => {
                    const custInvs = invoices.filter(i => i.customer_id === cust.id)
                    const custRevenue = custInvs.filter(i => i.status === 'PAID').reduce((s, i) => s + Number(i.total), 0)
                    return (
                      <motion.div
                        key={cust.id}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: idx * 0.04 }}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border px-4 py-3 hover:bg-muted/30 transition-colors"
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10">
                            <Building2 className="h-5 w-5 text-primary" />
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <p className="font-medium">{cust.name}</p>
                              <Badge className={`text-xs py-0 ${cust.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
                                {cust.is_active ? 'Activo' : 'Inactivo'}
                              </Badge>
                            </div>
                            <div className="flex flex-wrap items-center gap-2 mt-0.5">
                              {cust.nif && <span className="text-xs text-muted-foreground">NIF: {cust.nif}</span>}
                              {cust.email && <span className="text-xs text-muted-foreground">{cust.email}</span>}
                              {cust.city && <span className="text-xs text-muted-foreground">{cust.city}</span>}
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-4 text-sm">
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">Faturas</p>
                            <p className="font-semibold">{custInvs.length}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">Faturado</p>
                            <p className="font-semibold text-green-600">{fmt(custRevenue)}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">Prazo</p>
                            <p className="text-sm">{cust.payment_terms}d</p>
                          </div>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openCustDialog(cust)}>
                              <Edit className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost" size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => handleDeleteCustomer(cust.id, cust.name)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      </motion.div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════════════ TAB: IMPORTAR ════════════ */}
        <TabsContent value="import" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Upload className="h-5 w-5 text-primary" />
                Importação de Faturas e Ficheiros
              </CardTitle>
              <CardDescription>
                Importe faturas em massa a partir de ficheiros CSV ou JSON
              </CardDescription>
            </CardHeader>
            <CardContent>
              <InvoiceImport
                customers={customers}
                onImportDone={loadData}
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ══════════ MODAL: Criar/Editar Fatura ══════════ */}
      <Dialog open={isInvDialogOpen} onOpenChange={v => { if (!v) closeInvDialog() }}>
        <DialogContent className="max-w-3xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingInv ? `Editar ${editingInv.invoice_number}` : 'Nova Fatura'}</DialogTitle>
            <DialogDescription>
              {editingInv ? 'Actualize os dados da fatura' : 'Preencha os dados para criar uma nova fatura'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-5 mt-2">
            {/* Cliente + Série */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="sm:col-span-2 space-y-1">
                <Label>Cliente <span className="text-destructive">*</span></Label>
                <Select
                  value={invForm.customer_id || 'none'}
                  onValueChange={v => setInvForm(f => ({ ...f, customer_id: v === 'none' ? '' : v }))}
                >
                  <SelectTrigger><SelectValue placeholder="Selecione um cliente" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Selecione —</SelectItem>
                    {customers.filter(c => c.is_active).map(c => (
                      <SelectItem key={c.id} value={c.id}>{c.name}{c.nif ? ` (${c.nif})` : ''}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Série</Label>
                <Select value={invForm.series} onValueChange={v => setInvForm(f => ({ ...f, series: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="FT">FT — Fatura</SelectItem>
                    <SelectItem value="FS">FS — Fatura Simplificada</SelectItem>
                    <SelectItem value="FR">FR — Fatura-Recibo</SelectItem>
                    <SelectItem value="NC">NC — Nota de Crédito</SelectItem>
                    <SelectItem value="ND">ND — Nota de Débito</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Datas */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="space-y-1">
                <Label>Data Emissão <span className="text-destructive">*</span></Label>
                <Input type="date" value={invForm.issue_date} onChange={e => setInvForm(f => ({ ...f, issue_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Data Vencimento <span className="text-destructive">*</span></Label>
                <Input type="date" value={invForm.due_date} onChange={e => setInvForm(f => ({ ...f, due_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Moeda</Label>
                <Select value={invForm.currency} onValueChange={v => setInvForm(f => ({ ...f, currency: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="AOA">AOA — Kwanza</SelectItem>
                    <SelectItem value="USD">USD — Dólar</SelectItem>
                    <SelectItem value="EUR">EUR — Euro</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Método Pagamento</Label>
                <Select
                  value={invForm.payment_method || 'none'}
                  onValueChange={v => setInvForm(f => ({ ...f, payment_method: v === 'none' ? '' : v }))}
                >
                  <SelectTrigger><SelectValue placeholder="Opcional" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">Não especificado</SelectItem>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <Separator />

            {/* Itens da fatura */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-sm">Linhas da Fatura</h3>
                <Button variant="outline" size="sm" onClick={addItem}>
                  <Plus className="h-3.5 w-3.5 mr-1.5" /> Adicionar Linha
                </Button>
              </div>

              <div className="space-y-2">
                {/* Header colunas */}
                <div className="hidden sm:grid sm:grid-cols-[3fr_1fr_1fr_1fr_1fr_auto] gap-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  <span>Descrição</span>
                  <span>Qtd</span>
                  <span>Preço Unit.</span>
                  <span>Desc.%</span>
                  <span>IVA</span>
                  <span></span>
                </div>

                {invItems.map((item, idx) => {
                  const c = calcItem({ quantity: item.quantity, unit_price: item.unit_price, discount_percent: item.discount_percent || 0, iva_percent: item.iva_percent })
                  return (
                    <motion.div
                      key={idx}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="grid grid-cols-1 sm:grid-cols-[3fr_1fr_1fr_1fr_1fr_auto] gap-2 items-start rounded-lg bg-muted/20 p-2"
                    >
                      <Input
                        placeholder="Descrição do produto / serviço"
                        value={item.description}
                        onChange={e => updateItem(idx, 'description', e.target.value)}
                        className="text-sm"
                      />
                      <Input
                        type="number" min="0.001" step="0.001" placeholder="Qtd"
                        value={item.quantity}
                        onChange={e => updateItem(idx, 'quantity', parseFloat(e.target.value) || 0)}
                        className="text-sm"
                      />
                      <Input
                        type="number" min="0" step="0.01" placeholder="0.00"
                        value={item.unit_price}
                        onChange={e => updateItem(idx, 'unit_price', parseFloat(e.target.value) || 0)}
                        className="text-sm"
                      />
                      <Input
                        type="number" min="0" max="100" step="1" placeholder="0"
                        value={item.discount_percent || 0}
                        onChange={e => updateItem(idx, 'discount_percent', parseFloat(e.target.value) || 0)}
                        className="text-sm"
                      />
                      <Select
                        value={item.iva_rate}
                        onValueChange={v => updateItem(idx, 'iva_rate', v)}
                      >
                        <SelectTrigger className="text-sm"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(Object.keys(IVA_LABELS) as IvaRate[]).map(r => (
                            <SelectItem key={r} value={r}>{IVA_LABELS[r]}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <div className="flex flex-col items-end gap-1">
                        <span className="text-xs font-semibold text-green-700">{fmt(c.total)}</span>
                        <button onClick={() => removeItem(idx)} disabled={invItems.length === 1}>
                          <X className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive transition-colors" />
                        </button>
                      </div>
                    </motion.div>
                  )
                })}
              </div>

              {/* Totais */}
              <div className="mt-3 flex justify-end">
                <div className="space-y-1.5 text-sm min-w-52">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Subtotal</span>
                    <span>{fmt(invTotals.subtotal)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">IVA Total</span>
                    <span>{fmt(invTotals.iva_amount)}</span>
                  </div>
                  <Separator />
                  <div className="flex justify-between font-bold text-base">
                    <span>Total</span>
                    <span className="text-green-700">{fmt(invTotals.total)}</span>
                  </div>
                </div>
              </div>
            </div>

            <Separator />

            {/* Notas */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Notas para o Cliente</Label>
                <Textarea
                  placeholder="Ex: Condições de pagamento, agradecimentos..."
                  rows={3}
                  value={invForm.notes}
                  onChange={e => setInvForm(f => ({ ...f, notes: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <Label>Notas Internas</Label>
                <Textarea
                  placeholder="Notas de uso interno (não aparecem na fatura)"
                  rows={3}
                  value={invForm.internal_notes}
                  onChange={e => setInvForm(f => ({ ...f, internal_notes: e.target.value }))}
                />
              </div>
            </div>

            <div className="flex gap-2 justify-end pt-1">
              <Button variant="outline" onClick={closeInvDialog} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveInvoice} disabled={submitting || invTotals.total <= 0}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingInv ? 'Guardar Alterações' : 'Criar Fatura'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════════ MODAL: Detalhe da Fatura ══════════ */}
      <Dialog open={isDetailOpen} onOpenChange={setIsDetailOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              {detailInv?.invoice_number}
              {detailInv && <StatusBadge status={detailInv.status} />}
            </DialogTitle>
            <DialogDescription>
              {detailInv?.customer_name} {detailInv?.customer_nif ? `— NIF: ${detailInv.customer_nif}` : ''}
            </DialogDescription>
          </DialogHeader>
          {detailInv && (
            <div className="space-y-4">
              {/* Info */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                {[
                  { label: 'Data Emissão',  value: fmtDate(detailInv.issue_date) },
                  { label: 'Data Vencimento', value: fmtDate(detailInv.due_date) },
                  { label: 'Moeda',         value: detailInv.currency },
                  { label: 'Pagamento',     value: detailInv.payment_method || '—' },
                  ...(detailInv.payment_date ? [{ label: 'Pago em', value: fmtDate(detailInv.payment_date) }] : []),
                ].map((r, i) => (
                  <div key={i}>
                    <p className="text-xs text-muted-foreground">{r.label}</p>
                    <p className="font-medium">{r.value}</p>
                  </div>
                ))}
              </div>

              {/* Itens */}
              {detailInv.items && detailInv.items.length > 0 && (
                <div>
                  <Separator className="my-3" />
                  <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide mb-2">Linhas da Fatura</p>
                  <div className="space-y-1">
                    <div className="grid grid-cols-[3fr_1fr_1fr_1fr] gap-2 text-xs font-medium text-muted-foreground pb-1">
                      <span>Descrição</span>
                      <span className="text-right">Qtd × Preço</span>
                      <span className="text-right">IVA</span>
                      <span className="text-right">Total</span>
                    </div>
                    {detailInv.items.map((it, i) => (
                      <div key={i} className="grid grid-cols-[3fr_1fr_1fr_1fr] gap-2 text-sm py-1.5 border-b last:border-0">
                        <span>{it.description}</span>
                        <span className="text-right text-muted-foreground">{Number(it.quantity)} × {fmt(Number(it.unit_price))}</span>
                        <span className="text-right text-muted-foreground">{Number(it.iva_percent).toFixed(0)}%</span>
                        <span className="text-right font-medium">{fmt(Number(it.total))}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Totais */}
              <div className="rounded-lg bg-muted/30 p-4 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-muted-foreground">Subtotal</span><span>{fmt(Number(detailInv.subtotal))}</span></div>
                <div className="flex justify-between"><span className="text-muted-foreground">IVA</span><span>{fmt(Number(detailInv.iva_amount))}</span></div>
                {Number(detailInv.discount_amount) > 0 && (
                  <div className="flex justify-between"><span className="text-muted-foreground">Desconto</span><span className="text-red-600">- {fmt(Number(detailInv.discount_amount))}</span></div>
                )}
                <Separator />
                <div className="flex justify-between font-bold text-base">
                  <span>Total a Pagar</span>
                  <span className="text-green-700">{fmt(Number(detailInv.total))}</span>
                </div>
              </div>

              {detailInv.notes && <p className="text-sm text-muted-foreground"><strong>Notas:</strong> {detailInv.notes}</p>}

              {/* Acções */}
              <div className="flex flex-wrap gap-2 pt-2">
                <Button variant="outline" size="sm" onClick={() => invoiceService.printInvoice(detailInv)}>
                  <Printer className="h-4 w-4 mr-2" /> Imprimir
                </Button>
                {detailInv.status === 'DRAFT' && (
                  <Button size="sm" variant="outline" onClick={() => { handleStatusChange(detailInv.id, 'SENT', 'Enviada'); setIsDetailOpen(false) }}>
                    <Send className="h-4 w-4 mr-2 text-blue-600" /> Marcar Enviada
                  </Button>
                )}
                {['DRAFT','SENT','OVERDUE'].includes(detailInv.status) && (
                  <Button size="sm" onClick={() => { handleStatusChange(detailInv.id, 'PAID', 'Paga'); setIsDetailOpen(false) }}>
                    <CheckCircle2 className="h-4 w-4 mr-2" /> Marcar Paga
                  </Button>
                )}
                <Button variant="ghost" size="sm" className="ml-auto" onClick={() => setIsDetailOpen(false)}>Fechar</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ══════════ MODAL: Criar/Editar Cliente ══════════ */}
      <Dialog open={isCustDialogOpen} onOpenChange={v => { if (!v) { setIsCustDialogOpen(false); setEditingCust(null) } }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingCust ? 'Editar Cliente' : 'Novo Cliente'}</DialogTitle>
            <DialogDescription>Dados de identificação e contacto</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label>Nome Comercial <span className="text-destructive">*</span></Label>
                <Input placeholder="Ex: Petro Angola, SA" value={custForm.name} onChange={e => setCustForm(f => ({ ...f, name: e.target.value }))} />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Designação Legal</Label>
                <Input placeholder="Denominação social completa" value={custForm.legal_name} onChange={e => setCustForm(f => ({ ...f, legal_name: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>NIF</Label>
                <Input placeholder="5000000000LA000" value={custForm.nif} onChange={e => setCustForm(f => ({ ...f, nif: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Tipo</Label>
                <Select value={custForm.customer_type} onValueChange={v => setCustForm(f => ({ ...f, customer_type: v as 'BUSINESS' | 'INDIVIDUAL' | 'PUBLIC' }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="BUSINESS">Empresa</SelectItem>
                    <SelectItem value="INDIVIDUAL">Particular</SelectItem>
                    <SelectItem value="PUBLIC">Entidade Pública</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input type="email" placeholder="financeiro@empresa.ao" value={custForm.email} onChange={e => setCustForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Telefone</Label>
                <Input placeholder="+244 9xx xxx xxx" value={custForm.phone} onChange={e => setCustForm(f => ({ ...f, phone: e.target.value }))} />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Morada</Label>
                <Input placeholder="Rua, nº, bairro" value={custForm.address} onChange={e => setCustForm(f => ({ ...f, address: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Cidade</Label>
                <Input placeholder="Luanda" value={custForm.city} onChange={e => setCustForm(f => ({ ...f, city: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Prazo de Pagamento (dias)</Label>
                <Input type="number" min="0" value={custForm.payment_terms} onChange={e => setCustForm(f => ({ ...f, payment_terms: parseInt(e.target.value) || 30 }))} />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Notas</Label>
                <Input placeholder="Observações opcionais" value={custForm.notes} onChange={e => setCustForm(f => ({ ...f, notes: e.target.value }))} />
              </div>
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <Button variant="outline" onClick={() => { setIsCustDialogOpen(false); setEditingCust(null) }} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveCustomer} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingCust ? 'Guardar Alterações' : 'Criar Cliente'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
