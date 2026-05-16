// =====================================================
// KWANZACONTROL - Transaction Service
// Serviços para gestão de transações financeiras
// Data: 2026-04-04
// =====================================================

import { supabase } from '@/integrations/supabase/client';
import { Database } from '@/lib/supabase-types';

type Transaction = Database['public']['Tables']['transactions']['Row'];
type TransactionInsert = Database['public']['Tables']['transactions']['Insert'];
type TransactionCategory = Database['public']['Tables']['transaction_categories']['Row'];

export interface TransactionWithCategory extends Transaction {
  category: TransactionCategory | null;
}

export const transactionService = {
  /**
   * Buscar todas as transações do tenant
   */
  async getAll(tenantId: string) {
    const { data, error } = await supabase
      .from('transactions')
      .select(`
        *,
        category:transaction_categories (*)
      `)
      .eq('tenant_id', tenantId)
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data as TransactionWithCategory[];
  },

  /**
   * Criar nova transação
   */
  async create(transaction: TransactionInsert) {
    const { data, error } = await supabase
      .from('transactions')
      .insert(transaction)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Atualizar transação
   */
  async update(id: string, updates: Partial<TransactionInsert>) {
    const { data, error } = await supabase
      .from('transactions')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) throw error;
    return data;
  },

  /**
   * Deletar transação
   */
  async delete(id: string) {
    const { error } = await supabase
      .from('transactions')
      .delete()
      .eq('id', id);

    if (error) throw error;
  },

  /**
   * Classificar transação com IA
   */
  async classifyWithAI(transactionId: string, description: string, amount: number, type: string, tenantId: string) {
    const { data, error } = await supabase.functions.invoke(
      'ai_classify_transaction_2026_04_04',
      {
        body: {
          transaction_id: transactionId,
          description,
          amount,
          type,
          tenant_id: tenantId,
        },
      }
    );

    if (error) throw error;
    return data;
  },

  /**
   * Buscar transações por tipo
   */
  async getByType(tenantId: string, type: 'INCOME' | 'EXPENSE') {
    const { data, error } = await supabase
      .from('transactions')
      .select(`
        *,
        category:transaction_categories (*)
      `)
      .eq('tenant_id', tenantId)
      .eq('type', type)
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data as TransactionWithCategory[];
  },

  /**
   * Buscar transações por período
   */
  async getByPeriod(tenantId: string, startDate: string, endDate: string) {
    const { data, error } = await supabase
      .from('transactions')
      .select(`
        *,
        category:transaction_categories (*)
      `)
      .eq('tenant_id', tenantId)
      .gte('transaction_date', startDate)
      .lte('transaction_date', endDate)
      .order('transaction_date', { ascending: false });

    if (error) throw error;
    return data as TransactionWithCategory[];
  },

  /**
   * Buscar categorias
   */
  async getCategories(tenantId: string, type?: 'INCOME' | 'EXPENSE') {
    let query = supabase
      .from('transaction_categories')
      .select('*')
      .or(`tenant_id.eq.${tenantId},tenant_id.is.null`)
      .eq('is_active', true);

    if (type) {
      query = query.eq('type', type);
    }

    const { data, error } = await query.order('name');

    if (error) throw error;
    return data;
  },

  /**
   * Buscar estatísticas de transações
   */
  async getStats(tenantId: string, startDate?: string, endDate?: string) {
    let query = supabase
      .from('transactions')
      .select('type, amount, transaction_date')
      .eq('tenant_id', tenantId);

    if (startDate) {
      query = query.gte('transaction_date', startDate);
    }
    if (endDate) {
      query = query.lte('transaction_date', endDate);
    }

    const { data, error } = await query;

    if (error) throw error;

    const stats = {
      totalIncome: data.filter(t => t.type === 'INCOME').reduce((sum, t) => sum + Number(t.amount), 0),
      totalExpense: data.filter(t => t.type === 'EXPENSE').reduce((sum, t) => sum + Number(t.amount), 0),
      netCashFlow: 0,
      transactionCount: data.length,
    };

    stats.netCashFlow = stats.totalIncome - stats.totalExpense;

    return stats;
  },

  /**
   * Prever fluxo de caixa
   */
  async predictCashflow(tenantId: string, monthsAhead: number = 3) {
    const { data, error } = await supabase.functions.invoke(
      'ai_predict_cashflow_2026_04_04',
      {
        body: {
          tenant_id: tenantId,
          months_ahead: monthsAhead,
          include_seasonality: true,
        },
      }
    );

    if (error) throw error;
    return data;
  },
};
