// =====================================================
// KWANZACONTROL - Finance Service Real
// Usa estrutura real do Supabase: tenant_id, transaction_date, type INCOME/EXPENSE
// =====================================================
import { supabase } from '@/integrations/supabase/client'

export interface Transaction {
  id: string
  tenant_id: string
  transaction_number?: string
  type: 'INCOME' | 'EXPENSE'
  category_id?: string
  category_name?: string
  amount: number
  currency: string
  transaction_date: string
  description: string
  reference?: string
  payment_method?: string
  account?: string
  invoice_id?: string
  notes?: string
  tags?: string[]
  is_reconciled: boolean
  created_by?: string
  created_at: string
  updated_at: string
}

export interface TransactionCategory {
  id: string
  tenant_id?: string
  name: string
  type: 'INCOME' | 'EXPENSE'
  color?: string
  icon?: string
  is_system: boolean
  is_active: boolean
  created_at: string
}

export interface TransactionFilters {
  type?: 'INCOME' | 'EXPENSE'
  categoryId?: string
  searchTerm?: string
  startDate?: string
  endDate?: string
  paymentMethod?: string
  isReconciled?: boolean
}

export interface FinancialSummary {
  totalIncome: number
  totalExpense: number
  balance: number
  transactionCount: number
  pendingCount: number
}

// Resolver tenant_id do utilizador autenticado
async function getUserTenantId(): Promise<string | null> {
  const { data: { user } } = await supabase.auth.getUser()
  if (!user) return null

  // Tentar obter do perfil
  const { data: profile } = await supabase
    .from('users')
    .select('tenant_id')
    .eq('id', user.id)
    .maybeSingle()

  if (profile?.tenant_id) return profile.tenant_id

  // Fallback: primeiro tenant disponível
  const { data: tenant } = await supabase
    .from('tenants')
    .select('id')
    .limit(1)
    .maybeSingle()

  return tenant?.id || null
}

class FinanceServiceReal {
  // ==================== TRANSACTIONS ====================

  async getTransactions(filters?: TransactionFilters): Promise<Transaction[]> {
    const tenantId = await getUserTenantId()
    if (!tenantId) return []

    let query = supabase
      .from('transactions')
      .select('*')
      .eq('tenant_id', tenantId)
      .order('transaction_date', { ascending: false })
      .order('created_at', { ascending: false })

    if (filters?.type) {
      query = query.eq('type', filters.type)
    }
    if (filters?.categoryId && filters.categoryId !== 'all') {
      query = query.eq('category_id', filters.categoryId)
    }
    if (filters?.searchTerm) {
      query = query.ilike('description', `%${filters.searchTerm}%`)
    }
    if (filters?.startDate) {
      query = query.gte('transaction_date', filters.startDate)
    }
    if (filters?.endDate) {
      query = query.lte('transaction_date', filters.endDate)
    }
    if (filters?.paymentMethod) {
      query = query.eq('payment_method', filters.paymentMethod)
    }
    if (filters?.isReconciled !== undefined) {
      query = query.eq('is_reconciled', filters.isReconciled)
    }

    const { data, error } = await query
    if (error) {
      console.error('Erro ao carregar transações:', error)
      return []
    }
    return data || []
  }

  async createTransaction(
    transaction: Omit<Transaction, 'id' | 'tenant_id' | 'is_reconciled' | 'currency' | 'created_at' | 'updated_at'>
  ): Promise<Transaction | null> {
    const tenantId = await getUserTenantId()
    const { data: { user } } = await supabase.auth.getUser()
    if (!tenantId || !user) throw new Error('Não autenticado')

    const { data, error } = await supabase
      .from('transactions')
      .insert({
        ...transaction,
        tenant_id: tenantId,
        currency: 'AOA',
        is_reconciled: false,
        created_by: user.id,
      })
      .select()
      .single()

    if (error) throw error
    return data
  }

  async updateTransaction(
    id: string,
    updates: Partial<Omit<Transaction, 'id' | 'tenant_id' | 'created_at'>>
  ): Promise<Transaction | null> {
    const tenantId = await getUserTenantId()
    if (!tenantId) throw new Error('Não autenticado')

    const { data, error } = await supabase
      .from('transactions')
      .update({ ...updates, updated_at: new Date().toISOString() })
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .select()
      .single()

    if (error) throw error
    return data
  }

  async deleteTransaction(id: string): Promise<void> {
    const tenantId = await getUserTenantId()
    if (!tenantId) throw new Error('Não autenticado')

    const { error } = await supabase
      .from('transactions')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)

    if (error) throw error
  }

  // ==================== CATEGORIES ====================

  async getCategories(type?: 'INCOME' | 'EXPENSE'): Promise<TransactionCategory[]> {
    const tenantId = await getUserTenantId()

    let query = supabase
      .from('transaction_categories')
      .select('*')
      .eq('is_active', true)
      .order('name', { ascending: true })

    if (tenantId) {
      // Categorias do sistema OU deste tenant
      query = query.or(`is_system.eq.true,tenant_id.eq.${tenantId}`)
    } else {
      query = query.eq('is_system', true)
    }

    if (type) {
      query = query.eq('type', type)
    }

    const { data, error } = await query
    if (error) {
      console.error('Erro ao carregar categorias:', error)
      return []
    }
    return data || []
  }

  async createCategory(
    category: Omit<TransactionCategory, 'id' | 'tenant_id' | 'is_system' | 'created_at'>
  ): Promise<TransactionCategory | null> {
    const tenantId = await getUserTenantId()
    if (!tenantId) throw new Error('Não autenticado')

    const { data, error } = await supabase
      .from('transaction_categories')
      .insert({
        ...category,
        tenant_id: tenantId,
        is_system: false,
      })
      .select()
      .single()

    if (error) throw error
    return data
  }

  async deleteCategory(id: string): Promise<void> {
    const tenantId = await getUserTenantId()
    if (!tenantId) throw new Error('Não autenticado')

    const { error } = await supabase
      .from('transaction_categories')
      .delete()
      .eq('id', id)
      .eq('tenant_id', tenantId)
      .eq('is_system', false)

    if (error) throw error
  }

  // ==================== SUMMARY ====================

  async getFinancialSummary(filters?: TransactionFilters): Promise<FinancialSummary> {
    const transactions = await this.getTransactions(filters)

    const totalIncome = transactions
      .filter(t => t.type === 'INCOME')
      .reduce((sum, t) => sum + Number(t.amount), 0)

    const totalExpense = transactions
      .filter(t => t.type === 'EXPENSE')
      .reduce((sum, t) => sum + Number(t.amount), 0)

    return {
      totalIncome,
      totalExpense,
      balance: totalIncome - totalExpense,
      transactionCount: transactions.length,
      pendingCount: transactions.filter(t => !t.is_reconciled).length,
    }
  }

  // ==================== MONTHLY CASHFLOW ====================

  async getMonthlyCashflow(months: number = 6): Promise<Array<{
    month: string
    monthLabel: string
    receitas: number
    despesas: number
    saldo: number
  }>> {
    const tenantId = await getUserTenantId()
    if (!tenantId) return []

    const startDate = new Date()
    startDate.setMonth(startDate.getMonth() - months + 1)
    startDate.setDate(1)

    const { data, error } = await supabase
      .from('transactions')
      .select('type, amount, transaction_date')
      .eq('tenant_id', tenantId)
      .gte('transaction_date', startDate.toISOString().split('T')[0])
      .order('transaction_date', { ascending: true })

    if (error || !data) return []

    const monthMap: Record<string, { receitas: number; despesas: number }> = {}

    data.forEach(t => {
      const d = new Date(t.transaction_date)
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      if (!monthMap[key]) monthMap[key] = { receitas: 0, despesas: 0 }
      if (t.type === 'INCOME') {
        monthMap[key].receitas += Number(t.amount)
      } else {
        monthMap[key].despesas += Number(t.amount)
      }
    })

    return Object.entries(monthMap)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, val]) => ({
        month: key,
        monthLabel: new Date(key + '-01').toLocaleDateString('pt-AO', { month: 'short', year: '2-digit' }),
        receitas: val.receitas,
        despesas: val.despesas,
        saldo: val.receitas - val.despesas,
      }))
  }

  // ==================== EXPORT CSV ====================

  exportToCSV(transactions: Transaction[], categories: TransactionCategory[]): void {
    const catMap = new Map(categories.map(c => [c.id, c.name]))

    const headers = ['Nº', 'Data', 'Tipo', 'Categoria', 'Descrição', 'Método', 'Valor (AOA)', 'Reconciliado']
    const rows = transactions.map((t, i) => [
      i + 1,
      new Date(t.transaction_date).toLocaleDateString('pt-AO'),
      t.type === 'INCOME' ? 'Receita' : 'Despesa',
      t.category_name || catMap.get(t.category_id || '') || 'Sem categoria',
      `"${t.description}"`,
      t.payment_method || '-',
      Number(t.amount).toFixed(2),
      t.is_reconciled ? 'Sim' : 'Não',
    ])

    const csv = [headers.join(';'), ...rows.map(r => r.join(';'))].join('\n')
    const BOM = '\uFEFF'
    const blob = new Blob([BOM + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `transacoes_${new Date().toISOString().split('T')[0]}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }
}

export const financeService = new FinanceServiceReal()
