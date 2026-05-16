// Report Viewer Page - Visualização de Relatórios com Gráficos
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/hooks/use-toast';
import { reportsService } from '@/services/agtReportsServiceMock';
import { 
  ArrowLeft, 
  Download, 
  Share2, 
  Star, 
  BarChart3, 
  PieChart, 
  TrendingUp,
  Calendar,
  FileText,
  Filter
} from 'lucide-react';
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart as RechartsPieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Area,
  AreaChart
} from 'recharts';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884D8', '#82CA9D'];

export default function ReportViewerPage() {
  const { reportId } = useParams();
  const navigate = useNavigate();
  const { profile } = useAuth();
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [report, setReport] = useState<any>(null);
  const [chartType, setChartType] = useState<'line' | 'bar' | 'pie' | 'area'>('bar');

  useEffect(() => {
    if (reportId && profile?.tenant_id) {
      loadReport();
    }
  }, [reportId, profile?.tenant_id]);

  const loadReport = async () => {
    if (!reportId || !profile?.tenant_id) return;

    try {
      setLoading(true);
      const data = await reportsService.getReports(profile.tenant_id);
      const foundReport = data.reports?.find((r: any) => r.id === reportId);
      
      if (foundReport) {
        setReport(foundReport);
      } else {
        toast({
          title: 'Erro',
          description: 'Relatório não encontrado',
          variant: 'destructive',
        });
        navigate('/advanced-reports');
      }
    } catch (error: any) {
      toast({
        title: 'Erro',
        description: error.message || 'Erro ao carregar relatório',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (format: 'pdf' | 'excel' | 'csv') => {
    if (!report || !profile?.tenant_id) return;

    try {
      await reportsService.exportReport(profile.tenant_id, report.id, format);
      toast({
        title: 'Sucesso',
        description: `Relatório exportado em ${format.toUpperCase()}`,
      });
    } catch (error: any) {
      toast({
        title: 'Erro',
        description: error.message || 'Erro ao exportar relatório',
        variant: 'destructive',
      });
    }
  };

  const handleShare = () => {
    toast({
      title: 'Compartilhar',
      description: 'Funcionalidade de compartilhamento em desenvolvimento',
    });
  };

  const handleFavorite = () => {
    toast({
      title: 'Favorito',
      description: 'Relatório adicionado aos favoritos',
    });
  };

  // Mock data para demonstração de gráficos
  const mockChartData = [
    { name: 'Jan', valor: 4000, meta: 3500 },
    { name: 'Fev', valor: 3000, meta: 3500 },
    { name: 'Mar', valor: 5000, meta: 4000 },
    { name: 'Abr', valor: 4500, meta: 4000 },
    { name: 'Mai', valor: 6000, meta: 4500 },
    { name: 'Jun', valor: 5500, meta: 4500 },
  ];

  const mockPieData = [
    { name: 'Vendas', value: 400 },
    { name: 'Marketing', value: 300 },
    { name: 'Operações', value: 200 },
    { name: 'RH', value: 100 },
  ];

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto mb-4"></div>
            <p className="text-muted-foreground">Carregando relatório...</p>
          </div>
        </div>
      </Layout>
    );
  }

  if (!report) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96">
          <div className="text-center">
            <FileText className="h-16 w-16 text-muted-foreground mx-auto mb-4" />
            <p className="text-lg font-medium mb-2">Relatório não encontrado</p>
            <Button onClick={() => navigate('/advanced-reports')}>
              Voltar para Relatórios
            </Button>
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate('/advanced-reports')}
            >
              <ArrowLeft className="h-5 w-5" />
            </Button>
            <div>
              <h1 className="text-3xl font-bold">{report.name}</h1>
              <p className="text-muted-foreground">
                Gerado em {new Date(report.created_at).toLocaleDateString('pt-AO')}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="outline" size="icon" onClick={handleFavorite}>
              <Star className="h-4 w-4" />
            </Button>
            <Button variant="outline" size="icon" onClick={handleShare}>
              <Share2 className="h-4 w-4" />
            </Button>
            <Button variant="outline" onClick={() => handleExport('pdf')}>
              <Download className="h-4 w-4 mr-2" />
              PDF
            </Button>
            <Button variant="outline" onClick={() => handleExport('excel')}>
              <Download className="h-4 w-4 mr-2" />
              Excel
            </Button>
          </div>
        </div>

        {/* Info Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Status
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Badge variant={report.status === 'completed' ? 'default' : 'secondary'}>
                {report.status === 'completed' ? 'Completo' : report.status}
              </Badge>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Formato
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{report.format?.toUpperCase()}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Linhas
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{report.row_count || 0}</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Tamanho
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold">{report.file_size || '0 KB'}</p>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="charts" className="space-y-4">
          <TabsList>
            <TabsTrigger value="charts">
              <BarChart3 className="h-4 w-4 mr-2" />
              Gráficos
            </TabsTrigger>
            <TabsTrigger value="data">
              <FileText className="h-4 w-4 mr-2" />
              Dados
            </TabsTrigger>
            <TabsTrigger value="analysis">
              <TrendingUp className="h-4 w-4 mr-2" />
              Análise
            </TabsTrigger>
          </TabsList>

          {/* Charts Tab */}
          <TabsContent value="charts" className="space-y-4">
            {/* Chart Type Selector */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>Visualização de Dados</CardTitle>
                  <div className="flex gap-2">
                    <Button
                      variant={chartType === 'bar' ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setChartType('bar')}
                    >
                      <BarChart3 className="h-4 w-4" />
                    </Button>
                    <Button
                      variant={chartType === 'line' ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setChartType('line')}
                    >
                      <TrendingUp className="h-4 w-4" />
                    </Button>
                    <Button
                      variant={chartType === 'pie' ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setChartType('pie')}
                    >
                      <PieChart className="h-4 w-4" />
                    </Button>
                    <Button
                      variant={chartType === 'area' ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => setChartType('area')}
                    >
                      <BarChart3 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {chartType === 'bar' && (
                  <ResponsiveContainer width="100%" height={400}>
                    <BarChart data={mockChartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="name" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="valor" fill="#0088FE" name="Valor Real" />
                      <Bar dataKey="meta" fill="#00C49F" name="Meta" />
                    </BarChart>
                  </ResponsiveContainer>
                )}

                {chartType === 'line' && (
                  <ResponsiveContainer width="100%" height={400}>
                    <LineChart data={mockChartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="name" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Line type="monotone" dataKey="valor" stroke="#0088FE" name="Valor Real" />
                      <Line type="monotone" dataKey="meta" stroke="#00C49F" name="Meta" />
                    </LineChart>
                  </ResponsiveContainer>
                )}

                {chartType === 'pie' && (
                  <ResponsiveContainer width="100%" height={400}>
                    <RechartsPieChart>
                      <Pie
                        data={mockPieData}
                        cx="50%"
                        cy="50%"
                        labelLine={false}
                        label={(entry) => `${entry.name}: ${entry.value}`}
                        outerRadius={150}
                        fill="#8884d8"
                        dataKey="value"
                      >
                        {mockPieData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </RechartsPieChart>
                  </ResponsiveContainer>
                )}

                {chartType === 'area' && (
                  <ResponsiveContainer width="100%" height={400}>
                    <AreaChart data={mockChartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="name" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Area type="monotone" dataKey="valor" stroke="#0088FE" fill="#0088FE" fillOpacity={0.6} name="Valor Real" />
                      <Area type="monotone" dataKey="meta" stroke="#00C49F" fill="#00C49F" fillOpacity={0.6} name="Meta" />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            {/* Additional Charts */}
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Distribuição por Categoria</CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={300}>
                    <RechartsPieChart>
                      <Pie
                        data={mockPieData}
                        cx="50%"
                        cy="50%"
                        outerRadius={100}
                        fill="#8884d8"
                        dataKey="value"
                        label
                      >
                        {mockPieData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </RechartsPieChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Tendência Mensal</CardTitle>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={300}>
                    <LineChart data={mockChartData}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="name" />
                      <YAxis />
                      <Tooltip />
                      <Line type="monotone" dataKey="valor" stroke="#0088FE" strokeWidth={2} />
                    </LineChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* Data Tab */}
          <TabsContent value="data">
            <Card>
              <CardHeader>
                <CardTitle>Dados do Relatório</CardTitle>
                <CardDescription>
                  Visualização tabular dos dados
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="rounded-md border">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b bg-muted/50">
                        <th className="p-3 text-left font-medium">Período</th>
                        <th className="p-3 text-right font-medium">Valor</th>
                        <th className="p-3 text-right font-medium">Meta</th>
                        <th className="p-3 text-right font-medium">Variação</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mockChartData.map((row, idx) => (
                        <tr key={idx} className="border-b">
                          <td className="p-3">{row.name}</td>
                          <td className="p-3 text-right">{row.valor.toLocaleString()} AOA</td>
                          <td className="p-3 text-right">{row.meta.toLocaleString()} AOA</td>
                          <td className="p-3 text-right">
                            <Badge variant={row.valor >= row.meta ? 'default' : 'destructive'}>
                              {((row.valor - row.meta) / row.meta * 100).toFixed(1)}%
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Analysis Tab */}
          <TabsContent value="analysis">
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Insights Principais</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex items-start gap-3">
                    <div className="rounded-full bg-green-100 p-2">
                      <TrendingUp className="h-4 w-4 text-green-600" />
                    </div>
                    <div>
                      <p className="font-medium">Crescimento Positivo</p>
                      <p className="text-sm text-muted-foreground">
                        Aumento de 25% em relação ao período anterior
                      </p>
                    </div>
                  </div>

                  <div className="flex items-start gap-3">
                    <div className="rounded-full bg-blue-100 p-2">
                      <BarChart3 className="h-4 w-4 text-blue-600" />
                    </div>
                    <div>
                      <p className="font-medium">Meta Atingida</p>
                      <p className="text-sm text-muted-foreground">
                        83% das metas foram alcançadas no período
                      </p>
                    </div>
                  </div>

                  <div className="flex items-start gap-3">
                    <div className="rounded-full bg-yellow-100 p-2">
                      <Calendar className="h-4 w-4 text-yellow-600" />
                    </div>
                    <div>
                      <p className="font-medium">Melhor Mês</p>
                      <p className="text-sm text-muted-foreground">
                        Maio apresentou o melhor desempenho
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Recomendações</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="p-3 rounded-lg bg-muted">
                    <p className="font-medium mb-1">Otimizar Março</p>
                    <p className="text-sm text-muted-foreground">
                      Março apresentou queda. Revisar estratégias para este período.
                    </p>
                  </div>

                  <div className="p-3 rounded-lg bg-muted">
                    <p className="font-medium mb-1">Manter Tendência</p>
                    <p className="text-sm text-muted-foreground">
                      Maio e Junho mostraram crescimento consistente. Replicar ações.
                    </p>
                  </div>

                  <div className="p-3 rounded-lg bg-muted">
                    <p className="font-medium mb-1">Ajustar Metas</p>
                    <p className="text-sm text-muted-foreground">
                      Considerar aumento de 10% nas metas para o próximo trimestre.
                    </p>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
