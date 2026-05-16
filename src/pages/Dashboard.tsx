// Dashboard - Visão Geral do Negócio (Versão Funcional)
import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { 
  TrendingUp, 
  TrendingDown, 
  Users, 
  DollarSign, 
  FileText, 
  Calculator,
  Receipt,
  RefreshCw,
  Bell,
  CheckCircle,
  Clock,
  AlertTriangle
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { toast } from 'sonner';
import { PageLoader } from '@/components/LoadingStates';
import { ROUTE_PATHS } from '@/lib/index';
import { 
  dashboardService, 
  DashboardMetrics, 
  CashFlowData, 
  RecentActivity, 
  Notification 
} from '@/services/dashboardService';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer
} from 'recharts';

export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [metrics, setMetrics] = useState<DashboardMetrics | null>(null);
  const [cashFlow, setCashFlow] = useState<CashFlowData[]>([]);
  const [activities, setActivities] = useState<RecentActivity[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [metricsData, cashFlowData, activitiesData, notificationsData] = await Promise.all([
        dashboardService.getMetrics(),
        dashboardService.getCashFlowData(),
        dashboardService.getRecentActivities(5),
        dashboardService.getNotifications()
      ]);

      setMetrics(metricsData);
      setCashFlow(cashFlowData);
      setActivities(activitiesData);
      setNotifications(notificationsData);
    } catch (error: any) {
      toast.error('Erro ao carregar dados: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRefresh = async () => {
    try {
      setRefreshing(true);
      const data = await dashboardService.refreshData();
      setMetrics(data.metrics);
      setCashFlow(data.cashFlow);
      setActivities(data.activities.slice(0, 5));
      setNotifications(data.notifications);
      toast.success('Dados atualizados!');
    } catch (error: any) {
      toast.error('Erro ao atualizar: ' + error.message);
    } finally {
      setRefreshing(false);
    }
  };

  const handleNovaFatura = () => {
    toast.success('Redirecionando para criar nova fatura...');
    navigate(ROUTE_PATHS.INVOICING);
  };

  const handleNovaTransacao = () => {
    toast.success('Redirecionando para nova transação...');
    navigate(ROUTE_PATHS.FINANCE);
  };

  const handleCalcularPayroll = () => {
    toast.success('Redirecionando para calcular payroll...');
    navigate(ROUTE_PATHS.PAYROLL);
  };

  const handleMarkAsRead = async (notificationId: string) => {
    await dashboardService.markNotificationAsRead(notificationId);
    setNotifications(prev => 
      prev.map(n => n.id === notificationId ? { ...n, read: true } : n)
    );
    toast.success('Notificação marcada como lida');
  };

  const getActivityIcon = (type: string) => {
    switch (type) {
      case 'invoice': return FileText;
      case 'payment': return DollarSign;
      case 'expense': return TrendingDown;
      case 'employee': return Users;
      default: return Receipt;
    }
  };

  const getActivityColor = (status: string) => {
    switch (status) {
      case 'success': return 'text-green-600';
      case 'pending': return 'text-yellow-600';
      case 'failed': return 'text-red-600';
      default: return 'text-gray-600';
    }
  };

  const getNotificationIcon = (type: string) => {
    switch (type) {
      case 'warning': return '⚠️';
      case 'error': return '❌';
      case 'success': return '✅';
      default: return 'ℹ️';
    }
  };

  if (loading) return <Layout><PageLoader message="Carregando dashboard..." /></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Dashboard</h1>
            <p className="text-muted-foreground">Visão geral do seu negócio</p>
          </div>
          <div className="flex gap-3">
            <Button size="sm" variant="outline" onClick={handleRefresh} disabled={refreshing}>
              <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} />
              Atualizar
            </Button>
            <Button size="sm" variant="outline" onClick={handleNovaFatura}>
              <FileText className="h-4 w-4 mr-2" />
              Nova Fatura
            </Button>
            <Button size="sm" variant="outline" onClick={handleNovaTransacao}>
              <DollarSign className="h-4 w-4 mr-2" />
              Nova Transação
            </Button>
            <Button size="sm" onClick={handleCalcularPayroll}>
              <Calculator className="h-4 w-4 mr-2" />
              Calcular Payroll
            </Button>
          </div>
        </div>

        {/* KPI Cards */}
        {metrics && (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
                  <DollarSign className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{dashboardService.formatCurrency(metrics.totalRevenue)}</div>
                  <div className="flex items-center text-xs text-green-600">
                    <TrendingUp className="mr-1 h-3 w-3" />
                    {dashboardService.formatPercentage(metrics.revenueChange)} vs mês anterior
                  </div>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Despesas</CardTitle>
                  <TrendingDown className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{dashboardService.formatCurrency(metrics.totalExpenses)}</div>
                  <div className="flex items-center text-xs text-green-600">
                    <TrendingDown className="mr-1 h-3 w-3" />
                    {dashboardService.formatPercentage(metrics.expensesChange)} vs mês anterior
                  </div>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Margem de Lucro</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{metrics.profitMargin.toFixed(1)}%</div>
                  <p className="text-xs text-muted-foreground">
                    Lucro: {dashboardService.formatCurrency(metrics.netProfit)}
                  </p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Faturas Pendentes</CardTitle>
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{metrics.pendingInvoices}</div>
                  <p className="text-xs text-muted-foreground">
                    {metrics.activeEmployees} funcionários ativos
                  </p>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}

        {/* Charts */}
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Fluxo de Caixa (6 meses)</CardTitle>
              <CardDescription>Receitas vs Despesas</CardDescription>
            </CardHeader>
            <CardContent>
              {cashFlow.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={cashFlow}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" />
                    <YAxis />
                    <Tooltip formatter={(value: number) => dashboardService.formatCurrency(value)} />
                    <Legend />
                    <Bar dataKey="receita" fill="#10b981" name="Receita" />
                    <Bar dataKey="despesa" fill="#ef4444" name="Despesa" />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-[300px] items-center justify-center text-muted-foreground">
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
              {cashFlow.length > 0 ? (
                <ResponsiveContainer width="100%" height={300}>
                  <LineChart data={cashFlow}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" />
                    <YAxis />
                    <Tooltip formatter={(value: number) => dashboardService.formatCurrency(value)} />
                    <Legend />
                    <Line type="monotone" dataKey="receita" stroke="#10b981" strokeWidth={2} name="Receita" />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                  Sem dados disponíveis
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Activities and Notifications */}
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Atividade Recente</CardTitle>
              <CardDescription>Últimas ações no sistema</CardDescription>
            </CardHeader>
            <CardContent>
              {activities.length > 0 ? (
                <div className="space-y-4">
                  {activities.map((activity, index) => {
                    const Icon = getActivityIcon(activity.type);
                    return (
                      <div key={activity.id}>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-4">
                            <div className={`flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10`}>
                              <Icon className="h-5 w-5 text-primary" />
                            </div>
                            <div>
                              <p className="font-medium">{activity.description}</p>
                              <p className="text-sm text-muted-foreground">
                                {dashboardService.formatDate(activity.date)}
                              </p>
                            </div>
                          </div>
                          {activity.amount && (
                            <div className="text-right">
                              <p className={`font-semibold ${getActivityColor(activity.status)}`}>
                                {dashboardService.formatCurrency(activity.amount)}
                              </p>
                            </div>
                          )}
                        </div>
                        {index < activities.length - 1 && <Separator className="mt-4" />}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                  Nenhuma atividade recente
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Notificações</CardTitle>
              <CardDescription>Alertas e avisos importantes</CardDescription>
            </CardHeader>
            <CardContent>
              {notifications.length > 0 ? (
                <div className="space-y-3">
                  {notifications.slice(0, 4).map((notification) => (
                    <div
                      key={notification.id}
                      className={`rounded-lg border p-3 ${
                        notification.type === 'error'
                          ? 'border-red-200 bg-red-50'
                          : notification.type === 'warning'
                          ? 'border-yellow-200 bg-yellow-50'
                          : notification.type === 'success'
                          ? 'border-green-200 bg-green-50'
                          : 'border-blue-200 bg-blue-50'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <span className="text-lg">{getNotificationIcon(notification.type)}</span>
                        <div className="flex-1 space-y-1">
                          <p className="text-sm font-medium">{notification.title}</p>
                          <p className="text-xs text-muted-foreground">{notification.message}</p>
                          <p className="text-xs text-muted-foreground">
                            {dashboardService.formatDate(notification.date)}
                          </p>
                          {!notification.read && (
                            <Button
                              size="sm"
                              variant="ghost"
                              className="h-6 text-xs"
                              onClick={() => handleMarkAsRead(notification.id)}
                            >
                              Marcar como lida
                            </Button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                  Nenhuma notificação
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </Layout>
  );
}
