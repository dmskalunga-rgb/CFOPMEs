import { supabase } from '@/integrations/supabase/client'

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function getTenantId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  const { data, error } = await supabase
    .from('users').select('tenant_id').eq('id', user.id).single()
  if (error || !data?.tenant_id) throw new Error('Tenant não encontrado')
  return data.tenant_id as string
}

async function getUserId(): Promise<string> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) throw new Error('Utilizador não autenticado')
  return user.id
}

// ─── Tipos ────────────────────────────────────────────────────────────────────
export type ContactStage  = 'LEAD' | 'QUALIFIED' | 'PROPOSAL' | 'NEGOTIATION' | 'WON' | 'LOST' | 'COLD'
export type ContactSource = 'MANUAL' | 'REFERRAL' | 'WEBSITE' | 'EVENT' | 'LINKEDIN'
export type ActivityType  =
  | 'CALL' | 'EMAIL' | 'MEETING' | 'NOTE'
  | 'TASK' | 'DEMO' | 'PROPOSAL_SENT' | 'CONTRACT_SENT'

export interface CRMContact {
  id:             string
  tenant_id:      string
  full_name:      string
  company?:       string
  email?:         string
  phone?:         string
  position?:      string
  source:         ContactSource
  stage:          ContactStage
  deal_value:     number
  probability:    number
  expected_close?: string
  owner_id?:      string
  notes?:         string
  tags?:          string[]
  is_active:      boolean
  last_contact?:  string
  next_followup?: string
  created_at?:    string
  updated_at?:    string
  // relações
  activities?:    CRMActivity[]
}

export interface CRMActivity {
  id:            string
  tenant_id:     string
  contact_id:    string
  activity_type: ActivityType
  title:         string
  description?:  string
  outcome?:      string
  activity_date: string
  duration_min?: number
  owner_id?:     string
  created_at?:   string
}

export interface PipelineStats {
  total:          number
  leads:          number
  qualified:      number
  proposal:       number
  negotiation:    number
  won:            number
  lost:           number
  cold:           number
  totalValue:     number
  weightedValue:  number
  wonValue:       number
  conversionRate: number
  avgDealValue:   number
}

// ─── Labels ───────────────────────────────────────────────────────────────────
export const STAGE_LABELS: Record<ContactStage, string> = {
  LEAD:        'Lead',
  QUALIFIED:   'Qualificado',
  PROPOSAL:    'Proposta',
  NEGOTIATION: 'Negociação',
  WON:         'Ganho',
  LOST:        'Perdido',
  COLD:        'Frio',
}

export const STAGE_COLORS: Record<ContactStage, { bg: string; text: string; border: string }> = {
  LEAD:        { bg: 'bg-gray-100',   text: 'text-gray-700',   border: 'border-gray-300' },
  QUALIFIED:   { bg: 'bg-blue-100',   text: 'text-blue-700',   border: 'border-blue-300' },
  PROPOSAL:    { bg: 'bg-purple-100', text: 'text-purple-700', border: 'border-purple-300' },
  NEGOTIATION: { bg: 'bg-orange-100', text: 'text-orange-700', border: 'border-orange-300' },
  WON:         { bg: 'bg-green-100',  text: 'text-green-700',  border: 'border-green-300' },
  LOST:        { bg: 'bg-red-100',    text: 'text-red-700',    border: 'border-red-300' },
  COLD:        { bg: 'bg-slate-100',  text: 'text-slate-600',  border: 'border-slate-300' },
}

export const SOURCE_LABELS: Record<ContactSource, string> = {
  MANUAL:   'Manual',
  REFERRAL: 'Referência',
  WEBSITE:  'Website',
  EVENT:    'Evento',
  LINKEDIN: 'LinkedIn',
}

export const ACTIVITY_LABELS: Record<ActivityType, string> = {
  CALL:           'Chamada',
  EMAIL:          'Email',
  MEETING:        'Reunião',
  NOTE:           'Nota',
  TASK:           'Tarefa',
  DEMO:           'Demo',
  PROPOSAL_SENT:  'Proposta Enviada',
  CONTRACT_SENT:  'Contrato Enviado',
}

export const ACTIVITY_ICONS: Record<ActivityType, string> = {
  CALL:           '📞',
  EMAIL:          '✉️',
  MEETING:        '🤝',
  NOTE:           '📝',
  TASK:           '✅',
  DEMO:           '🖥️',
  PROPOSAL_SENT:  '📄',
  CONTRACT_SENT:  '📋',
}

// ─── Pipeline stages ordenados ────────────────────────────────────────────────
export const PIPELINE_STAGES: ContactStage[] = [
  'LEAD', 'QUALIFIED', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST', 'COLD',
]

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Contactos / Leads
// ═══════════════════════════════════════════════════════════════════════════════
export const contactsService = {
  async getAll(filters?: { stage?: ContactStage; search?: string }): Promise<CRMContact[]> {
    const tenantId = await getTenantId()
    let query = supabase
      .from('crm_contacts')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('created_at', { ascending: false })

    if (filters?.stage) query = query.eq('stage', filters.stage)
    if (filters?.search) {
      query = query.or(
        `full_name.ilike.%${filters.search}%,company.ilike.%${filters.search}%,email.ilike.%${filters.search}%`
      )
    }

    const { data, error } = await query
    if (error) throw error
    return (data || []) as CRMContact[]
  },

  async getWithActivities(id: string): Promise<CRMContact | null> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('crm_contacts')
      .select('*, activities:crm_activities(*)')
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .single()
    if (error) return null
    return data as CRMContact
  },

  async create(input: Omit<CRMContact, 'id' | 'tenant_id' | 'created_at' | 'updated_at' | 'activities'>): Promise<CRMContact> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const { data, error } = await supabase
      .from('crm_contacts')
      .insert({ ...input, tenant_id: tenantId, owner_id: userId })
      .select().single()
    if (error) throw error
    return data as CRMContact
  },

  async update(id: string, input: Partial<CRMContact>): Promise<CRMContact> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('crm_contacts')
      .update({ ...input, updated_at: new Date().toISOString() })
      .eq('id', id).eq('tenant_id', tenantId)
      .select().single()
    if (error) throw error
    return data as CRMContact
  },

  async updateStage(id: string, stage: ContactStage): Promise<CRMContact> {
    return this.update(id, { stage })
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('crm_contacts').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },

  async getStats(): Promise<PipelineStats> {
    const contacts = await this.getAll()
    const active   = contacts.filter((c: CRMContact) => c.is_active)
    const total    = active.length
    const won      = active.filter((c: CRMContact) => c.stage === 'WON')
    const wonLost  = active.filter((c: CRMContact) => c.stage === 'WON' || c.stage === 'LOST').length

    const totalValue    = active.reduce((s: number, c: CRMContact) => s + Number(c.deal_value), 0)
    const weightedValue = active.reduce((s: number, c: CRMContact) => s + (Number(c.deal_value) * Number(c.probability) / 100), 0)
    const wonValue      = won.reduce((s: number, c: CRMContact) => s + Number(c.deal_value), 0)

    return {
      total,
      leads:          active.filter((c: CRMContact) => c.stage === 'LEAD').length,
      qualified:      active.filter((c: CRMContact) => c.stage === 'QUALIFIED').length,
      proposal:       active.filter((c: CRMContact) => c.stage === 'PROPOSAL').length,
      negotiation:    active.filter((c: CRMContact) => c.stage === 'NEGOTIATION').length,
      won:            won.length,
      lost:           active.filter((c: CRMContact) => c.stage === 'LOST').length,
      cold:           active.filter((c: CRMContact) => c.stage === 'COLD').length,
      totalValue,
      weightedValue,
      wonValue,
      conversionRate: wonLost > 0 ? (won.length / wonLost) * 100 : 0,
      avgDealValue:   total > 0 ? totalValue / total : 0,
    }
  },
}

// ═══════════════════════════════════════════════════════════════════════════════
// SERVIÇO: Actividades
// ═══════════════════════════════════════════════════════════════════════════════
export const activitiesService = {
  async getByContact(contactId: string): Promise<CRMActivity[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('crm_activities')
      .select('*')
      .eq('contact_id', contactId)
      .eq('tenant_id', tenantId)
      .order('activity_date', { ascending: false })
    if (error) throw error
    return (data || []) as CRMActivity[]
  },

  async getRecent(limit = 10): Promise<CRMActivity[]> {
    const tenantId = await getTenantId()
    const { data, error } = await supabase
      .from('crm_activities')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('activity_date', { ascending: false })
      .limit(limit)
    if (error) throw error
    return (data || []) as CRMActivity[]
  },

  async create(input: Omit<CRMActivity, 'id' | 'tenant_id' | 'created_at'>): Promise<CRMActivity> {
    const tenantId = await getTenantId()
    const userId   = await getUserId()
    const { data, error } = await supabase
      .from('crm_activities')
      .insert({ ...input, tenant_id: tenantId, owner_id: userId })
      .select().single()
    if (error) throw error
    // Actualizar last_contact no contacto
    await supabase.from('crm_contacts').update({
      last_contact: new Date().toISOString().split('T')[0],
      updated_at: new Date().toISOString(),
    }).eq('id', input.contact_id).eq('tenant_id', tenantId)
    return data as CRMActivity
  },

  async delete(id: string): Promise<void> {
    const tenantId = await getTenantId()
    const { error } = await supabase
      .from('crm_activities').delete().eq('id', id).eq('tenant_id', tenantId)
    if (error) throw error
  },
}
