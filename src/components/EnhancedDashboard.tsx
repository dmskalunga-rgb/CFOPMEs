import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { TrendingUp, TrendingDown, DollarSign, CreditCard, Users, FileText, Download, Calendar } from 'lucide-react';
import { formatCurrency } from '@/lib/index';
import { supabase } from '@/integrations/supabase/client';
import { useToast } from '@/lib/toast-provider';
import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface DashboardMetrics {
  totalRevenue: number;
  totalExpenses: number;
  netProfit: number;
  profitMargin: number;
  activeContracts: number;
  totalBudget: number;
  budgetUsed: number;
  pendingTransactions: number;
  revenueChange: number;
  expensesChange: number;
}

interface ChartData {
  name: string;
  receita: number;
  despesa: number;
  lucro: number;
}

type PeriodType = 'today' | 'week' | 'month' | 'quarter' | 'year';

const COLORS = ['#10b981', '#ef4444', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899'];

export function EnhancedDashboard() {
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [chartData, setChartData] = useState<ChartData[]>([]);
  const [categoryData, setCategoryData] = useState<any[]>([]);
  const [period, setPeriod] = useState<PeriodType>('month');
  const [loading, setLoading] = useState(true);
  const { error: showError } = useToast();

  useEffect(() => {
    loadDashboardData();
  }, [period]);

  const getPeriodDates = () => {
    const now = new Date();
    const start = new Date();
    
    switch (period) {
      case 'today':
        start.setHours(0, 0, 0, 0);
        break;
      case 'week':
        start.setDate(now.getDate() - 7);
        break;
      case 'month':
        start.setMonth(now.getMonth() - 1);
        break;
      case 'quarter':
        start.setMonth(now.getMonth() - 3);
        break;
      case 'year':
        start.setFullYear(now.getFullYear() - 1);
        break;
    }
    
    return { start, end: now };
  };

  const loadDashboardData = async () => {
    setLoading(true);
    try {
      const { start, end } = getPeriodDates();
      
      // Load transactions
      const { data: transactions, error: txError } = await supabase
        .from('transactions')
        .select('*')
        .gte('date', start.toISOString())
        .lte('date', end.toISOString());

      if (txError) {
        console.warn('Erro ao carregar transações:', txError);
        // Usar dados mock se falhar
        setMetrics({
          totalRevenue: 1500000,
          totalExpenses: 1200000,
          netProfit: 300000,
          profitMargin: 20,
          activeContracts: 5,
          totalBudget: 2000000,
          budgetUsed: 1500000,
          pendingTransactions: 3,
          revenueChange: 15,
          expensesChange: -5,
        });
        setChartData([
          { name: 'Jan', receita: 1200000, despesa: 900000, lucro: 300000 },
          { name: 'Fev', receita: 1350000, despesa: 950000, lucro: 400000 },
          { name: 'Mar', receita: 1400000, despesa: 1000000, lucro: 400000 },
          { name: 'Abr', receita: 1450000, despesa: 1050000, lucro: 400000 },
          { name: 'Mai', receita: 1500000, despesa: 1100000, lucro: 400000 },
          { name: 'Jun', receita: 1600000, despesa: 1200000, lucro: 400000 },
        ]);
        setCategoryData([
          { name: 'Salários', value: 500000 },
          { name: 'Fornecedores', value: 300000 },
          { name: 'Marketing', value: 200000 },
          { name: 'Operações', value: 150000 },
          { name: 'Outros', value: 50000 },
        ]);
        setLoading(false);
        return;
      }

      // Calculate metrics
      const revenue = transactions?.filter(t => t.type === 'income').reduce((sum, t) => sum + t.amount, 0) || 0;
      const expenses = transactions?.filter(t => t.type === 'expense').reduce((sum, t) => sum + t.amount, 0) || 0;
      const netProfit = revenue - expenses;
      const profitMargin = revenue > 0 ? (netProfit / revenue) * 100 : 0;

      // Load contracts
      const { data: contracts } = await supabase
        .from('contracts')
        .select('*')
        .eq('status', 'active');

      // Load budgets
      const { data: budgets } = await supabase
        .from('budgets')
        .select('*');

      const totalBudget = budgets?.reduce((sum, b) => sum + b.total_amount, 0) || 0;
      const budgetUsed = budgets?.reduce((sum, b) => sum + b.spent_amount, 0) || 0;

      // Previous period comparison
      const prevStart = new Date(start);
      const prevEnd = new Date(start);
      const diff = end.getTime() - start.getTime();
      prevStart.setTime(prevStart.getTime() - diff);

      const { data: prevTransactions } = await supabase
        .from('transactions')
        .select('*')
        .gte('date', prevStart.toISOString())
        .lte('date', prevEnd.toISOString());

      const prevRevenue = prevTransactions?.filter(t => t.type === 'income').reduce((sum, t) => sum + t.amount, 0) || 0;
      const prevExpenses = prevTransactions?.filter(t => t.type === 'expense').reduce((sum, t) => sum + t.amount, 0) || 0;

      const revenueChange = prevRevenue > 0 ? ((revenue - prevRevenue) / prevRevenue) * 100 : 0;
      const expensesChange = prevExpenses > 0 ? ((expenses - prevExpenses) / prevExpenses) * 100 : 0;

      setMetrics({
        totalRevenue: revenue,
        totalExpenses: expenses,
        netProfit,
        profitMargin,
        activeContracts: contracts?.length || 0,
        totalBudget,
        budgetUsed,
        pendingTransactions: transactions?.filter(t => !t.reconciled).length || 0,
        revenueChange,
        expensesChange,
      });

      // Generate chart data
      const groupedData = generateChartData(transactions || [], period);
      setChartData(groupedData);

      // Category breakdown
      const categoryBreakdown = generateCategoryData(transactions || []);
      setCategoryData(categoryBreakdown);

    } catch (err) {
      console.error('Erro ao carregar dashboard:', err);
      showError('Erro', 'Não foi possível carregar os dados do dashboard');
    } finally {
      setLoading(false);
    }
  };

  const generateChartData = (transactions: any[], periodType: PeriodType): ChartData[] => {
    const grouped: Record<string, { receita: number; despesa: number }> = {};

    transactions.forEach(tx => {
      const date = new Date(tx.date);
      let key: string;

      switch (periodType) {
        case 'today':
          key = `${date.getHours()}:00`;
          break;
        case 'week':
          key = date.toLocaleDateString('pt-BR', { weekday: 'short' });
          break;
        case 'month':
          key = `${date.getDate()}/${date.getMonth() + 1}`;
          break;
        case 'quarter':
        case 'year':
          key = date.toLocaleDateString('pt-BR', { month: 'short' });
          break;
        default:
          key = date.toLocaleDateString();
      }

      if (!grouped[key]) {
        grouped[key] = { receita: 0, despesa: 0 };
      }

      if (tx.type === 'income') {
        grouped[key].receita += tx.amount;
      } else {
        grouped[key].despesa += tx.amount;
      }
    });

    return Object.entries(grouped).map(([name, data]) => ({
      name,
      receita: data.receita,
      despesa: data.despesa,
      lucro: data.receita - data.despesa,
    }));
  };

  const generateCategoryData = (transactions: any[]) => {
    const grouped: Record<string, number> = {};

    transactions.forEach(tx => {
      const category = tx.category || 'Sem Categoria';
      grouped[category] = (grouped[category] || 0) + tx.amount;
    });

    return Object.entries(grouped)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  };

  const handleExport = () => {
    // Export functionality placeholder
    alert('Exportação de relatório em desenvolvimento');
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <DollarSign className="h-12 w-12 animate-pulse text-primary" />
      </div>
    );
  }

  if (!metrics) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Dashboard Financeiro</h2>
          <p className="text-muted-foreground mt-1">Visão geral do desempenho financeiro</p>
        </div>
        <div className="flex gap-2">
          <Select value={period} onValueChange={(value) => setPeriod(value as PeriodType)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="today">Hoje</SelectItem>
              <SelectItem value="week">Última Semana</SelectItem>
              <SelectItem value="month">Último Mês</SelectItem>
              <SelectItem value="quarter">Último Trimestre</SelectItem>
              <SelectItem value="year">Último Ano</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={handleExport}>
            <Download className="h-4 w-4 mr-2" />
            Exportar
          </Button>
        </div>
      </div>

      {/* Metrics Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              Receita Total
              <DollarSign className="h-4 w-4 text-green-600" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(metrics.totalRevenue)}</div>
            <div className={`flex items-center text-sm mt-1 ${metrics.revenueChange >= 0 ? 'text-green-600' : 'text-red-600'}`}>
              {metrics.revenueChange >= 0 ? <TrendingUp className="h-4 w-4 mr-1" /> : <TrendingDown className="h-4 w-4 mr-1" />}
              {Math.abs(metrics.revenueChange).toFixed(1)}% vs período anterior
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              Despesas Totais
              <CreditCard className="h-4 w-4 text-red-600" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(metrics.totalExpenses)}</div>
            <div className={`flex items-center text-sm mt-1 ${metrics.expensesChange <= 0 ? 'text-green-600' : 'text-red-600'}`}>
              {metrics.expensesChange <= 0 ? <TrendingDown className="h-4 w-4 mr-1" /> : <TrendingUp className="h-4 w-4 mr-1" />}
              {Math.abs(metrics.expensesChange).toFixed(1)}% vs período anterior
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              Lucro Líquido
              <TrendingUp className="h-4 w-4 text-blue-600" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(metrics.netProfit)}</div>
            <div className="text-sm text-muted-foreground mt-1">
              Margem: {metrics.profitMargin.toFixed(1)}%
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              Contratos Ativos
              <FileText className="h-4 w-4 text-purple-600" />
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics.activeContracts}</div>
            <div className="text-sm text-muted-foreground mt-1">
              {metrics.pendingTransactions} transações pendentes
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Receitas vs Despesas</CardTitle>
            <CardDescription>Comparação ao longo do período</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                <Legend />
                <Bar dataKey="receita" fill="#10b981" name="Receita" />
                <Bar dataKey="despesa" fill="#ef4444" name="Despesa" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Lucro Líquido</CardTitle>
            <CardDescription>Evolução do lucro no período</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis />
                <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                <Legend />
                <Line type="monotone" dataKey="lucro" stroke="#3b82f6" strokeWidth={2} name="Lucro" />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Despesas por Categoria</CardTitle>
            <CardDescription>Top 6 categorias</CardDescription>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={categoryData}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {categoryData.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip formatter={(value) => formatCurrency(Number(value))} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Orçamento</CardTitle>
            <CardDescription>Utilização do orçamento total</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium">Orçamento Total</span>
                  <span className="text-sm font-bold">{formatCurrency(metrics.totalBudget)}</span>
                </div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-muted-foreground">Utilizado</span>
                  <span className="text-sm font-semibold">{formatCurrency(metrics.budgetUsed)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Disponível</span>
                  <span className="text-sm font-semibold text-green-600">
                    {formatCurrency(metrics.totalBudget - metrics.budgetUsed)}
                  </span>
                </div>
              </div>
              <div className="h-4 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all"
                  style={{ width: `${Math.min((metrics.budgetUsed / metrics.totalBudget) * 100, 100)}%` }}
                />
              </div>
              <p className="text-sm text-muted-foreground">
                {((metrics.budgetUsed / metrics.totalBudget) * 100).toFixed(1)}% do orçamento utilizado
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
