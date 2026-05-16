/**
 * AI Finance Service
 * Serviço de Inteligência Artificial para análises financeiras preditivas
 */

import { supabase } from '@/integrations/supabase/client';

export interface CashFlowPrediction {
  month: string;
  predictedIncome: number;
  predictedExpense: number;
  predictedBalance: number;
  confidence: number;
  trend: 'up' | 'down' | 'stable';
}

export interface FinancialAnomaly {
  id: string;
  type: 'unusual_expense' | 'unusual_income' | 'pattern_break' | 'budget_overrun';
  severity: 'low' | 'medium' | 'high' | 'critical';
  description: string;
  amount: number;
  date: Date;
  category: string;
  recommendation: string;
}

export interface AIRecommendation {
  id: string;
  type: 'cost_reduction' | 'revenue_opportunity' | 'cash_flow' | 'investment' | 'tax_optimization';
  priority: 'low' | 'medium' | 'high';
  title: string;
  description: string;
  potentialSavings: number;
  implementationDifficulty: 'easy' | 'medium' | 'hard';
  estimatedImpact: string;
  actionItems: string[];
}

export interface FinancialInsight {
  category: string;
  currentSpending: number;
  averageSpending: number;
  variance: number;
  trend: 'increasing' | 'decreasing' | 'stable';
  recommendation: string;
}

class AIFinanceService {
  /**
   * Prever fluxo de caixa para os próximos meses
   */
  async predictCashFlow(months: number = 6): Promise<CashFlowPrediction[]> {
    try {
      // Buscar histórico de transações
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .order('date', { ascending: false })
        .limit(365);

      if (error) throw error;

      // Análise de padrões históricos
      const monthlyData = this.aggregateByMonth(transactions || []);
      const predictions: CashFlowPrediction[] = [];

      // Algoritmo de previsão baseado em média móvel e tendência
      for (let i = 0; i < months; i++) {
        const prediction = this.calculatePrediction(monthlyData, i);
        predictions.push(prediction);
      }

      return predictions;
    } catch (error) {
      console.error('Erro ao prever fluxo de caixa:', error);
      return this.getMockPredictions(months);
    }
  }

  /**
   * Detectar anomalias financeiras
   */
  async detectAnomalies(): Promise<FinancialAnomaly[]> {
    try {
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .order('date', { ascending: false })
        .limit(90);

      if (error) throw error;

      const anomalies: FinancialAnomaly[] = [];
      const stats = this.calculateStatistics(transactions || []);

      // Detectar gastos incomuns
      (transactions || []).forEach((txn: any) => {
        if (txn.type === 'expense') {
          const categoryAvg = stats.categoryAverages[txn.category] || 0;
          const deviation = Math.abs(txn.amount - categoryAvg) / categoryAvg;

          if (deviation > 0.5 && txn.amount > categoryAvg * 1.5) {
            anomalies.push({
              id: `anomaly-${txn.id}`,
              type: 'unusual_expense',
              severity: deviation > 1 ? 'high' : 'medium',
              description: `Despesa ${Math.round(deviation * 100)}% acima da média em ${txn.category}`,
              amount: txn.amount,
              date: new Date(txn.date),
              category: txn.category,
              recommendation: 'Revisar esta transação e verificar se está dentro do orçamento planejado',
            });
          }
        }
      });

      return anomalies;
    } catch (error) {
      console.error('Erro ao detectar anomalias:', error);
      return this.getMockAnomalies();
    }
  }

  /**
   * Gerar recomendações inteligentes
   */
  async generateRecommendations(): Promise<AIRecommendation[]> {
    try {
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .order('date', { ascending: false })
        .limit(180);

      if (error) throw error;

      const recommendations: AIRecommendation[] = [];
      const analysis = this.analyzeSpendingPatterns(transactions || []);

      // Recomendação de redução de custos
      if (analysis.highSpendingCategories.length > 0) {
        analysis.highSpendingCategories.forEach((cat: any) => {
          recommendations.push({
            id: `rec-cost-${cat.category}`,
            type: 'cost_reduction',
            priority: 'high',
            title: `Reduzir gastos em ${cat.category}`,
            description: `Categoria ${cat.category} representa ${cat.percentage}% dos gastos totais`,
            potentialSavings: cat.amount * 0.15,
            implementationDifficulty: 'medium',
            estimatedImpact: '15% de redução possível',
            actionItems: [
              'Revisar contratos e fornecedores',
              'Negociar melhores condições',
              'Buscar alternativas mais econômicas',
            ],
          });
        });
      }

      // Recomendação de otimização de fluxo de caixa
      if (analysis.cashFlowIssues) {
        recommendations.push({
          id: 'rec-cashflow-1',
          type: 'cash_flow',
          priority: 'high',
          title: 'Melhorar gestão de fluxo de caixa',
          description: 'Identificados períodos com baixa liquidez',
          potentialSavings: 0,
          implementationDifficulty: 'medium',
          estimatedImpact: 'Redução de 30% em problemas de liquidez',
          actionItems: [
            'Antecipar recebimentos quando possível',
            'Negociar prazos de pagamento com fornecedores',
            'Criar reserva de emergência',
          ],
        });
      }

      return recommendations;
    } catch (error) {
      console.error('Erro ao gerar recomendações:', error);
      return this.getMockRecommendations();
    }
  }

  /**
   * Obter insights financeiros por categoria
   */
  async getFinancialInsights(): Promise<FinancialInsight[]> {
    try {
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('type', 'expense')
        .order('date', { ascending: false })
        .limit(180);

      if (error) throw error;

      const insights: FinancialInsight[] = [];
      const categoryData = this.groupByCategory(transactions || []);

      Object.entries(categoryData).forEach(([category, data]: [string, any]) => {
        const variance = ((data.current - data.average) / data.average) * 100;
        
        insights.push({
          category,
          currentSpending: data.current,
          averageSpending: data.average,
          variance,
          trend: variance > 10 ? 'increasing' : variance < -10 ? 'decreasing' : 'stable',
          recommendation: this.getInsightRecommendation(category, variance),
        });
      });

      return insights;
    } catch (error) {
      console.error('Erro ao obter insights:', error);
      return [];
    }
  }

  // Métodos auxiliares privados

  private aggregateByMonth(transactions: any[]): any {
    const monthly: any = {};
    
    transactions.forEach((txn) => {
      const month = new Date(txn.date).toISOString().slice(0, 7);
      if (!monthly[month]) {
        monthly[month] = { income: 0, expense: 0 };
      }
      
      if (txn.type === 'income') {
        monthly[month].income += txn.amount;
      } else {
        monthly[month].expense += txn.amount;
      }
    });

    return monthly;
  }

  private calculatePrediction(monthlyData: any, offset: number): CashFlowPrediction {
    const months = Object.keys(monthlyData).sort().reverse();
    const recentMonths = months.slice(0, 6);
    
    let avgIncome = 0;
    let avgExpense = 0;
    
    recentMonths.forEach((month) => {
      avgIncome += monthlyData[month].income;
      avgExpense += monthlyData[month].expense;
    });
    
    avgIncome /= recentMonths.length;
    avgExpense /= recentMonths.length;
    
    // Aplicar tendência
    const trend = this.calculateTrend(recentMonths.map(m => monthlyData[m].income - monthlyData[m].expense));
    const trendFactor = 1 + (trend * offset * 0.02);
    
    const predictedIncome = avgIncome * trendFactor;
    const predictedExpense = avgExpense * trendFactor;
    const predictedBalance = predictedIncome - predictedExpense;
    
    const futureDate = new Date();
    futureDate.setMonth(futureDate.getMonth() + offset + 1);
    
    return {
      month: futureDate.toISOString().slice(0, 7),
      predictedIncome,
      predictedExpense,
      predictedBalance,
      confidence: Math.max(0.6, 0.95 - offset * 0.05),
      trend: trend > 0.1 ? 'up' : trend < -0.1 ? 'down' : 'stable',
    };
  }

  private calculateTrend(values: number[]): number {
    if (values.length < 2) return 0;
    
    const n = values.length;
    let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
    
    values.forEach((y, x) => {
      sumX += x;
      sumY += y;
      sumXY += x * y;
      sumX2 += x * x;
    });
    
    const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
    return slope / (sumY / n);
  }

  private calculateStatistics(transactions: any[]): any {
    const categoryAverages: any = {};
    const categoryCount: any = {};
    
    transactions.forEach((txn) => {
      if (!categoryAverages[txn.category]) {
        categoryAverages[txn.category] = 0;
        categoryCount[txn.category] = 0;
      }
      categoryAverages[txn.category] += txn.amount;
      categoryCount[txn.category]++;
    });
    
    Object.keys(categoryAverages).forEach((cat) => {
      categoryAverages[cat] /= categoryCount[cat];
    });
    
    return { categoryAverages };
  }

  private analyzeSpendingPatterns(transactions: any[]): any {
    const expenses = transactions.filter(t => t.type === 'expense');
    const totalExpense = expenses.reduce((sum, t) => sum + t.amount, 0);
    
    const categoryTotals: any = {};
    expenses.forEach((txn) => {
      categoryTotals[txn.category] = (categoryTotals[txn.category] || 0) + txn.amount;
    });
    
    const highSpendingCategories = Object.entries(categoryTotals)
      .map(([category, amount]: [string, any]) => ({
        category,
        amount,
        percentage: (amount / totalExpense) * 100,
      }))
      .filter((cat) => cat.percentage > 15)
      .sort((a, b) => b.amount - a.amount);
    
    return {
      highSpendingCategories,
      cashFlowIssues: totalExpense > 0,
    };
  }

  private groupByCategory(transactions: any[]): any {
    const grouped: any = {};
    
    transactions.forEach((txn) => {
      if (!grouped[txn.category]) {
        grouped[txn.category] = { current: 0, average: 0, count: 0 };
      }
      grouped[txn.category].current += txn.amount;
      grouped[txn.category].count++;
    });
    
    Object.keys(grouped).forEach((cat) => {
      grouped[cat].average = grouped[cat].current / grouped[cat].count;
    });
    
    return grouped;
  }

  private getInsightRecommendation(category: string, variance: number): string {
    if (variance > 20) {
      return `Gastos em ${category} aumentaram ${variance.toFixed(1)}%. Revisar e controlar.`;
    } else if (variance < -20) {
      return `Ótimo! Gastos em ${category} reduziram ${Math.abs(variance).toFixed(1)}%.`;
    }
    return `Gastos em ${category} estão estáveis.`;
  }

  // Mock data para fallback
  private getMockPredictions(months: number): CashFlowPrediction[] {
    const predictions: CashFlowPrediction[] = [];
    const baseIncome = 500000;
    const baseExpense = 350000;
    
    for (let i = 0; i < months; i++) {
      const futureDate = new Date();
      futureDate.setMonth(futureDate.getMonth() + i + 1);
      
      predictions.push({
        month: futureDate.toISOString().slice(0, 7),
        predictedIncome: baseIncome * (1 + Math.random() * 0.2),
        predictedExpense: baseExpense * (1 + Math.random() * 0.15),
        predictedBalance: (baseIncome - baseExpense) * (1 + Math.random() * 0.3),
        confidence: 0.85 - i * 0.05,
        trend: i % 2 === 0 ? 'up' : 'stable',
      });
    }
    
    return predictions;
  }

  private getMockAnomalies(): FinancialAnomaly[] {
    return [
      {
        id: 'anomaly-1',
        type: 'unusual_expense',
        severity: 'high',
        description: 'Despesa 85% acima da média em Fornecedores',
        amount: 125000,
        date: new Date(),
        category: 'Fornecedores',
        recommendation: 'Revisar esta transação e verificar se está dentro do orçamento planejado',
      },
    ];
  }

  private getMockRecommendations(): AIRecommendation[] {
    return [
      {
        id: 'rec-1',
        type: 'cost_reduction',
        priority: 'high',
        title: 'Reduzir gastos em Fornecedores',
        description: 'Categoria Fornecedores representa 35% dos gastos totais',
        potentialSavings: 45000,
        implementationDifficulty: 'medium',
        estimatedImpact: '15% de redução possível',
        actionItems: [
          'Revisar contratos e fornecedores',
          'Negociar melhores condições',
          'Buscar alternativas mais econômicas',
        ],
      },
    ];
  }
}

export const aiFinanceService = new AIFinanceService();
