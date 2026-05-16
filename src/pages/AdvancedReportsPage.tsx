// Advanced Reports Page - Versão Completa e Funcional
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { FileText, Download, Calendar, Clock, BarChart3, Eye, TrendingUp, Loader2, Trash2, RefreshCw, CheckCircle, AlertCircle } from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion, AnimatePresence } from 'framer-motion';
import { reportsService, ReportTemplate, GeneratedReport, ReportsStats } from '@/services/reportsServiceMock';

export default function AdvancedReportsPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('templates');
  const [templates, setTemplates] = useState<ReportTemplate[]>([]);
  const [reports, setReports] = useState<GeneratedReport[]>([]);
  const [stats, setStats] = useState<ReportsStats | null>(null);
  const [generating, setGenerating] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<ReportTemplate | null>(null);
  const [reportName, setReportName] = useState('');

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [templatesData, reportsData, statsData] = await Promise.all([
        reportsService.listTemplates(),
        reportsService.listReports({ limit: 20 }),
        reportsService.getStats(),
      ]);

      setTemplates(templatesData);
      setReports(reportsData);
      setStats(statsData);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar dados',
        description: error.message,
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateClick = (template: ReportTemplate) => {
    setSelectedTemplate(template);
    setReportName(`${template.name} - ${new Date().toLocaleDateString('pt-AO')}`);
    setDialogOpen(true);
  };

  const handleGenerateReport = async () => {
    if (!selectedTemplate) return;

    try {
      setGenerating(selectedTemplate.id);
      setDialogOpen(false);

      const report = await reportsService.generateReport(
        selectedTemplate.id,
        reportName
      );

      toast({
        title: 'Relatório em geração',
        description: 'O relatório está sendo gerado. Aguarde alguns segundos...',
      });

      // Aguardar 4 segundos e recarregar
      setTimeout(async () => {
        await loadData();
        setGenerating(null);
        toast({
          title: 'Relatório gerado!',
          description: 'O relatório está pronto para download.',
        });
        setActiveTab('reports');
      }, 4000);
    } catch (error: any) {
      setGenerating(null);
      toast({
        title: 'Erro ao gerar relatório',
        description: error.message,
        variant: 'destructive',
      });
    }
  };

  const handleDownloadReport = async (reportId: string) => {
    try {
      setDownloading(reportId);
      await reportsService.downloadReport(reportId);
      
      toast({
        title: 'Download iniciado',
        description: 'O download do relatório foi iniciado.',
      });

      // Recarregar para atualizar contador de downloads
      await loadData();
    } catch (error: any) {
      toast({
        title: 'Erro ao baixar relatório',
        description: error.message,
        variant: 'destructive',
      });
    } finally {
      setDownloading(null);
    }
  };

  const handleViewReport = (reportId: string) => {
    navigate(`/report-viewer/${reportId}`);
  };

  const handleDeleteReport = async (reportId: string) => {
    if (!confirm('Tem certeza que deseja excluir este relatório?')) return;

    try {
      await reportsService.deleteReport(reportId);
      toast({
        title: 'Relatório excluído',
        description: 'O relatório foi excluído com sucesso.',
      });
      await loadData();
    } catch (error: any) {
      toast({
        title: 'Erro ao excluir relatório',
        description: error.message,
        variant: 'destructive',
      });
    }
  };

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Relatórios Avançados</h1>
            <p className="text-muted-foreground">Sistema completo de geração e gestão de relatórios</p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Atualizar
          </Button>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid gap-4 md:grid-cols-4">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total de Relatórios</CardTitle>
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.total_reports}</div>
                  <p className="text-xs text-muted-foreground">{stats.completed_reports} concluídos</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Este Mês</CardTitle>
                  <Calendar className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.reports_this_month}</div>
                  <p className="text-xs text-muted-foreground">Relatórios gerados</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Tempo Médio</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.avg_generation_time}s</div>
                  <p className="text-xs text-muted-foreground">Geração de relatório</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Tamanho Total</CardTitle>
                  <BarChart3 className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{reportsService.formatFileSize(stats.total_size)}</div>
                  <p className="text-xs text-muted-foreground">{stats.total_downloads} downloads</p>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="templates">Templates de Relatórios</TabsTrigger>
            <TabsTrigger value="reports">
              Relatórios Gerados
              {reports.filter(r => r.status === 'processing').length > 0 && (
                <Badge variant="secondary" className="ml-2">
                  {reports.filter(r => r.status === 'processing').length}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* TEMPLATES TAB */}
          <TabsContent value="templates" className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              {templates.map((template, index) => (
                <motion.div
                  key={template.id}
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: index * 0.1 }}
                >
                  <Card>
                    <CardHeader>
                      <div className="flex justify-between items-start">
                        <div>
                          <CardTitle className="text-lg">{template.name}</CardTitle>
                          <CardDescription>{template.description}</CardDescription>
                        </div>
                        <Badge>{template.category}</Badge>
                      </div>
                    </CardHeader>
                    <CardContent>
                      <Button
                        className="w-full"
                        onClick={() => handleGenerateClick(template)}
                        disabled={generating === template.id}
                      >
                        {generating === template.id ? (
                          <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Gerando...
                          </>
                        ) : (
                          <>
                            <FileText className="mr-2 h-4 w-4" />
                            Gerar Relatório
                          </>
                        )}
                      </Button>
                    </CardContent>
                  </Card>
                </motion.div>
              ))}
            </div>
          </TabsContent>

          {/* REPORTS TAB */}
          <TabsContent value="reports" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Relatórios Gerados Recentemente</CardTitle>
                <CardDescription>Últimos relatórios disponíveis para download</CardDescription>
              </CardHeader>
              <CardContent>
                {reports.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Nenhum relatório gerado ainda.</p>
                    <p className="text-sm">Vá para a aba "Templates" para gerar seu primeiro relatório.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <AnimatePresence>
                      {reports.map((report) => (
                        <motion.div
                          key={report.id}
                          initial={{ opacity: 0, y: 20 }}
                          animate={{ opacity: 1, y: 0 }}
                          exit={{ opacity: 0, y: -20 }}
                          className="flex items-center justify-between border-b pb-4 last:border-0"
                        >
                          <div className="space-y-1 flex-1">
                            <p className="font-medium">{report.name}</p>
                            <div className="flex items-center gap-4 text-sm text-muted-foreground">
                              <span className="flex items-center gap-1">
                                <Calendar className="h-3 w-3" />
                                {new Date(report.generated_at).toLocaleDateString('pt-AO')}
                              </span>
                              <span className="flex items-center gap-1">
                                <Clock className="h-3 w-3" />
                                {new Date(report.generated_at).toLocaleTimeString('pt-AO', {
                                  hour: '2-digit',
                                  minute: '2-digit',
                                })}
                              </span>
                              {report.file_size && (
                                <span>{reportsService.formatFileSize(report.file_size)}</span>
                              )}
                              {report.download_count > 0 && (
                                <span className="flex items-center gap-1">
                                  <Download className="h-3 w-3" />
                                  {report.download_count}x
                                </span>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge 
                              variant="outline" 
                              className={reportsService.getStatusColor(report.status)}
                            >
                              {report.status === 'processing' && (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              )}
                              {report.status === 'completed' && (
                                <CheckCircle className="mr-1 h-3 w-3" />
                              )}
                              {report.status === 'failed' && (
                                <AlertCircle className="mr-1 h-3 w-3" />
                              )}
                              {reportsService.getStatusLabel(report.status)}
                            </Badge>
                            
                            {report.status === 'completed' && (
                              <>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleViewReport(report.id)}
                                  title="Analisar"
                                >
                                  <Eye className="h-4 w-4" />
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleDownloadReport(report.id)}
                                  disabled={downloading === report.id}
                                  title="Download"
                                >
                                  {downloading === report.id ? (
                                    <Loader2 className="h-4 w-4 animate-spin" />
                                  ) : (
                                    <Download className="h-4 w-4" />
                                  )}
                                </Button>
                                <Button
                                  variant="outline"
                                  size="sm"
                                  onClick={() => handleDeleteReport(report.id)}
                                  title="Excluir"
                                >
                                  <Trash2 className="h-4 w-4" />
                                </Button>
                              </>
                            )}
                          </div>
                        </motion.div>
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

        {/* Generate Report Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Gerar Relatório</DialogTitle>
              <DialogDescription>
                Configure os parâmetros para gerar o relatório
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="report-name">Nome do Relatório</Label>
                <Input
                  id="report-name"
                  value={reportName}
                  onChange={(e) => setReportName(e.target.value)}
                  placeholder="Nome do relatório"
                />
              </div>
              {selectedTemplate && (
                <div className="space-y-2">
                  <Label>Template</Label>
                  <div className="p-3 border rounded-lg bg-muted/50">
                    <p className="font-medium">{selectedTemplate.name}</p>
                    <p className="text-sm text-muted-foreground">{selectedTemplate.description}</p>
                  </div>
                </div>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                Cancelar
              </Button>
              <Button onClick={handleGenerateReport} disabled={!reportName}>
                <FileText className="mr-2 h-4 w-4" />
                Gerar Relatório
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
}
