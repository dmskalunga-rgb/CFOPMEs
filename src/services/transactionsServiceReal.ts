// Real Supabase Service for Transactions
import { supabase } from '@/integrations/supabase/client';

export interface Transaction {
  id: string;
  type: 'income' | 'expense';
  category: string;
  description: string;
  amount: number;
  transaction_date: string;
  payment_method: string;
  reference?: string;
  status: 'pending' | 'completed' | 'cancelled';
  notes?: string;
  created_at: string;
  updated_at: string;
}

export const transactionsService = {
  // Get all transactions
  async getAll(): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  // Get transaction by ID
  async getById(id: string): Promise<Transaction | null> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .eq('id', id)
      .single();

    if (error) throw error;
    return data;
  },

  // Create transaction
  async create(transaction: Omit<Transaction, 'id' | 'created_at' | 'updated_at'>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .insert(transaction)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Update transaction
  async update(id: string, updates: Partial<Transaction>): Promise<Transaction> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  // Delete transaction
  async delete(id: string): Promise<void> {
    const { error } = await supabase
      .from('transactions_2026_04_09')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  // Get transactions by date range
  async getByDateRange(startDate: string, endDate: string): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .gte('transaction_date', startDate)
      .lte('transaction_date', endDate)
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  // Get transactions by type
  async getByType(type: 'income' | 'expense'): Promise<Transaction[]> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('*')
      .eq('type', type)
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data || [];
  },

  // Get summary statistics
  async getSummary(): Promise<{
    totalIncome: number;
    totalExpense: number;
    balance: number;
    transactionCount: number;
  }> {
    const { data, error } = await supabase
      .from('transactions_2026_04_09')
      .select('type, amount, status');

    if (error) throw error;

    const completed = (data || []).filter(t => t.status === 'completed');
    const totalIncome = completed
      .filter(t => t.type === 'income')
      .reduce((sum, t) => sum + t.amount, 0);
    const totalExpense = completed
      .filter(t => t.type === 'expense')
      .reduce((sum, t) => sum + t.amount, 0);

    return {
      totalIncome,
      totalExpense,
      balance: totalIncome - totalExpense,
      transactionCount: completed.length,
    };
  },
};
