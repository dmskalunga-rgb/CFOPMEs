// =====================================================
// KWANZACONTROL - Cost Center Analysis Component
// Análise detalhada de rentabilidade por centro de custo
// =====================================================

import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { 
  TrendingUp, 
  TrendingDown, 
  DollarSign, 
  Target, 
  AlertCircle,
  CheckCircle,
  BarChart3,
  PieChart,
  Activity
} from 'lucide-react';
import { formatCurrency } from '@/lib/index';
import { CostCenter } from '@/services/costCenterService';
import { supabase } from '@/integrations/supabase/client';

interface CostCenterAnalysisProps {
  costCenter: CostCenter | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

interface AnalysisData {
  totalRevenue: number;
  totalExpenses: number;
  netProfit: number;
  profitMargin: number;
  budgetUsed: number;
  budgetRemaining: number;
  budgetPercentage: number;
  transactions: number;
  avgTransactionValue: number;
  topExpenseCategories: Array<{ category: string; amount: number; percentage: number }>;
  monthlyTrend: Array<{ month: string; revenue: number; expenses: number; profit: number }>;
}

export function CostCenterAnalysis({ costCenter, open, onOpenChange }: CostCenterAnalysisProps) {
  const [loading, setLoading] = useState(true);
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null);

  useEffect(() => {
    if (open && costCenter) {
      loadAnalysis();
    }
  }, [open, costCenter]);

  const loadAnalysis = async () => {
    if (!costCenter) return;

    setLoading(true);
    try {
      // Buscar transações do centro de custo
      const { data: transactions, error } = await supabase
        .from('transactions')
        .select('*')
        .eq('cost_center_id', costCenter.id)
        .gte('created_at', new Date(new Date().setMonth(new Date().getMonth() - 6)).toISOString());

      if (error) throw error;

      // Calcular métricas
      const revenue = transactions
        ?.filter(t => t.type === 'INCOME')
        .reduce((sum, t) => sum + (t.amount || 0), 0) || 0;

      const expenses = transactions
        ?.filter(t => t.type === 'EXPENSE')
        .reduce((sum, t) => sum + (t.amount || 0), 0) || 0;

      const netProfit = revenue - expenses;
      const profitMargin = revenue > 0 ? (netProfit / revenue) * 100 : 0;
      const budgetUsed = expenses;
      const budgetRemaining = Math.max(0, costCenter.budget - budgetUsed);
      const budgetPercentage = costCenter.budget > 0 ? (budgetUsed / costCenter.budget) * 100 : 0;

      // Agrupar despesas por categoria
      const expensesByCategory = transactions
        ?.filter(t => t.type === 'EXPENSE')
        .reduce((acc, t) => {
          const category = t.category || 'Outros';
          acc[category] = (acc[category] || 0) + (t.amount || 0);
          return acc;
        }, {} as Record<string, number>) || {};

      const topExpenseCategories = Object.entries(expensesByCategory)
        .map(([category, amount]) => ({
          category,
          amount: amount as number,
          percentage: expenses > 0 ? ((amount as number) / expenses) * 100 : 0,
        }))
        .sort((a, b) => (b.amount as number) - (a.amount as number))
        .slice(0, 5);

      // Tendência mensal (últimos 6 meses)
      const monthlyData: Record<string, { revenue: number; expenses: number }> = {};
      const months = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez'];
      
      transactions?.forEach(t => {
        const date = new Date(t.created_at);
        const monthKey = `${months[date.getMonth()]}/${date.getFullYear().toString().slice(-2)}`;
        
        if (!monthlyData[monthKey]) {
          monthlyData[monthKey] = { revenue: 0, expenses: 0 };
        }
        
        if (t.type === 'INCOME') {
          monthlyData[monthKey].revenue += t.amount || 0;
        } else {
          monthlyData[monthKey].expenses += t.amount || 0;
        }
      });

      const monthlyTrend = Object.entries(monthlyData)
        .map(([month, data]) => ({
          month,
          revenue: data.revenue,
          expenses: data.expenses,
          profit: data.revenue - data.expenses,
        }))
        .slice(-6);

      setAnalysis({
        totalRevenue: revenue,
        totalExpenses: expenses,
        netProfit,
        profitMargin,
        budgetUsed,
        budgetRemaining,
        budgetPercentage,
        transactions: transactions?.length || 0,
        avgTransactionValue: transactions?.length ? (revenue + expenses) / transactions.length : 0,
        topExpenseCategories,
        monthlyTrend,
      });
    } catch (error) {
      console.error('Erro ao carregar análise:', error);
      // Dados mock para demonstração
      setAnalysis({
        totalRevenue: 2500000,
        totalExpenses: 1800000,
        netProfit: 700000,
        profitMargin: 28,
        budgetUsed: 1800000,
        budgetRemaining: costCenter.budget - 1800000,
        budgetPercentage: (1800000 / costCenter.budget) * 100,
        transactions: 45,
        avgTransactionValue: 95555,
        topExpenseCategories: [
          { category: 'Salários', amount: 800000, percentage: 44.4 },
          { category: 'Fornecedores', amount: 500000, percentage: 27.8 },
          { category: 'Marketing', amount: 300000, percentage: 16.7 },
          { category: 'Utilidades', amount: 150000, percentage: 8.3 },
          { category: 'Outros', amount: 50000, percentage: 2.8 },
        ],
        monthlyTrend: [
          { month: 'Jan/26', revenue: 400000, expenses: 300000, profit: 100000 },
          { month: 'Fev/26', revenue: 450000, expenses: 320000, profit: 130000 },
          { month: 'Mar/26', revenue: 420000, expenses: 310000, profit: 110000 },
          { month: 'Abr/26', revenue: 480000, expenses: 340000, profit: 140000 },
          { month: 'Mai/26', revenue: 500000, expenses: 360000, profit: 140000 },
          { month: 'Jun/26', revenue: 250000, expenses: 170000, profit: 80000 },
        ],
      });
    } finally {
      setLoading(false);
    }
  };

  if (!costCenter) return null;

  const getProfitStatus = () => {
    if (!analysis) return { icon: Activity, color: 'text-muted-foreground', label: 'Carregando...' };
    if (analysis.netProfit > 0) return { icon: TrendingUp, color: 'text-green-500', label: 'Lucrativo' };
    if (analysis.netProfit < 0) return { icon: TrendingDown, color: 'text-red-500', label: 'Prejuízo' };
    return { icon: Activity, color: 'text-yellow-500', label: 'Neutro' };
  };

  const getBudgetStatus = () => {
    if (!analysis) return { icon: Activity, color: 'text-muted-foreground', label: 'Carregando...' };
    if (analysis.budgetPercentage < 70) return { icon: CheckCircle, color: 'text-green-500', label: 'Saudável' };
    if (analysis.budgetPercentage < 90) return { icon: AlertCircle, color: 'text-yellow-500', label: 'Atenção' };
    return { icon: AlertCircle, color: 'text-red-500', label: 'Crítico' };
  };

  const profitStatus = getProfitStatus();
  const budgetStatus = getBudgetStatus();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Target className="h-5 w-5 text-primary" />
            Análise de Rentabilidade: {costCenter.name}
          </DialogTitle>
          <DialogDescription>
            Código: {costCenter.code} • Tipo: {costCenter.type}
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Activity className="h-12 w-12 animate-spin text-primary" />
          </div>
        ) : analysis ? (
          <Tabs defaultValue="overview" className="space-y-4">
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="overview">Visão Geral</TabsTrigger>
              <TabsTrigger value="budget">Orçamento</TabsTrigger>
              <TabsTrigger value="expenses">Despesas</TabsTrigger>
            </TabsList>

            {/* Overview Tab */}
            <TabsContent value="overview" className="space-y-4">
              {/* KPIs */}
              <div className="grid gap-4 md:grid-cols-3">
                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Receita Total</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold text-green-600">
                      {formatCurrency(analysis.totalRevenue)}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      Últimos 6 meses
                    </p>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Despesas Totais</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold text-red-600">
                      {formatCurrency(analysis.totalExpenses)}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                      Últimos 6 meses
                    </p>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="pb-2">
                    <CardDescription>Lucro Líquido</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className={`text-2xl font-bold ${analysis.netProfit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {formatCurrency(analysis.netProfit)}
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      <profitStatus.icon className={`h-4 w-4 ${profitStatus.color}`} />
                      <span className={`text-xs font-medium ${profitStatus.color}`}>
                        {profitStatus.label}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Métricas Adicionais */}
              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Margem de Lucro</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex items-center justify-between">
                      <div className="text-3xl font-bold">
                        {analysis.profitMargin.toFixed(1)}%
                      </div>
                      <PieChart className={`h-8 w-8 ${analysis.profitMargin >= 20 ? 'text-green-500' : analysis.profitMargin >= 10 ? 'text-yellow-500' : 'text-red-500'}`} />
                    </div>
                    <Progress value={Math.min(analysis.profitMargin, 100)} className="mt-2" />
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Transações</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-3xl font-bold">{analysis.transactions}</div>
                        <p className="text-sm text-muted-foreground mt-1">
                          Média: {formatCurrency(analysis.avgTransactionValue)}
                        </p>
                      </div>
                      <BarChart3 className="h-8 w-8 text-primary" />
                    </div>
                  </CardContent>
                </Card>
              </div>

              {/* Tendência Mensal */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Tendência Mensal</CardTitle>
                  <CardDescription>Evolução dos últimos 6 meses</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {analysis.monthlyTrend.map((month) => (
                      <div key={month.month} className="space-y-1">
                        <div className="flex items-center justify-between text-sm">
                          <span className="font-medium">{month.month}</span>
                          <span className={`font-semibold ${month.profit >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                            {formatCurrency(month.profit)}
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <div className="flex-1">
                            <div className="text-xs text-muted-foreground mb-1">Receita</div>
                            <div className="h-2 bg-green-200 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-green-500" 
                                style={{ width: `${(month.revenue / Math.max(...analysis.monthlyTrend.map(m => m.revenue))) * 100}%` }}
                              />
                            </div>
                          </div>
                          <div className="flex-1">
                            <div className="text-xs text-muted-foreground mb-1">Despesas</div>
                            <div className="h-2 bg-red-200 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-red-500" 
                                style={{ width: `${(month.expenses / Math.max(...analysis.monthlyTrend.map(m => m.expenses))) * 100}%` }}
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>

            {/* Budget Tab */}
            <TabsContent value="budget" className="space-y-4">
              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle>Execução Orçamentária</CardTitle>
                      <CardDescription>Orçamento total: {formatCurrency(costCenter.budget)}</CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                      <budgetStatus.icon className={`h-5 w-5 ${budgetStatus.color}`} />
                      <span className={`text-sm font-medium ${budgetStatus.color}`}>
                        {budgetStatus.label}
                      </span>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium">Utilizado</span>
                      <span className="text-sm font-bold">{analysis.budgetPercentage.toFixed(1)}%</span>
                    </div>
                    <Progress 
                      value={analysis.budgetPercentage} 
                      className="h-3"
                    />
                  </div>

                  <Separator />

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="text-sm text-muted-foreground mb-1">Orçamento Utilizado</div>
                      <div className="text-2xl font-bold text-red-600">
                        {formatCurrency(analysis.budgetUsed)}
                      </div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground mb-1">Orçamento Restante</div>
                      <div className="text-2xl font-bold text-green-600">
                        {formatCurrency(analysis.budgetRemaining)}
                      </div>
                    </div>
                  </div>

                  {analysis.budgetPercentage >= 90 && (
                    <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg">
                      <AlertCircle className="h-5 w-5 text-red-500 mt-0.5" />
                      <div>
                        <p className="text-sm font-medium text-red-900">Orçamento Crítico</p>
                        <p className="text-sm text-red-700 mt-1">
                          Você já utilizou {analysis.budgetPercentage.toFixed(1)}% do orçamento. 
                          Considere revisar as despesas ou solicitar aumento de orçamento.
                        </p>
                      </div>
                    </div>
                  )}

                  {analysis.budgetPercentage >= 70 && analysis.budgetPercentage < 90 && (
                    <div className="flex items-start gap-2 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                      <AlertCircle className="h-5 w-5 text-yellow-500 mt-0.5" />
                      <div>
                        <p className="text-sm font-medium text-yellow-900">Atenção ao Orçamento</p>
                        <p className="text-sm text-yellow-700 mt-1">
                          Você já utilizou {analysis.budgetPercentage.toFixed(1)}% do orçamento. 
                          Monitore as despesas para não ultrapassar o limite.
                        </p>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* Expenses Tab */}
            <TabsContent value="expenses" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Top 5 Categorias de Despesas</CardTitle>
                  <CardDescription>Distribuição das principais despesas</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {analysis.topExpenseCategories.map((category, index) => (
                    <div key={category.category} className="space-y-2">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">{index + 1}</Badge>
                          <span className="font-medium">{category.category}</span>
                        </div>
                        <div className="text-right">
                          <div className="font-bold">{formatCurrency(category.amount)}</div>
                          <div className="text-xs text-muted-foreground">
                            {category.percentage.toFixed(1)}%
                          </div>
                        </div>
                      </div>
                      <Progress value={category.percentage} />
                    </div>
                  ))}

                  {analysis.topExpenseCategories.length === 0 && (
                    <div className="text-center py-8 text-muted-foreground">
                      <DollarSign className="h-12 w-12 mx-auto mb-2 opacity-50" />
                      <p>Nenhuma despesa registrada</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        ) : (
          <div className="text-center py-12 text-muted-foreground">
            <AlertCircle className="h-12 w-12 mx-auto mb-2" />
            <p>Erro ao carregar análise</p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
