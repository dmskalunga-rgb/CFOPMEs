// Metrics Complete Page - Versão Completa e Funcional
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { TrendingUp, Users, DollarSign, Activity, BarChart3, ArrowUp, ArrowDown, ShoppingCart, FileText, Target, RefreshCw } from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion } from 'framer-motion';
import { metricsService, GeneralMetrics, KPI, ProductMetric, CustomerMetric } from '@/services/metricsService';

export default function MetricsComplete() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const [generalMetrics, setGeneralMetrics] = useState<GeneralMetrics | null>(null);
  const [kpis, setKPIs] = useState<KPI[]>([]);
  const [topProducts, setTopProducts] = useState<ProductMetric[]>([]);
  const [topCustomers, setTopCustomers] = useState<CustomerMetric[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [metricsData, kpisData, productsData, customersData] = await Promise.all([
        metricsService.getGeneralMetrics(),
        metricsService.getKPIs(),
        metricsService.getTopProducts(10),
        metricsService.getTopCustomers(10)
      ]);

      setGeneralMetrics(metricsData);
      setKPIs(kpisData);
      setTopProducts(productsData);
      setTopCustomers(customersData);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar métricas',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setLoading(false);
    }
  };

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Métricas Completas</h1>
            <p className="text-muted-foreground">Dashboard completo de métricas e analytics do negócio</p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Atualizar
          </Button>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="overview">Visão Geral</TabsTrigger>
            <TabsTrigger value="kpis">KPIs</TabsTrigger>
            <TabsTrigger value="products">Produtos</TabsTrigger>
            <TabsTrigger value="customers">Clientes</TabsTrigger>
          </TabsList>

          {/* OVERVIEW TAB */}
          <TabsContent value="overview" className="space-y-4">
            {generalMetrics && (
              <>
                <div className="grid gap-4 md:grid-cols-3">
                  <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
                    <Card>
                      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
                        <DollarSign className="h-4 w-4 text-muted-foreground" />
                      </CardHeader>
                      <CardContent>
                        <div className="text-2xl font-bold">{metricsService.formatCurrency(generalMetrics.revenue.total)}</div>
                        <div className="flex items-center text-xs text-muted-foreground">
                          {generalMetrics.revenue.trend === 'up' ? (
                            <ArrowUp className="mr-1 h-3 w-3 text-green-600" />
                          ) : (
                            <ArrowDown className="mr-1 h-3 w-3 text-red-600" />
                          )}
                          <span className={generalMetrics.revenue.trend === 'up' ? 'text-green-600' : 'text-red-600'}>
                            {generalMetrics.revenue.growth}%
                          </span>
                          <span className="ml-1">vs mês anterior</span>
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>

                  <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
                    <Card>
                      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Total de Clientes</CardTitle>
                        <Users className="h-4 w-4 text-muted-foreground" />
                      </CardHeader>
                      <CardContent>
                        <div className="text-2xl font-bold">{generalMetrics.customers.total}</div>
                        <p className="text-xs text-muted-foreground">
                          {generalMetrics.customers.new} novos • {generalMetrics.customers.active} ativos
                        </p>
                      </CardContent>
                    </Card>
                  </motion.div>

                  <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
                    <Card>
                      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Total de Vendas</CardTitle>
                        <ShoppingCart className="h-4 w-4 text-muted-foreground" />
                      </CardHeader>
                      <CardContent>
                        <div className="text-2xl font-bold">{metricsService.formatNumber(generalMetrics.sales.total)}</div>
                        <div className="flex items-center text-xs text-muted-foreground">
                          <ArrowUp className="mr-1 h-3 w-3 text-green-600" />
                          <span className="text-green-600">{generalMetrics.sales.growth}%</span>
                          <span className="ml-1">vs mês anterior</span>
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                </div>

                <div className="grid gap-4 md:grid-cols-2">
                  <Card>
                    <CardHeader>
                      <CardTitle>Métricas de Vendas</CardTitle>
                      <CardDescription>Performance de vendas</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Ticket Médio</span>
                        <span className="font-bold">{metricsService.formatCurrency(generalMetrics.sales.avgTicket)}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Taxa de Conversão</span>
                        <span className="font-bold">{generalMetrics.sales.conversion}%</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Crescimento</span>
                        <Badge variant="outline" className="bg-green-50 text-green-700">
                          +{generalMetrics.sales.growth}%
                        </Badge>
                      </div>
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle>Métricas de Clientes</CardTitle>
                      <CardDescription>Análise de base de clientes</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Novos Clientes</span>
                        <span className="font-bold">{generalMetrics.customers.new}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Clientes Ativos</span>
                        <span className="font-bold">{generalMetrics.customers.active}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Churn</span>
                        <Badge variant="outline" className="bg-red-50 text-red-700">
                          {generalMetrics.customers.churn}
                        </Badge>
                      </div>
                    </CardContent>
                  </Card>
                </div>
              </>
            )}
          </TabsContent>

          {/* KPIS TAB */}
          <TabsContent value="kpis" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Indicadores-Chave de Performance (KPIs)</CardTitle>
                <CardDescription>Principais métricas do negócio</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-2">
                  {kpis.map((kpi, index) => (
                    <motion.div
                      key={kpi.id}
                      initial={{ opacity: 0, scale: 0.95 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ delay: index * 0.1 }}
                      className="border rounded-lg p-4"
                    >
                      <div className="flex items-start justify-between mb-2">
                        <div>
                          <p className="text-sm text-muted-foreground">{kpi.kpi_name}</p>
                          <p className="text-2xl font-bold">{kpi.kpi_value}</p>
                        </div>
                        {kpi.trend === 'up' ? (
                          <ArrowUp className="h-5 w-5 text-green-600" />
                        ) : (
                          <ArrowDown className="h-5 w-5 text-red-600" />
                        )}
                      </div>
                      <div className="flex items-center justify-between text-sm">
                        {kpi.kpi_change && (
                          <span className={kpi.trend === 'up' ? 'text-green-600' : 'text-red-600'}>
                            {kpi.kpi_change}
                          </span>
                        )}
                        {kpi.target_value && (
                          <span className="text-muted-foreground">Meta: {kpi.target_value}</span>
                        )}
                      </div>
                    </motion.div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* PRODUCTS TAB */}
          <TabsContent value="products" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Top Produtos</CardTitle>
                <CardDescription>Produtos com melhor performance</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {topProducts.map((product, index) => (
                    <div key={index} className="flex items-center justify-between border-b pb-4 last:border-0">
                      <div className="flex-1">
                        <p className="font-medium">{product.product_name}</p>
                        <p className="text-sm text-muted-foreground">{product.sales} vendas</p>
                      </div>
                      <div className="text-right">
                        <p className="font-bold">{metricsService.formatCurrency(product.revenue)}</p>
                        <div className="flex items-center justify-end text-sm">
                          {product.growth > 0 ? (
                            <ArrowUp className="mr-1 h-3 w-3 text-green-600" />
                          ) : (
                            <ArrowDown className="mr-1 h-3 w-3 text-red-600" />
                          )}
                          <span className={product.growth > 0 ? 'text-green-600' : 'text-red-600'}>
                            {product.growth}%
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* CUSTOMERS TAB */}
          <TabsContent value="customers" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Top Clientes</CardTitle>
                <CardDescription>Clientes com maior receita</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {topCustomers.map((customer, index) => (
                    <div key={index} className="flex items-center justify-between border-b pb-4 last:border-0">
                      <div className="flex-1">
                        <p className="font-medium">{customer.customer_name}</p>
                        <p className="text-sm text-muted-foreground">
                          {customer.purchases} compras • Última: {new Date(customer.last_purchase).toLocaleDateString('pt-AO')}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="font-bold">{metricsService.formatCurrency(customer.revenue)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
