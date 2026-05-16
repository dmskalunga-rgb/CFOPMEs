import { supabase } from '@/integrations/supabase/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function getUserId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  return user.id
}

// ─── Tipos ────────────────────────────────────────────────────────────────────
export type TemplateType = 'financial' | 'hr' | 'sales' | 'crm' | 'custom' | 'inventory' | 'tax'
export type ReportStatus = 'pending' | 'processing' | 'completed' | 'failed'
export type ReportFormat = 'pdf' | 'excel' | 'csv'
export type ScheduleFreq = 'daily' | 'weekly' | 'monthly' | 'quarterly'

export interface ReportTemplate {
  id:              string
  name:            string
  description?:    string
  category:        string
  template_type:   TemplateType
  data_sources?:   string[]
  parameters?:     Record<string, unknown>
  is_active:       boolean
  created_by?:     string
  created_at?:     string
  updated_at?:     string
}

export interface GeneratedReport {
  id:               string
  template_id?:     string
  name:             string
  description?:     string
  status:           ReportStatus
  file_url?:        string
  file_size?:       number
  file_format:      ReportFormat
  generation_time?: number
  parameters_used?: Record<string, unknown>
  error_message?:   string
  generated_by?:    string
  generated_at?:    string
  expires_at?:      string
  download_count:   number
  last_downloaded_at?: string
  // join
  template?:        ReportTemplate
}

export interface ReportSchedule {
  id:              string
  template_id:     string
  name:            string
  frequency:       ScheduleFreq
  schedule_config?: Record<string, unknown>
  parameters?:     Record<string, unknown>
  recipients?:     string[]
  is_active:       boolean
  last_run_at?:    string
  next_run_at?:    string
  created_by?:     string
  created_at?:     string
  // join
  template?:       ReportTemplate
}

export interface ReportStats {
  total:           number
  completed:       number
  pending:         number
  failed:          number
  thisMonth:       number
  totalSize:       number
  totalDownloads:  number
  avgGenTime:      number
}

// ─── Labels ───────────────────────────────────────────────────────────────────
export const TEMPLATE_TYPE_LABELS: Record<TemplateType, string> = {
  financial: 'Financeiro',
  hr:        'Recursos Humanos',
  sales:     'Vendas',
  crm:       'CRM / Clientes',
  custom:    'Personalizado',
  inventory: 'Inventário',
  tax:       'Fiscal / Impostos',
}

export const REPORT_STATUS_LABELS: Record<ReportStatus, string> = {
  pending:    'Pendente',
  processing: 'A gerar...',
  completed:  'Concluído',
  failed:     'Falhou',
}

export const SCHEDULE_FREQ_LABELS: Record<ScheduleFreq, string> = {
  daily:     'Diário',
  weekly:    'Semanal',
  monthly:   'Mensal',
  quarterly: 'Trimestral',
}

export const FORMAT_LABELS: Record<ReportFormat, string> = {
  pdf:   'PDF',
  excel: 'Excel',
  csv:   'CSV',
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Templates
// ═══════════════════════════════════════════════════════════════════════════════
export const templatesService = {
  async getAll(): Promise<ReportTemplate[]> {
    const { data, error } = await supabase
      .from('report_templates_2026_04_09')
      .select('*')
      .eq('is_active', true)
      .order('category')
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      data_sources: typeof r.data_sources === 'string' ? JSON.parse(r.data_sources) : (r.data_sources || []),
      parameters:   typeof r.parameters   === 'string' ? JSON.parse(r.parameters)   : (r.parameters   || {}),
    })) as ReportTemplate[]
  },

  async create(input: Omit<ReportTemplate, 'id' | 'created_at' | 'updated_at'>): Promise<ReportTemplate> {
    const userId = await getUserId()
    const { data, error } = await supabase
      .from('report_templates_2026_04_09')
      .insert({ ...input, created_by: userId })
      .select().single()
    if (error) throw error
    return data as ReportTemplate
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('report_templates_2026_04_09').delete().eq('id', id)
    if (error) throw error
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Relatórios Gerados
// ═══════════════════════════════════════════════════════════════════════════════
export const reportsService = {
  async getAll(filters?: { status?: ReportStatus; format?: ReportFormat }): Promise<GeneratedReport[]> {
    let query = supabase
      .from('generated_reports_2026_04_09')
      .select(`*, template:report_templates_2026_04_09(name,category,template_type)`)
      .order('generated_at', { ascending: false })

    if (filters?.status) query = query.eq('status', filters.status)
    if (filters?.format) query = query.eq('file_format', filters.format)

    const { data, error } = await query
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      parameters_used: typeof r.parameters_used === 'string'
        ? JSON.parse(r.parameters_used) : (r.parameters_used || {}),
    })) as GeneratedReport[]
  },

  async generate(templateId: string, name: string, format: ReportFormat = 'pdf'): Promise<GeneratedReport> {
    const userId = await getUserId()

    // 1. Criar registo com status 'processing'
    const { data: created, error: createErr } = await supabase
      .from('generated_reports_2026_04_09')
      .insert({
        template_id:    templateId,
        name,
        status:         'processing',
        file_format:    format,
        generated_by:   userId,
        generated_at:   new Date().toISOString(),
        download_count: 0,
        expires_at:     new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
      })
      .select().single()
    if (createErr) throw createErr

    // 2. Simular geração (cliente-side): esperar 2s, marcar como completed
    //    Em produção isto seria uma Edge Function ou webhook
    await new Promise(r => setTimeout(r, 2000))

    const genTime = Math.floor(Math.random() * 8) + 2
    const fileSizeKB = Math.floor(Math.random() * 3000) + 500

    const { data: updated, error: updateErr } = await supabase
      .from('generated_reports_2026_04_09')
      .update({
        status:          'completed',
        generation_time: genTime,
        file_size:       fileSizeKB * 1024,
        file_url:        `#report-${created.id}`,
      })
      .eq('id', created.id)
      .select(`*, template:report_templates_2026_04_09(name,category,template_type)`)
      .single()
    if (updateErr) throw updateErr

    return updated as GeneratedReport
  },

  async delete(id: string): Promise<void> {
    const userId = await getUserId()
    const { error } = await supabase
      .from('generated_reports_2026_04_09')
      .delete().eq('id', id).eq('generated_by', userId)
    if (error) throw error
  },

  /** Exportar relatório como CSV baseado em dados reais */
  async exportAsCSV(report: GeneratedReport, data: Record<string, unknown>[]): Promise<void> {
    if (!data.length) return
    const headers = Object.keys(data[0])
    const rows    = data.map(row => headers.map(h => `"${String(row[h] ?? '')}"`).join(','))
    const csv     = [headers.join(','), ...rows].join('\n')
    const blob    = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const a       = document.createElement('a')
    a.href        = URL.createObjectURL(blob)
    a.download    = `${report.name.replace(/\s+/g, '_')}.csv`
    a.click()
    URL.revokeObjectURL(a.href)

    // Registar download
    await supabase.from('report_downloads_2026_04_09').insert({
      report_id: report.id,
      user_id:   await getUserId(),
    })
  },

  async getStats(): Promise<ReportStats> {
    const { data, error } = await supabase
      .from('generated_reports_2026_04_09')
      .select('status,file_size,download_count,generation_time,generated_at')
    if (error) throw error
    const rows = data || []
    const thisMonth = rows.filter(r => {
      if (!r.generated_at) return false
      const d = new Date(r.generated_at)
      const now = new Date()
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()
    })

    return {
      total:          rows.length,
      completed:      rows.filter(r => r.status === 'completed').length,
      pending:        rows.filter(r => r.status === 'pending' || r.status === 'processing').length,
      failed:         rows.filter(r => r.status === 'failed').length,
      thisMonth:      thisMonth.length,
      totalSize:      rows.reduce((s, r) => s + Number(r.file_size || 0), 0),
      totalDownloads: rows.reduce((s, r) => s + Number(r.download_count || 0), 0),
      avgGenTime:     rows.length > 0
        ? rows.reduce((s, r) => s + Number(r.generation_time || 0), 0) / rows.length
        : 0,
    }
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Agendamentos
// ═══════════════════════════════════════════════════════════════════════════════
export const schedulesService = {
  async getAll(): Promise<ReportSchedule[]> {
    const { data, error } = await supabase
      .from('report_schedules_2026_04_09')
      .select(`*, template:report_templates_2026_04_09(name,category)`)
      .order('created_at', { ascending: false })
    if (error) throw error
    return (data || []).map(r => ({
      ...r,
      recipients: typeof r.recipients === 'string' ? JSON.parse(r.recipients) : (r.recipients || []),
    })) as ReportSchedule[]
  },

  async create(input: Omit<ReportSchedule, 'id' | 'created_at' | 'template'>): Promise<ReportSchedule> {
    const userId = await getUserId()
    const { data, error } = await supabase
      .from('report_schedules_2026_04_09')
      .insert({ ...input, created_by: userId })
      .select().single()
    if (error) throw error
    return data as ReportSchedule
  },

  async toggleActive(id: string, active: boolean): Promise<void> {
    const { error } = await supabase
      .from('report_schedules_2026_04_09').update({ is_active: active }).eq('id', id)
    if (error) throw error
  },

  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('report_schedules_2026_04_09').delete().eq('id', id)
    if (error) throw error
  },
}
