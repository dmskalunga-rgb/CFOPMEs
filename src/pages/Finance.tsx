import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Plus, TrendingUp, TrendingDown, DollarSign, Download,
  Search, RefreshCw, Edit, Trash2, Loader2, Filter,
  X, CheckCircle2, Clock, BarChart3, ArrowUpRight, ArrowDownRight,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader,
  DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { toast } from 'sonner'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, BarChart, Bar,
} from 'recharts'
import {
  financeService, Transaction, TransactionCategory, TransactionFilters,
} from '@/services/financeServiceReal'

// ─── Tipos de pagamento disponíveis ────────────────────────────────────────
const PAYMENT_METHODS = [
  { value: 'TRANSFER', label: 'Transferência Bancária' },
  { value: 'CASH', label: 'Numerário' },
  { value: 'CHEQUE', label: 'Cheque' },
  { value: 'CARD', label: 'TPA / Cartão' },
  { value: 'MOBILE', label: 'Pagamento Móvel' },
]

// ─── Formatador de moeda ────────────────────────────────────────────────────
const formatKz = (v: number) =>
  new Intl.NumberFormat('pt-AO', {
    style: 'currency', currency: 'AOA', minimumFractionDigits: 0,
  }).format(v)

// ─── Badge de tipo ──────────────────────────────────────────────────────────
function TypeBadge({ type }: { type: 'INCOME' | 'EXPENSE' }) {
  return type === 'INCOME' ? (
    <Badge className="bg-green-100 text-green-700 border-green-200 gap-1">
      <ArrowUpRight className="h-3 w-3" /> Receita
    </Badge>
  ) : (
    <Badge className="bg-red-100 text-red-700 border-red-200 gap-1">
      <ArrowDownRight className="h-3 w-3" /> Despesa
    </Badge>
  )
}

// ─── Formulário vazio ───────────────────────────────────────────────────────
const EMPTY_FORM = {
  type: 'INCOME' as 'INCOME' | 'EXPENSE',
  category_id: '',
  description: '',
  amount: '',
  transaction_date: new Date().toISOString().split('T')[0],
  payment_method: 'TRANSFER',
  reference: '',
  notes: '',
}

// ═══════════════════════════════════════════════════════════════════════════
export default function Finance() {
  const [transactions, setTransactions] = useState<Transaction[]>([])
  const [categories, setCategories] = useState<TransactionCategory[]>([])
  const [filtered, setFiltered] = useState<Transaction[]>([])
  const [cashflow, setCashflow] = useState<{ monthLabel: string; receitas: number; despesas: number; saldo: number }[]>([])

  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const [editingTx, setEditingTx] = useState<Transaction | null>(null)
  const [showFilters, setShowFilters] = useState(false)
  const [activeTab, setActiveTab] = useState<'list' | 'charts'>('list')

  // Filtros
  const [filterType, setFilterType] = useState<'all' | 'INCOME' | 'EXPENSE'>('all')
  const [filterCategory, setFilterCategory] = useState('all')
  const [filterMethod, setFilterMethod] = useState('all')
  const [searchTerm, setSearchTerm] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')

  // Formulário
  const [form, setForm] = useState(EMPTY_FORM)

  // ─── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [txData, catData, cfData] = await Promise.all([
        financeService.getTransactions(),
        financeService.getCategories(),
        financeService.getMonthlyCashflow(6),
      ])
      setTransactions(txData)
      setCategories(catData)
      setCashflow(cfData)
      setFiltered(txData)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados financeiros')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Aplicar filtros ─────────────────────────────────────────────────────
  useEffect(() => {
    let result = [...transactions]
    if (filterType !== 'all') result = result.filter(t => t.type === filterType)
    if (filterCategory !== 'all') result = result.filter(t => t.category_id === filterCategory)
    if (filterMethod !== 'all') result = result.filter(t => t.payment_method === filterMethod)
    if (searchTerm) result = result.filter(t =>
      t.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (t.reference || '').toLowerCase().includes(searchTerm.toLowerCase())
    )
    if (startDate) result = result.filter(t => t.transaction_date >= startDate)
    if (endDate) result = result.filter(t => t.transaction_date <= endDate)
    setFiltered(result)
  }, [transactions, filterType, filterCategory, filterMethod, searchTerm, startDate, endDate])

  // ─── Métricas calculadas ─────────────────────────────────────────────────
  const totalIncome = transactions.filter(t => t.type === 'INCOME').reduce((s, t) => s + Number(t.amount), 0)
  const totalExpense = transactions.filter(t => t.type === 'EXPENSE').reduce((s, t) => s + Number(t.amount), 0)
  const balance = totalIncome - totalExpense
  const notReconciled = transactions.filter(t => !t.is_reconciled).length

  // Categorias filtradas por tipo de formulário
  const formCategories = categories.filter(c => c.type === form.type)

  // ─── Criar / Actualizar ──────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!form.description.trim() || !form.amount || !form.transaction_date) {
      toast.error('Preencha: Descrição, Valor e Data')
      return
    }
    const amount = parseFloat(form.amount)
    if (isNaN(amount) || amount <= 0) {
      toast.error('Valor deve ser um número positivo')
      return
    }

    setSubmitting(true)
    try {
      const payload = {
        type: form.type,
        category_id: form.category_id || undefined,
        category_name: categories.find(c => c.id === form.category_id)?.name,
        description: form.description.trim(),
        amount,
        transaction_date: form.transaction_date,
        payment_method: form.payment_method,
        reference: form.reference || undefined,
        notes: form.notes || undefined,
      }

      if (editingTx) {
        await financeService.updateTransaction(editingTx.id, payload)
        toast.success('Transação actualizada com sucesso!')
      } else {
        await financeService.createTransaction(payload)
        toast.success('Transação registada com sucesso!')
      }

      await loadData()
      handleCloseDialog()
    } catch (err) {
      console.error(err)
      toast.error(editingTx ? 'Erro ao actualizar' : 'Erro ao registar transação')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEdit = (tx: Transaction) => {
    setEditingTx(tx)
    setForm({
      type: tx.type,
      category_id: tx.category_id || '',
      description: tx.description,
      amount: String(tx.amount),
      transaction_date: tx.transaction_date,
      payment_method: tx.payment_method || 'TRANSFER',
      reference: tx.reference || '',
      notes: tx.notes || '',
    })
    setIsDialogOpen(true)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Confirma a eliminação desta transação?')) return
    try {
      await financeService.deleteTransaction(id)
      toast.success('Transação eliminada')
      await loadData()
    } catch {
      toast.error('Erro ao eliminar transação')
    }
  }

  const handleCloseDialog = () => {
    setIsDialogOpen(false)
    setEditingTx(null)
    setForm(EMPTY_FORM)
  }

  const clearFilters = () => {
    setFilterType('all')
    setFilterCategory('all')
    setFilterMethod('all')
    setSearchTerm('')
    setStartDate('')
    setEndDate('')
  }

  const hasActiveFilters = filterType !== 'all' || filterCategory !== 'all' ||
    filterMethod !== 'all' || searchTerm || startDate || endDate

  // ─── Skeleton de carregamento ────────────────────────────────────────────
  if (loading) {
    return (
      <div className="space-y-6 p-6">
        <div className="grid gap-4 md:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i}><CardContent className="p-6"><Skeleton className="h-20 w-full" /></CardContent></Card>
          ))}
        </div>
        <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>
      </div>
    )
  }

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">
      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Finanças</h1>
          <p className="text-muted-foreground">Gestão de transações e fluxo de caixa</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => loadData()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
          <Button variant="outline" size="sm" onClick={() => financeService.exportToCSV(filtered, categories)}>
            <Download className="h-4 w-4 mr-2" />
            Exportar CSV
          </Button>
          <Dialog open={isDialogOpen} onOpenChange={(open) => { if (!open) handleCloseDialog(); else setIsDialogOpen(true) }}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="h-4 w-4 mr-2" />
                Nova Transação
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-lg">
              <DialogHeader>
                <DialogTitle>{editingTx ? 'Editar Transação' : 'Nova Transação'}</DialogTitle>
                <DialogDescription>
                  {editingTx ? 'Actualize os dados da transação' : 'Registe uma nova receita ou despesa'}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 mt-2">
                {/* Tipo */}
                <div className="grid grid-cols-2 gap-3">
                  <button
                    type="button"
                    onClick={() => setForm(f => ({ ...f, type: 'INCOME', category_id: '' }))}
                    className={`flex items-center justify-center gap-2 rounded-lg border-2 p-3 text-sm font-medium transition-all ${
                      form.type === 'INCOME'
                        ? 'border-green-500 bg-green-50 text-green-700'
                        : 'border-muted hover:border-green-300'
                    }`}
                  >
                    <TrendingUp className="h-4 w-4" /> Receita
                  </button>
                  <button
                    type="button"
                    onClick={() => setForm(f => ({ ...f, type: 'EXPENSE', category_id: '' }))}
                    className={`flex items-center justify-center gap-2 rounded-lg border-2 p-3 text-sm font-medium transition-all ${
                      form.type === 'EXPENSE'
                        ? 'border-red-500 bg-red-50 text-red-700'
                        : 'border-muted hover:border-red-300'
                    }`}
                  >
                    <TrendingDown className="h-4 w-4" /> Despesa
                  </button>
                </div>

                {/* Categoria */}
                <div className="space-y-1">
                  <Label>Categoria</Label>
                  <Select
                    value={form.category_id || 'none'}
                    onValueChange={v => setForm(f => ({ ...f, category_id: v === 'none' ? '' : v }))}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Selecione uma categoria" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">Sem categoria</SelectItem>
                      {formCategories.map(c => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Descrição */}
                <div className="space-y-1">
                  <Label>Descrição <span className="text-destructive">*</span></Label>
                  <Input
                    placeholder="Ex: Venda de mercadorias"
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  />
                </div>

                {/* Valor + Data */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <Label>Valor (AOA) <span className="text-destructive">*</span></Label>
                    <Input
                      type="number"
                      min="0"
                      step="0.01"
                      placeholder="0.00"
                      value={form.amount}
                      onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>Data <span className="text-destructive">*</span></Label>
                    <Input
                      type="date"
                      value={form.transaction_date}
                      onChange={e => setForm(f => ({ ...f, transaction_date: e.target.value }))}
                    />
                  </div>
                </div>

                {/* Método de pagamento */}
                <div className="space-y-1">
                  <Label>Método de Pagamento</Label>
                  <Select
                    value={form.payment_method}
                    onValueChange={v => setForm(f => ({ ...f, payment_method: v }))}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PAYMENT_METHODS.map(m => (
                        <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Referência */}
                <div className="space-y-1">
                  <Label>Referência / Nº Documento</Label>
                  <Input
                    placeholder="Ex: FAT-2026-001"
                    value={form.reference}
                    onChange={e => setForm(f => ({ ...f, reference: e.target.value }))}
                  />
                </div>

                {/* Notas */}
                <div className="space-y-1">
                  <Label>Notas</Label>
                  <Input
                    placeholder="Observações opcionais"
                    value={form.notes}
                    onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                  />
                </div>

                <div className="flex gap-2 justify-end pt-2">
                  <Button variant="outline" onClick={handleCloseDialog} disabled={submitting}>
                    Cancelar
                  </Button>
                  <Button onClick={handleSubmit} disabled={submitting}>
                    {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    {editingTx ? 'Guardar Alterações' : 'Registar Transação'}
                  </Button>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* ── Cards de métricas ── */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {[
          {
            title: 'Total Receitas',
            value: formatKz(totalIncome),
            icon: <TrendingUp className="h-5 w-5 text-green-600" />,
            bg: 'bg-green-50',
            sub: `${transactions.filter(t => t.type === 'INCOME').length} transações`,
            color: 'text-green-600',
          },
          {
            title: 'Total Despesas',
            value: formatKz(totalExpense),
            icon: <TrendingDown className="h-5 w-5 text-red-600" />,
            bg: 'bg-red-50',
            sub: `${transactions.filter(t => t.type === 'EXPENSE').length} transações`,
            color: 'text-red-600',
          },
          {
            title: 'Saldo Líquido',
            value: formatKz(balance),
            icon: <DollarSign className={`h-5 w-5 ${balance >= 0 ? 'text-green-600' : 'text-red-600'}`} />,
            bg: balance >= 0 ? 'bg-green-50' : 'bg-red-50',
            sub: balance >= 0 ? 'Posição positiva' : 'Posição negativa',
            color: balance >= 0 ? 'text-green-600' : 'text-red-600',
          },
          {
            title: 'Por Reconciliar',
            value: String(notReconciled),
            icon: <Clock className="h-5 w-5 text-orange-500" />,
            bg: 'bg-orange-50',
            sub: `de ${transactions.length} transações`,
            color: 'text-orange-500',
          },
        ].map((m, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.07 }}
          >
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

      {/* ── Tabs: Lista / Gráficos ── */}
      <div className="flex gap-2 border-b pb-0">
        {[
          { key: 'list', label: 'Transações', icon: <Filter className="h-4 w-4" /> },
          { key: 'charts', label: 'Gráficos', icon: <BarChart3 className="h-4 w-4" /> },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key as 'list' | 'charts')}
            className={`flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.key
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* ── Tab: Gráficos ── */}
      {activeTab === 'charts' && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Fluxo de Caixa — Últimos 6 Meses</CardTitle>
            </CardHeader>
            <CardContent>
              {cashflow.length === 0 ? (
                <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                  Sem dados de cashflow disponíveis
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart data={cashflow}>
                    <defs>
                      <linearGradient id="colorIncome" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22C55E" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#22C55E" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="colorExpense" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#EF4444" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#EF4444" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="monthLabel" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} />
                    <Tooltip formatter={(v: number) => formatKz(v)} />
                    <Legend />
                    <Area type="monotone" dataKey="receitas" stroke="#22C55E" fill="url(#colorIncome)" name="Receitas" strokeWidth={2} />
                    <Area type="monotone" dataKey="despesas" stroke="#EF4444" fill="url(#colorExpense)" name="Despesas" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Saldo Mensal</CardTitle>
            </CardHeader>
            <CardContent>
              {cashflow.length === 0 ? (
                <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">
                  Sem dados disponíveis
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={280}>
                  <BarChart data={cashflow}>
                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                    <XAxis dataKey="monthLabel" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} />
                    <Tooltip formatter={(v: number) => formatKz(v)} />
                    <Bar
                      dataKey="saldo"
                      name="Saldo"
                      radius={[4, 4, 0, 0]}
                      fill="#6366F1"
                    />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── Tab: Transações ── */}
      {activeTab === 'list' && (
        <Card>
          <CardHeader>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <CardTitle>Transações ({filtered.length})</CardTitle>
                <CardDescription>
                  {hasActiveFilters
                    ? `${filtered.length} de ${transactions.length} transações filtradas`
                    : `Total de ${transactions.length} transações`}
                </CardDescription>
              </div>
              <div className="flex gap-2">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Pesquisar..."
                    className="pl-9 w-52"
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                  />
                  {searchTerm && (
                    <button onClick={() => setSearchTerm('')} className="absolute right-3 top-1/2 -translate-y-1/2">
                      <X className="h-3 w-3 text-muted-foreground" />
                    </button>
                  )}
                </div>
                <Button
                  variant={showFilters ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setShowFilters(!showFilters)}
                >
                  <Filter className="h-4 w-4 mr-2" />
                  Filtros {hasActiveFilters && <span className="ml-1 bg-primary-foreground text-primary rounded-full w-4 h-4 text-xs flex items-center justify-center">•</span>}
                </Button>
              </div>
            </div>

            {/* Painel de filtros avançados */}
            <AnimatePresence>
              {showFilters && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden"
                >
                  <Separator className="my-3" />
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <div className="space-y-1">
                      <Label className="text-xs">Tipo</Label>
                      <Select value={filterType} onValueChange={v => setFilterType(v as 'all' | 'INCOME' | 'EXPENSE')}>
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todos</SelectItem>
                          <SelectItem value="INCOME">Receitas</SelectItem>
                          <SelectItem value="EXPENSE">Despesas</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Categoria</Label>
                      <Select value={filterCategory} onValueChange={setFilterCategory}>
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todas</SelectItem>
                          {categories.map(c => (
                            <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Método Pagamento</Label>
                      <Select value={filterMethod} onValueChange={setFilterMethod}>
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="all">Todos</SelectItem>
                          {PAYMENT_METHODS.map(m => (
                            <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Data Início</Label>
                      <Input type="date" className="h-8 text-xs" value={startDate} onChange={e => setStartDate(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Data Fim</Label>
                      <Input type="date" className="h-8 text-xs" value={endDate} onChange={e => setEndDate(e.target.value)} />
                    </div>
                    <div className="flex items-end">
                      <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={clearFilters}>
                        <X className="h-3 w-3 mr-1" /> Limpar Filtros
                      </Button>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </CardHeader>

          <CardContent>
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                <DollarSign className="h-12 w-12 opacity-20" />
                <p className="text-sm font-medium">Nenhuma transação encontrada</p>
                {hasActiveFilters && (
                  <Button variant="outline" size="sm" onClick={clearFilters}>
                    Limpar filtros
                  </Button>
                )}
              </div>
            ) : (
              <div className="space-y-1">
                <AnimatePresence>
                  {filtered.map((tx, idx) => {
                    const catName = tx.category_name ||
                      categories.find(c => c.id === tx.category_id)?.name || '—'
                    const method = PAYMENT_METHODS.find(m => m.value === tx.payment_method)?.label || tx.payment_method || '—'

                    return (
                      <motion.div
                        key={tx.id}
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 10 }}
                        transition={{ delay: idx * 0.02 }}
                        className="flex items-center justify-between rounded-lg border px-4 py-3 hover:bg-muted/30 transition-colors"
                      >
                        {/* Ícone */}
                        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full mr-3 ${
                          tx.type === 'INCOME' ? 'bg-green-100' : 'bg-red-100'
                        }`}>
                          {tx.type === 'INCOME'
                            ? <TrendingUp className="h-5 w-5 text-green-600" />
                            : <TrendingDown className="h-5 w-5 text-red-600" />}
                        </div>

                        {/* Info principal */}
                        <div className="flex-1 min-w-0">
                          <p className="font-medium truncate">{tx.description}</p>
                          <div className="flex flex-wrap items-center gap-1.5 mt-1">
                            <TypeBadge type={tx.type} />
                            <Badge variant="outline" className="text-xs">{catName}</Badge>
                            <span className="text-xs text-muted-foreground">{method}</span>
                            <span className="text-xs text-muted-foreground">
                              {new Date(tx.transaction_date).toLocaleDateString('pt-AO')}
                            </span>
                            {tx.is_reconciled && (
                              <span title="Reconciliado"><CheckCircle2 className="h-3.5 w-3.5 text-green-500" /></span>
                            )}
                          </div>
                          {tx.reference && (
                            <p className="text-xs text-muted-foreground mt-0.5">Ref: {tx.reference}</p>
                          )}
                        </div>

                        {/* Valor + Acções */}
                        <div className="flex items-center gap-3 ml-3">
                          <p className={`text-lg font-bold tabular-nums ${
                            tx.type === 'INCOME' ? 'text-green-600' : 'text-red-600'
                          }`}>
                            {tx.type === 'INCOME' ? '+' : '-'}{formatKz(Number(tx.amount))}
                          </p>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleEdit(tx)}>
                              <Edit className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => handleDelete(tx.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>
                      </motion.div>
                    )
                  })}
                </AnimatePresence>
              </div>
            )}

            {/* Rodapé com totais dos filtros activos */}
            {filtered.length > 0 && (
              <div className="mt-4 pt-4 border-t flex flex-wrap gap-4 justify-end text-sm">
                <span className="text-green-600 font-medium">
                  Receitas: {formatKz(filtered.filter(t => t.type === 'INCOME').reduce((s, t) => s + Number(t.amount), 0))}
                </span>
                <span className="text-red-600 font-medium">
                  Despesas: {formatKz(filtered.filter(t => t.type === 'EXPENSE').reduce((s, t) => s + Number(t.amount), 0))}
                </span>
                <span className={`font-bold ${
                  filtered.filter(t => t.type === 'INCOME').reduce((s, t) => s + Number(t.amount), 0) -
                  filtered.filter(t => t.type === 'EXPENSE').reduce((s, t) => s + Number(t.amount), 0) >= 0
                    ? 'text-green-700' : 'text-red-700'
                }`}>
                  Saldo: {formatKz(
                    filtered.filter(t => t.type === 'INCOME').reduce((s, t) => s + Number(t.amount), 0) -
                    filtered.filter(t => t.type === 'EXPENSE').reduce((s, t) => s + Number(t.amount), 0)
                  )}
                </span>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
