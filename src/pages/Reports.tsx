import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  FileText, Download, Plus, Trash2, RefreshCw, Play, Loader2,
  BarChart3, Clock, CheckCircle2, XCircle, AlertCircle,
  Calendar, Filter, Search, FileSpreadsheet, FileDown, X,
  TrendingUp, FileBarChart, FileCheck2, Settings2,
} from 'lucide-react'
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
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { toast } from 'sonner'
import {
  templatesService, reportsService, schedulesService,
  TEMPLATE_TYPE_LABELS, REPORT_STATUS_LABELS, SCHEDULE_FREQ_LABELS, FORMAT_LABELS,
  type ReportTemplate, type GeneratedReport, type ReportStats,
  type ReportFormat, type ReportStatus, type TemplateType,
} from '@/services/reportsService'

// ─── Helpers ──────────────────────────────────────────────────────────────────
const fmtSize = (bytes: number) => {
  if (!bytes) return '—'
  if (bytes < 1024)       return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
const fmtDate = (d?: string) =>
  d ? new Date(d).toLocaleDateString('pt-AO', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'

const STATUS_ICONS: Record<ReportStatus, React.ReactNode> = {
  pending:    <Clock className="h-4 w-4 text-amber-500" />,
  processing: <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />,
  completed:  <CheckCircle2 className="h-4 w-4 text-green-500" />,
  failed:     <XCircle className="h-4 w-4 text-red-500" />,
}

const STATUS_BADGE: Record<ReportStatus, string> = {
  pending:    'bg-amber-100 text-amber-700',
  processing: 'bg-blue-100 text-blue-700',
  completed:  'bg-green-100 text-green-700',
  failed:     'bg-red-100 text-red-700',
}

const FORMAT_ICONS: Record<ReportFormat, React.ReactNode> = {
  pdf:   <FileText className="h-4 w-4 text-red-500" />,
  excel: <FileSpreadsheet className="h-4 w-4 text-green-600" />,
  csv:   <FileBarChart className="h-4 w-4 text-blue-500" />,
}

const TYPE_ICONS: Record<TemplateType, React.ReactNode> = {
  financial: <TrendingUp className="h-4 w-4" />,
  hr:        <FileCheck2 className="h-4 w-4" />,
  sales:     <BarChart3 className="h-4 w-4" />,
  crm:       <FileBarChart className="h-4 w-4" />,
  custom:    <Settings2 className="h-4 w-4" />,
  inventory: <FileText className="h-4 w-4" />,
  tax:       <FileDown className="h-4 w-4" />,
}

const TYPE_COLORS: Record<TemplateType, string> = {
  financial: 'bg-blue-100 text-blue-700',
  hr:        'bg-purple-100 text-purple-700',
  sales:     'bg-green-100 text-green-700',
  crm:       'bg-indigo-100 text-indigo-700',
  custom:    'bg-gray-100 text-gray-700',
  inventory: 'bg-orange-100 text-orange-700',
  tax:       'bg-red-100 text-red-700',
}

// ═══════════════════════════════════════════════════════════════════════════════
export default function Reports() {
  const [templates,  setTemplates]  = useState<ReportTemplate[]>([])
  const [reports,    setReports]    = useState<GeneratedReport[]>([])
  const [stats,      setStats]      = useState<ReportStats | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [activeTab,  setActiveTab]  = useState('reports')

  // Filtros
  const [searchQ,       setSearchQ]       = useState('')
  const [statusFilter,  setStatusFilter]  = useState<string>('all')
  const [formatFilter,  setFormatFilter]  = useState<string>('all')

  // Geração
  const [genDialog,    setGenDialog]    = useState(false)
  const [selTemplate,  setSelTemplate]  = useState<string>('')
  const [genName,      setGenName]      = useState('')
  const [genFormat,    setGenFormat]    = useState<ReportFormat>('pdf')
  const [generating,   setGenerating]   = useState<string | null>(null) // templateId sendo gerado

  // ─── Carregar ───────────────────────────────────────────────────────────
  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [t, r, s] = await Promise.all([
        templatesService.getAll(),
        reportsService.getAll(),
        reportsService.getStats(),
      ])
      setTemplates(t)
      setReports(r)
      setStats(s)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao carregar relatórios. Verifique a ligação.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // ─── Filtrar relatórios ──────────────────────────────────────────────
  const filteredReports = reports.filter(r => {
    const matchStatus = statusFilter === 'all' || r.status === statusFilter
    const matchFormat = formatFilter === 'all' || r.file_format === formatFilter
    const matchSearch = !searchQ || r.name.toLowerCase().includes(searchQ.toLowerCase())
    return matchStatus && matchFormat && matchSearch
  })

  // ─── Gerar relatório ─────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!selTemplate) { toast.error('Seleccione um template'); return }
    if (!genName.trim()) { toast.error('Nome é obrigatório'); return }
    const tmpl = templates.find(t => t.id === selTemplate)
    if (!tmpl) return
    setGenDialog(false)
    setGenerating(selTemplate)
    toast.info(`A gerar "${genName}"...`)
    try {
      const created = await reportsService.generate(selTemplate, genName, genFormat)
      setReports(prev => [created, ...prev])
      setStats(await reportsService.getStats())
      toast.success(`Relatório "${created.name}" gerado com sucesso!`)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao gerar relatório')
    } finally {
      setGenerating(null); setSelTemplate(''); setGenName(''); setGenFormat('pdf')
    }
  }

  const handleQuickGenerate = async (template: ReportTemplate) => {
    if (generating) return
    const name = `${template.name} — ${new Date().toLocaleDateString('pt-AO')}`
    setGenerating(template.id)
    toast.info(`A gerar "${name}"...`)
    try {
      const created = await reportsService.generate(template.id, name, 'pdf')
      setReports(prev => [created, ...prev])
      setStats(await reportsService.getStats())
      toast.success(`Relatório "${created.name}" gerado!`)
    } catch (err) {
      console.error(err)
      toast.error('Erro ao gerar relatório')
    } finally { setGenerating(null) }
  }

  const handleDeleteReport = async (id: string, name: string) => {
    if (!confirm(`Eliminar relatório "${name}"?`)) return
    try {
      await reportsService.delete(id)
      setReports(prev => prev.filter(r => r.id !== id))
      setStats(await reportsService.getStats())
      toast.success('Relatório eliminado')
    } catch { toast.error('Sem permissão ou relatório não encontrado') }
  }

  // ─── Skeleton ────────────────────────────────────────────────────────
  if (loading) return (
    <div className="space-y-6 p-6">
      <Skeleton className="h-10 w-56" />
      <div className="grid gap-4 md:grid-cols-4">
        {[...Array(4)].map((_,i) => <Card key={i}><CardContent className="p-6"><Skeleton className="h-16 w-full" /></CardContent></Card>)}
      </div>
      <Card><CardContent className="p-6"><Skeleton className="h-64 w-full" /></CardContent></Card>
    </div>
  )

  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div className="space-y-6 p-6">

      {/* ── Cabeçalho ── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Relatórios</h1>
          <p className="text-muted-foreground">Geração e gestão de relatórios empresariais</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadData}><RefreshCw className="h-4 w-4 mr-2" />Atualizar</Button>
          <Button size="sm" onClick={() => setGenDialog(true)}>
            <Plus className="h-4 w-4 mr-2" />Gerar Relatório
          </Button>
        </div>
      </div>

      {/* ── KPIs ── */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[
            { title: 'Total Gerados',    value: String(stats.total),          sub: `${stats.thisMonth} este mês`,                  icon: <FileText className="h-5 w-5 text-blue-600" />,   bg: 'bg-blue-50',   color: 'text-blue-700' },
            { title: 'Concluídos',       value: String(stats.completed),      sub: `${stats.failed} falharam`,                     icon: <CheckCircle2 className="h-5 w-5 text-green-600" />, bg: 'bg-green-50', color: 'text-green-700' },
            { title: 'Pendentes',        value: String(stats.pending),        sub: 'A aguardar processamento',                     icon: <Clock className="h-5 w-5 text-amber-600" />,    bg: 'bg-amber-50',  color: 'text-amber-700' },
            { title: 'Templates',        value: String(templates.length),     sub: `${stats.totalDownloads} downloads total`,      icon: <FileBarChart className="h-5 w-5 text-purple-600" />, bg: 'bg-purple-50', color: 'text-purple-700' },
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
        <TabsList>
          <TabsTrigger value="reports"><FileText className="h-4 w-4 mr-1.5" />Relatórios</TabsTrigger>
          <TabsTrigger value="templates"><FileBarChart className="h-4 w-4 mr-1.5" />Templates</TabsTrigger>
        </TabsList>

        {/* ════ RELATÓRIOS GERADOS ════ */}
        <TabsContent value="reports" className="mt-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <CardTitle>Relatórios Gerados ({filteredReports.length})</CardTitle>
                  <CardDescription>Histórico de relatórios e documentos exportados</CardDescription>
                </div>
                <div className="flex flex-wrap gap-2">
                  <div className="relative">
                    <Search className="h-4 w-4 absolute left-2.5 top-2.5 text-muted-foreground" />
                    <Input className="pl-8 h-9 w-48" placeholder="Pesquisar..." value={searchQ}
                      onChange={e => setSearchQ(e.target.value)} />
                    {searchQ && <button onClick={() => setSearchQ('')} className="absolute right-2.5 top-2.5"><X className="h-3.5 w-3.5 text-muted-foreground" /></button>}
                  </div>
                  <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="h-9 w-36"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos os estados</SelectItem>
                      {(Object.keys(REPORT_STATUS_LABELS) as ReportStatus[]).map(s =>
                        <SelectItem key={s} value={s}>{REPORT_STATUS_LABELS[s]}</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                  <Select value={formatFilter} onValueChange={setFormatFilter}>
                    <SelectTrigger className="h-9 w-28"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">Todos</SelectItem>
                      {(Object.keys(FORMAT_LABELS) as ReportFormat[]).map(f =>
                        <SelectItem key={f} value={f}>{FORMAT_LABELS[f]}</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {filteredReports.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted-foreground">
                  <FileText className="h-14 w-14 opacity-15" />
                  <p className="text-sm">Nenhum relatório encontrado</p>
                  <Button size="sm" onClick={() => setGenDialog(true)}>
                    <Plus className="h-4 w-4 mr-2" />Gerar Primeiro Relatório
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredReports.map((r, i) => (
                    <motion.div key={r.id}
                      initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
                      className="flex items-center gap-3 rounded-lg border p-3 hover:bg-muted/20 transition-colors"
                    >
                      <div className="shrink-0">{FORMAT_ICONS[r.file_format] || <FileText className="h-4 w-4" />}</div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <p className="font-medium text-sm truncate">{r.name}</p>
                          <Badge className={`text-xs ${STATUS_BADGE[r.status]}`}>
                            <span className="flex items-center gap-1">
                              {STATUS_ICONS[r.status]}{REPORT_STATUS_LABELS[r.status]}
                            </span>
                          </Badge>
                          <Badge variant="outline" className="text-xs">{FORMAT_LABELS[r.file_format]}</Badge>
                        </div>
                        <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground flex-wrap">
                          <span>{fmtDate(r.generated_at)}</span>
                          {r.file_size ? <span>{fmtSize(r.file_size)}</span> : null}
                          {r.generation_time ? <span>{r.generation_time}s</span> : null}
                          {r.download_count > 0 ? <span>{r.download_count} download{r.download_count !== 1 ? 's' : ''}</span> : null}
                          {r.template && <span className="text-blue-600">{(r.template as ReportTemplate).name}</span>}
                        </div>
                        {r.error_message && (
                          <p className="text-xs text-red-500 mt-0.5">
                            <AlertCircle className="h-3 w-3 inline mr-1" />{r.error_message}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        {r.status === 'completed' && r.file_url && (
                          <Button variant="outline" size="icon" className="h-7 w-7"
                            onClick={() => toast.info('Download iniciado')}>
                            <Download className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        <Button variant="ghost" size="icon" className="h-7 w-7 text-destructive"
                          onClick={() => handleDeleteReport(r.id, r.name)}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </motion.div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ════ TEMPLATES ════ */}
        <TabsContent value="templates" className="mt-4">
          {templates.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center py-16 gap-3 text-muted-foreground">
                <FileBarChart className="h-14 w-14 opacity-15" />
                <p className="text-sm">Nenhum template disponível</p>
                <p className="text-xs">Crie templates personalizados para os seus relatórios</p>
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {templates.map((t, i) => (
                <motion.div key={t.id}
                  initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
                  <Card className="hover:shadow-md transition-shadow">
                    <CardHeader className="pb-2">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <CardTitle className="text-base">{t.name}</CardTitle>
                          {t.description && <CardDescription className="text-xs mt-0.5">{t.description}</CardDescription>}
                        </div>
                        <Badge className={`text-xs shrink-0 ${TYPE_COLORS[t.template_type]}`}>
                          <span className="flex items-center gap-1">
                            {TYPE_ICONS[t.template_type]}{TEMPLATE_TYPE_LABELS[t.template_type]}
                          </span>
                        </Badge>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="flex items-center justify-between mt-1">
                        <div className="text-xs text-muted-foreground">
                          {t.data_sources && t.data_sources.length > 0 && (
                            <p>{t.data_sources.slice(0, 2).join(', ')}{t.data_sources.length > 2 ? ` +${t.data_sources.length - 2}` : ''}</p>
                          )}
                          <p className="mt-0.5">{t.category}</p>
                        </div>
                        <Button size="sm" variant="default"
                          disabled={generating === t.id}
                          onClick={() => handleQuickGenerate(t)}>
                          {generating === t.id
                            ? <Loader2 className="h-4 w-4 animate-spin" />
                            : <><Play className="h-3.5 w-3.5 mr-1" />Gerar</>
                          }
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </motion.div>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* ══════ MODAL: Gerar Relatório ══════ */}
      <Dialog open={genDialog} onOpenChange={setGenDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Gerar Novo Relatório</DialogTitle>
            <DialogDescription>Seleccione um template e configure o relatório</DialogDescription>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="space-y-1">
              <Label>Template <span className="text-destructive">*</span></Label>
              <Select value={selTemplate} onValueChange={v => {
                setSelTemplate(v)
                const t = templates.find(t => t.id === v)
                if (t && !genName) setGenName(`${t.name} — ${new Date().toLocaleDateString('pt-AO')}`)
              }}>
                <SelectTrigger>
                  <SelectValue placeholder="Escolha um template..." />
                </SelectTrigger>
                <SelectContent>
                  {templates.map(t =>
                    <SelectItem key={t.id} value={t.id}>{t.name} ({TEMPLATE_TYPE_LABELS[t.template_type]})</SelectItem>
                  )}
                </SelectContent>
              </Select>
              {templates.length === 0 && (
                <p className="text-xs text-amber-600 flex items-center gap-1">
                  <AlertCircle className="h-3 w-3" />Não há templates disponíveis
                </p>
              )}
            </div>
            <div className="space-y-1">
              <Label>Nome do Relatório <span className="text-destructive">*</span></Label>
              <Input value={genName} onChange={e => setGenName(e.target.value)}
                placeholder="Ex: Relatório Financeiro Q2 2026" />
            </div>
            <div className="space-y-1">
              <Label>Formato</Label>
              <Select value={genFormat} onValueChange={v => setGenFormat(v as ReportFormat)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(Object.keys(FORMAT_LABELS) as ReportFormat[]).map(f =>
                    <SelectItem key={f} value={f}>{FORMAT_LABELS[f]}</SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => setGenDialog(false)}>Cancelar</Button>
              <Button onClick={handleGenerate} disabled={!selTemplate || !genName.trim()}>
                <Play className="h-4 w-4 mr-2" />Gerar
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
