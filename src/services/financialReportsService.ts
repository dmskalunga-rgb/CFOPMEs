import { supabase } from '@/integrations/supabase/client';

export interface IncomeStatementData {
  period: { start: Date; end: Date };
  revenue: {
    total: number;
    byCategory: { category: string; amount: number }[];
  };
  expenses: {
    total: number;
    byCategory: { category: string; amount: number }[];
  };
  grossProfit: number;
  netProfit: number;
  profitMargin: number;
}

export interface CashFlowStatementData {
  period: { start: Date; end: Date };
  openingBalance: number;
  closingBalance: number;
  operatingActivities: {
    receipts: number;
    payments: number;
    net: number;
  };
  investingActivities: {
    receipts: number;
    payments: number;
    net: number;
  };
  financingActivities: {
    receipts: number;
    payments: number;
    net: number;
  };
  netCashFlow: number;
}

export interface BalanceSheetData {
  date: Date;
  assets: {
    current: { name: string; amount: number }[];
    nonCurrent: { name: string; amount: number }[];
    total: number;
  };
  liabilities: {
    current: { name: string; amount: number }[];
    nonCurrent: { name: string; amount: number }[];
    total: number;
  };
  equity: {
    items: { name: string; amount: number }[];
    total: number;
  };
}

export interface CostAnalysisData {
  period: { start: Date; end: Date };
  byCostCenter: { costCenter: string; amount: number; percentage: number }[];
  byCategory: { category: string; amount: number; percentage: number }[];
  byMonth: { month: string; amount: number }[];
  total: number;
}

class FinancialReportsService {
  // DRE (Demonstração de Resultados)
  async generateIncomeStatement(
    companyId: string,
    startDate: Date,
    endDate: Date
  ): Promise<IncomeStatementData> {
    try {
      // Buscar transações do período
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('company_id', companyId)
        .gte('date', startDate.toISOString())
        .lte('date', endDate.toISOString());

      if (error) throw error;

      // Calcular receitas
      const revenueTransactions = transactions?.filter(t => t.type === 'income') || [];
      const totalRevenue = revenueTransactions.reduce((sum, t) => sum + t.amount, 0);
      
      const revenueByCategory = this.groupByCategory(revenueTransactions);

      // Calcular despesas
      const expenseTransactions = transactions?.filter(t => t.type === 'expense') || [];
      const totalExpenses = expenseTransactions.reduce((sum, t) => sum + t.amount, 0);
      
      const expensesByCategory = this.groupByCategory(expenseTransactions);

      // Calcular lucros
      const grossProfit = totalRevenue - totalExpenses;
      const netProfit = grossProfit; // Simplificado
      const profitMargin = totalRevenue > 0 ? (netProfit / totalRevenue) * 100 : 0;

      return {
        period: { start: startDate, end: endDate },
        revenue: {
          total: totalRevenue,
          byCategory: revenueByCategory,
        },
        expenses: {
          total: totalExpenses,
          byCategory: expensesByCategory,
        },
        grossProfit,
        netProfit,
        profitMargin,
      };
    } catch (error) {
      console.error('Erro ao gerar DRE:', error);
      throw error;
    }
  }

  // Fluxo de Caixa
  async generateCashFlowStatement(
    companyId: string,
    startDate: Date,
    endDate: Date
  ): Promise<CashFlowStatementData> {
    try {
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('company_id', companyId)
        .gte('date', startDate.toISOString())
        .lte('date', endDate.toISOString());

      if (error) throw error;

      // Saldo inicial (simplificado - buscar do período anterior)
      const openingBalance = 15000000; // Mock

      // Atividades operacionais
      const operatingReceipts = transactions?.filter(t => t.type === 'income').reduce((sum, t) => sum + t.amount, 0) || 0;
      const operatingPayments = transactions?.filter(t => t.type === 'expense').reduce((sum, t) => sum + t.amount, 0) || 0;
      const operatingNet = operatingReceipts - operatingPayments;

      // Atividades de investimento (mock)
      const investingReceipts = 0;
      const investingPayments = 0;
      const investingNet = 0;

      // Atividades de financiamento (mock)
      const financingReceipts = 0;
      const financingPayments = 0;
      const financingNet = 0;

      const netCashFlow = operatingNet + investingNet + financingNet;
      const closingBalance = openingBalance + netCashFlow;

      return {
        period: { start: startDate, end: endDate },
        openingBalance,
        closingBalance,
        operatingActivities: {
          receipts: operatingReceipts,
          payments: operatingPayments,
          net: operatingNet,
        },
        investingActivities: {
          receipts: investingReceipts,
          payments: investingPayments,
          net: investingNet,
        },
        financingActivities: {
          receipts: financingReceipts,
          payments: financingPayments,
          net: financingNet,
        },
        netCashFlow,
      };
    } catch (error) {
      console.error('Erro ao gerar fluxo de caixa:', error);
      throw error;
    }
  }

  // Balanço Patrimonial
  async generateBalanceSheet(
    companyId: string,
    date: Date
  ): Promise<BalanceSheetData> {
    try {
      // Mock data - em produção, buscar dados reais
      const currentAssets = [
        { name: 'Caixa e Equivalentes', amount: 15000000 },
        { name: 'Contas a Receber', amount: 8000000 },
        { name: 'Estoque', amount: 5000000 },
      ];

      const nonCurrentAssets = [
        { name: 'Imobilizado', amount: 20000000 },
        { name: 'Intangível', amount: 3000000 },
      ];

      const currentLiabilities = [
        { name: 'Contas a Pagar', amount: 6000000 },
        { name: 'Empréstimos de Curto Prazo', amount: 4000000 },
      ];

      const nonCurrentLiabilities = [
        { name: 'Empréstimos de Longo Prazo', amount: 15000000 },
      ];

      const totalAssets = [...currentAssets, ...nonCurrentAssets].reduce((sum, item) => sum + item.amount, 0);
      const totalLiabilities = [...currentLiabilities, ...nonCurrentLiabilities].reduce((sum, item) => sum + item.amount, 0);
      const totalEquity = totalAssets - totalLiabilities;

      const equityItems = [
        { name: 'Capital Social', amount: 20000000 },
        { name: 'Lucros Acumulados', amount: totalEquity - 20000000 },
      ];

      return {
        date,
        assets: {
          current: currentAssets,
          nonCurrent: nonCurrentAssets,
          total: totalAssets,
        },
        liabilities: {
          current: currentLiabilities,
          nonCurrent: nonCurrentLiabilities,
          total: totalLiabilities,
        },
        equity: {
          items: equityItems,
          total: totalEquity,
        },
      };
    } catch (error) {
      console.error('Erro ao gerar balanço:', error);
      throw error;
    }
  }

  // Análise de Custos
  async generateCostAnalysis(
    companyId: string,
    startDate: Date,
    endDate: Date
  ): Promise<CostAnalysisData> {
    try {
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('company_id', companyId)
        .eq('type', 'expense')
        .gte('date', startDate.toISOString())
        .lte('date', endDate.toISOString());

      if (error) throw error;

      const total = transactions?.reduce((sum, t) => sum + t.amount, 0) || 0;

      // Por categoria
      const byCategory = this.groupByCategory(transactions || []).map(item => ({
        ...item,
        percentage: total > 0 ? (item.amount / total) * 100 : 0,
      }));

      // Por centro de custo (mock)
      const byCostCenter = [
        { costCenter: 'Administrativo', amount: total * 0.3, percentage: 30 },
        { costCenter: 'Comercial', amount: total * 0.4, percentage: 40 },
        { costCenter: 'Operacional', amount: total * 0.3, percentage: 30 },
      ];

      // Por mês
      const byMonth = this.groupByMonth(transactions || []);

      return {
        period: { start: startDate, end: endDate },
        byCostCenter,
        byCategory,
        byMonth,
        total,
      };
    } catch (error) {
      console.error('Erro ao gerar análise de custos:', error);
      throw error;
    }
  }

  // Helpers
  private groupByCategory(transactions: any[]): { category: string; amount: number }[] {
    const grouped: Record<string, number> = {};
    
    transactions.forEach(t => {
      const category = t.category || 'Sem Categoria';
      grouped[category] = (grouped[category] || 0) + t.amount;
    });

    return Object.entries(grouped)
      .map(([category, amount]) => ({ category, amount }))
      .sort((a, b) => b.amount - a.amount);
  }

  private groupByMonth(transactions: any[]): { month: string; amount: number }[] {
    const grouped: Record<string, number> = {};
    
    transactions.forEach(t => {
      const date = new Date(t.date);
      const month = date.toLocaleDateString('pt-BR', { year: 'numeric', month: 'short' });
      grouped[month] = (grouped[month] || 0) + t.amount;
    });

    return Object.entries(grouped)
      .map(([month, amount]) => ({ month, amount }))
      .sort((a, b) => a.month.localeCompare(b.month));
  }
}

export const financialReportsService = new FinancialReportsService();
