// Finance Service - Serviço completo de finanças com Supabase
import { supabase } from '@/integrations/supabase/client';

export interface Transaction {
  id: string;
  user_id: string;
  type: 'income' | 'expense';
  category: string;
  description: string;
  amount: number;
  date: string;
  payment_method: string;
  account: string;
  reference?: string;
  status: 'completed' | 'pending' | 'cancelled';
  created_at: string;
  updated_at: string;
}

export interface FinancialSummary {
  total_income: number;
  total_expense: number;
  balance: number;
  income_growth: number;
  expense_growth: number;
  transactions_count: number;
}

export interface Budget {
  id: string;
  user_id: string;
  category: string;
  amount: number;
  period: 'monthly' | 'quarterly' | 'yearly';
  spent: number;
  remaining: number;
  start_date: string;
  end_date: string;
}

export interface CashFlowProjection {
  id: string;
  user_id: string;
  month: string;
  projected_income: number;
  projected_expense: number;
  actual_income: number;
  actual_expense: number;
  variance: number;
}

class FinanceService {
  // ==================== TRANSACTIONS ====================
  
  async getTransactions(userId: string, limit = 50, offset = 0): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions')
      .select('*')
      .eq('user_id', userId)
      .order('date', { ascending: false })
      .range(offset, offset + limit - 1);

    if (error) throw error;
    return data || [];
  }

  async getTransaction(id: string): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions')
      .select('*')
      .eq('id', id)
      .single();

    if (error) throw error;
    return data;
  }

  async createTransaction(transaction: Omit<Transaction, 'id' | 'created_at' | 'updated_at'>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions')
      .insert(transaction)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  async updateTransaction(id: string, updates: Partial<Transaction>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  async deleteTransaction(id: string): Promise<void> {
    const { error } = await supabase
      .from('transactions')
      .delete()
      .eq('id', id);

    if (error) throw error;
  }

  async getFinancialSummary(userId: string, startDate?: string, endDate?: string): Promise<FinancialSummary> {
    let query = supabase
      .from('transactions')
      .select('type, amount')
      .eq('user_id', userId)
      .eq('status', 'completed');

    if (startDate) query = query.gte('date', startDate);
    if (endDate) query = query.lte('date', endDate);

    const { data, error } = await query;
    if (error) throw error;

    const income = data?.filter(t => t.type === 'income').reduce((sum, t) => sum + t.amount, 0) || 0;
    const expense = data?.filter(t => t.type === 'expense').reduce((sum, t) => sum + t.amount, 0) || 0;

    return {
      total_income: income,
      total_expense: expense,
      balance: income - expense,
      income_growth: 0, // Calcular comparando com período anterior
      expense_growth: 0,
      transactions_count: data?.length || 0
    };
  }

  // ==================== BUDGETS ====================

  async getBudgets(userId: string): Promise<Budget[]> {
    const { data, error } = await supabase
      .from('budgets')
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false });

    if (error) throw error;
    return data || [];
  }

  async createBudget(budget: Omit<Budget, 'id' | 'spent' | 'remaining'>): Promise<Budget> {
    const budgetData = {
      ...budget,
      spent: 0,
      remaining: budget.amount
    };

    const { data, error } = await supabase
      .from('budgets')
      .insert(budgetData)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  async updateBudget(id: string, updates: Partial<Budget>): Promise<Budget> {
    const { data, error } = await supabase
      .from('budgets')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  // ==================== CASH FLOW ====================

  async getCashFlowProjections(userId: string, months = 6): Promise<CashFlowProjection[]> {
    const { data, error } = await supabase
      .from('cash_flow_projections')
      .select('*')
      .eq('user_id', userId)
      .order('month', { ascending: true })
      .limit(months);

    if (error) throw error;
    return data || [];
  }

  // ==================== EXPORT ====================

  async exportTransactions(userId: string, format: 'csv' | 'excel' | 'pdf', filters?: any): Promise<Blob> {
    // Buscar transações
    const transactions = await this.getTransactions(userId, 1000, 0);

    if (format === 'csv') {
      return this.exportToCSV(transactions);
    } else if (format === 'excel') {
      return this.exportToExcel(transactions);
    } else {
      return this.exportToPDF(transactions);
    }
  }

  private exportToCSV(transactions: Transaction[]): Blob {
    const headers = ['Data', 'Tipo', 'Categoria', 'Descrição', 'Valor', 'Método', 'Status'];
    const rows = transactions.map(t => [
      new Date(t.date).toLocaleDateString('pt-AO'),
      t.type === 'income' ? 'Receita' : 'Despesa',
      t.category,
      t.description,
      t.amount.toFixed(2),
      t.payment_method,
      t.status
    ]);

    const csv = [
      headers.join(','),
      ...rows.map(row => row.join(','))
    ].join('\n');

    return new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  }

  private exportToExcel(transactions: Transaction[]): Blob {
    // Implementação simplificada - em produção usar biblioteca como xlsx
    return this.exportToCSV(transactions);
  }

  private exportToPDF(transactions: Transaction[]): Blob {
    // Implementação simplificada - em produção usar biblioteca como jspdf
    const content = transactions.map(t => 
      `${new Date(t.date).toLocaleDateString('pt-AO')} - ${t.description}: ${t.amount}`
    ).join('\n');

    return new Blob([content], { type: 'application/pdf' });
  }

  // ==================== FILTERS & SEARCH ====================

  async searchTransactions(userId: string, searchTerm: string): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions')
      .select('*')
      .eq('user_id', userId)
      .or(`description.ilike.%${searchTerm}%,category.ilike.%${searchTerm}%`)
      .order('date', { ascending: false });

    if (error) throw error;
    return data || [];
  }

  async filterTransactions(
    userId: string,
    filters: {
      type?: 'income' | 'expense';
      category?: string;
      startDate?: string;
      endDate?: string;
      minAmount?: number;
      maxAmount?: number;
    }
  ): Promise<Transaction[]> {
    let query = supabase
      .from('transactions')
      .select('*')
      .eq('user_id', userId);

    if (filters.type) query = query.eq('type', filters.type);
    if (filters.category) query = query.eq('category', filters.category);
    if (filters.startDate) query = query.gte('date', filters.startDate);
    if (filters.endDate) query = query.lte('date', filters.endDate);
    if (filters.minAmount) query = query.gte('amount', filters.minAmount);
    if (filters.maxAmount) query = query.lte('amount', filters.maxAmount);

    query = query.order('date', { ascending: false });

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  }

  // ==================== CATEGORIES ====================

  async getCategories(userId: string): Promise<string[]> {
    const { data, error } = await supabase
      .from('transactions')
      .select('category')
      .eq('user_id', userId);

    if (error) throw error;
    
    const categories = [...new Set(data?.map(t => t.category) || [])];
    return categories.sort();
  }

  async getCategoryExpenses(userId: string): Promise<{ category: string; amount: number }[]> {
    const { data, error } = await supabase
      .from('transactions')
      .select('category, amount')
      .eq('user_id', userId)
      .eq('type', 'expense')
      .eq('status', 'completed');

    if (error) throw error;

    const categoryMap = new Map<string, number>();
    data?.forEach(t => {
      const current = categoryMap.get(t.category) || 0;
      categoryMap.set(t.category, current + t.amount);
    });

    return Array.from(categoryMap.entries()).map(([category, amount]) => ({
      category,
      amount
    })).sort((a, b) => b.amount - a.amount);
  }

  // ==================== FORMATTERS ====================

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format(value);
  }

  formatDate(date: string): string {
    return new Date(date).toLocaleDateString('pt-AO', {
      day: '2-digit',
      month: 'short',
      year: 'numeric'
    });
  }
}

export const financeService = new FinanceService();
