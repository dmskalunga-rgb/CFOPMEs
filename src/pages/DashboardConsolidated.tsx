// Dashboard Consolidado - Real Supabase Integration
import { useState, useEffect, useMemo } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  Users,
  Package,
  ShoppingCart,
  AlertTriangle,
  ArrowRight,
  Calendar,
  Download,
} from 'lucide-react';
import { toast } from 'sonner';
import { productsService, type Product } from '@/services/supabaseServices';
import { customersService, type Customer } from '@/services/customersServiceReal';
import { transactionsService, type Transaction } from '@/services/transactionsServiceReal';
import { PageSkeleton } from '@/components/ui/skeletons';
import { ErrorState } from '@/components/ui/states';
import { ExportButton } from '@/lib/export';
import {
  CustomLineChart,
  CustomPieChart,
  CustomBarChart,
  CustomAreaChart,
  StatCard,
} from '@/components/ui/charts';
import { useNavigate } from 'react-router-dom';

interface DashboardData {
  products: Product[];
  customers: Customer[];
  transactions: Transaction[];
}

export default function DashboardConsolidated() {
  const navigate = useNavigate();
  const [data, setData] = useState<DashboardData>({
    products: [],
    customers: [],
    transactions: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [period, setPeriod] = useState<'7d' | '30d' | '90d' | '1y'>('30d');

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);

      const [products, customers, transactions] = await Promise.all([
        productsService.getAll(),
        customersService.getAll(),
        transactionsService.getAll(),
      ]);

      setData({ products, customers, transactions });
    } catch (err) {
      setError(err as Error);
      toast.error('Erro ao carregar dados do dashboard');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // Filter transactions by period
  const filteredTransactions = useMemo(() => {
    const now = new Date();
    const periodDays = {
      '7d': 7,
      '30d': 30,
      '90d': 90,
      '1y': 365,
    };
    const days = periodDays[period];
    const startDate = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);

    return data.transactions.filter(
      (t) => new Date(t.transaction_date) >= startDate && t.status === 'completed'
    );
  }, [data.transactions, period]);

  // Calculate stats
  const stats = useMemo(() => {
    const completedTransactions = filteredTransactions;
    const totalIncome = completedTransactions
      .filter((t) => t.type === 'income')
      .reduce((sum, t) => sum + t.amount, 0);
    const totalExpense = completedTransactions
      .filter((t) => t.type === 'expense')
      .reduce((sum, t) => sum + t.amount, 0);
    const balance = totalIncome - totalExpense;

    const totalProducts = data.products.length;
    const activeProducts = data.products.filter((p) => p.product_status === 'active').length;
    const lowStockProducts = data.products.filter((p) => p.stock <= p.min_stock).length;
    const totalStockValue = data.products.reduce((sum, p) => sum + p.price * p.stock, 0);

    const totalCustomers = data.customers.length;
    const activeCustomers = data.customers.filter((c) => c.customer_status === 'active').length;
    const totalCustomerValue = data.customers.reduce((sum, c) => sum + c.total_purchases, 0);
    const avgCustomerValue = totalCustomers > 0 ? totalCustomerValue / totalCustomers : 0;

    return {
      totalIncome,
      totalExpense,
      balance,
      totalProducts,
      activeProducts,
      lowStockProducts,
      totalStockValue,
      totalCustomers,
      activeCustomers,
      totalCustomerValue,
      avgCustomerValue,
      transactionCount: completedTransactions.length,
    };
  }, [data, filteredTransactions]);

  // Chart data - Monthly income vs expense
  const monthlyFinanceData = useMemo(() => {
    const months: Record<string, { income: number; expense: number }> = {};

    filteredTransactions.forEach((transaction) => {
      const date = new Date(transaction.transaction_date);
      const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;

      if (!months[monthKey]) {
        months[monthKey] = { income: 0, expense: 0 };
      }

      if (transaction.type === 'income') {
        months[monthKey].income += transaction.amount;
      } else {
        months[monthKey].expense += transaction.amount;
      }
    });

    return Object.entries(months)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-6)
      .map(([month, data]) => ({
        name: new Date(month + '-01').toLocaleDateString('pt-AO', {
          month: 'short',
          year: '2-digit',
        }),
        receita: data.income,
        despesa: data.expense,
        saldo: data.income - data.expense,
      }));
  }, [filteredTransactions]);

  // Chart data - Products by category
  const productsByCategoryData = useMemo(() => {
    const categories: Record<string, number> = {};

    data.products.forEach((product) => {
      categories[product.category] = (categories[product.category] || 0) + 1;
    });

    return Object.entries(categories)
      .map(([name, value]) => ({ name, value: value as number }))
      .sort((a, b) => b.value - a.value);
  }, [data.products]);

  // Chart data - Top 10 customers
  const topCustomersData = useMemo(() => {
    return data.customers
      .sort((a, b) => b.total_purchases - a.total_purchases)
      .slice(0, 10)
      .map((c) => ({
        name: c.name.length > 20 ? c.name.substring(0, 20) + '...' : c.name,
        valor: c.total_purchases,
      }));
  }, [data.customers]);

  // Chart data - Low stock products
  const lowStockData = useMemo(() => {
    return data.products
      .filter((p) => p.stock <= p.min_stock)
      .sort((a, b) => (a.stock / a.min_stock) - (b.stock / b.min_stock))
      .slice(0, 10)
      .map((p) => ({
        name: p.name.length > 20 ? p.name.substring(0, 20) + '...' : p.name,
        estoque: p.stock,
        minimo: p.min_stock,
      }));
  }, [data.products]);

  // Chart data - Transaction distribution
  const transactionDistributionData = useMemo(() => {
    const income = filteredTransactions.filter((t) => t.type === 'income').length;
    const expense = filteredTransactions.filter((t) => t.type === 'expense').length;

    return [
      { name: 'Receitas', value: income },
      { name: 'Despesas', value: expense },
    ];
  }, [filteredTransactions]);

  // Recent transactions
  const recentTransactions = useMemo(() => {
    return [...filteredTransactions]
      .sort((a, b) => new Date(b.transaction_date).getTime() - new Date(a.transaction_date).getTime())
      .slice(0, 5);
  }, [filteredTransactions]);

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA',
      minimumFractionDigits: 0,
    }).format(value);

  // Calculate trends (mock - would need historical data)
  const incomeTrend = 12.5;
  const expenseTrend = -5.2;
  const customerTrend = 8.3;
  const productTrend = 3.1;

  // Export data
  const exportData = useMemo(() => {
    return filteredTransactions.map((t) => ({
      data: t.transaction_date,
      tipo: t.type === 'income' ? 'Receita' : 'Despesa',
      categoria: t.category,
      descricao: t.description,
      valor: t.amount,
      metodo: t.payment_method,
      status: t.status,
    }));
  }, [filteredTransactions]);

  if (loading) return <Layout><PageSkeleton /></Layout>;
  if (error) return <Layout><ErrorState error={error} onRetry={loadData} /></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold">Dashboard Consolidado</h1>
            <p className="text-muted-foreground">Visão geral do seu negócio</p>
          </div>
          <div className="flex gap-2 items-center">
            <Select value={period} onValueChange={(value: any) => setPeriod(value)}>
              <SelectTrigger className="w-40">
                <Calendar className="h-4 w-4 mr-2" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7d">Últimos 7 dias</SelectItem>
                <SelectItem value="30d">Últimos 30 dias</SelectItem>
                <SelectItem value="90d">Últimos 90 dias</SelectItem>
                <SelectItem value="1y">Último ano</SelectItem>
              </SelectContent>
            </Select>
            <ExportButton
              data={exportData}
              filename={`dashboard_${period}`}
              title="Relatório do Dashboard"
              columns={[
                { key: 'data', label: 'Data' },
                { key: 'tipo', label: 'Tipo' },
                { key: 'categoria', label: 'Categoria' },
                { key: 'descricao', label: 'Descrição' },
                { key: 'valor', label: 'Valor' },
              ]}
            />
          </div>
        </div>

        {/* Main Stats Cards */}
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <StatCard
            title="Receitas"
            value={formatCurrency(stats.totalIncome)}
            change={incomeTrend}
            changeLabel="vs período anterior"
            icon={<TrendingUp className="h-4 w-4 text-green-600" />}
          />

          <StatCard
            title="Despesas"
            value={formatCurrency(stats.totalExpense)}
            change={expenseTrend}
            changeLabel="vs período anterior"
            icon={<TrendingDown className="h-4 w-4 text-red-600" />}
          />

          <StatCard
            title="Saldo"
            value={formatCurrency(stats.balance)}
            icon={
              stats.balance >= 0 ? (
                <DollarSign className="h-4 w-4 text-green-600" />
              ) : (
                <DollarSign className="h-4 w-4 text-red-600" />
              )
            }
            description={stats.balance >= 0 ? 'Positivo' : 'Negativo'}
          />

          <StatCard
            title="Clientes"
            value={stats.totalCustomers}
            change={customerTrend}
            changeLabel="novos clientes"
            icon={<Users className="h-4 w-4 text-blue-600" />}
            description={`${stats.activeCustomers} ativos`}
          />

          <StatCard
            title="Produtos"
            value={stats.totalProducts}
            change={productTrend}
            changeLabel="novos produtos"
            icon={<Package className="h-4 w-4 text-purple-600" />}
            description={`${stats.activeProducts} ativos`}
          />

          <StatCard
            title="Estoque Baixo"
            value={stats.lowStockProducts}
            icon={<AlertTriangle className="h-4 w-4 text-yellow-600" />}
            description="Produtos abaixo do mínimo"
          />
        </div>

        {/* Secondary Stats */}
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Valor em Estoque</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(stats.totalStockValue)}</div>
              <p className="text-xs text-muted-foreground">Total de produtos em estoque</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Ticket Médio</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(stats.avgCustomerValue)}</div>
              <p className="text-xs text-muted-foreground">Por cliente</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Transações</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.transactionCount}</div>
              <p className="text-xs text-muted-foreground">No período selecionado</p>
            </CardContent>
          </Card>
        </div>

        {/* Main Charts */}
        <div className="grid gap-4 md:grid-cols-2">
          <CustomLineChart
            data={monthlyFinanceData}
            lines={[
              { dataKey: 'receita', stroke: '#10b981', name: 'Receita' },
              { dataKey: 'despesa', stroke: '#ef4444', name: 'Despesa' },
              { dataKey: 'saldo', stroke: '#2563eb', name: 'Saldo' },
            ]}
            title="Fluxo de Caixa"
            description="Últimos 6 meses"
            height={300}
          />

          <CustomPieChart
            data={productsByCategoryData}
            title="Produtos por Categoria"
            description="Distribuição do inventário"
            height={300}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <CustomBarChart
            data={topCustomersData}
            bars={[{ dataKey: 'valor', fill: '#2563eb', name: 'Valor Total' }]}
            title="Top 10 Clientes"
            description="Maiores compradores"
            height={300}
          />

          <CustomAreaChart
            data={monthlyFinanceData}
            areas={[{ dataKey: 'saldo', fill: '#2563eb', stroke: '#1d4ed8', name: 'Saldo' }]}
            title="Evolução do Saldo"
            description="Últimos 6 meses"
            height={300}
          />
        </div>

        {/* Additional Charts */}
        {lowStockData.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2">
            <CustomBarChart
              data={lowStockData}
              bars={[
                { dataKey: 'estoque', fill: '#ef4444', name: 'Estoque Atual' },
                { dataKey: 'minimo', fill: '#f59e0b', name: 'Estoque Mínimo' },
              ]}
              title="Produtos com Estoque Baixo"
              description="Atenção necessária"
              height={300}
            />

            <CustomPieChart
              data={transactionDistributionData}
              title="Distribuição de Transações"
              description="Receitas vs Despesas"
              height={300}
              colors={['#10b981', '#ef4444']}
            />
          </div>
        )}

        {/* Recent Transactions */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Transações Recentes</CardTitle>
              <CardDescription>Últimas 5 transações</CardDescription>
            </div>
            <Button variant="outline" size="sm" onClick={() => navigate('/finance-real')}>
              Ver Todas
              <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          </CardHeader>
          <CardContent>
            {recentTransactions.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                Nenhuma transação no período selecionado
              </p>
            ) : (
              <div className="space-y-3">
                {recentTransactions.map((transaction) => (
                  <div
                    key={transaction.id}
                    className="flex items-center justify-between border-b pb-3 last:border-0"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`flex h-10 w-10 items-center justify-center rounded-lg ${
                          transaction.type === 'income' ? 'bg-green-100' : 'bg-red-100'
                        }`}
                      >
                        {transaction.type === 'income' ? (
                          <TrendingUp className="h-5 w-5 text-green-600" />
                        ) : (
                          <TrendingDown className="h-5 w-5 text-red-600" />
                        )}
                      </div>
                      <div>
                        <p className="font-medium text-sm">{transaction.description}</p>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span>{transaction.category}</span>
                          <span>•</span>
                          <span>
                            {new Date(transaction.transaction_date).toLocaleDateString('pt-AO')}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <p
                        className={`font-bold ${
                          transaction.type === 'income' ? 'text-green-600' : 'text-red-600'
                        }`}
                      >
                        {transaction.type === 'income' ? '+' : '-'}
                        {formatCurrency(transaction.amount)}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Quick Links */}
        <div className="grid gap-4 md:grid-cols-3">
          <Card className="cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => navigate('/products-real')}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Package className="h-5 w-5" />
                Produtos
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Gerencie seu inventário de {stats.totalProducts} produtos
              </p>
              <Button variant="link" className="px-0 mt-2">
                Ir para Produtos
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            </CardContent>
          </Card>

          <Card className="cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => navigate('/customers-real')}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users className="h-5 w-5" />
                Clientes
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Gerencie sua base de {stats.totalCustomers} clientes
              </p>
              <Button variant="link" className="px-0 mt-2">
                Ir para Clientes
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            </CardContent>
          </Card>

          <Card className="cursor-pointer hover:bg-muted/50 transition-colors" onClick={() => navigate('/finance-real')}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <DollarSign className="h-5 w-5" />
                Finanças
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Gerencie suas {stats.transactionCount} transações
              </p>
              <Button variant="link" className="px-0 mt-2">
                Ir para Finanças
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </Layout>
  );
}
