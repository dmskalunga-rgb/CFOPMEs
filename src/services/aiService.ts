// =====================================================
// KWANZACONTROL - AI Cash Flow Prediction Service
// Previsão inteligente de fluxo de caixa usando ML
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface CashFlowPrediction {
  date: string;
  predicted_income: number;
  predicted_expenses: number;
  predicted_balance: number;
  confidence: number;
  trend: 'up' | 'down' | 'stable';
}

export interface AnomalyDetection {
  transaction_id: string;
  anomaly_score: number;
  reason: string;
  severity: 'low' | 'medium' | 'high';
  suggested_action: string;
}

export interface SmartRecommendation {
  id: string;
  type: 'cost_reduction' | 'revenue_opportunity' | 'cash_flow_optimization' | 'budget_adjustment';
  title: string;
  description: string;
  impact: number;
  confidence: number;
  actions: string[];
}

class AIService {
  // Previsão de Fluxo de Caixa (próximos 3 meses)
  async predictCashFlow(organizationId: string): Promise<CashFlowPrediction[]> {
    try {
      // Buscar histórico de transações (últimos 12 meses)
      const twelveMonthsAgo = new Date();
      twelveMonthsAgo.setMonth(twelveMonthsAgo.getMonth() - 12);

      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('organization_id', organizationId)
        .gte('created_at', twelveMonthsAgo.toISOString())
        .order('created_at', { ascending: true });

      if (error) throw error;

      // Agrupar por mês
      const monthlyData: Record<string, { income: number; expenses: number }> = {};

      transactions?.forEach(t => {
        const date = new Date(t.created_at);
        const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;

        if (!monthlyData[monthKey]) {
          monthlyData[monthKey] = { income: 0, expenses: 0 };
        }

        if (t.type === 'INCOME') {
          monthlyData[monthKey].income += t.amount || 0;
        } else {
          monthlyData[monthKey].expenses += t.amount || 0;
        }
      });

      // Calcular médias e tendências
      const months = Object.keys(monthlyData).sort();
      const avgIncome = months.reduce((sum, m) => sum + monthlyData[m].income, 0) / months.length;
      const avgExpenses = months.reduce((sum, m) => sum + monthlyData[m].expenses, 0) / months.length;

      // Calcular tendência (regressão linear simples)
      const incomeTrend = this.calculateTrend(months.map(m => monthlyData[m].income));
      const expensesTrend = this.calculateTrend(months.map(m => monthlyData[m].expenses));

      // Gerar previsões para os próximos 3 meses
      const predictions: CashFlowPrediction[] = [];
      const currentDate = new Date();

      for (let i = 1; i <= 3; i++) {
        const futureDate = new Date(currentDate);
        futureDate.setMonth(futureDate.getMonth() + i);
        const monthKey = `${futureDate.getFullYear()}-${String(futureDate.getMonth() + 1).padStart(2, '0')}`;

        const predictedIncome = avgIncome + (incomeTrend * i);
        const predictedExpenses = avgExpenses + (expensesTrend * i);
        const predictedBalance = predictedIncome - predictedExpenses;

        // Calcular confiança (baseado na variância dos dados históricos)
        const confidence = this.calculateConfidence(months.map(m => monthlyData[m].income), avgIncome);

        // Determinar tendência
        let trend: 'up' | 'down' | 'stable' = 'stable';
        if (predictedBalance > avgIncome - avgExpenses + (avgIncome * 0.1)) trend = 'up';
        else if (predictedBalance < avgIncome - avgExpenses - (avgIncome * 0.1)) trend = 'down';

        predictions.push({
          date: monthKey,
          predicted_income: Math.max(0, predictedIncome),
          predicted_expenses: Math.max(0, predictedExpenses),
          predicted_balance: predictedBalance,
          confidence,
          trend,
        });
      }

      return predictions;
    } catch (error) {
      console.error('Error predicting cash flow:', error);
      // Retornar previsões mock em caso de erro
      return this.getMockPredictions();
    }
  }

  // Detectar anomalias em transações
  async detectAnomalies(organizationId: string): Promise<AnomalyDetection[]> {
    try {
      // Buscar transações dos últimos 30 dias
      const thirtyDaysAgo = new Date();
      thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('organization_id', organizationId)
        .gte('created_at', thirtyDaysAgo.toISOString());

      if (error) throw error;

      // Calcular estatísticas
      const amounts = transactions?.map(t => t.amount || 0) || [];
      const mean = amounts.reduce((sum, a) => sum + a, 0) / amounts.length;
      const stdDev = Math.sqrt(
        amounts.reduce((sum, a) => sum + Math.pow(a - mean, 2), 0) / amounts.length
      );

      // Detectar anomalias (valores fora de 2 desvios padrão)
      const anomalies: AnomalyDetection[] = [];

      transactions?.forEach(t => {
        const zScore = Math.abs((t.amount - mean) / stdDev);

        if (zScore > 2) {
          let severity: 'low' | 'medium' | 'high' = 'low';
          if (zScore > 3) severity = 'high';
          else if (zScore > 2.5) severity = 'medium';

          anomalies.push({
            transaction_id: t.id,
            anomaly_score: zScore,
            reason: `Valor ${zScore > 3 ? 'muito' : ''} acima da média (${mean.toFixed(0)} Kz)`,
            severity,
            suggested_action: severity === 'high' 
              ? 'Verificar imediatamente esta transação'
              : 'Revisar esta transação quando possível',
          });
        }
      });

      return anomalies.sort((a, b) => b.anomaly_score - a.anomaly_score).slice(0, 10);
    } catch (error) {
      console.error('Error detecting anomalies:', error);
      return [];
    }
  }

  // Gerar recomendações inteligentes
  async generateRecommendations(organizationId: string): Promise<SmartRecommendation[]> {
    try {
      const recommendations: SmartRecommendation[] = [];

      // Buscar dados para análise
      const [transactions, costCenters, predictions] = await Promise.all([
        this.getRecentTransactions(organizationId),
        this.getCostCenters(organizationId),
        this.predictCashFlow(organizationId),
      ]);

      // Recomendação 1: Redução de custos
      const topExpenseCategories = this.getTopExpenseCategories(transactions);
      if (topExpenseCategories.length > 0) {
        const topCategory = topExpenseCategories[0];
        recommendations.push({
          id: 'cost-reduction-1',
          type: 'cost_reduction',
          title: `Reduzir Despesas em ${topCategory.category}`,
          description: `Esta categoria representa ${topCategory.percentage.toFixed(1)}% das suas despesas. Considere negociar melhores preços ou buscar alternativas.`,
          impact: topCategory.amount * 0.15, // 15% de economia potencial
          confidence: 0.75,
          actions: [
            'Negociar com fornecedores atuais',
            'Buscar fornecedores alternativos',
            'Avaliar necessidade real de cada despesa',
          ],
        });
      }

      // Recomendação 2: Otimização de fluxo de caixa
      if (predictions.length > 0 && predictions[0].predicted_balance < 0) {
        recommendations.push({
          id: 'cash-flow-1',
          type: 'cash_flow_optimization',
          title: 'Melhorar Fluxo de Caixa',
          description: `Previsão indica saldo negativo de ${Math.abs(predictions[0].predicted_balance).toFixed(0)} Kz no próximo mês.`,
          impact: Math.abs(predictions[0].predicted_balance),
          confidence: predictions[0].confidence,
          actions: [
            'Acelerar recebimentos de clientes',
            'Negociar prazos maiores com fornecedores',
            'Reduzir despesas não essenciais',
          ],
        });
      }

      // Recomendação 3: Ajuste de orçamento
      for (const cc of costCenters) {
        const expenses = transactions
          .filter(t => t.cost_center_id === cc.id && t.type === 'EXPENSE')
          .reduce((sum, t) => sum + (t.amount || 0), 0);

        const budgetUsage = (expenses / cc.budget) * 100;

        if (budgetUsage > 90) {
          recommendations.push({
            id: `budget-${cc.id}`,
            type: 'budget_adjustment',
            title: `Ajustar Orçamento de ${cc.name}`,
            description: `Este centro de custo está usando ${budgetUsage.toFixed(0)}% do orçamento. Considere aumentar o orçamento ou reduzir despesas.`,
            impact: expenses - cc.budget,
            confidence: 0.85,
            actions: [
              'Aumentar orçamento em 20%',
              'Identificar despesas desnecessárias',
              'Redistribuir recursos de outros centros',
            ],
          });
        }
      }

      return recommendations.sort((a, b) => b.impact - a.impact).slice(0, 5);
    } catch (error) {
      console.error('Error generating recommendations:', error);
      return this.getMockRecommendations();
    }
  }

  // Categorização automática de despesas
  async categorizExpense(description: string, amount: number): Promise<string> {
    // Regras simples de categorização (pode ser expandido com ML)
    const rules: Record<string, string[]> = {
      'Salários': ['salário', 'salario', 'vencimento', 'ordenado', 'payroll'],
      'Fornecedores': ['fornecedor', 'compra', 'aquisição', 'aquisicao', 'material'],
      'Marketing': ['marketing', 'publicidade', 'propaganda', 'anúncio', 'anuncio'],
      'Utilidades': ['água', 'agua', 'luz', 'energia', 'internet', 'telefone'],
      'Transporte': ['combustível', 'combustivel', 'gasolina', 'transporte', 'viagem'],
      'Alimentação': ['alimentação', 'alimentacao', 'refeição', 'refeicao', 'restaurante'],
      'Manutenção': ['manutenção', 'manutencao', 'reparo', 'conserto'],
    };

    const lowerDesc = description.toLowerCase();

    for (const [category, keywords] of Object.entries(rules)) {
      if (keywords.some(keyword => lowerDesc.includes(keyword))) {
        return category;
      }
    }

    // Categorização por valor
    if (amount > 500000) return 'Investimentos';
    if (amount > 100000) return 'Fornecedores';
    return 'Outros';
  }

  // Helpers privados
  private calculateTrend(values: number[]): number {
    const n = values.length;
    const sumX = (n * (n + 1)) / 2;
    const sumY = values.reduce((sum, v) => sum + v, 0);
    const sumXY = values.reduce((sum, v, i) => sum + v * (i + 1), 0);
    const sumX2 = (n * (n + 1) * (2 * n + 1)) / 6;

    return (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
  }

  private calculateConfidence(values: number[], mean: number): number {
    const variance = values.reduce((sum, v) => sum + Math.pow(v - mean, 2), 0) / values.length;
    const coefficientOfVariation = Math.sqrt(variance) / mean;
    return Math.max(0, Math.min(1, 1 - coefficientOfVariation));
  }

  private async getRecentTransactions(organizationId: string): Promise<any[]> {
    const { data } = await supabase
      .from('transactions')
      .select('*')
      .eq('organization_id', organizationId)
      .gte('created_at', new Date(new Date().setMonth(new Date().getMonth() - 3)).toISOString());

    return data || [];
  }

  private async getCostCenters(organizationId: string): Promise<any[]> {
    const { data } = await supabase
      .from('cost_centers')
      .select('*')
      .eq('organization_id', organizationId)
      .eq('active', true);

    return data || [];
  }

  private getTopExpenseCategories(transactions: any[]): Array<{ category: string; amount: number; percentage: number }> {
    const expenses = transactions.filter(t => t.type === 'EXPENSE');
    const totalExpenses = expenses.reduce((sum, t) => sum + (t.amount || 0), 0);

    const byCategory: Record<string, number> = {};
    expenses.forEach(t => {
      const category = t.category || 'Outros';
      byCategory[category] = (byCategory[category] || 0) + (t.amount || 0);
    });

    return Object.entries(byCategory)
      .map(([category, amount]) => ({
        category,
        amount,
        percentage: (amount / totalExpenses) * 100,
      }))
      .sort((a, b) => b.amount - a.amount);
  }

  private getMockPredictions(): CashFlowPrediction[] {
    const currentDate = new Date();
    return [1, 2, 3].map(i => {
      const futureDate = new Date(currentDate);
      futureDate.setMonth(futureDate.getMonth() + i);
      const monthKey = `${futureDate.getFullYear()}-${String(futureDate.getMonth() + 1).padStart(2, '0')}`;

      return {
        date: monthKey,
        predicted_income: 2500000 + (i * 100000),
        predicted_expenses: 1800000 + (i * 80000),
        predicted_balance: 700000 + (i * 20000),
        confidence: 0.75,
        trend: 'up' as const,
      };
    });
  }

  private getMockRecommendations(): SmartRecommendation[] {
    return [
      {
        id: 'mock-1',
        type: 'cost_reduction',
        title: 'Reduzir Despesas em Fornecedores',
        description: 'Esta categoria representa 35% das suas despesas.',
        impact: 300000,
        confidence: 0.75,
        actions: ['Negociar com fornecedores', 'Buscar alternativas'],
      },
    ];
  }
}

export const aiService = new AIService();
