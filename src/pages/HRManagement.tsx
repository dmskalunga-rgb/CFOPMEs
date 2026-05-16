import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Users, UserPlus, Calendar, Award, Download, RefreshCw, Edit,
  Trash2, Search, TrendingUp, Loader2, Building2, Phone, Mail,
  Filter, X, CheckCircle2, XCircle, Clock, FileText, Star,
  MoreVertical, ChevronRight, Briefcase, BarChart3, Plus,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
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
import { Progress } from '@/components/ui/progress'
import { toast } from 'sonner'
import {
  employeeService, absenceService, performanceService, contractService, getHRStats,
  ABSENCE_TYPE_LABELS, ABSENCE_STATUS_LABELS, EMPLOYEE_STATUS_LABELS,
  CONTRACT_TYPE_LABELS, EMPLOYMENT_TYPE_LABELS, EVAL_PERIOD_LABELS, DEPARTMENTS,
  type Employee, type EmployeeAbsence, type EmployeePerformance, type EmployeeContract,
  type EmployeeStatus, type AbsenceType, type EvalPeriod, type ContractType, type EmploymentType,
  type HRStats,
} from '@/services/hrServiceReal'

// ─── Formatadores ─────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)

const fmtDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'

// ─── Helpers de estilo ────────────────────────────────────────────────────────
const STATUS_STYLE: Record<EmployeeStatus, { bg: string; text: string; border: string }> = {
  ACTIVE:     { bg: 'bg-green-100',  text: 'text-green-700',  border: 'border-green-200' },
  INACTIVE:   { bg: 'bg-gray-100',   text: 'text-gray-600',   border: 'border-gray-200' },
  ON_LEAVE:   { bg: 'bg-blue-100',   text: 'text-blue-700',   border: 'border-blue-200' },
  TERMINATED: { bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-200' },
}

const ABSENCE_STATUS_STYLE: Record<string, { bg: string; text: string; border: string }> = {
  PENDING:   { bg: 'bg-yellow-100', text: 'text-yellow-700', border: 'border-yellow-200' },
  APPROVED:  { bg: 'bg-green-100',  text: 'text-green-700',  border: 'border-green-200' },
  REJECTED:  { bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-200' },
  CANCELLED: { bg: 'bg-gray-100',   text: 'text-gray-600',   border: 'border-gray-200' },
}

function EmpBadge({ status }: { status: EmployeeStatus }) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.INACTIVE
  return (
    <Badge className={`${s.bg} ${s.text} ${s.border} border text-xs`}>
      {EMPLOYEE_STATUS_LABELS[status] || status}
    </Badge>
  )
}

function AbsBadge({ status }: { status: string }) {
  const s = ABSENCE_STATUS_STYLE[status] || ABSENCE_STATUS_STYLE.PENDING
  return (
    <Badge className={`${s.bg} ${s.text} ${s.border} border text-xs`}>
      {ABSENCE_STATUS_LABELS[status as keyof typeof ABSENCE_STATUS_LABELS] || status}
    </Badge>
  )
}

function ScoreStars({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-1">
      {[1, 2, 3, 4, 5].map(i => (
        <Star
          key={i}
          className={`h-3.5 w-3.5 ${i <= Math.round(score) ? 'text-yellow-400 fill-yellow-400' : 'text-gray-200'}`}
        />
      ))}
      <span className="text-sm font-semibold ml-1">{Number(score).toFixed(1)}</span>
    </div>
  )
}

// ─── Formulário funcionário vazio ─────────────────────────────────────────────
const EMPTY_EMP: Omit<Employee, 'id' | 'tenant_id' | 'created_at' | 'updated_at'> = {
  full_name: '', email: '', phone: '', position: '', department: '',
  hire_date: new Date().toISOString().split('T')[0],
  employment_type: 'FULL_TIME', contract_type: 'PERMANENT',
  gross_salary: 0, status: 'ACTIVE',
  marital_status: 'SINGLE', dependents: 0,
  vacation_days_total: 22, vacation_days_used: 0,
  nif: '', bi_number: '', inss_number: '',
  bank_name: '', bank_account: '',
  emergency_contact_name: '', emergency_contact_phone: '',
  notes: '',
}

// ─── Formulário ausência vazio ────────────────────────────────────────────────
const EMPTY_ABS = {
  employee_id: '',
  absence_type: 'VACATION' as AbsenceType,
  start_date: new Date().toISOString().split('T')[0],
  end_date: new Date(Date.now() + 7 * 86400000).toISOString().split('T')[0],
  reason: '', notes: '',
}

// ─── Formulário avaliação vazio ───────────────────────────────────────────────
const EMPTY_EVAL = {
  employee_id: '',
  evaluation_period: 'QUARTERLY' as EvalPeriod,
  evaluation_date: new Date().toISOString().split('T')[0],
  overall_score: 3,
  productivity_score: 3, quality_score: 3, teamwork_score: 3,
  punctuality_score: 3, initiative_score: 3,
  strengths: '', weaknesses: '', goals: '', comments: '',
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function HRManagement() {
  const [employees,   setEmployees]   = useState<Employee[]>([])
  const [absences,    setAbsences]    = useState<EmployeeAbsence[]>([])
  const [performance, setPerformance] = useState<EmployeePerformance[]>([])
  const [contracts,   setContracts]   = useState<EmployeeContract[]>([])
  const [stats,       setStats]       = useState<HRStats | null>(null)
  const [loading,     setLoading]     = useState(true)
  const [submitting,  setSubmitting]  = useState(false)

  const [activeTab, setActiveTab]   = useState('employees')
  const [searchTerm, setSearchTerm] = useState('')
  const [deptFilter, setDeptFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState<EmployeeStatus | 'all'>('all')

  // Diálogos
  const [empDialog,     setEmpDialog]     = useState(false)
  const [absDialog,     setAbsDialog]     = useState(false)
  const [evalDialog,    setEvalDialog]    = useState(false)
  const [detailDialog,  setDetailDialog]  = useState(false)
  const [editingEmp,    setEditingEmp]    = useState<Employee | null>(null)
  const [detailEmp,     setDetailEmp]     = useState<Employee | null>(null)

  // Forms
  const [empForm,  setEmpForm]  = useState({ ...EMPTY_EMP })
  const [absForm,  setAbsForm]  = useState({ ...EMPTY_ABS })
  const [evalForm, setEvalForm] = useState({ ...EMPTY_EVAL })

  // ─── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [empData, absData, perfData, contrData] = await Promise.all([
        employeeService.getAll(),
        absenceService.getAll(),
        performanceService.getAll(),
        contractService.getAll(),
      ])
      setEmployees(empData)
      setAbsences(absData)
      setPerformance(perfData)
      setContracts(contrData)
      const s = await getHRStats(empData, absData, perfData)
      setStats(s)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados de RH. Verifique a sua ligação.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Filtros funcionários ────────────────────────────────────────────────
  const filteredEmployees = useMemo(() => {
    let r = [...employees]
    if (statusFilter !== 'all') r = r.filter(e => e.status === statusFilter)
    if (deptFilter !== 'all')   r = r.filter(e => e.department === deptFilter)
    if (searchTerm) r = r.filter(e =>
      e.full_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (e.email || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
      e.position.toLowerCase().includes(searchTerm.toLowerCase())
    )
    return r
  }, [employees, statusFilter, deptFilter, searchTerm])

  // ─── Departamentos únicos ─────────────────────────────────────────────────
  const uniqueDepts = useMemo(() => {
    const d = new Set(employees.map(e => e.department).filter(Boolean))
    return Array.from(d) as string[]
  }, [employees])

  // ─── Cálculo de dias de ausência ──────────────────────────────────────────
  const calcDays = (start: string, end: string) =>
    Math.max(1, Math.ceil((new Date(end).getTime() - new Date(start).getTime()) / 86400000) + 1)

  // ─── Funcionário: guardar ────────────────────────────────────────────────
  const handleSaveEmployee = async () => {
    if (!empForm.full_name.trim() || !empForm.position.trim()) {
      toast.error('Nome completo e cargo são obrigatórios')
      return
    }
    if (Number(empForm.gross_salary) <= 0) {
      toast.error('Salário deve ser maior que zero')
      return
    }
    setSubmitting(true)
    try {
      if (editingEmp) {
        const updated = await employeeService.update(editingEmp.id, empForm)
        setEmployees(prev => prev.map(e => e.id === updated.id ? updated : e))
        toast.success('Funcionário actualizado!')
      } else {
        const created = await employeeService.create(empForm)
        setEmployees(prev => [created, ...prev])
        toast.success('Funcionário criado!')
      }
      setEmpDialog(false)
      setEditingEmp(null)
      setEmpForm({ ...EMPTY_EMP })
      const s = await getHRStats(employees, absences, performance)
      setStats(s)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao guardar funcionário')
    } finally { setSubmitting(false) }
  }

  const openEditEmployee = (emp: Employee) => {
    setEditingEmp(emp)
    setEmpForm({
      full_name: emp.full_name, email: emp.email || '', phone: emp.phone || '',
      position: emp.position, department: emp.department || '',
      hire_date: emp.hire_date,
      employment_type: emp.employment_type || 'FULL_TIME',
      contract_type: emp.contract_type || 'PERMANENT',
      gross_salary: Number(emp.gross_salary),
      status: emp.status,
      marital_status: emp.marital_status || 'SINGLE',
      dependents: emp.dependents || 0,
      vacation_days_total: emp.vacation_days_total || 22,
      vacation_days_used: emp.vacation_days_used || 0,
      nif: emp.nif || '', bi_number: emp.bi_number || '', inss_number: emp.inss_number || '',
      bank_name: emp.bank_name || '', bank_account: emp.bank_account || '',
      emergency_contact_name: emp.emergency_contact_name || '',
      emergency_contact_phone: emp.emergency_contact_phone || '',
      notes: emp.notes || '',
    })
    setEmpDialog(true)
  }

  const handleDeleteEmployee = async (id: string, name: string) => {
    if (!confirm(`Eliminar funcionário "${name}"? Esta acção é irreversível.`)) return
    try {
      await employeeService.delete(id)
      setEmployees(prev => prev.filter(e => e.id !== id))
      toast.success('Funcionário eliminado')
    } catch { toast.error('Erro ao eliminar (pode ter dados associados)') }
  }

  // ─── Ausências: guardar ───────────────────────────────────────────────────
  const handleSaveAbsence = async () => {
    if (!absForm.employee_id) { toast.error('Selecione um funcionário'); return }
    if (!absForm.reason.trim()) { toast.error('Motivo é obrigatório'); return }
    const days = calcDays(absForm.start_date, absForm.end_date)
    setSubmitting(true)
    try {
      const created = await absenceService.create({ ...absForm, days_count: days })
      setAbsences(prev => [created, ...prev])
      toast.success('Ausência registada!')
      setAbsDialog(false)
      setAbsForm({ ...EMPTY_ABS })
    } catch (err) {
      console.error(err)
      toast.error('Erro ao registar ausência')
    } finally { setSubmitting(false) }
  }

  const handleApproveAbsence = async (id: string) => {
    try {
      const updated = await absenceService.approve(id)
      setAbsences(prev => prev.map(a => a.id === updated.id ? updated : a))
      toast.success('Ausência aprovada!')
    } catch { toast.error('Erro ao aprovar ausência') }
  }

  const handleRejectAbsence = async (id: string) => {
    const reason = prompt('Motivo da rejeição (opcional):')
    try {
      const updated = await absenceService.reject(id, reason || undefined)
      setAbsences(prev => prev.map(a => a.id === updated.id ? updated : a))
      toast.success('Ausência rejeitada')
    } catch { toast.error('Erro ao rejeitar ausência') }
  }

  const handleDeleteAbsence = async (id: string) => {
    if (!confirm('Eliminar este registo de ausência?')) return
    try {
      await absenceService.delete(id)
      setAbsences(prev => prev.filter(a => a.id !== id))
      toast.success('Ausência eliminada')
    } catch { toast.error('Erro ao eliminar ausência') }
  }

  // ─── Avaliações: guardar ─────────────────────────────────────────────────
  const handleSaveEval = async () => {
    if (!evalForm.employee_id) { toast.error('Selecione um funcionário'); return }
    setSubmitting(true)
    try {
      const created = await performanceService.create(evalForm)
      setPerformance(prev => [created, ...prev])
      toast.success('Avaliação registada!')
      setEvalDialog(false)
      setEvalForm({ ...EMPTY_EVAL })
    } catch (err) {
      console.error(err)
      toast.error('Erro ao guardar avaliação')
    } finally { setSubmitting(false) }
  }

  const handleDeleteEval = async (id: string) => {
    if (!confirm('Eliminar esta avaliação?')) return
    try {
      await performanceService.delete(id)
      setPerformance(prev => prev.filter(p => p.id !== id))
      toast.success('Avaliação eliminada')
    } catch { toast.error('Erro ao eliminar avaliação') }
  }

  // ─── Ver detalhe ─────────────────────────────────────────────────────────
  const openDetail = (emp: Employee) => {
    setDetailEmp(emp)
    setDetailDialog(true)
  }

  // ─── Skeleton ──────────────────────────────────────────────────────────
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

  // ─── Dados auxiliares para avaliações ────────────────────────────────────
  const activeEmployees = employees.filter(e => e.status === 'ACTIVE' || e.status === 'ON_LEAVE')

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Gestão de RH</h1>
          <p className="text-muted-foreground">Recursos humanos — funcionários, ausências e desempenho</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Atualizar
          </Button>
          <Button variant="outline" size="sm" onClick={() => employeeService.exportCSV(filteredEmployees)} disabled={employees.length === 0}>
            <Download className="h-4 w-4 mr-2" /> Exportar CSV
          </Button>
          <Button size="sm" onClick={() => { setEditingEmp(null); setEmpForm({ ...EMPTY_EMP }); setEmpDialog(true) }}>
            <UserPlus className="h-4 w-4 mr-2" /> Novo Funcionário
          </Button>
        </div>
      </div>

      {/* ── Métricas ── */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            {
              title: 'Funcionários Activos', value: stats.active_employees, sub: `${stats.total_employees} total • ${stats.on_leave} em licença`,
              icon: <Users className="h-5 w-5 text-blue-600" />, bg: 'bg-blue-50', color: 'text-blue-600',
            },
            {
              title: 'Massa Salarial', value: fmt(stats.total_salary_mass), sub: `Média: ${fmt(stats.avg_salary)}`,
              icon: <TrendingUp className="h-5 w-5 text-green-600" />, bg: 'bg-green-50', color: 'text-green-600',
            },
            {
              title: 'Ausências Pendentes', value: stats.pending_absences, sub: `${stats.vacations_this_month} férias este mês`,
              icon: <Calendar className="h-5 w-5 text-yellow-600" />, bg: 'bg-yellow-50', color: 'text-yellow-600',
            },
            {
              title: 'Desempenho Médio', value: `${stats.avg_performance.toFixed(1)}/5`, sub: `${performance.length} avaliações`,
              icon: <Award className="h-5 w-5 text-purple-600" />, bg: 'bg-purple-50', color: 'text-purple-600',
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
        <TabsList className="grid grid-cols-4 w-full max-w-lg">
          <TabsTrigger value="employees"><Users className="h-4 w-4 mr-1.5" />Equipa</TabsTrigger>
          <TabsTrigger value="absences"><Calendar className="h-4 w-4 mr-1.5" />Ausências</TabsTrigger>
          <TabsTrigger value="performance"><Award className="h-4 w-4 mr-1.5" />Desempenho</TabsTrigger>
          <TabsTrigger value="analytics"><BarChart3 className="h-4 w-4 mr-1.5" />Análise</TabsTrigger>
        </TabsList>

        {/* ════ TAB: FUNCIONÁRIOS ════ */}
        <TabsContent value="employees" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <CardTitle>Equipa ({filteredEmployees.length})</CardTitle>
                  <CardDescription>
                    {filteredEmployees.length} de {employees.length} funcionários
                  </CardDescription>
                </div>
                <div className="flex flex-wrap gap-2">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      placeholder="Nome, cargo, email..."
                      className="pl-9 w-48"
                      value={searchTerm}
                      onChange={e => setSearchTerm(e.target.value)}
                    />
                    {searchTerm && <button onClick={() => setSearchTerm('')} className="absolute right-3 top-1/2 -translate-y-1/2"><X className="h-3 w-3" /></button>}
                  </div>
                  <Select value={statusFilter} onValueChange={v => setStatusFilter(v as EmployeeStatus | 'all')}>
                    <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos os estados</SelectItem>
                      <SelectItem value="ACTIVE">Activos</SelectItem>
                      <SelectItem value="ON_LEAVE">Em Licença</SelectItem>
                      <SelectItem value="INACTIVE">Inactivos</SelectItem>
                      <SelectItem value="TERMINATED">Saíram</SelectItem>
                    </SelectContent>
                  </Select>
                  {uniqueDepts.length > 0 && (
                    <Select value={deptFilter} onValueChange={setDeptFilter}>
                      <SelectTrigger className="w-40"><SelectValue placeholder="Departamento" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">Todos os dept.</SelectItem>
                        {uniqueDepts.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {filteredEmployees.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <Users className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhum funcionário encontrado</p>
                  <Button size="sm" onClick={() => { setEditingEmp(null); setEmpForm({ ...EMPTY_EMP }); setEmpDialog(true) }}>
                    <UserPlus className="h-4 w-4 mr-2" /> Adicionar Funcionário
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {/* Header */}
                  <div className="hidden lg:grid lg:grid-cols-[2.5fr_1.5fr_1fr_1fr_1fr_auto] gap-4 text-xs font-semibold text-muted-foreground uppercase tracking-wide px-3 pb-1">
                    <span>Nome / Cargo</span>
                    <span>Departamento</span>
                    <span>Contrato</span>
                    <span className="text-right">Salário Bruto</span>
                    <span className="text-center">Estado</span>
                    <span className="text-center">Acções</span>
                  </div>
                  <Separator />

                  <AnimatePresence>
                    {filteredEmployees.map((emp, idx) => {
                      const empPerf = performance.filter(p => p.employee_id === emp.id)
                      const avgPerf = empPerf.length > 0
                        ? empPerf.reduce((s, p) => s + Number(p.overall_score), 0) / empPerf.length
                        : null
                      const vacLeft = (emp.vacation_days_total || 22) - (emp.vacation_days_used || 0)
                      return (
                        <motion.div
                          key={emp.id}
                          initial={{ opacity: 0, y: 6 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -6 }}
                          transition={{ delay: idx * 0.025 }}
                          className="grid grid-cols-1 lg:grid-cols-[2.5fr_1.5fr_1fr_1fr_1fr_auto] gap-4 items-center rounded-lg border px-3 py-3 hover:bg-muted/30 transition-colors"
                        >
                          {/* Nome */}
                          <div className="flex items-center gap-3">
                            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary font-bold text-sm">
                              {emp.full_name.split(' ').slice(0, 2).map((n: string) => n[0]).join('')}
                            </div>
                            <div>
                              <p className="font-semibold text-sm">{emp.full_name}</p>
                              <p className="text-xs text-muted-foreground">{emp.position}</p>
                              <div className="flex gap-1.5 mt-0.5 flex-wrap">
                                {emp.email && <span className="text-xs text-muted-foreground truncate max-w-32">{emp.email}</span>}
                                {avgPerf !== null && (
                                  <span className="text-xs text-yellow-600 font-medium">★ {avgPerf.toFixed(1)}</span>
                                )}
                              </div>
                            </div>
                          </div>

                          {/* Departamento */}
                          <div>
                            <div className="flex items-center gap-1.5">
                              <Building2 className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                              <span className="text-sm">{emp.department || '—'}</span>
                            </div>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              Adm: {fmtDate(emp.hire_date)}
                            </p>
                          </div>

                          {/* Contrato */}
                          <div>
                            <p className="text-sm">{CONTRACT_TYPE_LABELS[emp.contract_type as ContractType] || emp.contract_type || '—'}</p>
                            <p className="text-xs text-muted-foreground">{EMPLOYMENT_TYPE_LABELS[emp.employment_type as EmploymentType] || emp.employment_type}</p>
                          </div>

                          {/* Salário */}
                          <div className="lg:text-right">
                            <p className="font-bold text-sm">{fmt(Number(emp.gross_salary))}</p>
                            <p className="text-xs text-muted-foreground">
                              {vacLeft}d férias disp.
                            </p>
                          </div>

                          {/* Estado */}
                          <div className="lg:flex lg:justify-center">
                            <EmpBadge status={emp.status} />
                          </div>

                          {/* Acções */}
                          <div className="flex items-center gap-1 lg:justify-center">
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openDetail(emp)}>
                              <ChevronRight className="h-3.5 w-3.5" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={() => openEditEmployee(emp)}>
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
                        </motion.div>
                      )
                    })}
                  </AnimatePresence>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TAB: AUSÊNCIAS ════ */}
        <TabsContent value="absences" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Ausências ({absences.length})</CardTitle>
                  <CardDescription>
                    {absences.filter(a => a.status === 'PENDING').length} pendentes de aprovação
                  </CardDescription>
                </div>
                <Button size="sm" onClick={() => { setAbsForm({ ...EMPTY_ABS }); setAbsDialog(true) }}>
                  <Plus className="h-4 w-4 mr-2" /> Registar Ausência
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {absences.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <Calendar className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhuma ausência registada</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {absences.map((abs, idx) => {
                    const empName = (abs.employee as Employee | undefined)?.full_name ||
                      employees.find(e => e.id === abs.employee_id)?.full_name || '—'
                    return (
                      <motion.div
                        key={abs.id}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: idx * 0.03 }}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border px-4 py-3 hover:bg-muted/30 transition-colors"
                      >
                        <div className="flex items-center gap-3">
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-blue-50">
                            <Calendar className="h-5 w-5 text-blue-600" />
                          </div>
                          <div>
                            <p className="font-semibold text-sm">{empName}</p>
                            <div className="flex flex-wrap items-center gap-2 mt-0.5">
                              <Badge variant="outline" className="text-xs py-0">
                                {ABSENCE_TYPE_LABELS[abs.absence_type] || abs.absence_type}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {fmtDate(abs.start_date)} → {fmtDate(abs.end_date)}
                              </span>
                              <span className="text-xs font-medium text-primary">{abs.days_count}d</span>
                            </div>
                            {abs.reason && <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{abs.reason}</p>}
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <AbsBadge status={abs.status} />
                          {abs.status === 'PENDING' && (
                            <>
                              <Button size="sm" variant="outline" className="h-7 text-green-600 border-green-200 hover:bg-green-50" onClick={() => handleApproveAbsence(abs.id)}>
                                <CheckCircle2 className="h-3.5 w-3.5 mr-1" /> Aprovar
                              </Button>
                              <Button size="sm" variant="outline" className="h-7 text-red-600 border-red-200 hover:bg-red-50" onClick={() => handleRejectAbsence(abs.id)}>
                                <XCircle className="h-3.5 w-3.5 mr-1" /> Rejeitar
                              </Button>
                            </>
                          )}
                          <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground" onClick={() => handleDeleteAbsence(abs.id)}>
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </motion.div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TAB: DESEMPENHO ════ */}
        <TabsContent value="performance" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Avaliações de Desempenho ({performance.length})</CardTitle>
                  <CardDescription>
                    Nota média: {stats ? stats.avg_performance.toFixed(2) : '—'}/5.00
                  </CardDescription>
                </div>
                <Button size="sm" onClick={() => { setEvalForm({ ...EMPTY_EVAL }); setEvalDialog(true) }}>
                  <Plus className="h-4 w-4 mr-2" /> Nova Avaliação
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {performance.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <Award className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhuma avaliação registada</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {performance.map((perf, idx) => {
                    const empName = (perf.employee as Employee | undefined)?.full_name ||
                      employees.find(e => e.id === perf.employee_id)?.full_name || '—'
                    const criteria = [
                      { label: 'Produtividade',  val: perf.productivity_score },
                      { label: 'Qualidade',      val: perf.quality_score },
                      { label: 'Trabalho Equipa', val: perf.teamwork_score },
                      { label: 'Pontualidade',   val: perf.punctuality_score },
                      { label: 'Iniciativa',     val: perf.initiative_score },
                    ].filter(c => c.val != null)

                    return (
                      <motion.div
                        key={perf.id}
                        initial={{ opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: idx * 0.04 }}
                        className="rounded-lg border p-4"
                      >
                        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                          <div>
                            <div className="flex items-center gap-2 mb-1">
                              <p className="font-semibold">{empName}</p>
                              <Badge variant="outline" className="text-xs py-0">
                                {EVAL_PERIOD_LABELS[perf.evaluation_period] || perf.evaluation_period}
                              </Badge>
                              <span className="text-xs text-muted-foreground">{fmtDate(perf.evaluation_date)}</span>
                            </div>
                            <ScoreStars score={Number(perf.overall_score)} />
                            {perf.strengths && (
                              <p className="text-xs text-muted-foreground mt-1.5 line-clamp-2">
                                <strong>Pontos fortes:</strong> {perf.strengths}
                              </p>
                            )}
                            {perf.goals && (
                              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                                <strong>Objectivos:</strong> {perf.goals}
                              </p>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge className={`text-xs border ${
                              perf.status === 'APPROVED' ? 'bg-green-100 text-green-700 border-green-200' :
                              perf.status === 'SUBMITTED' ? 'bg-blue-100 text-blue-700 border-blue-200' :
                              'bg-gray-100 text-gray-600 border-gray-200'
                            }`}>
                              {perf.status === 'APPROVED' ? 'Aprovada' : perf.status === 'SUBMITTED' ? 'Submetida' : perf.status}
                            </Badge>
                            <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" onClick={() => handleDeleteEval(perf.id)}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </div>
                        </div>

                        {criteria.length > 0 && (
                          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mt-3">
                            {criteria.map(c => (
                              <div key={c.label} className="space-y-1">
                                <div className="flex justify-between text-xs">
                                  <span className="text-muted-foreground">{c.label}</span>
                                  <span className="font-medium">{Number(c.val).toFixed(1)}</span>
                                </div>
                                <Progress value={(Number(c.val) / 5) * 100} className="h-1.5" />
                              </div>
                            ))}
                          </div>
                        )}
                      </motion.div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TAB: ANÁLISE ════ */}
        <TabsContent value="analytics" className="mt-4">
          <div className="grid gap-4 md:grid-cols-2">

            {/* Distribuição por departamento */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Por Departamento</CardTitle>
                <CardDescription>Distribuição de colaboradores activos</CardDescription>
              </CardHeader>
              <CardContent>
                {(stats?.departments || []).length === 0 ? (
                  <p className="text-center py-8 text-muted-foreground text-sm">Sem dados</p>
                ) : (
                  <div className="space-y-3">
                    {(stats?.departments || []).map((dept, i) => (
                      <div key={i} className="space-y-1">
                        <div className="flex justify-between text-sm">
                          <span className="font-medium">{dept.name}</span>
                          <span className="text-muted-foreground">{dept.count} col. · {fmt(dept.avg_salary)}/mês</span>
                        </div>
                        <Progress
                          value={(dept.count / Math.max(1, stats?.active_employees || 1)) * 100}
                          className="h-2"
                        />
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Ausências por tipo */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Ausências por Tipo</CardTitle>
                <CardDescription>Distribuição dos pedidos registados</CardDescription>
              </CardHeader>
              <CardContent>
                {absences.length === 0 ? (
                  <p className="text-center py-8 text-muted-foreground text-sm">Sem ausências registadas</p>
                ) : (
                  <div className="space-y-3">
                    {(Object.keys(ABSENCE_TYPE_LABELS) as AbsenceType[]).map(type => {
                      const count = absences.filter(a => a.absence_type === type).length
                      if (count === 0) return null
                      return (
                        <div key={type} className="space-y-1">
                          <div className="flex justify-between text-sm">
                            <span className="font-medium">{ABSENCE_TYPE_LABELS[type]}</span>
                            <span className="text-muted-foreground">{count} pedido{count !== 1 ? 's' : ''}</span>
                          </div>
                          <Progress value={(count / absences.length) * 100} className="h-2" />
                        </div>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Resumo de contratos */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Contratos Activos</CardTitle>
                <CardDescription>{contracts.filter(c => c.status === 'ACTIVE').length} contratos em vigor</CardDescription>
              </CardHeader>
              <CardContent>
                {contracts.length === 0 ? (
                  <p className="text-center py-8 text-muted-foreground text-sm">Sem contratos registados</p>
                ) : (
                  <div className="space-y-2">
                    {contracts.filter(c => c.status === 'ACTIVE').slice(0, 6).map((c, i) => {
                      const empName = (c.employee as Employee | undefined)?.full_name ||
                        employees.find(e => e.id === c.employee_id)?.full_name || '—'
                      return (
                        <div key={i} className="flex items-center justify-between text-sm py-1.5 border-b last:border-0">
                          <div>
                            <p className="font-medium">{empName}</p>
                            <p className="text-xs text-muted-foreground">
                              {CONTRACT_TYPE_LABELS[c.contract_type as ContractType] || c.contract_type} · desde {fmtDate(c.start_date)}
                            </p>
                          </div>
                          <p className="font-semibold text-green-600">{fmt(Number(c.gross_salary))}</p>
                        </div>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Top performers */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Top Performers</CardTitle>
                <CardDescription>Melhor desempenho nas últimas avaliações</CardDescription>
              </CardHeader>
              <CardContent>
                {performance.length === 0 ? (
                  <p className="text-center py-8 text-muted-foreground text-sm">Sem avaliações registadas</p>
                ) : (
                  <div className="space-y-2">
                    {[...performance]
                      .sort((a, b) => Number(b.overall_score) - Number(a.overall_score))
                      .slice(0, 5)
                      .map((p, i) => {
                        const empName = (p.employee as Employee | undefined)?.full_name ||
                          employees.find(e => e.id === p.employee_id)?.full_name || '—'
                        return (
                          <div key={i} className="flex items-center justify-between py-1.5 border-b last:border-0">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-bold text-muted-foreground w-5">#{i + 1}</span>
                              <div>
                                <p className="text-sm font-medium">{empName}</p>
                                <p className="text-xs text-muted-foreground">{fmtDate(p.evaluation_date)}</p>
                              </div>
                            </div>
                            <ScoreStars score={Number(p.overall_score)} />
                          </div>
                        )
                      })}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      {/* ══════ MODAL: Criar/Editar Funcionário ══════ */}
      <Dialog open={empDialog} onOpenChange={v => { if (!v) { setEmpDialog(false); setEditingEmp(null); setEmpForm({ ...EMPTY_EMP }) } }}>
        <DialogContent className="max-w-2xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingEmp ? `Editar — ${editingEmp.full_name}` : 'Novo Funcionário'}</DialogTitle>
            <DialogDescription>Dados pessoais e contratuais do colaborador</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 mt-2">
            {/* Dados Pessoais */}
            <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Dados Pessoais</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label>Nome Completo <span className="text-destructive">*</span></Label>
                <Input value={empForm.full_name} onChange={e => setEmpForm(f => ({ ...f, full_name: e.target.value }))} placeholder="Ex: Ana Maria dos Santos" />
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input type="email" value={empForm.email || ''} onChange={e => setEmpForm(f => ({ ...f, email: e.target.value }))} placeholder="ana@empresa.ao" />
              </div>
              <div className="space-y-1">
                <Label>Telefone</Label>
                <Input value={empForm.phone || ''} onChange={e => setEmpForm(f => ({ ...f, phone: e.target.value }))} placeholder="+244 9XX XXX XXX" />
              </div>
              <div className="space-y-1">
                <Label>BI / Passaporte</Label>
                <Input value={empForm.bi_number || ''} onChange={e => setEmpForm(f => ({ ...f, bi_number: e.target.value }))} placeholder="001234567LA" />
              </div>
              <div className="space-y-1">
                <Label>NIF</Label>
                <Input value={empForm.nif || ''} onChange={e => setEmpForm(f => ({ ...f, nif: e.target.value }))} placeholder="5400001001LA000" />
              </div>
              <div className="space-y-1">
                <Label>Estado Civil</Label>
                <Select value={empForm.marital_status || 'SINGLE'} onValueChange={v => setEmpForm(f => ({ ...f, marital_status: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="SINGLE">Solteiro(a)</SelectItem>
                    <SelectItem value="MARRIED">Casado(a)</SelectItem>
                    <SelectItem value="DIVORCED">Divorciado(a)</SelectItem>
                    <SelectItem value="WIDOWED">Viúvo(a)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Nº Dependentes</Label>
                <Input type="number" min="0" value={empForm.dependents || 0} onChange={e => setEmpForm(f => ({ ...f, dependents: parseInt(e.target.value) || 0 }))} />
              </div>
            </div>

            <Separator />

            {/* Dados Contratuais */}
            <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Dados Contratuais</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Cargo <span className="text-destructive">*</span></Label>
                <Input value={empForm.position} onChange={e => setEmpForm(f => ({ ...f, position: e.target.value }))} placeholder="Ex: Analista Financeiro" />
              </div>
              <div className="space-y-1">
                <Label>Departamento</Label>
                <Select value={empForm.department || 'none'} onValueChange={v => setEmpForm(f => ({ ...f, department: v === 'none' ? '' : v }))}>
                  <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Sem departamento —</SelectItem>
                    {DEPARTMENTS.map(d => <SelectItem key={d} value={d}>{d}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Data Admissão <span className="text-destructive">*</span></Label>
                <Input type="date" value={empForm.hire_date} onChange={e => setEmpForm(f => ({ ...f, hire_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Tipo de Emprego</Label>
                <Select value={empForm.employment_type} onValueChange={v => setEmpForm(f => ({ ...f, employment_type: v as EmploymentType }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(EMPLOYMENT_TYPE_LABELS) as EmploymentType[]).map(k => (
                      <SelectItem key={k} value={k}>{EMPLOYMENT_TYPE_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Tipo de Contrato</Label>
                <Select value={empForm.contract_type || 'PERMANENT'} onValueChange={v => setEmpForm(f => ({ ...f, contract_type: v as ContractType }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(CONTRACT_TYPE_LABELS) as ContractType[]).map(k => (
                      <SelectItem key={k} value={k}>{CONTRACT_TYPE_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Salário Bruto (AOA) <span className="text-destructive">*</span></Label>
                <Input type="number" min="0" step="1000" value={empForm.gross_salary || ''} onChange={e => setEmpForm(f => ({ ...f, gross_salary: parseFloat(e.target.value) || 0 }))} placeholder="0" />
              </div>
              <div className="space-y-1">
                <Label>Estado</Label>
                <Select value={empForm.status} onValueChange={v => setEmpForm(f => ({ ...f, status: v as EmployeeStatus }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(EMPLOYEE_STATUS_LABELS) as EmployeeStatus[]).map(k => (
                      <SelectItem key={k} value={k}>{EMPLOYEE_STATUS_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Dias Férias / Ano</Label>
                <Input type="number" min="0" value={empForm.vacation_days_total || 22} onChange={e => setEmpForm(f => ({ ...f, vacation_days_total: parseInt(e.target.value) || 22 }))} />
              </div>
            </div>

            <Separator />

            {/* Dados Bancários */}
            <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Dados Bancários</p>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Banco</Label>
                <Input value={empForm.bank_name || ''} onChange={e => setEmpForm(f => ({ ...f, bank_name: e.target.value }))} placeholder="BAI, BFA, BPC..." />
              </div>
              <div className="space-y-1">
                <Label>Conta / IBAN</Label>
                <Input value={empForm.bank_account || ''} onChange={e => setEmpForm(f => ({ ...f, bank_account: e.target.value }))} placeholder="AO06 0040..." />
              </div>
            </div>

            <div className="space-y-1">
              <Label>Notas</Label>
              <Textarea rows={2} value={empForm.notes || ''} onChange={e => setEmpForm(f => ({ ...f, notes: e.target.value }))} placeholder="Observações internas..." />
            </div>

            <div className="flex gap-2 justify-end pt-1">
              <Button variant="outline" onClick={() => { setEmpDialog(false); setEditingEmp(null) }} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveEmployee} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingEmp ? 'Guardar Alterações' : 'Criar Funcionário'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Registar Ausência ══════ */}
      <Dialog open={absDialog} onOpenChange={v => { if (!v) setAbsDialog(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Registar Ausência</DialogTitle>
            <DialogDescription>Pedido de férias, baixa médica ou outra ausência</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1">
              <Label>Funcionário <span className="text-destructive">*</span></Label>
              <Select value={absForm.employee_id || 'none'} onValueChange={v => setAbsForm(f => ({ ...f, employee_id: v === 'none' ? '' : v }))}>
                <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— Selecione —</SelectItem>
                  {activeEmployees.map(e => <SelectItem key={e.id} value={e.id}>{e.full_name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>Tipo <span className="text-destructive">*</span></Label>
              <Select value={absForm.absence_type} onValueChange={v => setAbsForm(f => ({ ...f, absence_type: v as AbsenceType }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(ABSENCE_TYPE_LABELS) as AbsenceType[]).map(k => (
                    <SelectItem key={k} value={k}>{ABSENCE_TYPE_LABELS[k]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Início <span className="text-destructive">*</span></Label>
                <Input type="date" value={absForm.start_date} onChange={e => setAbsForm(f => ({ ...f, start_date: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Fim <span className="text-destructive">*</span></Label>
                <Input type="date" value={absForm.end_date} onChange={e => setAbsForm(f => ({ ...f, end_date: e.target.value }))} />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Duração: <strong>{calcDays(absForm.start_date, absForm.end_date)} dia(s)</strong>
            </p>
            <div className="space-y-1">
              <Label>Motivo <span className="text-destructive">*</span></Label>
              <Textarea rows={3} value={absForm.reason} onChange={e => setAbsForm(f => ({ ...f, reason: e.target.value }))} placeholder="Descreva o motivo..." />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setAbsDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveAbsence} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Registar
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Nova Avaliação ══════ */}
      <Dialog open={evalDialog} onOpenChange={v => { if (!v) setEvalDialog(false) }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Nova Avaliação de Desempenho</DialogTitle>
            <DialogDescription>Avaliar colaborador em múltiplas dimensões (0–5)</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label>Funcionário <span className="text-destructive">*</span></Label>
                <Select value={evalForm.employee_id || 'none'} onValueChange={v => setEvalForm(f => ({ ...f, employee_id: v === 'none' ? '' : v }))}>
                  <SelectTrigger><SelectValue placeholder="Selecione" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">— Selecione —</SelectItem>
                    {employees.map(e => <SelectItem key={e.id} value={e.id}>{e.full_name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Período</Label>
                <Select value={evalForm.evaluation_period} onValueChange={v => setEvalForm(f => ({ ...f, evaluation_period: v as EvalPeriod }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(EVAL_PERIOD_LABELS) as EvalPeriod[]).map(k => (
                      <SelectItem key={k} value={k}>{EVAL_PERIOD_LABELS[k]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Data Avaliação</Label>
                <Input type="date" value={evalForm.evaluation_date} onChange={e => setEvalForm(f => ({ ...f, evaluation_date: e.target.value }))} />
              </div>
            </div>

            <Separator />
            <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Pontuações (0 – 5)</p>

            {[
              { key: 'overall_score',       label: 'Nota Geral ★' },
              { key: 'productivity_score',  label: 'Produtividade' },
              { key: 'quality_score',       label: 'Qualidade' },
              { key: 'teamwork_score',      label: 'Trabalho em Equipa' },
              { key: 'punctuality_score',   label: 'Pontualidade' },
              { key: 'initiative_score',    label: 'Iniciativa' },
            ].map(({ key, label }) => (
              <div key={key} className="space-y-1">
                <div className="flex justify-between">
                  <Label className="text-sm">{label}</Label>
                  <span className="text-sm font-bold text-primary">
                    {Number((evalForm as unknown as Record<string, number>)[key]).toFixed(1)}
                  </span>
                </div>
                <input
                  type="range" min="0" max="5" step="0.5"
                  value={(evalForm as unknown as Record<string, number>)[key]}
                  onChange={e => setEvalForm(f => ({ ...f, [key]: parseFloat(e.target.value) }))}
                  className="w-full accent-primary"
                />
                <Progress value={((evalForm as unknown as Record<string, number>)[key] / 5) * 100} className="h-1.5" />
              </div>
            ))}

            <Separator />
            <div className="space-y-3">
              <div className="space-y-1">
                <Label>Pontos Fortes</Label>
                <Textarea rows={2} value={evalForm.strengths} onChange={e => setEvalForm(f => ({ ...f, strengths: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Áreas a Melhorar</Label>
                <Textarea rows={2} value={evalForm.weaknesses} onChange={e => setEvalForm(f => ({ ...f, weaknesses: e.target.value }))} />
              </div>
              <div className="space-y-1">
                <Label>Objectivos</Label>
                <Textarea rows={2} value={evalForm.goals} onChange={e => setEvalForm(f => ({ ...f, goals: e.target.value }))} />
              </div>
            </div>

            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setEvalDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveEval} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                Guardar Avaliação
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Detalhe Funcionário ══════ */}
      <Dialog open={detailDialog} onOpenChange={setDetailDialog}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-3">
              {detailEmp?.full_name}
              {detailEmp && <EmpBadge status={detailEmp.status} />}
            </DialogTitle>
            <DialogDescription>{detailEmp?.position} · {detailEmp?.department || 'Sem departamento'}</DialogDescription>
          </DialogHeader>
          {detailEmp && (
            <div className="space-y-4 mt-2">
              <div className="grid grid-cols-2 gap-3 text-sm">
                {[
                  { label: 'Email',          value: detailEmp.email || '—' },
                  { label: 'Telefone',       value: detailEmp.phone || '—' },
                  { label: 'BI',             value: detailEmp.bi_number || '—' },
                  { label: 'NIF',            value: detailEmp.nif || '—' },
                  { label: 'INSS',           value: detailEmp.inss_number || '—' },
                  { label: 'Admissão',       value: fmtDate(detailEmp.hire_date) },
                  { label: 'Contrato',       value: CONTRACT_TYPE_LABELS[detailEmp.contract_type as ContractType] || '—' },
                  { label: 'Estado Civil',   value: detailEmp.marital_status || '—' },
                  { label: 'Dependentes',    value: String(detailEmp.dependents || 0) },
                  { label: 'Férias Dispon.', value: `${(detailEmp.vacation_days_total || 22) - (detailEmp.vacation_days_used || 0)} dias` },
                  { label: 'Salário Bruto',  value: fmt(Number(detailEmp.gross_salary)) },
                  { label: 'Banco',          value: detailEmp.bank_name || '—' },
                ].map((r, i) => (
                  <div key={i}>
                    <p className="text-xs text-muted-foreground">{r.label}</p>
                    <p className="font-medium">{r.value}</p>
                  </div>
                ))}
              </div>

              {/* Ausências do funcionário */}
              {absences.filter(a => a.employee_id === detailEmp.id).length > 0 && (
                <>
                  <Separator />
                  <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Ausências</p>
                  <div className="space-y-1">
                    {absences.filter(a => a.employee_id === detailEmp.id).map(a => (
                      <div key={a.id} className="flex justify-between text-sm py-1 border-b last:border-0">
                        <span>{ABSENCE_TYPE_LABELS[a.absence_type]} · {a.days_count}d</span>
                        <span className="text-muted-foreground">{fmtDate(a.start_date)}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* Avaliações do funcionário */}
              {performance.filter(p => p.employee_id === detailEmp.id).length > 0 && (
                <>
                  <Separator />
                  <p className="text-xs font-semibold uppercase text-muted-foreground tracking-wide">Avaliações</p>
                  <div className="space-y-2">
                    {performance.filter(p => p.employee_id === detailEmp.id).map(p => (
                      <div key={p.id} className="flex justify-between items-center text-sm py-1 border-b last:border-0">
                        <span>{EVAL_PERIOD_LABELS[p.evaluation_period]} · {fmtDate(p.evaluation_date)}</span>
                        <ScoreStars score={Number(p.overall_score)} />
                      </div>
                    ))}
                  </div>
                </>
              )}

              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => { setDetailDialog(false); openEditEmployee(detailEmp) }}>
                  <Edit className="h-4 w-4 mr-2" /> Editar
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setDetailDialog(false)}>Fechar</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
