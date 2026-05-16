// Audit Complete Page - Versão Completa e Funcional
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { Shield, Search, Download, CheckCircle, XCircle, Activity, Users, BarChart3, AlertTriangle, FileText, Clock, RefreshCw, Loader2 } from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion } from 'framer-motion';
import { auditService, AuditLog, CriticalEvent, UserActivity, AuditStats } from '@/services/auditService';

export default function AuditCompletePage() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('logs');
  const [searchTerm, setSearchTerm] = useState('');
  const [stats, setStats] = useState<AuditStats | null>(null);
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [filteredLogs, setFilteredLogs] = useState<AuditLog[]>([]);
  const [userActivity, setUserActivity] = useState<UserActivity[]>([]);
  const [criticalEvents, setCriticalEvents] = useState<CriticalEvent[]>([]);
  const [exporting, setExporting] = useState(false);
  const [resolving, setResolving] = useState<string | null>(null);

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    if (searchTerm) {
      const filtered = logs.filter(log =>
        log.user_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.user_email?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.resource?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        log.details?.toLowerCase().includes(searchTerm.toLowerCase())
      );
      setFilteredLogs(filtered);
    } else {
      setFilteredLogs(logs);
    }
  }, [searchTerm, logs]);

  const loadData = async () => {
    try {
      setLoading(true);
      const [statsData, logsData, activityData, eventsData] = await Promise.all([
        auditService.getStatistics(),
        auditService.listLogs({ limit: 100 }),
        auditService.getUserActivity(7),
        auditService.listCriticalEvents()
      ]);

      setStats(statsData);
      setLogs(logsData);
      setFilteredLogs(logsData);
      setUserActivity(activityData);
      setCriticalEvents(eventsData);
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

  const handleExportLogs = async () => {
    try {
      setExporting(true);
      await auditService.exportLogs(filteredLogs);
      toast({
        title: 'Logs exportados',
        description: 'Os logs foram exportados com sucesso.'
      });
    } catch (error: any) {
      toast({
        title: 'Erro ao exportar logs',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setExporting(false);
    }
  };

  const handleResolveEvent = async (eventId: string) => {
    try {
      setResolving(eventId);
      await auditService.resolveCriticalEvent(eventId);
      toast({
        title: 'Evento resolvido',
        description: 'O evento crítico foi marcado como resolvido.'
      });
      await loadData();
    } catch (error: any) {
      toast({
        title: 'Erro ao resolver evento',
        description: error.message,
        variant: 'destructive'
      });
    } finally {
      setResolving(null);
    }
  };

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Auditoria Completa</h1>
            <p className="text-muted-foreground">Sistema completo de auditoria e logs de atividades</p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Atualizar
          </Button>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid gap-4 md:grid-cols-3 lg:grid-cols-6">
            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Total de Logs</CardTitle>
                  <FileText className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.total_logs}</div>
                  <p className="text-xs text-muted-foreground">Registros totais</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Hoje</CardTitle>
                  <Clock className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.logs_today}</div>
                  <p className="text-xs text-muted-foreground">Logs de hoje</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Taxa de Sucesso</CardTitle>
                  <CheckCircle className="h-4 w-4 text-green-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-green-600">{stats.success_rate}%</div>
                  <p className="text-xs text-muted-foreground">Ações bem-sucedidas</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Falhas</CardTitle>
                  <XCircle className="h-4 w-4 text-red-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-red-600">{stats.failed_actions}</div>
                  <p className="text-xs text-muted-foreground">Ações falhadas</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.5 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Usuários Ativos</CardTitle>
                  <Users className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{stats.active_users}</div>
                  <p className="text-xs text-muted-foreground">Últimos 7 dias</p>
                </CardContent>
              </Card>
            </motion.div>

            <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.6 }}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium">Eventos Críticos</CardTitle>
                  <AlertTriangle className="h-4 w-4 text-red-600" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold text-red-600">{stats.critical_events}</div>
                  <p className="text-xs text-muted-foreground">Requerem atenção</p>
                </CardContent>
              </Card>
            </motion.div>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="logs">Logs de Auditoria</TabsTrigger>
            <TabsTrigger value="activity">Atividade por Usuário</TabsTrigger>
            <TabsTrigger value="critical">
              Eventos Críticos
              {stats && stats.critical_events > 0 && (
                <Badge variant="destructive" className="ml-2">{stats.critical_events}</Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* LOGS TAB */}
          <TabsContent value="logs" className="space-y-4">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Logs de Auditoria</CardTitle>
                    <CardDescription>Histórico completo de ações no sistema</CardDescription>
                  </div>
                  <Button onClick={handleExportLogs} disabled={exporting || filteredLogs.length === 0}>
                    {exporting ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Download className="mr-2 h-4 w-4" />
                    )}
                    Exportar Logs
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="mb-4">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      placeholder="Buscar logs..."
                      value={searchTerm}
                      onChange={(e) => setSearchTerm(e.target.value)}
                      className="pl-10"
                    />
                  </div>
                </div>

                {filteredLogs.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <FileText className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Nenhum log encontrado.</p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {filteredLogs.map((log) => (
                      <div key={log.id} className="flex items-start gap-3 border-b pb-3 last:border-0">
                        <div className={`mt-1 ${log.status === 'success' ? 'text-green-600' : 'text-red-600'}`}>
                          {log.status === 'success' ? (
                            <CheckCircle className="h-5 w-5" />
                          ) : (
                            <XCircle className="h-5 w-5" />
                          )}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <p className="font-medium text-sm">{auditService.getActionLabel(log.action)}</p>
                              <Badge className={auditService.getStatusColor(log.status)}>
                                {log.status}
                              </Badge>
                            </div>
                            <span className="text-xs text-muted-foreground">
                              {new Date(log.created_at).toLocaleString('pt-AO')}
                            </span>
                          </div>
                          <p className="text-sm text-muted-foreground mt-1">
                            <span className="font-medium">{log.user_name || log.user_email}</span>
                            {log.resource && <> • {log.resource}</>}
                            {log.ip_address && <> • IP: {log.ip_address}</>}
                          </p>
                          {log.details && (
                            <p className="text-sm text-muted-foreground mt-1">{log.details}</p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* ACTIVITY TAB */}
          <TabsContent value="activity" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Atividade por Usuário</CardTitle>
                <CardDescription>Usuários mais ativos no sistema (últimos 7 dias)</CardDescription>
              </CardHeader>
              <CardContent>
                {userActivity.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Users className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>Nenhuma atividade registrada.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {userActivity.map((activity, index) => (
                      <div key={index} className="flex items-center justify-between border-b pb-4 last:border-0">
                        <div className="flex items-center gap-3">
                          <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center">
                            <Users className="h-5 w-5 text-primary" />
                          </div>
                          <div>
                            <p className="font-medium">{activity.user_name || activity.user_email || 'Usuário'}</p>
                            <p className="text-sm text-muted-foreground">
                              Última atividade: {new Date(activity.last_active).toLocaleString('pt-AO')}
                            </p>
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="text-2xl font-bold">{activity.actions_count}</p>
                          <p className="text-xs text-muted-foreground">ações</p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* CRITICAL EVENTS TAB */}
          <TabsContent value="critical" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Eventos Críticos</CardTitle>
                <CardDescription>Eventos que requerem atenção</CardDescription>
              </CardHeader>
              <CardContent>
                {criticalEvents.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <CheckCircle className="h-12 w-12 mx-auto mb-4 opacity-50 text-green-600" />
                    <p>Nenhum evento crítico pendente.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {criticalEvents.map((event) => (
                      <div key={event.id} className="border rounded-lg p-4">
                        <div className="flex items-start justify-between">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-2">
                              <Badge className={auditService.getSeverityColor(event.severity)}>
                                {auditService.getSeverityLabel(event.severity)}
                              </Badge>
                              <Badge variant={event.status === 'pending' ? 'destructive' : 'secondary'}>
                                {event.status === 'pending' ? 'Pendente' : 'Resolvido'}
                              </Badge>
                            </div>
                            <h4 className="font-semibold mb-1">{event.title}</h4>
                            <p className="text-sm text-muted-foreground mb-2">{event.description}</p>
                            <div className="flex items-center gap-4 text-xs text-muted-foreground">
                              <span>Tipo: {event.event_type}</span>
                              {event.user_email && <span>Usuário: {event.user_email}</span>}
                              {event.resource && <span>Recurso: {event.resource}</span>}
                              <span>{new Date(event.created_at).toLocaleString('pt-AO')}</span>
                            </div>
                          </div>
                          {event.status === 'pending' && (
                            <Button
                              size="sm"
                              onClick={() => handleResolveEvent(event.id)}
                              disabled={resolving === event.id}
                            >
                              {resolving === event.id ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                'Resolver'
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
        </Tabs>
      </div>
    </Layout>
  );
}
