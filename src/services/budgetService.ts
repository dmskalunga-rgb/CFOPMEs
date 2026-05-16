/**
 * Budget Service
 * Serviço de gestão de orçamentos e planejamento financeiro
 */

import { supabase } from '@/integrations/supabase/client';

export interface Budget {
  id: string;
  companyId: string;
  name: string;
  period: 'monthly' | 'quarterly' | 'yearly';
  startDate: Date;
  endDate: Date;
  totalBudget: number;
  categories: BudgetCategory[];
  status: 'draft' | 'active' | 'completed' | 'exceeded';
  createdAt: Date;
  updatedAt: Date;
}

export interface BudgetCategory {
  category: string;
  allocatedAmount: number;
  spentAmount: number;
  remainingAmount: number;
  percentage: number;
  status: 'on_track' | 'warning' | 'exceeded';
}

export interface BudgetAlert {
  id: string;
  budgetId: string;
  category: string;
  type: 'approaching_limit' | 'exceeded' | 'milestone';
  severity: 'info' | 'warning' | 'critical';
  message: string;
  percentage: number;
  date: Date;
}

class BudgetService {
  /**
   * Criar novo orçamento
   */
  async createBudget(budget: Omit<Budget, 'id' | 'createdAt' | 'updatedAt'>): Promise<Budget> {
    try {
      const { data, error } = await supabase
        .from('budgets')
        .insert({
          company_id: budget.companyId,
          name: budget.name,
          period: budget.period,
          start_date: budget.startDate,
          end_date: budget.endDate,
          total_budget: budget.totalBudget,
          categories: budget.categories,
          status: budget.status,
        })
        .select()
        .single();

      if (error) throw error;

      return this.mapBudget(data);
    } catch (error) {
      console.error('Erro ao criar orçamento:', error);
      throw error;
    }
  }

  /**
   * Listar orçamentos
   */
  async listBudgets(companyId: string): Promise<Budget[]> {
    try {
      const { data, error } = await supabase
        .from('budgets')
        .select('*')
        .eq('company_id', companyId)
        .order('created_at', { ascending: false });

      if (error) throw error;

      return (data || []).map(this.mapBudget);
    } catch (error) {
      console.error('Erro ao listar orçamentos:', error);
      return this.getMockBudgets();
    }
  }

  /**
   * Obter orçamento por ID
   */
  async getBudget(budgetId: string): Promise<Budget | null> {
    try {
      const { data, error } = await supabase
        .from('budgets')
        .select('*')
        .eq('id', budgetId)
        .single();

      if (error) throw error;

      return this.mapBudget(data);
    } catch (error) {
      console.error('Erro ao obter orçamento:', error);
      return null;
    }
  }

  /**
   * Atualizar orçamento
   */
  async updateBudget(budgetId: string, updates: Partial<Budget>): Promise<Budget> {
    try {
      const { data, error } = await supabase
        .from('budgets')
        .update({
          name: updates.name,
          total_budget: updates.totalBudget,
          categories: updates.categories,
          status: updates.status,
        })
        .eq('id', budgetId)
        .select()
        .single();

      if (error) throw error;

      return this.mapBudget(data);
    } catch (error) {
      console.error('Erro ao atualizar orçamento:', error);
      throw error;
    }
  }

  /**
   * Calcular progresso do orçamento
   */
  async calculateBudgetProgress(budgetId: string): Promise<BudgetCategory[]> {
    try {
      const budget = await this.getBudget(budgetId);
      if (!budget) throw new Error('Orçamento não encontrado');

      // Buscar transações do período
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('type', 'expense')
        .gte('date', budget.startDate.toISOString())
        .lte('date', budget.endDate.toISOString());

      if (error) throw error;

      // Calcular gastos por categoria
      const categorySpending: { [key: string]: number } = {};
      (transactions || []).forEach((txn: any) => {
        categorySpending[txn.category] = (categorySpending[txn.category] || 0) + txn.amount;
      });

      // Atualizar categorias do orçamento
      const updatedCategories: BudgetCategory[] = budget.categories.map((cat) => {
        const spentAmount = categorySpending[cat.category] || 0;
        const remainingAmount = cat.allocatedAmount - spentAmount;
        const percentage = (spentAmount / cat.allocatedAmount) * 100;

        let status: 'on_track' | 'warning' | 'exceeded' = 'on_track';
        if (percentage >= 100) status = 'exceeded';
        else if (percentage >= 80) status = 'warning';

        return {
          ...cat,
          spentAmount,
          remainingAmount,
          percentage,
          status,
        };
      });

      return updatedCategories;
    } catch (error) {
      console.error('Erro ao calcular progresso:', error);
      return [];
    }
  }

  /**
   * Obter alertas de orçamento
   */
  async getBudgetAlerts(budgetId: string): Promise<BudgetAlert[]> {
    try {
      const categories = await this.calculateBudgetProgress(budgetId);
      const alerts: BudgetAlert[] = [];

      categories.forEach((cat) => {
        if (cat.status === 'exceeded') {
          alerts.push({
            id: `alert-${budgetId}-${cat.category}`,
            budgetId,
            category: cat.category,
            type: 'exceeded',
            severity: 'critical',
            message: `Orçamento de ${cat.category} excedido em ${(cat.percentage - 100).toFixed(1)}%`,
            percentage: cat.percentage,
            date: new Date(),
          });
        } else if (cat.status === 'warning') {
          alerts.push({
            id: `alert-${budgetId}-${cat.category}`,
            budgetId,
            category: cat.category,
            type: 'approaching_limit',
            severity: 'warning',
            message: `${cat.category} atingiu ${cat.percentage.toFixed(1)}% do orçamento`,
            percentage: cat.percentage,
            date: new Date(),
          });
        }
      });

      return alerts;
    } catch (error) {
      console.error('Erro ao obter alertas:', error);
      return [];
    }
  }

  /**
   * Comparar orçamento vs realizado
   */
  async compareBudgetVsActual(budgetId: string): Promise<{
    categories: Array<{
      category: string;
      budgeted: number;
      actual: number;
      variance: number;
      variancePercentage: number;
    }>;
    totalBudgeted: number;
    totalActual: number;
    totalVariance: number;
  }> {
    try {
      const categories = await this.calculateBudgetProgress(budgetId);

      const comparison = categories.map((cat) => ({
        category: cat.category,
        budgeted: cat.allocatedAmount,
        actual: cat.spentAmount,
        variance: cat.allocatedAmount - cat.spentAmount,
        variancePercentage: ((cat.allocatedAmount - cat.spentAmount) / cat.allocatedAmount) * 100,
      }));

      const totalBudgeted = categories.reduce((sum, cat) => sum + cat.allocatedAmount, 0);
      const totalActual = categories.reduce((sum, cat) => sum + cat.spentAmount, 0);
      const totalVariance = totalBudgeted - totalActual;

      return {
        categories: comparison,
        totalBudgeted,
        totalActual,
        totalVariance,
      };
    } catch (error) {
      console.error('Erro ao comparar orçamento:', error);
      throw error;
    }
  }

  // Métodos auxiliares

  private mapBudget(data: any): Budget {
    return {
      id: data.id,
      companyId: data.company_id,
      name: data.name,
      period: data.period,
      startDate: new Date(data.start_date),
      endDate: new Date(data.end_date),
      totalBudget: data.total_budget,
      categories: data.categories || [],
      status: data.status,
      createdAt: new Date(data.created_at),
      updatedAt: new Date(data.updated_at),
    };
  }

  private getMockBudgets(): Budget[] {
    return [
      {
        id: 'budget-1',
        companyId: 'comp-001',
        name: 'Orçamento Q1 2026',
        period: 'quarterly',
        startDate: new Date('2026-01-01'),
        endDate: new Date('2026-03-31'),
        totalBudget: 1500000,
        categories: [
          {
            category: 'Fornecedores',
            allocatedAmount: 500000,
            spentAmount: 425000,
            remainingAmount: 75000,
            percentage: 85,
            status: 'warning',
          },
          {
            category: 'Salários',
            allocatedAmount: 600000,
            spentAmount: 400000,
            remainingAmount: 200000,
            percentage: 67,
            status: 'on_track',
          },
          {
            category: 'Marketing',
            allocatedAmount: 200000,
            spentAmount: 150000,
            remainingAmount: 50000,
            percentage: 75,
            status: 'on_track',
          },
          {
            category: 'Utilidades',
            allocatedAmount: 200000,
            spentAmount: 180000,
            remainingAmount: 20000,
            percentage: 90,
            status: 'warning',
          },
        ],
        status: 'active',
        createdAt: new Date('2026-01-01'),
        updatedAt: new Date(),
      },
    ];
  }
}

export const budgetService = new BudgetService();
