import { useState, useEffect, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Calculator, Download, Users, DollarSign, TrendingUp,
  FileText, RefreshCw, Search, Eye, Plus, Edit, Trash2,
  Loader2, CheckCircle2, Clock, XCircle, ChevronDown,
  Building2, CreditCard, AlertTriangle, X, UserPlus,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { toast } from 'sonner'
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts'
import {
  getActiveEmployees, getAllEmployees, createEmployee, updateEmployee, deleteEmployee,
  getPayslipsByMonth, getEmployeePayslips, processMonthlyPayroll, updatePayslipStatus,
  deletePayslip, upsertPayslip, getPayrollStats, exportPayrollCSV,
  calculateFullPayroll, calculateIRT, calculateINSS, toPayrollMonth,
  type Employee, type Payslip, type PayrollStats,
} from '@/services/payrollServiceReal'

// ─── Constantes ──────────────────────────────────────────────────────────────
const MONTHS = [
  { v: '1',  l: 'Janeiro'   }, { v: '2',  l: 'Fevereiro' }, { v: '3',  l: 'Março'    },
  { v: '4',  l: 'Abril'     }, { v: '5',  l: 'Maio'      }, { v: '6',  l: 'Junho'    },
  { v: '7',  l: 'Julho'     }, { v: '8',  l: 'Agosto'    }, { v: '9',  l: 'Setembro' },
  { v: '10', l: 'Outubro'   }, { v: '11', l: 'Novembro'  }, { v: '12', l: 'Dezembro' },
]
const YEARS = ['2026', '2025', '2024']
const DEPARTMENTS = ['Financeiro', 'Contabilidade', 'TI', 'Comercial', 'Logística', 'RH', 'Jurídico', 'Administração']
const EMPLOYMENT_TYPES = [
  { v: 'FULL_TIME', l: 'Tempo Inteiro' },
  { v: 'PART_TIME', l: 'Tempo Parcial' },
  { v: 'CONTRACT', l: 'Contrato' },
]

const formatKz = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)

// ─── Status Badge ─────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  if (status === 'PAID') return (
    <Badge className="bg-green-100 text-green-700 border-green-200 gap-1 text-xs">
      <CheckCircle2 className="h-3 w-3" /> Pago
    </Badge>
  )
  if (status === 'CANCELLED') return (
    <Badge className="bg-red-100 text-red-700 border-red-200 gap-1 text-xs">
      <XCircle className="h-3 w-3" /> Cancelado
    </Badge>
  )
  return (
    <Badge className="bg-amber-100 text-amber-700 border-amber-200 gap-1 text-xs">
      <Clock className="h-3 w-3" /> Pendente
    </Badge>
  )
}

// ─── Formulário de Funcionário (vazio) ───────────────────────────────────────
const EMPTY_EMP: Omit<Employee, 'id' | 'tenant_id' | 'created_at' | 'updated_at'> = {
  full_name: '', email: '', phone: '', position: '', department: 'Financeiro',
  hire_date: new Date().toISOString().split('T')[0], gross_salary: 150000,
  employment_type: 'FULL_TIME', status: 'ACTIVE', employee_number: '', nif: '',
  bank_name: '', bank_account: '', notes: '',
}

// ─── Formulário de Bónus ─────────────────────────────────────────────────────
interface BonusForm { allowances: string; bonuses: string; overtime: string; other_deductions: string }
const EMPTY_BONUS: BonusForm = { allowances: '0', bonuses: '0', overtime: '0', other_deductions: '0' }

// ═══════════════════════════════════════════════════════════════════════════════
export default function Payroll() {
  const now = new Date()
  const [selectedMonth, setSelectedMonth] = useState(String(now.getMonth() + 1))
  const [selectedYear, setSelectedYear]   = useState(String(now.getFullYear()))

  const [employees, setEmployees]   = useState<Employee[]>([])
  const [payslips, setPayslips]     = useState<Payslip[]>([])
  const [stats, setStats]           = useState<PayrollStats | null>(null)
  const [loading, setLoading]       = useState(true)
  const [processing, setProcessing] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [activeTab, setActiveTab]   = useState('payroll')

  // Diálogos
  const [detailPayslip, setDetailPayslip]       = useState<Payslip | null>(null)
  const [detailEmpHistory, setDetailEmpHistory] = useState<Payslip[]>([])
  const [isDetailOpen, setIsDetailOpen]         = useState(false)
  const [isEmpDialogOpen, setIsEmpDialogOpen]   = useState(false)
  const [editingEmp, setEditingEmp]             = useState<Employee | null>(null)
  const [empForm, setEmpForm]                   = useState(EMPTY_EMP)
  const [submittingEmp, setSubmittingEmp]       = useState(false)
  const [isBonusOpen, setIsBonusOpen]           = useState(false)
  const [bonusEmployee, setBonusEmployee]       = useState<Employee | null>(null)
  const [bonusForm, setBonusForm]               = useState(EMPTY_BONUS)
  const [simulatorSalary, setSimulatorSalary]   = useState('200000')

  // ─── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const month = parseInt(selectedMonth)
      const year  = parseInt(selectedYear)
      const [empData, psData, statsData] = await Promise.all([
        getAllEmployees(),
        getPayslipsByMonth(month, year),
        getPayrollStats(month, year),
      ])
      setEmployees(empData)
      setPayslips(psData)
      setStats(statsData)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados da folha de pagamento')
    } finally {
      setLoading(false)
    }
  }, [selectedMonth, selectedYear])

  useEffect(() => { loadData() }, [loadData])

  // ─── Filtro de pesquisa ──────────────────────────────────────────────────
  const filteredPayslips = payslips.filter(p =>
    !searchQuery || p.employee_name.toLowerCase().includes(searchQuery.toLowerCase())
  )
  const filteredEmployees = employees.filter(e =>
    !searchQuery || e.full_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    (e.department || '').toLowerCase().includes(searchQuery.toLowerCase())
  )

  // ─── Processar folha ─────────────────────────────────────────────────────
  const handleProcessPayroll = async () => {
    const month = parseInt(selectedMonth)
    const year  = parseInt(selectedYear)
    const monthLabel = MONTHS.find(m => m.v === selectedMonth)?.l
    if (!confirm(`Processar folha de pagamento de ${monthLabel} ${year} para todos os funcionários activos?`)) return
    setProcessing(true)
    try {
      const result = await processMonthlyPayroll(month, year)
      toast.success(`Folha processada: ${result.length} recibos gerados`)
      await loadData()
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Erro ao processar folha'
      toast.error(msg)
    } finally {
      setProcessing(false)
    }
  }

  // ─── Actualizar estado recibo ─────────────────────────────────────────────
  const handleUpdateStatus = async (
    payslipId: string,
    status: 'PENDING' | 'PAID' | 'CANCELLED',
    label: string,
  ) => {
    try {
      const paymentDate = status === 'PAID' ? new Date().toISOString().split('T')[0] : undefined
      await updatePayslipStatus(payslipId, status, paymentDate)
      toast.success(`Recibo marcado como ${label}`)
      await loadData()
    } catch {
      toast.error('Erro ao actualizar estado do recibo')
    }
  }

  // ─── Eliminar recibo ─────────────────────────────────────────────────────
  const handleDeletePayslip = async (id: string) => {
    if (!confirm('Confirma a eliminação deste recibo?')) return
    try {
      await deletePayslip(id)
      toast.success('Recibo eliminado')
      await loadData()
    } catch { toast.error('Erro ao eliminar recibo') }
  }

  // ─── Detalhe recibo ──────────────────────────────────────────────────────
  const handleViewDetail = async (payslip: Payslip) => {
    setDetailPayslip(payslip)
    const history = await getEmployeePayslips(payslip.employee_id, 6)
    setDetailEmpHistory(history)
    setIsDetailOpen(true)
  }

  // ─── CRUD Funcionários ────────────────────────────────────────────────────
  const handleOpenEmpDialog = (emp?: Employee) => {
    if (emp) {
      setEditingEmp(emp)
      setEmpForm({
        full_name: emp.full_name, email: emp.email || '', phone: emp.phone || '',
        position: emp.position, department: emp.department || 'Financeiro',
        hire_date: emp.hire_date, gross_salary: emp.gross_salary,
        employment_type: emp.employment_type, status: emp.status,
        employee_number: emp.employee_number || '', nif: emp.nif || '',
        bank_name: emp.bank_name || '', bank_account: emp.bank_account || '', notes: emp.notes || '',
      })
    } else {
      setEditingEmp(null)
      setEmpForm(EMPTY_EMP)
    }
    setIsEmpDialogOpen(true)
  }

  const handleSubmitEmp = async () => {
    if (!empForm.full_name.trim() || !empForm.position.trim() || !empForm.hire_date) {
      toast.error('Preencha: Nome, Cargo e Data de Admissão')
      return
    }
    if (empForm.gross_salary <= 0) { toast.error('Salário deve ser positivo'); return }
    setSubmittingEmp(true)
    try {
      if (editingEmp) {
        await updateEmployee(editingEmp.id, empForm)
        toast.success('Funcionário actualizado!')
      } else {
        await createEmployee(empForm)
        toast.success('Funcionário criado!')
      }
      setIsEmpDialogOpen(false)
      setEditingEmp(null)
      await loadData()
    } catch (err) {
      console.error(err)
      toast.error('Erro ao guardar funcionário')
    } finally { setSubmittingEmp(false) }
  }

  const handleDeleteEmployee = async (id: string, name: string) => {
    if (!confirm(`Eliminar funcionário "${name}"? Esta acção é irreversível.`)) return
    try {
      await deleteEmployee(id)
      toast.success('Funcionário eliminado')
      await loadData()
    } catch { toast.error('Erro ao eliminar funcionário') }
  }

  // ─── Recalcular com bónus ─────────────────────────────────────────────────
  const handleOpenBonus = (emp: Employee) => {
    setBonusEmployee(emp)
    setBonusForm(EMPTY_BONUS)
    setIsBonusOpen(true)
  }

  const handleSaveBonus = async () => {
    if (!bonusEmployee) return
    try {
      await upsertPayslip(
        bonusEmployee.id,
        parseInt(selectedMonth),
        parseInt(selectedYear),
        parseFloat(bonusForm.bonuses) || 0,
        parseFloat(bonusForm.allowances) || 0,
        parseFloat(bonusForm.overtime) || 0,
        parseFloat(bonusForm.other_deductions) || 0,
      )
      toast.success(`Recibo de ${bonusEmployee.full_name} actualizado!`)
      setIsBonusOpen(false)
      await loadData()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Erro ao guardar'
      toast.error(msg)
    }
  }

  // ─── Simulador ───────────────────────────────────────────────────────────
  const simSalary = parseFloat(simulatorSalary) || 0
  const simCalc   = simSalary > 0 ? calculateFullPayroll(simSalary) : null
  const simIRT    = simSalary > 0 ? calculateIRT(simSalary) : null
  const simINSS   = simSalary > 0 ? calculateINSS(simSalary) : null

  // ─── Gráfico de barras (salários da folha actual) ─────────────────────────
  const chartData = payslips.slice(0, 10).map(p => ({
    name: p.employee_name.split(' ')[0],
    bruto: Number(p.gross_salary),
    irt: Number(p.irt),
    inss: Number(p.inss_employee),
    liquido: Number(p.net_salary),
  }))

  // ─── Skeleton ────────────────────────────────────────────────────────────
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

  const monthLabel = MONTHS.find(m => m.v === selectedMonth)?.l

  // ═════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">
      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Folha de Pagamento</h1>
          <p className="text-muted-foreground">Processamento de salários com IRT e INSS Angola</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
          <Button
            variant="outline" size="sm"
            onClick={() => exportPayrollCSV(payslips, parseInt(selectedMonth), parseInt(selectedYear))}
            disabled={payslips.length === 0}
          >
            <Download className="h-4 w-4 mr-2" /> Exportar CSV
          </Button>
          <Button size="sm" onClick={handleProcessPayroll} disabled={processing}>
            {processing
              ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> A processar...</>
              : <><Calculator className="h-4 w-4 mr-2" /> Processar Folha</>}
          </Button>
        </div>
      </div>

      {/* ── Selecção mês/ano ── */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="flex items-center gap-2">
          <Label className="text-sm font-medium whitespace-nowrap">Mês:</Label>
          <Select value={selectedMonth} onValueChange={setSelectedMonth}>
            <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
            <SelectContent>
              {MONTHS.map(m => <SelectItem key={m.v} value={m.v}>{m.l}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Label className="text-sm font-medium">Ano:</Label>
          <Select value={selectedYear} onValueChange={setSelectedYear}>
            <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
            <SelectContent>
              {YEARS.map(y => <SelectItem key={y} value={y}>{y}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        {payslips.length === 0 && (
          <Badge variant="outline" className="text-amber-600 border-amber-300 bg-amber-50 gap-1">
            <AlertTriangle className="h-3 w-3" />
            Sem folha processada para {monthLabel} {selectedYear}
          </Badge>
        )}
        {payslips.length > 0 && (
          <Badge variant="outline" className="text-green-700 border-green-300 bg-green-50 gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {payslips.length} recibos — {monthLabel} {selectedYear}
          </Badge>
        )}
      </div>

      {/* ── Cards de métricas ── */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: 'Total Bruto',
              value: formatKz(stats.total_gross),
              icon: <DollarSign className="h-5 w-5 text-blue-600" />,
              bg: 'bg-blue-50',
              sub: `${stats.total_employees} funcionários`,
              color: 'text-blue-600',
            },
            {
              title: 'Total Líquido',
              value: formatKz(stats.total_net),
              icon: <CreditCard className="h-5 w-5 text-green-600" />,
              bg: 'bg-green-50',
              sub: `${stats.paid_count} pagos / ${stats.pending_count} pendentes`,
              color: 'text-green-600',
            },
            {
              title: 'Total IRT',
              value: formatKz(stats.total_irt),
              icon: <FileText className="h-5 w-5 text-purple-600" />,
              bg: 'bg-purple-50',
              sub: 'Retido na fonte (AGT)',
              color: 'text-purple-600',
            },
            {
              title: 'Custo Total Empresa',
              value: formatKz(stats.total_cost),
              icon: <Building2 className="h-5 w-5 text-orange-600" />,
              bg: 'bg-orange-50',
              sub: `INSS empregador: ${formatKz(stats.total_inss_employer)}`,
              color: 'text-orange-600',
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

      {/* ── Tabs principais ── */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="grid grid-cols-4 w-full max-w-lg">
          <TabsTrigger value="payroll">
            <FileText className="h-4 w-4 mr-2" /> Recibos
          </TabsTrigger>
          <TabsTrigger value="employees">
            <Users className="h-4 w-4 mr-2" /> Funcionários
          </TabsTrigger>
          <TabsTrigger value="charts">
            <TrendingUp className="h-4 w-4 mr-2" /> Análise
          </TabsTrigger>
          <TabsTrigger value="simulator">
            <Calculator className="h-4 w-4 mr-2" /> Simulador
          </TabsTrigger>
        </TabsList>

        {/* ══════════════ TAB: RECIBOS ══════════════ */}
        <TabsContent value="payroll" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Recibos de Salário — {monthLabel} {selectedYear}</CardTitle>
                  <CardDescription>{filteredPayslips.length} recibos</CardDescription>
                </div>
                <div className="relative w-52">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input placeholder="Pesquisar..." className="pl-9" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
                  {searchQuery && <button onClick={() => setSearchQuery('')} className="absolute right-3 top-1/2 -translate-y-1/2"><X className="h-3 w-3" /></button>}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {filteredPayslips.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                  <Calculator className="h-12 w-12 opacity-20" />
                  <p className="text-sm font-medium">Sem recibos para este período</p>
                  <p className="text-xs text-center max-w-xs">
                    Clique em <strong>Processar Folha</strong> para gerar os recibos de {monthLabel} {selectedYear}
                  </p>
                  <Button size="sm" onClick={handleProcessPayroll} disabled={processing}>
                    {processing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Calculator className="h-4 w-4 mr-2" />}
                    Processar Folha
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {/* Header */}
                  <div className="hidden md:grid md:grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr_auto] gap-3 text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 pb-1">
                    <span>Funcionário</span>
                    <span className="text-right">Bruto</span>
                    <span className="text-right">IRT</span>
                    <span className="text-right">INSS</span>
                    <span className="text-right">Líquido</span>
                    <span className="text-center">Estado</span>
                    <span className="text-center">Acções</span>
                  </div>
                  <Separator />
                  <AnimatePresence>
                    {filteredPayslips.map((p, idx) => (
                      <motion.div
                        key={p.id}
                        initial={{ opacity: 0, x: -8 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 8 }}
                        transition={{ delay: idx * 0.03 }}
                        className="grid grid-cols-1 md:grid-cols-[2fr_1fr_1fr_1fr_1fr_1fr_auto] gap-3 items-center rounded-lg border px-3 py-3 hover:bg-muted/30 transition-colors"
                      >
                        <div>
                          <p className="font-medium text-sm">{p.employee_name}</p>
                          {p.employee_nif && <p className="text-xs text-muted-foreground">NIF: {p.employee_nif}</p>}
                          {p.payment_date && (
                            <p className="text-xs text-muted-foreground">Pago: {new Date(p.payment_date).toLocaleDateString('pt-AO')}</p>
                          )}
                        </div>
                        <div className="md:text-right">
                          <span className="text-xs text-muted-foreground md:hidden">Bruto: </span>
                          <span className="font-semibold text-sm">{formatKz(Number(p.gross_salary))}</span>
                        </div>
                        <div className="md:text-right">
                          <span className="text-xs text-muted-foreground md:hidden">IRT: </span>
                          <span className="text-sm text-red-600">{formatKz(Number(p.irt))}</span>
                        </div>
                        <div className="md:text-right">
                          <span className="text-xs text-muted-foreground md:hidden">INSS: </span>
                          <span className="text-sm text-orange-600">{formatKz(Number(p.inss_employee))}</span>
                        </div>
                        <div className="md:text-right">
                          <span className="text-xs text-muted-foreground md:hidden">Líquido: </span>
                          <span className="font-bold text-sm text-green-600">{formatKz(Number(p.net_salary))}</span>
                        </div>
                        <div className="md:flex md:justify-center">
                          <StatusBadge status={p.payment_status} />
                        </div>
                        <div className="flex gap-1 md:justify-center">
                          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleViewDetail(p)}>
                            <Eye className="h-3.5 w-3.5" />
                          </Button>
                          {p.payment_status === 'PENDING' && (
                            <Button
                              variant="ghost" size="icon"
                              className="h-7 w-7 text-green-600 hover:text-green-700"
                              onClick={() => handleUpdateStatus(p.id, 'PAID', 'Pago')}
                            >
                              <CheckCircle2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                          {p.payment_status !== 'CANCELLED' && (
                            <Button
                              variant="ghost" size="icon"
                              className="h-7 w-7 text-destructive hover:text-destructive"
                              onClick={() => handleDeletePayslip(p.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      </motion.div>
                    ))}
                  </AnimatePresence>

                  {/* Totais */}
                  {stats && (
                    <div className="mt-4 pt-4 border-t grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                      <div className="text-center">
                        <p className="text-muted-foreground text-xs">Total Bruto</p>
                        <p className="font-bold">{formatKz(stats.total_gross)}</p>
                      </div>
                      <div className="text-center">
                        <p className="text-muted-foreground text-xs">Total IRT (AGT)</p>
                        <p className="font-bold text-red-600">{formatKz(stats.total_irt)}</p>
                      </div>
                      <div className="text-center">
                        <p className="text-muted-foreground text-xs">Total INSS (3%)</p>
                        <p className="font-bold text-orange-600">{formatKz(stats.total_inss_employee)}</p>
                      </div>
                      <div className="text-center">
                        <p className="text-muted-foreground text-xs">Total Líquido</p>
                        <p className="font-bold text-green-600">{formatKz(stats.total_net)}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ══════════════ TAB: FUNCIONÁRIOS ══════════════ */}
        <TabsContent value="employees" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Funcionários ({filteredEmployees.length})</CardTitle>
                  <CardDescription>
                    {employees.filter(e => e.status === 'ACTIVE').length} activos de {employees.length} total
                  </CardDescription>
                </div>
                <div className="flex gap-2">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input placeholder="Pesquisar..." className="pl-9 w-48" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
                  </div>
                  <Button size="sm" onClick={() => handleOpenEmpDialog()}>
                    <UserPlus className="h-4 w-4 mr-2" /> Novo
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {filteredEmployees.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground gap-3">
                  <Users className="h-12 w-12 opacity-20" />
                  <p className="text-sm">Nenhum funcionário encontrado</p>
                  <Button size="sm" onClick={() => handleOpenEmpDialog()}>
                    <UserPlus className="h-4 w-4 mr-2" /> Adicionar Funcionário
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredEmployees.map((emp, idx) => {
                    const calc = calculateFullPayroll(emp.gross_salary)
                    return (
                      <motion.div
                        key={emp.id}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: idx * 0.04 }}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border px-4 py-3 hover:bg-muted/30 transition-colors"
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10">
                            <span className="text-sm font-bold text-primary">
                              {emp.full_name.split(' ').map(n => n[0]).slice(0, 2).join('')}
                            </span>
                          </div>
                          <div>
                            <p className="font-medium">{emp.full_name}</p>
                            <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                              <span className="text-xs text-muted-foreground">{emp.position}</span>
                              {emp.department && <Badge variant="outline" className="text-xs py-0">{emp.department}</Badge>}
                              <Badge className={`text-xs py-0 ${emp.status === 'ACTIVE' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                                {emp.status === 'ACTIVE' ? 'Activo' : 'Inactivo'}
                              </Badge>
                            </div>
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-4 text-sm">
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">Salário Bruto</p>
                            <p className="font-semibold">{formatKz(emp.gross_salary)}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">Líquido Estimado</p>
                            <p className="font-semibold text-green-600">{formatKz(calc.net_salary)}</p>
                          </div>
                          <div className="text-right">
                            <p className="text-xs text-muted-foreground">IRT + INSS</p>
                            <p className="text-sm text-red-600">{formatKz(calc.irt + calc.inss_employee)}</p>
                          </div>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleOpenBonus(emp)}>
                              <Plus className="h-3.5 w-3.5" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => handleOpenEmpDialog(emp)}>
                              <Edit className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost" size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive"
                              onClick={() => handleDeleteEmployee(emp.id, emp.full_name)}
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

        {/* ══════════════ TAB: ANÁLISE ══════════════ */}
        <TabsContent value="charts" className="mt-4">
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Distribuição Salarial — {monthLabel} {selectedYear}</CardTitle>
                <CardDescription>Bruto vs IRT vs INSS vs Líquido</CardDescription>
              </CardHeader>
              <CardContent>
                {chartData.length === 0 ? (
                  <div className="flex items-center justify-center h-52 text-muted-foreground text-sm">
                    Sem dados para o período seleccionado
                  </div>
                ) : (
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={chartData} margin={{ bottom: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `${(v / 1000).toFixed(0)}k`} />
                      <Tooltip formatter={(v: number) => formatKz(v)} />
                      <Legend />
                      <Bar dataKey="bruto"  name="Bruto"  fill="#6366F1" radius={[3,3,0,0]} />
                      <Bar dataKey="irt"    name="IRT"    fill="#EF4444" radius={[3,3,0,0]} />
                      <Bar dataKey="inss"   name="INSS"   fill="#F97316" radius={[3,3,0,0]} />
                      <Bar dataKey="liquido" name="Líquido" fill="#22C55E" radius={[3,3,0,0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Resumo Fiscal — {monthLabel} {selectedYear}</CardTitle>
                <CardDescription>Obrigações fiscais da empresa</CardDescription>
              </CardHeader>
              <CardContent>
                {!stats || stats.total_employees === 0 ? (
                  <div className="flex items-center justify-center h-52 text-muted-foreground text-sm">
                    Sem dados para o período seleccionado
                  </div>
                ) : (
                  <div className="space-y-4 mt-2">
                    {[
                      { label: 'IRT a entregar à AGT', value: stats.total_irt, color: 'text-red-600', bg: 'bg-red-50' },
                      { label: 'INSS empregados (3%)', value: stats.total_inss_employee, color: 'text-orange-600', bg: 'bg-orange-50' },
                      { label: 'INSS empregador (8%)', value: stats.total_inss_employer, color: 'text-amber-600', bg: 'bg-amber-50' },
                      { label: 'Total a pagar ao INSS', value: stats.total_inss_employee + stats.total_inss_employer, color: 'text-purple-600', bg: 'bg-purple-50' },
                      { label: 'Custo Total da Empresa', value: stats.total_cost, color: 'text-blue-700', bg: 'bg-blue-50' },
                    ].map((item, i) => (
                      <div key={i} className={`flex items-center justify-between rounded-lg p-3 ${item.bg}`}>
                        <span className="text-sm font-medium">{item.label}</span>
                        <span className={`font-bold text-sm ${item.color}`}>{formatKz(item.value)}</span>
                      </div>
                    ))}
                    <Separator />
                    <div className="flex justify-between text-xs text-muted-foreground">
                      <span>{stats.paid_count} recibos pagos</span>
                      <span>{stats.pending_count} pendentes</span>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* ══════════════ TAB: SIMULADOR IRT/INSS ══════════════ */}
        <TabsContent value="simulator" className="mt-4">
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Simulador de Salário</CardTitle>
                <CardDescription>Calcule IRT e INSS conforme legislação angolana</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-1">
                  <Label>Salário Bruto (AOA)</Label>
                  <Input
                    type="number" min="0" step="1000"
                    placeholder="Ex: 200000"
                    value={simulatorSalary}
                    onChange={e => setSimulatorSalary(e.target.value)}
                    className="text-lg font-mono"
                  />
                </div>

                {simCalc && simIRT && simINSS && (
                  <motion.div
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="space-y-3 pt-2"
                  >
                    <Separator />
                    <div className="grid gap-2">
                      {[
                        { label: 'Salário Bruto', value: formatKz(simCalc.gross_salary), color: 'text-foreground', bold: false },
                        { label: `IRT (Escalão ${simIRT.bracket})`, value: `- ${formatKz(simCalc.irt)}`, color: 'text-red-600', bold: false },
                        { label: 'INSS Empregado (3%)', value: `- ${formatKz(simCalc.inss_employee)}`, color: 'text-orange-600', bold: false },
                        { label: 'Total Deduções', value: `- ${formatKz(simCalc.total_deductions)}`, color: 'text-red-700', bold: true },
                        { label: '💰 Salário Líquido', value: formatKz(simCalc.net_salary), color: 'text-green-600', bold: true },
                      ].map((row, i) => (
                        <div key={i} className={`flex justify-between items-center py-1.5 ${i === 3 ? 'border-t' : ''} ${i === 4 ? 'bg-green-50 rounded-lg px-3 py-2' : ''}`}>
                          <span className={`text-sm ${row.bold ? 'font-semibold' : ''}`}>{row.label}</span>
                          <span className={`text-sm font-mono ${row.color} ${row.bold ? 'font-bold' : ''}`}>{row.value}</span>
                        </div>
                      ))}
                    </div>
                    <Separator />
                    <div className="text-xs text-muted-foreground space-y-1">
                      <p><strong>Custo empresa:</strong> {formatKz(simCalc.gross_salary + simCalc.inss_employer)} (+ INSS empregador {formatKz(simCalc.inss_employer)})</p>
                      <p><strong>Taxa efectiva IRT:</strong> {simCalc.gross_salary > 0 ? ((simCalc.irt / simCalc.gross_salary) * 100).toFixed(1) : 0}%</p>
                    </div>
                  </motion.div>
                )}
              </CardContent>
            </Card>

            {/* Tabela de escalões IRT */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Tabela IRT Angola (Lei n.º 28/11)</CardTitle>
                <CardDescription>Escalões do Imposto sobre o Rendimento do Trabalho</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-1 text-xs">
                  <div className="grid grid-cols-4 font-semibold text-muted-foreground uppercase tracking-wide pb-1 border-b gap-2">
                    <span>Esc.</span>
                    <span>Intervalo (AOA)</span>
                    <span className="text-right">Fixo</span>
                    <span className="text-right">Taxa</span>
                  </div>
                  {[
                    { e:1, min:'0',          max:'70.000',      fixo:'0',          taxa:'0%'     },
                    { e:2, min:'70.001',      max:'100.000',     fixo:'0',          taxa:'13%'    },
                    { e:3, min:'100.001',     max:'150.000',     fixo:'3.900',      taxa:'16%'    },
                    { e:4, min:'150.001',     max:'200.000',     fixo:'11.900',     taxa:'18%'    },
                    { e:5, min:'200.001',     max:'300.000',     fixo:'20.900',     taxa:'19%'    },
                    { e:6, min:'300.001',     max:'500.000',     fixo:'39.900',     taxa:'20%'    },
                    { e:7, min:'500.001',     max:'1.000.000',   fixo:'79.900',     taxa:'21%'    },
                    { e:8, min:'1.000.001',   max:'1.500.000',   fixo:'184.900',    taxa:'22%'    },
                    { e:9, min:'1.500.001',   max:'2.000.000',   fixo:'294.900',    taxa:'23%'    },
                    { e:10, min:'2.000.001',  max:'2.500.000',   fixo:'409.900',    taxa:'24%'    },
                    { e:11, min:'2.500.001',  max:'5.000.000',   fixo:'529.900',    taxa:'24,5%'  },
                    { e:12, min:'5.000.001',  max:'10.000.000',  fixo:'1.142.400',  taxa:'25%'    },
                    { e:13, min:'> 10.000.000', max:'—',         fixo:'2.392.400',  taxa:'25%'    },
                  ].map(row => {
                    const active = simIRT && simIRT.bracket === row.e
                    return (
                      <div
                        key={row.e}
                        className={`grid grid-cols-4 gap-2 py-1 px-1 rounded transition-colors ${active ? 'bg-primary/10 font-semibold' : 'hover:bg-muted/40'}`}
                      >
                        <span className={active ? 'text-primary' : ''}>{row.e}</span>
                        <span className="text-muted-foreground">{row.min} – {row.max}</span>
                        <span className="text-right">{row.fixo}</span>
                        <span className={`text-right font-medium ${active ? 'text-primary' : ''}`}>{row.taxa}</span>
                      </div>
                    )
                  })}
                  <div className="pt-2 border-t text-muted-foreground">
                    <p><strong>INSS:</strong> 3% empregado + 8% empregador (tecto: 500.000 AOA)</p>
                    <p className="mt-0.5">Isenção IRT: salário ≤ 70.000 AOA/mês</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* ══════════════ MODAL: Detalhe do Recibo ══════════════ */}
      <Dialog open={isDetailOpen} onOpenChange={setIsDetailOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Recibo de Salário</DialogTitle>
            <DialogDescription>
              {detailPayslip?.employee_name} — {MONTHS.find(m => m.v === String(parseInt(detailPayslip?.payroll_month?.split('-')[1] || '1')))?.l} {detailPayslip?.payroll_month?.split('-')[0]}
            </DialogDescription>
          </DialogHeader>
          {detailPayslip && (
            <div className="space-y-4">
              {/* Vencimentos */}
              <div className="rounded-lg bg-muted/40 p-4 space-y-2">
                <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Vencimentos</p>
                {[
                  { label: 'Salário Base', value: Number(detailPayslip.gross_salary) },
                  { label: 'Subsídios', value: Number(detailPayslip.allowances) },
                  { label: 'Bónus', value: Number(detailPayslip.bonuses) },
                  { label: 'Horas Extra', value: Number(detailPayslip.overtime) },
                ].filter(r => r.value > 0).map((r, i) => (
                  <div key={i} className="flex justify-between text-sm">
                    <span>{r.label}</span>
                    <span className="font-mono">{formatKz(r.value)}</span>
                  </div>
                ))}
                <Separator />
                <div className="flex justify-between text-sm font-semibold">
                  <span>Total Vencimentos</span>
                  <span>{formatKz(Number(detailPayslip.total_earnings))}</span>
                </div>
              </div>
              {/* Descontos */}
              <div className="rounded-lg bg-red-50 p-4 space-y-2">
                <p className="text-xs font-semibold uppercase text-red-700 tracking-wide">Descontos</p>
                {[
                  { label: `IRT (Escalão ${detailPayslip.irt_bracket || '—'})`, value: Number(detailPayslip.irt) },
                  { label: 'INSS Empregado (3%)', value: Number(detailPayslip.inss_employee) },
                  { label: 'Outros Descontos', value: Number(detailPayslip.other_deductions) },
                ].filter(r => r.value > 0).map((r, i) => (
                  <div key={i} className="flex justify-between text-sm">
                    <span>{r.label}</span>
                    <span className="text-red-600 font-mono">- {formatKz(r.value)}</span>
                  </div>
                ))}
                <Separator />
                <div className="flex justify-between text-sm font-semibold text-red-700">
                  <span>Total Descontos</span>
                  <span>- {formatKz(Number(detailPayslip.total_deductions))}</span>
                </div>
              </div>
              {/* Salário Líquido */}
              <div className="rounded-lg bg-green-50 border border-green-200 p-4 flex justify-between items-center">
                <span className="font-bold text-green-800">Salário Líquido a Receber</span>
                <span className="text-xl font-bold text-green-700">{formatKz(Number(detailPayslip.net_salary))}</span>
              </div>
              {/* Encargos empresa */}
              <div className="rounded-lg bg-blue-50 p-3 flex justify-between items-center text-sm">
                <span className="text-blue-700">Custo Total Empresa (+ INSS 8%)</span>
                <span className="font-bold text-blue-700">{formatKz(Number(detailPayslip.gross_salary) + Number(detailPayslip.inss_employer))}</span>
              </div>
              <div className="flex items-center justify-between pt-2">
                <StatusBadge status={detailPayslip.payment_status} />
                <div className="flex gap-2">
                  {detailPayslip.payment_status === 'PENDING' && (
                    <Button size="sm" onClick={() => {
                      handleUpdateStatus(detailPayslip.id, 'PAID', 'Pago')
                      setIsDetailOpen(false)
                    }}>
                      <CheckCircle2 className="h-4 w-4 mr-2" /> Marcar como Pago
                    </Button>
                  )}
                  <Button variant="outline" size="sm" onClick={() => setIsDetailOpen(false)}>Fechar</Button>
                </div>
              </div>
              {/* Histórico */}
              {detailEmpHistory.length > 1 && (
                <div>
                  <Separator />
                  <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide my-2">Últimos meses</p>
                  <div className="space-y-1">
                    {detailEmpHistory.slice(0, 5).map(h => (
                      <div key={h.id} className="flex justify-between text-xs">
                        <span className="text-muted-foreground">{h.payroll_month}</span>
                        <span>{formatKz(Number(h.net_salary))}</span>
                        <StatusBadge status={h.payment_status} />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ══════════════ MODAL: Criar/Editar Funcionário ══════════════ */}
      <Dialog open={isEmpDialogOpen} onOpenChange={v => { if (!v) { setIsEmpDialogOpen(false); setEditingEmp(null) } }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingEmp ? 'Editar Funcionário' : 'Novo Funcionário'}</DialogTitle>
            <DialogDescription>Preencha os dados do funcionário</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label>Nome Completo <span className="text-destructive">*</span></Label>
                <Input placeholder="João Manuel da Silva" value={empForm.full_name} onChange={e => setEmpForm(f => ({ ...f, full_name: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Nº Funcionário</Label>
                <Input placeholder="EMP-006" value={empForm.employee_number || ''} onChange={e => setEmpForm(f => ({ ...f, employee_number: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>NIF</Label>
                <Input placeholder="5000000000LA000" value={empForm.nif || ''} onChange={e => setEmpForm(f => ({ ...f, nif: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input type="email" placeholder="email@empresa.ao" value={empForm.email || ''} onChange={e => setEmpForm(f => ({ ...f, email: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Telefone</Label>
                <Input placeholder="+244 9xx xxx xxx" value={empForm.phone || ''} onChange={e => setEmpForm(f => ({ ...f, phone: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Cargo <span className="text-destructive">*</span></Label>
                <Input placeholder="Ex: Contabilista" value={empForm.position} onChange={e => setEmpForm(f => ({ ...f, position: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Departamento</Label>
                <Select value={empForm.department || 'Financeiro'} onValueChange={v => setEmpForm(f => ({ ...f, department: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {DEPARTMENTS.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Tipo Contrato</Label>
                <Select value={empForm.employment_type} onValueChange={v => setEmpForm(f => ({ ...f, employment_type: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {EMPLOYMENT_TYPES.map(t => <SelectItem key={t.v} value={t.v}>{t.l}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Data Admissão <span className="text-destructive">*</span></Label>
                <Input type="date" value={empForm.hire_date} onChange={e => setEmpForm(f => ({ ...f, hire_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Salário Bruto (AOA) <span className="text-destructive">*</span></Label>
                <Input
                  type="number" min="0" step="1000"
                  value={empForm.gross_salary}
                  onChange={e => setEmpForm(f => ({ ...f, gross_salary: parseFloat(e.target.value) || 0 }))}
                />
              </div>
              {empForm.gross_salary > 0 && (
                <div className="col-span-2 rounded-lg bg-muted/40 p-3 text-xs space-y-1">
                  {(() => {
                    const c = calculateFullPayroll(empForm.gross_salary)
                    return <>
                      <p>IRT: <strong className="text-red-600">{formatKz(c.irt)}</strong> | INSS: <strong className="text-orange-600">{formatKz(c.inss_employee)}</strong> | Líquido: <strong className="text-green-600">{formatKz(c.net_salary)}</strong></p>
                    </>
                  })()}
                </div>
              )}
              <div className="space-y-1">
                <Label>Estado</Label>
                <Select value={empForm.status} onValueChange={v => setEmpForm(f => ({ ...f, status: v as 'ACTIVE' | 'INACTIVE' | 'TERMINATED' }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ACTIVE">Activo</SelectItem>
                    <SelectItem value="INACTIVE">Inactivo</SelectItem>
                    <SelectItem value="TERMINATED">Terminado</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Banco / NIB</Label>
                <div className="grid grid-cols-2 gap-2">
                  <Input placeholder="Nome do Banco" value={empForm.bank_name || ''} onChange={e => setEmpForm(f => ({ ...f, bank_name: e.target.value }))} />
                  <Input placeholder="Nº Conta / IBAN" value={empForm.bank_account || ''} onChange={e => setEmpForm(f => ({ ...f, bank_account: e.target.value }))} />
                </div>
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Notas</Label>
                <Input placeholder="Observações opcionais" value={empForm.notes || ''} onChange={e => setEmpForm(f => ({ ...f, notes: e.target.value }))} />
              </div>
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <Button variant="outline" onClick={() => { setIsEmpDialogOpen(false); setEditingEmp(null) }} disabled={submittingEmp}>Cancelar</Button>
              <Button onClick={handleSubmitEmp} disabled={submittingEmp}>
                {submittingEmp && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingEmp ? 'Guardar Alterações' : 'Criar Funcionário'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════════════ MODAL: Bónus / Subsídios ══════════════ */}
      <Dialog open={isBonusOpen} onOpenChange={setIsBonusOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Ajustes Salariais</DialogTitle>
            <DialogDescription>
              {bonusEmployee?.full_name} — {monthLabel} {selectedYear}
            </DialogDescription>
          </DialogHeader>
          {bonusEmployee && (
            <div className="space-y-4 mt-2">
              {[
                { key: 'allowances', label: 'Subsídios (AOA)' },
                { key: 'bonuses', label: 'Bónus (AOA)' },
                { key: 'overtime', label: 'Horas Extra (AOA)' },
                { key: 'other_deductions', label: 'Outros Descontos (AOA)' },
              ].map(({ key, label }) => (
                <div key={key} className="space-y-1">
                  <Label>{label}</Label>
                  <Input
                    type="number" min="0" step="100"
                    value={bonusForm[key as keyof BonusForm]}
                    onChange={e => setBonusForm(f => ({ ...f, [key]: e.target.value }))}
                  />
                </div>
              ))}
              {(() => {
                const c = calculateFullPayroll(
                  bonusEmployee.gross_salary,
                  parseFloat(bonusForm.allowances) || 0,
                  parseFloat(bonusForm.bonuses) || 0,
                  parseFloat(bonusForm.overtime) || 0,
                  parseFloat(bonusForm.other_deductions) || 0,
                )
                return (
                  <div className="rounded-lg bg-muted/40 p-3 text-sm space-y-1">
                    <div className="flex justify-between"><span>Total Vencimentos:</span> <span>{formatKz(c.total_earnings)}</span></div>
                    <div className="flex justify-between"><span>Total Descontos:</span> <span className="text-red-600">- {formatKz(c.total_deductions)}</span></div>
                    <Separator />
                    <div className="flex justify-between font-bold"><span>Salário Líquido:</span> <span className="text-green-600">{formatKz(c.net_salary)}</span></div>
                  </div>
                )
              })()}
              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setIsBonusOpen(false)}>Cancelar</Button>
                <Button onClick={handleSaveBonus}>Guardar Recibo</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
