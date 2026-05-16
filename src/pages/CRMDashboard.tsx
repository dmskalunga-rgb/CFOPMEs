import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Users, DollarSign, TrendingUp, Target, Plus, Edit, Trash2,
  RefreshCw, Phone, Mail, Building2, Calendar, Loader2,
  ChevronRight, Activity, PhoneCall, MessageSquare, HandshakeIcon,
  FileText, Star, Clock, Filter, Search, X, ArrowUpRight,
  BarChart3, PieChart, Briefcase, AlertCircle,
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart as RePieChart, Pie, Cell, FunnelChart, Funnel, LabelList,
} from 'recharts'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
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
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { toast } from 'sonner'
import {
  contactsService, activitiesService,
  STAGE_LABELS, STAGE_COLORS, SOURCE_LABELS, ACTIVITY_LABELS, ACTIVITY_ICONS,
  PIPELINE_STAGES,
  type CRMContact, type CRMActivity,
  type ContactStage, type ContactSource, type ActivityType, type PipelineStats,
} from '@/services/crmService'

// ─── Formatadores ─────────────────────────────────────────────────────────────
const fmt = (v: number) =>
  new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(v)
const fmtM = (v: number) =>
  Math.abs(v) >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` :
  Math.abs(v) >= 1_000     ? `${(v / 1_000).toFixed(0)}K` : String(v)
const fmtDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'

const PIE_COLORS = ['#6366f1','#3b82f6','#a855f7','#f59e0b','#22c55e','#ef4444','#94a3b8']

// ─── Form vazio ────────────────────────────────────────────────────────────────
const EMPTY_CONTACT: Omit<CRMContact,'id'|'tenant_id'|'created_at'|'updated_at'|'activities'> = {
  full_name: '', company: '', email: '', phone: '', position: '',
  source: 'MANUAL', stage: 'LEAD', deal_value: 0, probability: 20,
  expected_close: '', notes: '', is_active: true,
}

const EMPTY_ACTIVITY: Omit<CRMActivity,'id'|'tenant_id'|'contact_id'|'created_at'> = {
  activity_type: 'CALL', title: '', description: '', outcome: '',
  activity_date: new Date().toISOString().slice(0,16), duration_min: 30,
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function CRMDashboard() {
  const [contacts,   setContacts]   = useState<CRMContact[]>([])
  const [activities, setActivities] = useState<CRMActivity[]>([])
  const [stats,      setStats]      = useState<PipelineStats | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [activeTab,  setActiveTab]  = useState('pipeline')

  // Filtros
  const [searchQ,      setSearchQ]      = useState('')
  const [stageFilter,  setStageFilter]  = useState<string>('all')

  // Detalhe do contacto
  const [selectedContact,   setSelectedContact]   = useState<CRMContact | null>(null)
  const [contactActivities, setContactActivities] = useState<CRMActivity[]>([])
  const [loadingDetail,     setLoadingDetail]      = useState(false)

  // Diálogos
  const [contactDialog,  setContactDialog]  = useState(false)
  const [activityDialog, setActivityDialog] = useState(false)
  const [detailDialog,   setDetailDialog]   = useState(false)
  const [editingContact, setEditingContact] = useState<CRMContact | null>(null)

  // Forms
  const [contactForm,  setContactForm]  = useState({ ...EMPTY_CONTACT })
  const [activityForm, setActivityForm] = useState({ ...EMPTY_ACTIVITY })

  // ─── Carregar dados ──────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [c, s, a] = await Promise.all([
        contactsService.getAll(),
        contactsService.getStats(),
        activitiesService.getRecent(15),
      ])
      setContacts(c)
      setStats(s)
      setActivities(a)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar dados CRM. Verifique a ligação.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Filtros ─────────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    return contacts.filter(c => {
      const matchStage  = stageFilter === 'all' || c.stage === stageFilter
      const matchSearch = !searchQ || [c.full_name, c.company, c.email].some(
        f => f?.toLowerCase().includes(searchQ.toLowerCase())
      )
      return matchStage && matchSearch
    })
  }, [contacts, stageFilter, searchQ])

  // ─── Dados do gráfico de funil ─────────────────────────────────────────
  const funnelData = useMemo(() => {
    if (!stats) return []
    return [
      { name: 'Leads',       value: stats.leads,       fill: '#6366f1' },
      { name: 'Qualificados', value: stats.qualified,  fill: '#3b82f6' },
      { name: 'Proposta',    value: stats.proposal,    fill: '#a855f7' },
      { name: 'Negociação',  value: stats.negotiation, fill: '#f59e0b' },
      { name: 'Ganhos',      value: stats.won,         fill: '#22c55e' },
    ].filter(d => d.value > 0)
  }, [stats])

  // ─── Dados pie por fonte ───────────────────────────────────────────────
  const sourceData = useMemo(() => {
    const map = new Map<string, number>()
    contacts.forEach(c => {
      const label = SOURCE_LABELS[c.source] || c.source
      map.set(label, (map.get(label) || 0) + 1)
    })
    return Array.from(map.entries()).map(([name, value]) => ({ name, value }))
  }, [contacts])

  // ─── Abrir detalhe de contacto ────────────────────────────────────────
  const openDetail = async (contact: CRMContact) => {
    setSelectedContact(contact)
    setDetailDialog(true)
    setLoadingDetail(true)
    try {
      const acts = await activitiesService.getByContact(contact.id)
      setContactActivities(acts)
    } catch { toast.error('Erro ao carregar actividades') }
    finally { setLoadingDetail(false) }
  }

  // ─── CRUD Contacto ────────────────────────────────────────────────────
  const handleSaveContact = async () => {
    if (!contactForm.full_name.trim()) { toast.error('Nome é obrigatório'); return }
    setSubmitting(true)
    try {
      if (editingContact) {
        const updated = await contactsService.update(editingContact.id, contactForm)
        setContacts(prev => prev.map(c => c.id === updated.id ? updated : c))
        toast.success('Contacto actualizado!')
      } else {
        const created = await contactsService.create(contactForm)
        setContacts(prev => [created, ...prev])
        toast.success('Contacto criado!')
      }
      setContactDialog(false); setEditingContact(null); setContactForm({ ...EMPTY_CONTACT })
      const s = await contactsService.getStats(); setStats(s)
    } catch (err) {
      console.error(err); toast.error('Erro ao guardar contacto')
    } finally { setSubmitting(false) }
  }

  const openEditContact = (contact: CRMContact) => {
    setEditingContact(contact)
    setContactForm({
      full_name: contact.full_name, company: contact.company || '',
      email: contact.email || '', phone: contact.phone || '',
      position: contact.position || '', source: contact.source,
      stage: contact.stage, deal_value: Number(contact.deal_value),
      probability: Number(contact.probability),
      expected_close: contact.expected_close || '',
      notes: contact.notes || '', is_active: contact.is_active,
    })
    setContactDialog(true)
  }

  const handleDeleteContact = async (id: string, name: string) => {
    if (!confirm(`Eliminar contacto "${name}"?`)) return
    try {
      await contactsService.delete(id)
      setContacts(prev => prev.filter(c => c.id !== id))
      toast.success('Contacto eliminado')
      const s = await contactsService.getStats(); setStats(s)
    } catch { toast.error('Erro ao eliminar') }
  }

  const handleStageChange = async (id: string, stage: ContactStage) => {
    try {
      const updated = await contactsService.updateStage(id, stage)
      setContacts(prev => prev.map(c => c.id === updated.id ? updated : c))
      if (selectedContact?.id === id) setSelectedContact(updated)
      toast.success(`Etapa actualizada para ${STAGE_LABELS[stage]}`)
      const s = await contactsService.getStats(); setStats(s)
    } catch { toast.error('Erro ao actualizar etapa') }
  }

  // ─── Criar actividade ─────────────────────────────────────────────────
  const handleSaveActivity = async () => {
    if (!selectedContact) return
    if (!activityForm.title.trim()) { toast.error('Título é obrigatório'); return }
    setSubmitting(true)
    try {
      const created = await activitiesService.create({
        ...activityForm, contact_id: selectedContact.id,
      })
      setContactActivities(prev => [created, ...prev])
      setActivities(prev => [created, ...prev.slice(0, 14)])
      toast.success('Actividade registada!')
      setActivityDialog(false); setActivityForm({ ...EMPTY_ACTIVITY })
    } catch (err) {
      console.error(err); toast.error('Erro ao guardar actividade')
    } finally { setSubmitting(false) }
  }

  // ─── Skeleton ─────────────────────────────────────────────────────────
  if (loading) return (
    <div className="space-y-6 p-6">
      <Skeleton className="h-10 w-64" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_,i) => <Card key={i}><CardContent className="p-6"><Skeleton className="h-16 w-full" /></CardContent></Card>)}
      </div>
      <Card><CardContent className="p-6"><Skeleton className="h-80 w-full" /></CardContent></Card>
    </div>
  )

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">CRM — Pipeline de Vendas</h1>
          <p className="text-muted-foreground">Gestão de contactos e oportunidades de negócio</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData}>
            <RefreshCw className="h-4 w-4 mr-2" />Atualizar
          </Button>
          <Button size="sm" onClick={() => { setEditingContact(null); setContactForm({ ...EMPTY_CONTACT }); setContactDialog(true) }}>
            <Plus className="h-4 w-4 mr-2" />Novo Contacto
          </Button>
        </div>
      </div>

      {/* ── KPIs ── */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            { title: 'Total Pipeline', value: `${stats.total}`, sub: `${stats.won} ganhos · ${stats.lost} perdidos`, icon: <Users className="h-5 w-5 text-indigo-600" />, color: 'text-indigo-700', bg: 'bg-indigo-50', num: stats.total },
            { title: 'Valor Total',    value: fmtM(stats.totalValue) + ' AOA', sub: `Ponderado: ${fmtM(stats.weightedValue)} AOA`, icon: <DollarSign className="h-5 w-5 text-green-600" />, color: 'text-green-700', bg: 'bg-green-50', num: stats.totalValue },
            { title: 'Negócios Ganhos', value: fmt(stats.wonValue), sub: `${stats.won} contratos fechados`, icon: <Star className="h-5 w-5 text-amber-600" />, color: 'text-amber-700', bg: 'bg-amber-50', num: stats.wonValue },
            { title: 'Taxa de Conversão', value: `${stats.conversionRate.toFixed(1)}%`, sub: `Ticket médio: ${fmtM(stats.avgDealValue)} AOA`, icon: <TrendingUp className="h-5 w-5 text-purple-600" />, color: 'text-purple-700', bg: 'bg-purple-50', num: stats.conversionRate },
          ].map((m, i) => (
            <motion.div key={i} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.07 }}>
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
        <TabsList className="grid grid-cols-4 w-full max-w-xl">
          <TabsTrigger value="pipeline"><Briefcase className="h-4 w-4 mr-1.5" />Pipeline</TabsTrigger>
          <TabsTrigger value="contacts"><Users className="h-4 w-4 mr-1.5" />Contactos</TabsTrigger>
          <TabsTrigger value="activities"><Activity className="h-4 w-4 mr-1.5" />Actividades</TabsTrigger>
          <TabsTrigger value="analytics"><BarChart3 className="h-4 w-4 mr-1.5" />Análise</TabsTrigger>
        </TabsList>

        {/* ════ PIPELINE ════ */}
        <TabsContent value="pipeline" className="mt-4">
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${PIPELINE_STAGES.filter(s => s !== 'LOST' && s !== 'COLD').length}, 1fr)` }}>
            {PIPELINE_STAGES.filter(s => s !== 'LOST' && s !== 'COLD').map(stage => {
              const stageContacts = filtered.filter(c => c.stage === stage)
              const stageValue    = stageContacts.reduce((s, c) => s + Number(c.deal_value), 0)
              const colors        = STAGE_COLORS[stage]
              return (
                <div key={stage} className="flex flex-col gap-2">
                  <div className={`rounded-lg border ${colors.border} px-3 py-2 ${colors.bg}`}>
                    <p className={`text-xs font-semibold uppercase tracking-wide ${colors.text}`}>
                      {STAGE_LABELS[stage]}
                    </p>
                    <div className="flex items-center justify-between mt-0.5">
                      <span className="text-xs text-muted-foreground">{stageContacts.length} contactos</span>
                      <span className={`text-xs font-medium ${colors.text}`}>{fmtM(stageValue)}</span>
                    </div>
                  </div>
                  <div className="space-y-2 min-h-16">
                    {stageContacts.map(c => (
                      <motion.div key={c.id}
                        initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }}
                        className="rounded-lg border bg-card p-3 cursor-pointer hover:shadow-md transition-shadow"
                        onClick={() => openDetail(c)}
                      >
                        <div className="flex items-start justify-between gap-1">
                          <div className="min-w-0">
                            <p className="text-sm font-medium truncate">{c.full_name}</p>
                            {c.company && <p className="text-xs text-muted-foreground truncate">{c.company}</p>}
                          </div>
                          <Button variant="ghost" size="icon" className="h-5 w-5 shrink-0" onClick={e => { e.stopPropagation(); openEditContact(c) }}>
                            <Edit className="h-3 w-3" />
                          </Button>
                        </div>
                        <div className="mt-2">
                          <p className="text-xs font-semibold text-green-600">{fmtM(Number(c.deal_value))} AOA</p>
                          <div className="flex items-center gap-1 mt-1">
                            <Progress value={Number(c.probability)} className="h-1.5 flex-1" />
                            <span className="text-xs text-muted-foreground">{c.probability}%</span>
                          </div>
                        </div>
                        {c.next_followup && (
                          <p className="text-xs text-orange-600 mt-1.5 flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            Follow-up: {fmtDate(c.next_followup)}
                          </p>
                        )}
                      </motion.div>
                    ))}
                    {stageContacts.length === 0 && (
                      <div className="rounded-lg border-2 border-dashed border-border/40 flex items-center justify-center h-16">
                        <p className="text-xs text-muted-foreground">Sem contactos</p>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>

          {/* Perdidos e frios */}
          <div className="grid gap-3 mt-4 md:grid-cols-2">
            {(['LOST','COLD'] as ContactStage[]).map(stage => {
              const sc = filtered.filter(c => c.stage === stage)
              const colors = STAGE_COLORS[stage]
              return (
                <Card key={stage}>
                  <CardHeader className="pb-2">
                    <CardTitle className={`text-sm ${colors.text}`}>
                      {STAGE_LABELS[stage]} ({sc.length})
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {sc.slice(0, 3).map(c => (
                      <div key={c.id} className="flex items-center justify-between text-sm">
                        <span className="truncate">{c.full_name} — {c.company}</span>
                        <span className="text-muted-foreground shrink-0 ml-2">{fmtM(Number(c.deal_value))} AOA</span>
                      </div>
                    ))}
                    {sc.length === 0 && <p className="text-xs text-muted-foreground">Nenhum</p>}
                    {sc.length > 3 && <p className="text-xs text-muted-foreground">+{sc.length - 3} mais</p>}
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </TabsContent>

        {/* ════ CONTACTOS ════ */}
        <TabsContent value="contacts" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
                <div>
                  <CardTitle>Contactos ({filtered.length})</CardTitle>
                  <CardDescription>Base de dados de clientes e leads</CardDescription>
                </div>
                <div className="flex flex-wrap gap-2">
                  <div className="relative">
                    <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-muted-foreground" />
                    <Input className="pl-8 h-9 w-52" placeholder="Pesquisar..." value={searchQ}
                      onChange={e => setSearchQ(e.target.value)} />
                    {searchQ && <button onClick={() => setSearchQ('')} className="absolute right-2.5 top-2.5"><X className="h-3.5 w-3.5 text-muted-foreground" /></button>}
                  </div>
                  <Select value={stageFilter} onValueChange={setStageFilter}>
                    <SelectTrigger className="h-9 w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todas as etapas</SelectItem>
                      {PIPELINE_STAGES.map(s => <SelectItem key={s} value={s}>{STAGE_LABELS[s]}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <Users className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhum contacto encontrado</p>
                  <Button size="sm" onClick={() => { setEditingContact(null); setContactForm({ ...EMPTY_CONTACT }); setContactDialog(true) }}>
                    <Plus className="h-4 w-4 mr-2" />Criar Contacto
                  </Button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="text-left pb-2 font-medium text-muted-foreground">Nome</th>
                        <th className="text-left pb-2 font-medium text-muted-foreground hidden md:table-cell">Empresa</th>
                        <th className="text-left pb-2 font-medium text-muted-foreground">Etapa</th>
                        <th className="text-right pb-2 font-medium text-muted-foreground hidden sm:table-cell">Valor</th>
                        <th className="text-right pb-2 font-medium text-muted-foreground hidden lg:table-cell">Prob.</th>
                        <th className="text-right pb-2 font-medium text-muted-foreground hidden lg:table-cell">Próx. Follow-up</th>
                        <th className="pb-2"></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {filtered.map(c => {
                        const colors = STAGE_COLORS[c.stage]
                        return (
                          <tr key={c.id} className="hover:bg-muted/20 cursor-pointer" onClick={() => openDetail(c)}>
                            <td className="py-3">
                              <div className="flex items-center gap-2">
                                <Avatar className="h-7 w-7">
                                  <AvatarFallback className="text-xs">{c.full_name.slice(0,2).toUpperCase()}</AvatarFallback>
                                </Avatar>
                                <div>
                                  <p className="font-medium">{c.full_name}</p>
                                  {c.position && <p className="text-xs text-muted-foreground">{c.position}</p>}
                                </div>
                              </div>
                            </td>
                            <td className="py-3 text-muted-foreground hidden md:table-cell">{c.company || '—'}</td>
                            <td className="py-3">
                              <Badge className={`${colors.bg} ${colors.text} ${colors.border} border text-xs`}>
                                {STAGE_LABELS[c.stage]}
                              </Badge>
                            </td>
                            <td className="py-3 text-right font-medium text-green-600 hidden sm:table-cell">
                              {fmtM(Number(c.deal_value))} AOA
                            </td>
                            <td className="py-3 text-right hidden lg:table-cell">
                              <div className="flex items-center justify-end gap-1">
                                <Progress value={Number(c.probability)} className="h-1.5 w-16" />
                                <span className="text-xs text-muted-foreground w-8">{c.probability}%</span>
                              </div>
                            </td>
                            <td className="py-3 text-right text-xs text-muted-foreground hidden lg:table-cell">
                              {c.next_followup ? <span className="text-orange-600">{fmtDate(c.next_followup)}</span> : '—'}
                            </td>
                            <td className="py-3" onClick={e => e.stopPropagation()}>
                              <div className="flex items-center justify-end gap-1">
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => openEditContact(c)}>
                                  <Edit className="h-3.5 w-3.5" />
                                </Button>
                                <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                                  onClick={() => handleDeleteContact(c.id, c.full_name)}>
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ ACTIVIDADES ════ */}
        <TabsContent value="activities" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Actividades Recentes</CardTitle>
              <CardDescription>Últimas interacções com contactos</CardDescription>
            </CardHeader>
            <CardContent>
              {activities.length === 0 ? (
                <div className="flex flex-col items-center py-12 gap-3 text-muted-foreground">
                  <Activity className="h-12 w-12 opacity-15" />
                  <p className="text-sm">Nenhuma actividade registada</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {activities.map((act, i) => {
                    const contact = contacts.find(c => c.id === act.contact_id)
                    return (
                      <motion.div key={act.id}
                        initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
                        className="flex gap-3 rounded-lg border p-3 hover:bg-muted/20 transition-colors"
                      >
                        <div className="text-xl shrink-0">{ACTIVITY_ICONS[act.activity_type] || '📌'}</div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-start justify-between gap-2">
                            <div>
                              <p className="font-medium text-sm">{act.title}</p>
                              <p className="text-xs text-muted-foreground">
                                {ACTIVITY_LABELS[act.activity_type]} · {contact ? `${contact.full_name} (${contact.company || '—'})` : '—'}
                              </p>
                            </div>
                            <p className="text-xs text-muted-foreground shrink-0">
                              {fmtDate(act.activity_date)}
                            </p>
                          </div>
                          {act.outcome && (
                            <p className="text-xs text-green-600 mt-1">✓ {act.outcome}</p>
                          )}
                          {act.duration_min && (
                            <p className="text-xs text-muted-foreground mt-0.5">
                              <Clock className="h-3 w-3 inline mr-1" />{act.duration_min} min
                            </p>
                          )}
                        </div>
                      </motion.div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ ANÁLISE ════ */}
        <TabsContent value="analytics" className="mt-4 space-y-4">
          {stats && (
            <div className="grid gap-4 md:grid-cols-2">
              {/* Funil */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Funil de Vendas</CardTitle>
                  <CardDescription>Contactos por etapa do pipeline</CardDescription>
                </CardHeader>
                <CardContent>
                  {funnelData.length === 0 ? (
                    <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">Sem dados</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={funnelData} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 80 }}>
                        <XAxis type="number" tick={{ fontSize: 11 }} />
                        <YAxis dataKey="name" type="category" tick={{ fontSize: 11 }} width={75} />
                        <Tooltip />
                        <Bar dataKey="value" name="Contactos" radius={[0,4,4,0]}>
                          {funnelData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
                        </Bar>
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              {/* Fontes */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Origem dos Contactos</CardTitle>
                  <CardDescription>Distribuição por fonte de captação</CardDescription>
                </CardHeader>
                <CardContent>
                  {sourceData.length === 0 ? (
                    <div className="flex items-center justify-center h-48 text-muted-foreground text-sm">Sem dados</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <RePieChart>
                        <Pie data={sourceData} cx="50%" cy="50%" innerRadius={50} outerRadius={80}
                          dataKey="value" nameKey="name"
                          label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                          labelLine={false}>
                          {sourceData.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                        </Pie>
                        <Tooltip />
                      </RePieChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              {/* Valor por etapa */}
              <Card className="md:col-span-2">
                <CardHeader>
                  <CardTitle className="text-base">Valor do Pipeline por Etapa</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {PIPELINE_STAGES.map(stage => {
                      const sc      = contacts.filter(c => c.stage === stage)
                      const val     = sc.reduce((s, c) => s + Number(c.deal_value), 0)
                      const maxVal  = Math.max(...PIPELINE_STAGES.map(s =>
                        contacts.filter(c => c.stage === s).reduce((a, c) => a + Number(c.deal_value), 0)
                      ), 1)
                      const pct     = (val / maxVal) * 100
                      const colors  = STAGE_COLORS[stage]
                      return (
                        <div key={stage} className="flex items-center gap-3">
                          <span className={`text-xs font-medium w-24 shrink-0 ${colors.text}`}>{STAGE_LABELS[stage]}</span>
                          <div className="flex-1">
                            <Progress value={pct} className={`h-3 ${
                              stage === 'WON' ? '[&>div]:bg-green-500' :
                              stage === 'LOST' ? '[&>div]:bg-red-400' :
                              stage === 'NEGOTIATION' ? '[&>div]:bg-orange-400' :
                              '[&>div]:bg-blue-400'
                            }`} />
                          </div>
                          <span className="text-xs font-medium w-28 text-right">{fmt(val)}</span>
                          <span className="text-xs text-muted-foreground w-8">{sc.length}×</span>
                        </div>
                      )
                    })}
                  </div>
                </CardContent>
              </Card>
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ══════ MODAL: Criar/Editar Contacto ══════ */}
      <Dialog open={contactDialog} onOpenChange={v => { if (!v) { setContactDialog(false); setEditingContact(null) } }}>
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingContact ? `Editar: ${editingContact.full_name}` : 'Novo Contacto'}</DialogTitle>
            <DialogDescription>Preencha os dados do contacto/lead</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1">
                <Label>Nome completo <span className="text-destructive">*</span></Label>
                <Input value={contactForm.full_name} onChange={e => setContactForm(f => ({ ...f, full_name: e.target.value }))} placeholder="Nome do contacto" />
              </div>
              <div className="space-y-1">
                <Label>Empresa</Label>
                <Input value={contactForm.company || ''} onChange={e => setContactForm(f => ({ ...f, company: e.target.value }))} placeholder="Nome da empresa" />
              </div>
              <div className="space-y-1">
                <Label>Cargo</Label>
                <Input value={contactForm.position || ''} onChange={e => setContactForm(f => ({ ...f, position: e.target.value }))} placeholder="Ex: Director Financeiro" />
              </div>
              <div className="space-y-1">
                <Label>Email</Label>
                <Input type="email" value={contactForm.email || ''} onChange={e => setContactForm(f => ({ ...f, email: e.target.value }))} placeholder="email@empresa.ao" />
              </div>
              <div className="space-y-1">
                <Label>Telefone</Label>
                <Input value={contactForm.phone || ''} onChange={e => setContactForm(f => ({ ...f, phone: e.target.value }))} placeholder="+244 9xx xxx xxx" />
              </div>
              <div className="space-y-1">
                <Label>Etapa</Label>
                <Select value={contactForm.stage} onValueChange={v => setContactForm(f => ({ ...f, stage: v as ContactStage }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PIPELINE_STAGES.map(s => <SelectItem key={s} value={s}>{STAGE_LABELS[s]}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Origem</Label>
                <Select value={contactForm.source} onValueChange={v => setContactForm(f => ({ ...f, source: v as ContactSource }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(SOURCE_LABELS) as ContactSource[]).map(s =>
                      <SelectItem key={s} value={s}>{SOURCE_LABELS[s]}</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Valor do Negócio (AOA)</Label>
                <Input type="number" min="0" step="100000"
                  value={contactForm.deal_value || ''}
                  onChange={e => setContactForm(f => ({ ...f, deal_value: parseFloat(e.target.value) || 0 }))} placeholder="0" />
              </div>
              <div className="space-y-1">
                <Label>Probabilidade (%)</Label>
                <Input type="number" min="0" max="100"
                  value={contactForm.probability || ''}
                  onChange={e => setContactForm(f => ({ ...f, probability: parseInt(e.target.value) || 0 }))} placeholder="0-100" />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Data de Fecho Prevista</Label>
                <Input type="date" value={contactForm.expected_close || ''}
                  onChange={e => setContactForm(f => ({ ...f, expected_close: e.target.value }))} />
              </div>
            </div>
            <div className="space-y-1">
              <Label>Notas</Label>
              <Textarea rows={2} value={contactForm.notes || ''}
                onChange={e => setContactForm(f => ({ ...f, notes: e.target.value }))}
                placeholder="Contexto, informação relevante..." />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setContactDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveContact} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {editingContact ? 'Guardar' : 'Criar'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Detalhe do Contacto ══════ */}
      <Dialog open={detailDialog} onOpenChange={v => { if (!v) { setDetailDialog(false); setSelectedContact(null) } }}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          {selectedContact && (
            <>
              <DialogHeader>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <DialogTitle className="text-xl">{selectedContact.full_name}</DialogTitle>
                    <DialogDescription>{selectedContact.position} · {selectedContact.company}</DialogDescription>
                  </div>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" onClick={() => { setDetailDialog(false); openEditContact(selectedContact) }}>
                      <Edit className="h-4 w-4 mr-1" />Editar
                    </Button>
                    <Button size="sm" onClick={() => setActivityDialog(true)}>
                      <Plus className="h-4 w-4 mr-1" />Actividade
                    </Button>
                  </div>
                </div>
              </DialogHeader>
              <div className="space-y-4 mt-2">
                {/* Contactos e etapa */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    {selectedContact.email && (
                      <div className="flex items-center gap-2 text-sm">
                        <Mail className="h-4 w-4 text-muted-foreground" />
                        <a href={`mailto:${selectedContact.email}`} className="text-blue-600 hover:underline">{selectedContact.email}</a>
                      </div>
                    )}
                    {selectedContact.phone && (
                      <div className="flex items-center gap-2 text-sm">
                        <Phone className="h-4 w-4 text-muted-foreground" />
                        <span>{selectedContact.phone}</span>
                      </div>
                    )}
                    {selectedContact.company && (
                      <div className="flex items-center gap-2 text-sm">
                        <Building2 className="h-4 w-4 text-muted-foreground" />
                        <span>{selectedContact.company}</span>
                      </div>
                    )}
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Etapa:</span>
                      <Select value={selectedContact.stage}
                        onValueChange={v => handleStageChange(selectedContact.id, v as ContactStage)}>
                        <SelectTrigger className="h-7 w-36 text-xs"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {PIPELINE_STAGES.map(s => <SelectItem key={s} value={s}>{STAGE_LABELS[s]}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="text-sm">
                      <span className="text-muted-foreground">Valor: </span>
                      <span className="font-bold text-green-600">{fmt(Number(selectedContact.deal_value))}</span>
                    </div>
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-muted-foreground">Prob.:</span>
                      <Progress value={Number(selectedContact.probability)} className="h-2 w-20" />
                      <span>{selectedContact.probability}%</span>
                    </div>
                    {selectedContact.expected_close && (
                      <div className="text-sm">
                        <span className="text-muted-foreground">Fecho previsto: </span>
                        <span>{fmtDate(selectedContact.expected_close)}</span>
                      </div>
                    )}
                  </div>
                </div>

                {selectedContact.notes && (
                  <div className="rounded-md bg-muted/30 p-3 text-sm">{selectedContact.notes}</div>
                )}

                <Separator />
                <div>
                  <p className="text-sm font-semibold mb-3">
                    Actividades ({contactActivities.length})
                    {loadingDetail && <Loader2 className="h-3 w-3 inline ml-2 animate-spin" />}
                  </p>
                  {contactActivities.length === 0 && !loadingDetail ? (
                    <p className="text-sm text-muted-foreground">Nenhuma actividade registada</p>
                  ) : (
                    <div className="space-y-2 max-h-60 overflow-y-auto">
                      {contactActivities.map(act => (
                        <div key={act.id} className="flex gap-3 rounded-lg border p-3 text-sm">
                          <span className="text-base shrink-0">{ACTIVITY_ICONS[act.activity_type] || '📌'}</span>
                          <div>
                            <p className="font-medium">{act.title}</p>
                            <p className="text-xs text-muted-foreground">
                              {ACTIVITY_LABELS[act.activity_type]} · {fmtDate(act.activity_date)}
                              {act.duration_min ? ` · ${act.duration_min} min` : ''}
                            </p>
                            {act.outcome && <p className="text-xs text-green-600 mt-0.5">✓ {act.outcome}</p>}
                            {act.description && <p className="text-xs text-muted-foreground mt-0.5">{act.description}</p>}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* ══════ MODAL: Registar Actividade ══════ */}
      <Dialog open={activityDialog} onOpenChange={v => { if (!v) setActivityDialog(false) }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Registar Actividade</DialogTitle>
            <DialogDescription>{selectedContact?.full_name} · {selectedContact?.company}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>Tipo</Label>
                <Select value={activityForm.activity_type}
                  onValueChange={v => setActivityForm(f => ({ ...f, activity_type: v as ActivityType }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(Object.keys(ACTIVITY_LABELS) as ActivityType[]).map(t =>
                      <SelectItem key={t} value={t}>{ACTIVITY_ICONS[t]} {ACTIVITY_LABELS[t]}</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Duração (min)</Label>
                <Input type="number" min="1"
                  value={activityForm.duration_min || ''}
                  onChange={e => setActivityForm(f => ({ ...f, duration_min: parseInt(e.target.value) || 0 }))} />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Título <span className="text-destructive">*</span></Label>
                <Input value={activityForm.title}
                  onChange={e => setActivityForm(f => ({ ...f, title: e.target.value }))}
                  placeholder="Ex: Reunião de apresentação" />
              </div>
              <div className="col-span-2 space-y-1">
                <Label>Data e hora</Label>
                <Input type="datetime-local" value={activityForm.activity_date}
                  onChange={e => setActivityForm(f => ({ ...f, activity_date: e.target.value }))} />
              </div>
            </div>
            <div className="space-y-1">
              <Label>Descrição</Label>
              <Textarea rows={2} value={activityForm.description || ''}
                onChange={e => setActivityForm(f => ({ ...f, description: e.target.value }))}
                placeholder="Detalhes da actividade..." />
            </div>
            <div className="space-y-1">
              <Label>Resultado / Outcome</Label>
              <Input value={activityForm.outcome || ''}
                onChange={e => setActivityForm(f => ({ ...f, outcome: e.target.value }))}
                placeholder="Ex: Cliente interessado, aguarda proposta" />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setActivityDialog(false)} disabled={submitting}>Cancelar</Button>
              <Button onClick={handleSaveActivity} disabled={submitting}>
                {submitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}Guardar
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
