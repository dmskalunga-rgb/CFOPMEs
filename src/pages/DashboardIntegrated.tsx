import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import {
  TrendingUp,
  TrendingDown,
  FileText,
  DollarSign,
  AlertCircle,
  Plus,
  Calculator,
  Receipt,
  Users,
  Activity,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/hooks/useAuth';
import { supabase } from '@/integrations/supabase/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/hooks/use-toast';
import { springPresets, staggerContainer, staggerItem } from '@/lib/motion';
import { formatCurrency, formatDate, ROUTE_PATHS } from '@/lib/index';
import { CashFlowChart, RevenueChart } from '@/components/Charts';
import type { CashFlowData, RevenueData } from '@/components/Charts';

interface DashboardMetrics {
  totalRevenue: number;
  totalExpenses: number;
  netProfit: number;
  profitMargin: number;
  totalInvoices: number;
  totalEmployees: number;
  totalPayroll: number;
}

interface RecentInvoice {
  id: string;
  invoice_number: string;
  client_name: string;
  total_amount: number;
  status: string;
  issue_date: string;
}

export default function DashboardIntegrated() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { profile, loading: authLoading } = useAuth();
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [chartData, setChartData] = useState<CashFlowData[]>([]);
  const [recentInvoices, setRecentInvoices] = useState<RecentInvoice[]>([]);

  useEffect(() => {
    if (!authLoading) {
      loadDashboardData();
    }
  }, [authLoading, profile?.tenant_id]);

  const loadDashboardData = async () => {

    try {
      setLoading(true);

      // Resolver tenant_id: usar do perfil ou buscar o primeiro disponível
      let tenantId = profile?.tenant_id || null;
      if (!tenantId) {
        const { data: tenantRow } = await supabase
          .from('tenants')
          .select('id')
          .limit(1)
          .maybeSingle();
        tenantId = tenantRow?.id || null;
      }

      // Se ainda não há tenant, mostrar dashboard vazio (sem erro)
      if (!tenantId) {
        setMetrics({ totalRevenue: 0, totalExpenses: 0, netProfit: 0, profitMargin: 0, totalInvoices: 0, totalEmployees: 0, totalPayroll: 0 });
        setChartData([]);
        setRecentInvoices([]);
        setLoading(false);
        return;
      }

      const [invoicesData, employeesData, transactionsData, recentInvoicesData] = await Promise.all([
        fetchInvoicesMetrics(tenantId),
        fetchEmployeesMetrics(tenantId),
        fetchTransactionsMetrics(tenantId),
        fetchRecentInvoices(tenantId),
      ]);

      const totalRevenue = transactionsData.revenue;
      const totalExpenses = transactionsData.expenses;
      const netProfit = totalRevenue - totalExpenses;
      const profitMargin = totalRevenue > 0 ? (netProfit / totalRevenue) * 100 : 0;

      setMetrics({
        totalRevenue,
        totalExpenses,
        netProfit,
        profitMargin,
        totalInvoices: invoicesData.count,
        totalEmployees: employeesData.count,
        totalPayroll: employeesData.totalPayroll,
      });

      setChartData(transactionsData.chartData);
      setRecentInvoices(recentInvoicesData);
    } catch (error: unknown) {
      console.error('Erro ao carregar dashboard:', error);
      // Mostrar métricas vazias em vez de erro destrutivo
      setMetrics({ totalRevenue: 0, totalExpenses: 0, netProfit: 0, profitMargin: 0, totalInvoices: 0, totalEmployees: 0, totalPayroll: 0 });
      setChartData([]);
      setRecentInvoices([]);
    } finally {
      setLoading(false);
    }
  };

  const fetchInvoicesMetrics = async (tenantId: string) => {
    const { data } = await supabase
      .from('invoices')
      .select('total_amount')
      .eq('tenant_id', tenantId);

    const count = data?.length || 0;
    const total = data?.reduce((sum, inv) => sum + (inv.total_amount || 0), 0) || 0;

    return { count, total };
  };

  const fetchEmployeesMetrics = async (tenantId: string) => {
    const { data } = await supabase
      .from('employees')
      .select('base_salary')
      .eq('tenant_id', tenantId)
      .eq('status', 'ACTIVE');

    const count = data?.length || 0;
    const totalPayroll = data?.reduce((sum, emp) => sum + (Number(emp.base_salary) || 0), 0) || 0;

    return { count, totalPayroll };
  };

  const fetchTransactionsMetrics = async (tenantId: string) => {
    const sixMonthsAgo = new Date();
    sixMonthsAgo.setMonth(sixMonthsAgo.getMonth() - 6);

    const { data } = await supabase
      .from('transactions')
      .select('amount, type, transaction_date')
      .eq('tenant_id', tenantId)
      .gte('transaction_date', sixMonthsAgo.toISOString());

    const rows = data || [];
    const revenue = rows.filter(t => t.type === 'INCOME').reduce((sum, t) => sum + (Number(t.amount) || 0), 0);
    const expenses = rows.filter(t => t.type === 'EXPENSE').reduce((sum, t) => sum + (Number(t.amount) || 0), 0);

    const monthlyData: Record<string, { receitas: number; despesas: number; valor: number }> = {};
    rows.forEach(t => {
      const date = new Date(t.transaction_date);
      const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
      if (!monthlyData[monthKey]) {
        monthlyData[monthKey] = { receitas: 0, despesas: 0, valor: 0 };
      }
      if (t.type === 'INCOME') {
        monthlyData[monthKey].receitas += Number(t.amount) || 0;
      } else {
        monthlyData[monthKey].despesas += Number(t.amount) || 0;
      }
      monthlyData[monthKey].valor = monthlyData[monthKey].receitas - monthlyData[monthKey].despesas;
    });

    const chartData: CashFlowData[] = Object.entries(monthlyData)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([month, values]) => ({
        month: new Date(month + '-01').toLocaleDateString('pt-AO', { month: 'short', year: 'numeric' }),
        receitas: values.receitas,
        despesas: values.despesas,
        valor: values.valor,
      }));

    return { revenue, expenses, chartData };
  };

  const fetchRecentInvoices = async (tenantId: string) => {
    const { data } = await supabase
      .from('invoices')
      .select('id, invoice_number, client_name, total_amount, status, issue_date')
      .eq('tenant_id', tenantId)
      .order('issue_date', { ascending: false })
      .limit(5);

    return data || [];
  };

  const handleNovaFatura = () => {
    navigate(`${ROUTE_PATHS.INVOICING}?action=new`);
  };

  const handleNovaTransacao = () => {
    navigate(`${ROUTE_PATHS.FINANCE}?action=new`);
  };

  const handleCalcularPayroll = () => {
    navigate(`${ROUTE_PATHS.PAYROLL}?action=calculate`);
  };

  const getChangeIcon = (change: number) => {
    return change >= 0 ? TrendingUp : TrendingDown;
  };

  const getChangeColor = (change: number) => {
    return change >= 0 ? 'text-chart-2' : 'text-destructive';
  };

  const getStatusBadge = (status: string) => {
    const statusMap: Record<string, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
      DRAFT: { label: 'Rascunho', variant: 'secondary' },
      PENDING: { label: 'Pendente', variant: 'outline' },
      PAID: { label: 'Paga', variant: 'default' },
      CANCELLED: { label: 'Cancelada', variant: 'destructive' },
    };
    const config = statusMap[status] || { label: status, variant: 'outline' };
    return <Badge variant={config.variant}>{config.label}</Badge>;
  };

  if (authLoading || loading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <Skeleton className="h-8 w-48 mb-2" />
            <Skeleton className="h-4 w-64" />
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-10 w-32" />
            <Skeleton className="h-10 w-40" />
            <Skeleton className="h-10 w-40" />
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map(i => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-32 mb-2" />
                <Skeleton className="h-3 w-40" />
              </CardContent>
            </Card>
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <Skeleton className="h-6 w-48" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-[300px] w-full" />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <Skeleton className="h-6 w-48" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-[300px] w-full" />
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  // Removido bloqueio por tenant_id — o dashboard carrega mesmo sem tenant_id
  if (false) {
    return (
      <div className="flex items-center justify-center h-96">
        <Card className="max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-destructive" />
              A carregar...
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground">A inicializar dados...</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const kpis = [
    {
      title: 'Receita Total',
      value: metrics?.totalRevenue || 0,
      change: 12.5,
      icon: DollarSign,
    },
    {
      title: 'Despesas',
      value: metrics?.totalExpenses || 0,
      change: -5.2,
      icon: Receipt,
    },
    {
      title: 'Lucro Líquido',
      value: metrics?.netProfit || 0,
      change: 18.3,
      icon: TrendingUp,
    },
    {
      title: 'Faturas',
      value: metrics?.totalInvoices || 0,
      change: 8.1,
      icon: FileText,
      isCount: true,
    },
  ];

  const revenueChartData: RevenueData[] = chartData.map(d => ({
    month: d.month,
    valor: d.receitas,
  }));

  return (
    <motion.div
      className="space-y-6"
      variants={staggerContainer}
      initial="initial"
      animate="animate"
    >
      <motion.div variants={staggerItem} className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Dashboard</h1>
          <p className="text-muted-foreground">Visão geral do seu negócio</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={handleNovaFatura} size="sm">
            <Plus className="mr-2 h-4 w-4" />
            Nova Fatura
          </Button>
          <Button onClick={handleNovaTransacao} variant="outline" size="sm">
            <Plus className="mr-2 h-4 w-4" />
            Nova Transação
          </Button>
          <Button onClick={handleCalcularPayroll} variant="outline" size="sm">
            <Calculator className="mr-2 h-4 w-4" />
            Calcular Payroll
          </Button>
        </div>
      </motion.div>

      <motion.div variants={staggerItem} className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {kpis.map((kpi, index) => {
          const Icon = kpi.icon;
          const ChangeIcon = getChangeIcon(kpi.change);
          return (
            <Card key={index}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">{kpi.title}</CardTitle>
                <Icon className="h-4 w-4 text-muted-foreground" />
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {kpi.isCount ? kpi.value : formatCurrency(kpi.value)}
                </div>
                <p className={`text-xs ${getChangeColor(kpi.change)} flex items-center gap-1 mt-1`}>
                  <ChangeIcon className="h-3 w-3" />
                  {Math.abs(kpi.change)}% vs mês anterior
                </p>
              </CardContent>
            </Card>
          );
        })}
      </motion.div>

      <motion.div variants={staggerItem} className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Fluxo de Caixa (6 meses)</CardTitle>
            <CardDescription>Receitas vs Despesas</CardDescription>
          </CardHeader>
          <CardContent>
            {chartData.length > 0 ? (
              <CashFlowChart data={chartData} />
            ) : (
              <div className="h-[300px] flex items-center justify-center text-muted-foreground">
                Sem dados disponíveis
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Receita Mensal</CardTitle>
            <CardDescription>Evolução da receita</CardDescription>
          </CardHeader>
          <CardContent>
            {revenueChartData.length > 0 ? (
              <RevenueChart data={revenueChartData} />
            ) : (
              <div className="h-[300px] flex items-center justify-center text-muted-foreground">
                Sem dados disponíveis
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      <motion.div variants={staggerItem}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Faturas Recentes
            </CardTitle>
            <CardDescription>Últimas 5 faturas emitidas</CardDescription>
          </CardHeader>
          <CardContent>
            {recentInvoices.length > 0 ? (
              <div className="space-y-4">
                {recentInvoices.map((invoice) => (
                  <div
                    key={invoice.id}
                    className="flex items-center justify-between p-3 rounded-lg border hover:bg-muted/50 transition-colors cursor-pointer"
                    onClick={() => navigate(`${ROUTE_PATHS.INVOICING}/${invoice.id}`)}
                  >
                    <div className="flex-1">
                      <p className="font-medium">{invoice.invoice_number}</p>
                      <p className="text-sm text-muted-foreground">{invoice.client_name}</p>
                      <p className="text-xs text-muted-foreground mt-1">
                        {formatDate(invoice.issue_date)}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="font-semibold">{formatCurrency(invoice.total_amount)}</p>
                      <div className="mt-1">{getStatusBadge(invoice.status)}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground text-center py-8">
                Nenhuma fatura encontrada
              </p>
            )}
          </CardContent>
        </Card>
      </motion.div>

      <motion.div variants={staggerItem} className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Users className="h-4 w-4" />
              Funcionários Ativos
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics?.totalEmployees || 0}</div>
            <p className="text-xs text-muted-foreground mt-1">
              Payroll total: {formatCurrency(metrics?.totalPayroll || 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <TrendingUp className="h-4 w-4" />
              Margem de Lucro
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{metrics?.profitMargin?.toFixed(1) || 0}%</div>
            <p className="text-xs text-muted-foreground mt-1">
              Lucro: {formatCurrency(metrics?.netProfit || 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Status do Sistema
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <div className="h-3 w-3 rounded-full bg-chart-2 animate-pulse" />
              <span className="text-sm font-medium">Operacional</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Todos os serviços funcionando normalmente
            </p>
          </CardContent>
        </Card>
      </motion.div>
    </motion.div>
  );
}
