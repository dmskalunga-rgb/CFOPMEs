// AGT Integration Page - Versão Completa e Funcional
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { Building2, FileText, CheckCircle, XCircle, Clock, Send, Shield, Activity, TrendingUp, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion } from 'framer-motion';
import { agtService, AGTInvoice, AGTLog, AGTStats, AGTConnectionStatus } from '@/services/agtService';

export default function AGTIntegrationPage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('overview');
  const [connectionStatus, setConnectionStatus] = useState<AGTConnectionStatus | null>(null);
  const [statistics, setStatistics] = useState<AGTStats | null>(null);
  const [invoices, setInvoices] = useState<AGTInvoice[]>([]);
  const [logs, setLogs] = useState<AGTLog[]>([]);
  const [sending, setSending] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [statusData, statsData, invoicesData, logsData] = await Promise.all([
        agtService.getConnectionStatus(),
        agtService.getStatistics(),
        agtService.listInvoices({ limit: 20 }),
        agtService.listLogs(50)
      ]);

      setConnectionStatus(statusData);
      setStatistics(statsData);
      setInvoices(invoicesData);
      setLogs(logsData);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar dados',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSendToAGT = async (invoiceId: string) => {
    try {
      setSending(invoiceId);
      await agtService.sendToAGT(invoiceId);
      toast({
        title: 'Fatura enviada',
        description: 'A fatura foi enviada para a AGT com sucesso.'
      });
      await loadData();
    } catch (error: any) {
      toast({
        title: 'Erro ao enviar fatura',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setSending(null);
    }
  };

  const handleRetry = async (invoiceId: string) => {
    try {
      setSending(invoiceId);
      await agtService.retryInvoice(invoiceId);
      toast({
        title: 'Reenvio iniciado',
        description: 'A fatura está sendo reenviada para a AGT.'
      });
      await loadData();
    } catch (error: any) {
      toast({
        title: 'Erro ao reenviar fatura',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setSending(null);
    }
  };

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Integração AGT Angola</h1>
            <p className="text-muted-foreground">Sistema de integração com a Administração Geral Tributária</p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Atualizar
          </Button>
        </div>

        {/* Connection Status Card */}
        {connectionStatus && (
          <Card className={connectionStatus.connection_status === 'online' ? 'border-green-200' : ''}>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`h-3 w-3 rounded-full ${
                    connectionStatus.connection_status === 'online' ? 'bg-green-500 animate-pulse' :
                    connectionStatus.connection_status === 'idle' ? 'bg-yellow-500' : 'bg-gray-400'
                  }`} />
                  <div>
                    <CardTitle>Status da Integração</CardTitle>
                    <CardDescription>
                      {connectionStatus.is_test_mode ? 'Modo de Teste' : 'Modo de Produção'}
                    </CardDescription>
                  </div>
                </div>
                <Badge variant={connectionStatus.is_active ? 'default' : 'secondary'}>
                  {connectionStatus.connection_status === 'online' ? 'Conectado' :
                   connectionStatus.connection_status === 'idle' ? 'Inativo' : 'Desconectado'}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <p className="text-sm text-muted-foreground">Empresa</p>
                  <p className="font-medium">{connectionStatus.company_name}</p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">NIF</p>
                  <p className="font-medium">{connectionStatus.company_nif}</p>
                </div>
                {connectionStatus.last_sync_at && (
                  <div>
                    <p className="text-sm text-muted-foreground">Última Sincronização</p>
                    <p className="font-medium">{new Date(connectionStatus.last_sync_at).toLocaleString('pt-AO')}</p>
                  </div>
                )}
                {connectionStatus.last_activity && (
                  <div>
                    <p className="text-sm text-muted-foreground">Última Atividade</p>
                    <p className="font-medium">{new Date(connectionStatus.last_activity).toLocaleString('pt-AO')}</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Stats Cards */}
        {statistics && (
          <div className="grid gap-4 md:grid-cols-4">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total de Faturas</CardTitle>
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{statistics.total_invoices}</div>
                  <p className="text-xs text-muted-foreground">{statistics.sent_to_agt} enviadas</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Validadas</CardTitle>
                  <CheckCircle className="h-4 w-4 text-green-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-green-600">{statistics.validated}</div>
                  <p className="text-xs text-muted-foreground">Aprovadas pela AGT</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Rejeitadas</CardTitle>
                  <XCircle className="h-4 w-4 text-red-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-red-600">{statistics.rejected}</div>
                  <p className="text-xs text-muted-foreground">Necessitam correção</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Taxa de Sucesso</CardTitle>
                  <TrendingUp className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{statistics.success_rate}%</div>
                  <p className="text-xs text-muted-foreground">{statistics.pending} pendentes</p>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="overview">Visão Geral</TabsTrigger>
            <TabsTrigger value="invoices">Faturas</TabsTrigger>
            <TabsTrigger value="logs">Logs de Integração</TabsTrigger>
          </TabsList>

          {/* OVERVIEW TAB */}
          <TabsContent value="overview" className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Informações da Conexão</CardTitle>
                  <CardDescription>Detalhes da integração com a AGT</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {connectionStatus && (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Status</span>
                        <Badge variant={connectionStatus.is_active ? 'default' : 'secondary'}>
                          {connectionStatus.is_active ? 'Ativo' : 'Inativo'}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Modo</span>
                        <Badge variant="outline">
                          {connectionStatus.is_test_mode ? 'Teste' : 'Produção'}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Configurado</span>
                        {connectionStatus.is_configured ? (
                          <CheckCircle className="h-5 w-5 text-green-600" />
                        ) : (
                          <XCircle className="h-5 w-5 text-red-600" />
                        )}
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Resumo de Atividades</CardTitle>
                  <CardDescription>Últimas 24 horas</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  {statistics && (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Faturas Enviadas</span>
                        <span className="font-bold">{statistics.sent_to_agt}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Validações</span>
                        <span className="font-bold text-green-600">{statistics.validated}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-sm">Rejeições</span>
                        <span className="font-bold text-red-600">{statistics.rejected}</span>
                      </div>
                    </>
                  )}
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* INVOICES TAB */}
          <TabsContent value="invoices" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Faturas Enviadas para AGT</CardTitle>
                <CardDescription>Lista de faturas e seus status na AGT</CardDescription>
              </CardHeader>
              <CardContent>
                {invoices.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Nenhuma fatura enviada para a AGT ainda.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {invoices.map((invoice) => (
                      <div key={invoice.id} className="flex items-center justify-between border-b pb-4 last:border-0">
                        <div className="space-y-1 flex-1">
                          <p className="font-medium">{invoice.invoice_number}</p>
                          <p className="text-sm text-muted-foreground">{invoice.customer_name}</p>
                          <div className="flex items-center gap-4 text-sm text-muted-foreground">
                            <span>{new Date(invoice.issue_date).toLocaleDateString('pt-AO')}</span>
                            <span>{agtService.formatCurrency(invoice.total_amount)}</span>
                            {invoice.agt_reference && (
                              <span className="text-xs">Ref: {invoice.agt_reference}</span>
                            )}
                          </div>
                          {invoice.rejection_reason && (
                            <p className="text-sm text-red-600">Motivo: {invoice.rejection_reason}</p>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge className={agtService.getStatusColor(invoice.agt_status)}>
                            {agtService.getStatusLabel(invoice.agt_status)}
                          </Badge>
                          {invoice.agt_status === 'pending' && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleSendToAGT(invoice.id)}
                              disabled={sending === invoice.id}
                            >
                              {sending === invoice.id ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <Send className="h-4 w-4" />
                              )}
                            </Button>
                          )}
                          {invoice.agt_status === 'rejected' && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleRetry(invoice.id)}
                              disabled={sending === invoice.id}
                            >
                              {sending === invoice.id ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <RefreshCw className="h-4 w-4" />
                              )}
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

          {/* LOGS TAB */}
          <TabsContent value="logs" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Logs de Integração</CardTitle>
                <CardDescription>Histórico de comunicação com a AGT</CardDescription>
              </CardHeader>
              <CardContent>
                {logs.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Activity className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Nenhum log de integração disponível.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {logs.map((log) => (
                      <div key={log.id} className="flex items-start gap-3 border-b pb-3 last:border-0">
                        <div className={`mt-1 ${agtService.getLogStatusColor(log.status)}`}>
                          {log.status === 'success' && <CheckCircle className="h-5 w-5" />}
                          {log.status === 'error' && <XCircle className="h-5 w-5" />}
                          {log.status === 'pending' && <Clock className="h-5 w-5" />}
                          {log.status === 'warning' && <AlertCircle className="h-5 w-5" />}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center justify-between">
                            <p className="font-medium text-sm">{log.action}</p>
                            <span className="text-xs text-muted-foreground">
                              {new Date(log.created_at).toLocaleString('pt-AO')}
                            </span>
                          </div>
                          <p className="text-sm text-muted-foreground">{log.message}</p>
                          {log.duration_ms && (
                            <p className="text-xs text-muted-foreground mt-1">
                              Duração: {log.duration_ms}ms
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
