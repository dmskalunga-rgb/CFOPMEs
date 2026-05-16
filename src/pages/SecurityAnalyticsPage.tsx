// Security & Analytics Page - Segurança e Análise
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { 
  Shield, 
  BarChart3, 
  RefreshCw,
  Monitor,
  AlertTriangle,
  TrendingUp,
  Download,
  FileText,
  Activity
} from 'lucide-react';
import { securityAnalyticsService, SecurityStats, AuditLog, UserSession, AnalyticsSummary } from '@/services/securityAnalyticsService';
import { useAuth } from '@/hooks/useAuth';
import { toast } from 'sonner';
import { PageLoader } from '@/components/LoadingStates';
import { NoDataYet } from '@/components/EmptyStates';

export default function SecurityAnalyticsPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [securityStats, setSecurityStats] = useState<SecurityStats | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [sessions, setSessions] = useState<UserSession[]>([]);
  const [analyticsSummary, setAnalyticsSummary] = useState<AnalyticsSummary | null>(null);

  useEffect(() => {
    if (user) loadData();
  }, [user]);

  const loadData = async () => {
    if (!user) return;
    try {
      setLoading(true);
      
      const endDate = new Date().toISOString();
      const startDate = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
      
      const [stats, logs, userSessions, summary] = await Promise.all([
        securityAnalyticsService.getSecurityStats(user.id),
        securityAnalyticsService.getAuditLogs(user.id, 20),
        securityAnalyticsService.getUserSessions(user.id),
        securityAnalyticsService.getAnalyticsSummary(user.id, startDate, endDate),
      ]);
      
      setSecurityStats(stats);
      setAuditLogs(logs);
      setSessions(userSessions);
      setAnalyticsSummary(summary);
    } catch (error: any) {
      toast.error('Erro ao carregar dados: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    if (!confirm('Tem certeza que deseja revogar esta sessão?')) return;
    try {
      await securityAnalyticsService.revokeSession(sessionId);
      toast.success('Sessão revogada!');
      loadData();
    } catch (error: any) {
      toast.error('Erro ao revogar sessão: ' + error.message);
    }
  };

  const handleExportData = async (type: string, format: string) => {
    if (!user) return;
    try {
      await securityAnalyticsService.requestDataExport(user.id, type, format);
      toast.success('Exportação solicitada! Você receberá um email quando estiver pronta.');
    } catch (error: any) {
      toast.error('Erro ao solicitar exportação: ' + error.message);
    }
  };

  if (loading) {
    return (
      <Layout>
        <PageLoader message="Carregando dados..." />
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Shield className="h-8 w-8 text-primary" />
              Segurança & Analytics
            </h1>
            <p className="text-muted-foreground">
              Monitoramento de segurança e análise de dados
            </p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Atualizar
          </Button>
        </div>

        {/* Stats Grid */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Monitor className="h-4 w-4" />
                Sessões Ativas
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{securityStats?.active_sessions || 0}</div>
              <p className="text-xs text-muted-foreground">
                {securityStats?.total_sessions || 0} total
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <AlertTriangle className="h-4 w-4" />
                Eventos de Segurança
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{securityStats?.security_events || 0}</div>
              <p className="text-xs text-muted-foreground">Últimos 30 dias</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <TrendingUp className="h-4 w-4" />
                Receita Total
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {analyticsSummary?.total_revenue?.toLocaleString('pt-BR', {
                  style: 'currency',
                  currency: 'AOA',
                }) || 'AOA 0'}
              </div>
              <p className="text-xs text-muted-foreground">
                {analyticsSummary?.growth_rate?.toFixed(1) || 0}% crescimento
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Faturas
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{analyticsSummary?.total_invoices || 0}</div>
              <p className="text-xs text-muted-foreground">
                Média: {analyticsSummary?.avg_invoice_value?.toLocaleString('pt-BR', {
                  style: 'currency',
                  currency: 'AOA',
                }) || 'AOA 0'}
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="security" className="space-y-4">
          <TabsList>
            <TabsTrigger value="security">
              <Shield className="h-4 w-4 mr-2" />
              Segurança
            </TabsTrigger>
            <TabsTrigger value="analytics">
              <BarChart3 className="h-4 w-4 mr-2" />
              Analytics
            </TabsTrigger>
            <TabsTrigger value="audit">
              <Activity className="h-4 w-4 mr-2" />
              Auditoria
            </TabsTrigger>
            <TabsTrigger value="export">
              <Download className="h-4 w-4 mr-2" />
              Exportar
            </TabsTrigger>
          </TabsList>

          {/* Security Tab */}
          <TabsContent value="security">
            <Card>
              <CardHeader>
                <CardTitle>Sessões Ativas</CardTitle>
                <CardDescription>
                  Gerencie suas sessões e dispositivos conectados
                </CardDescription>
              </CardHeader>
              <CardContent>
                {sessions.length === 0 ? (
                  <NoDataYet />
                ) : (
                  <div className="space-y-3">
                    {sessions.map((session) => (
                      <div key={session.id} className="flex items-center justify-between p-4 border rounded-lg">
                        <div className="flex-1">
                          <p className="font-medium">
                            {session.device_name || 'Dispositivo Desconhecido'}
                          </p>
                          <p className="text-sm text-muted-foreground">
                            {session.browser} • {session.os}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {session.ip_address} • {session.location || 'Localização desconhecida'}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            Última atividade: {new Date(session.last_activity_at).toLocaleString('pt-BR')}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge variant={session.is_active ? 'default' : 'secondary'}>
                            {session.is_active ? 'Ativa' : 'Inativa'}
                          </Badge>
                          {session.is_active && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleRevokeSession(session.id)}
                            >
                              Revogar
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Analytics Tab */}
          <TabsContent value="analytics">
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Resumo Financeiro</CardTitle>
                  <CardDescription>Últimos 30 dias</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <p className="text-sm text-muted-foreground">Receita Total</p>
                    <p className="text-2xl font-bold">
                      {analyticsSummary?.total_revenue?.toLocaleString('pt-BR', {
                        style: 'currency',
                        currency: 'AOA',
                      }) || 'AOA 0'}
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Taxa de Crescimento</p>
                    <p className="text-2xl font-bold text-green-600">
                      {analyticsSummary?.growth_rate?.toFixed(1) || 0}%
                    </p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Valor Médio por Fatura</p>
                    <p className="text-2xl font-bold">
                      {analyticsSummary?.avg_invoice_value?.toLocaleString('pt-BR', {
                        style: 'currency',
                        currency: 'AOA',
                      }) || 'AOA 0'}
                    </p>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Métricas de Negócio</CardTitle>
                  <CardDescription>Últimos 30 dias</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <p className="text-sm text-muted-foreground">Total de Faturas</p>
                    <p className="text-2xl font-bold">{analyticsSummary?.total_invoices || 0}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Total de Clientes</p>
                    <p className="text-2xl font-bold">{analyticsSummary?.total_customers || 0}</p>
                  </div>
                  <div>
                    <p className="text-sm text-muted-foreground">Sessões de Segurança</p>
                    <p className="text-2xl font-bold">{securityStats?.total_sessions || 0}</p>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* Audit Tab */}
          <TabsContent value="audit">
            <Card>
              <CardHeader>
                <CardTitle>Logs de Auditoria</CardTitle>
                <CardDescription>
                  Histórico de ações no sistema
                </CardDescription>
              </CardHeader>
              <CardContent>
                {auditLogs.length === 0 ? (
                  <NoDataYet />
                ) : (
                  <div className="space-y-2">
                    {auditLogs.map((log) => (
                      <div key={log.id} className="flex items-center justify-between p-3 border rounded-lg">
                        <div className="flex-1">
                          <p className="font-medium">{log.action}</p>
                          <p className="text-sm text-muted-foreground">
                            {log.resource_type} {log.resource_id && `• ${log.resource_id}`}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(log.created_at).toLocaleString('pt-BR')}
                            {log.ip_address && ` • ${log.ip_address}`}
                          </p>
                        </div>
                        <Badge variant="outline">
                          {log.resource_type}
                        </Badge>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Export Tab */}
          <TabsContent value="export">
            <Card>
              <CardHeader>
                <CardTitle>Exportar Dados</CardTitle>
                <CardDescription>
                  Solicite exportação de dados em diferentes formatos
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="border rounded-lg p-4">
                    <h3 className="font-semibold mb-2">Faturas</h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      Exportar todas as faturas
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('invoices', 'csv')}
                      >
                        CSV
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('invoices', 'xlsx')}
                      >
                        Excel
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('invoices', 'pdf')}
                      >
                        PDF
                      </Button>
                    </div>
                  </div>

                  <div className="border rounded-lg p-4">
                    <h3 className="font-semibold mb-2">Clientes</h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      Exportar lista de clientes
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('customers', 'csv')}
                      >
                        CSV
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('customers', 'xlsx')}
                      >
                        Excel
                      </Button>
                    </div>
                  </div>

                  <div className="border rounded-lg p-4">
                    <h3 className="font-semibold mb-2">Logs de Auditoria</h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      Exportar logs de segurança
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('audit_logs', 'csv')}
                      >
                        CSV
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('audit_logs', 'json')}
                      >
                        JSON
                      </Button>
                    </div>
                  </div>

                  <div className="border rounded-lg p-4">
                    <h3 className="font-semibold mb-2">Métricas</h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      Exportar dados analíticos
                    </p>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('analytics', 'csv')}
                      >
                        CSV
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleExportData('analytics', 'xlsx')}
                      >
                        Excel
                      </Button>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
