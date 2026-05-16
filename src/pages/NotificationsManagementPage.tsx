// Notifications Management Page - Gerenciamento de Notificações
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { 
  Mail, 
  Send, 
  Clock, 
  CheckCircle, 
  XCircle,
  RefreshCw,
  FileText,
  Settings as SettingsIcon,
  BarChart3,
  Loader2
} from 'lucide-react';
import { notificationsService, EmailLog, EmailTemplate, NotificationStats } from '@/services/notificationsService';
import { useAuth } from '@/hooks/useAuth';
import { toast } from 'sonner';
import { PageLoader } from '@/components/LoadingStates';
import { NoDataYet } from '@/components/EmptyStates';

export default function NotificationsManagementPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [processing, setProcessing] = useState(false);
  
  // Stats
  const [stats, setStats] = useState<NotificationStats>({ pending: 0, sent: 0, failed: 0 });
  
  // Templates
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  
  // Logs
  const [logs, setLogs] = useState<EmailLog[]>([]);
  const [logsTotal, setLogsTotal] = useState(0);
  
  // Preferences
  const [preferences, setPreferences] = useState<any>(null);
  
  // Send email form
  const [emailForm, setEmailForm] = useState({
    to: '',
    subject: '',
    html: '',
    template: '',
    variables: '{}',
  });

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      
      // Load stats
      const statsData = await notificationsService.getStats();
      setStats(statsData);
      
      // Load templates
      const templatesData = await notificationsService.getTemplates();
      setTemplates(templatesData);
      
      // Load logs
      const logsData = await notificationsService.getLogs(20, 0);
      setLogs(logsData.logs);
      setLogsTotal(logsData.total);
      
      // Load preferences
      if (user) {
        const prefsData = await notificationsService.getPreferences(user.id);
        setPreferences(prefsData);
      }
    } catch (error: any) {
      toast.error('Erro ao carregar dados: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSendEmail = async () => {
    try {
      setSending(true);
      
      const data: any = {
        to: emailForm.to.split(',').map(e => e.trim()),
        subject: emailForm.subject,
      };
      
      if (emailForm.template) {
        data.template = emailForm.template;
        try {
          data.variables = JSON.parse(emailForm.variables);
        } catch {
          toast.error('Variáveis inválidas (deve ser JSON)');
          return;
        }
      } else {
        data.html = emailForm.html;
      }
      
      await notificationsService.sendEmail(data);
      toast.success('Email enviado com sucesso!');
      
      // Reset form
      setEmailForm({
        to: '',
        subject: '',
        html: '',
        template: '',
        variables: '{}',
      });
      
      // Reload data
      loadData();
    } catch (error: any) {
      toast.error('Erro ao enviar email: ' + error.message);
    } finally {
      setSending(false);
    }
  };

  const handleProcessQueue = async () => {
    try {
      setProcessing(true);
      const result = await notificationsService.processQueue();
      toast.success(`${result.processed} emails processados!`);
      loadData();
    } catch (error: any) {
      toast.error('Erro ao processar fila: ' + error.message);
    } finally {
      setProcessing(false);
    }
  };

  const handleUpdatePreferences = async (updates: any) => {
    try {
      if (!user) return;
      
      const updated = await notificationsService.updatePreferences(user.id, updates);
      setPreferences(updated);
      toast.success('Preferências atualizadas!');
    } catch (error: any) {
      toast.error('Erro ao atualizar preferências: ' + error.message);
    }
  };

  if (loading) {
    return (
      <Layout>
        <PageLoader message="Carregando notificações..." />
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
              <Mail className="h-8 w-8 text-primary" />
              Sistema de Notificações
            </h1>
            <p className="text-muted-foreground">
              Gerenciar emails, templates e preferências
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={loadData}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Atualizar
            </Button>
            <Button onClick={handleProcessQueue} disabled={processing}>
              {processing ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Send className="h-4 w-4 mr-2" />
              )}
              Processar Fila
            </Button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <Clock className="h-4 w-4" />
                Pendentes
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.pending}</div>
              <p className="text-xs text-muted-foreground">Na fila de envio</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <CheckCircle className="h-4 w-4" />
                Enviados
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{stats.sent}</div>
              <p className="text-xs text-muted-foreground">Com sucesso</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                <XCircle className="h-4 w-4" />
                Falhados
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{stats.failed}</div>
              <p className="text-xs text-muted-foreground">Com erro</p>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="send" className="space-y-4">
          <TabsList>
            <TabsTrigger value="send">
              <Send className="h-4 w-4 mr-2" />
              Enviar Email
            </TabsTrigger>
            <TabsTrigger value="templates">
              <FileText className="h-4 w-4 mr-2" />
              Templates
            </TabsTrigger>
            <TabsTrigger value="logs">
              <BarChart3 className="h-4 w-4 mr-2" />
              Logs
            </TabsTrigger>
            <TabsTrigger value="preferences">
              <SettingsIcon className="h-4 w-4 mr-2" />
              Preferências
            </TabsTrigger>
          </TabsList>

          {/* Send Email Tab */}
          <TabsContent value="send">
            <Card>
              <CardHeader>
                <CardTitle>Enviar Email</CardTitle>
                <CardDescription>
                  Envie emails usando templates ou HTML customizado
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="to">Para (separar por vírgula)</Label>
                  <Input
                    id="to"
                    placeholder="email@example.com, outro@example.com"
                    value={emailForm.to}
                    onChange={(e) => setEmailForm({ ...emailForm, to: e.target.value })}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="template">Template (opcional)</Label>
                  <select
                    id="template"
                    className="w-full p-2 border rounded-md"
                    value={emailForm.template}
                    onChange={(e) => setEmailForm({ ...emailForm, template: e.target.value })}
                  >
                    <option value="">Nenhum (usar HTML customizado)</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.name}>
                        {t.name} - {t.subject}
                      </option>
                    ))}
                  </select>
                </div>

                {emailForm.template ? (
                  <div className="space-y-2">
                    <Label htmlFor="variables">Variáveis (JSON)</Label>
                    <Textarea
                      id="variables"
                      placeholder='{"name": "João", "amount": "100"}'
                      value={emailForm.variables}
                      onChange={(e) => setEmailForm({ ...emailForm, variables: e.target.value })}
                      rows={4}
                    />
                  </div>
                ) : (
                  <>
                    <div className="space-y-2">
                      <Label htmlFor="subject">Assunto</Label>
                      <Input
                        id="subject"
                        placeholder="Assunto do email"
                        value={emailForm.subject}
                        onChange={(e) => setEmailForm({ ...emailForm, subject: e.target.value })}
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="html">Conteúdo HTML</Label>
                      <Textarea
                        id="html"
                        placeholder="<h1>Olá!</h1><p>Conteúdo do email...</p>"
                        value={emailForm.html}
                        onChange={(e) => setEmailForm({ ...emailForm, html: e.target.value })}
                        rows={8}
                      />
                    </div>
                  </>
                )}

                <Button onClick={handleSendEmail} disabled={sending || !emailForm.to}>
                  {sending ? (
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4 mr-2" />
                  )}
                  Enviar Email
                </Button>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Templates Tab */}
          <TabsContent value="templates">
            <Card>
              <CardHeader>
                <CardTitle>Templates de Email</CardTitle>
                <CardDescription>
                  {templates.length} templates disponíveis
                </CardDescription>
              </CardHeader>
              <CardContent>
                {templates.length === 0 ? (
                  <NoDataYet />
                ) : (
                  <div className="space-y-4">
                    {templates.map((template) => (
                      <div key={template.id} className="border rounded-lg p-4">
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <h3 className="font-semibold">{template.name}</h3>
                            <p className="text-sm text-muted-foreground">{template.subject}</p>
                          </div>
                          <Badge variant={template.is_active ? 'default' : 'secondary'}>
                            {template.is_active ? 'Ativo' : 'Inativo'}
                          </Badge>
                        </div>
                        <div className="flex gap-2 mt-2">
                          <Badge variant="outline">{template.category}</Badge>
                          {template.variables.length > 0 && (
                            <Badge variant="outline">
                              Variáveis: {template.variables.join(', ')}
                            </Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Logs Tab */}
          <TabsContent value="logs">
            <Card>
              <CardHeader>
                <CardTitle>Logs de Envio</CardTitle>
                <CardDescription>
                  {logsTotal} emails registrados
                </CardDescription>
              </CardHeader>
              <CardContent>
                {logs.length === 0 ? (
                  <NoDataYet />
                ) : (
                  <div className="space-y-3">
                    {logs.map((log) => (
                      <div key={log.id} className="flex items-center justify-between p-3 border rounded-lg">
                        <div className="flex-1">
                          <p className="font-medium">{log.to_email}</p>
                          <p className="text-sm text-muted-foreground">{log.subject}</p>
                          <p className="text-xs text-muted-foreground">
                            {new Date(log.sent_at).toLocaleString('pt-BR')}
                          </p>
                        </div>
                        <Badge
                          variant={
                            log.status === 'sent'
                              ? 'default'
                              : log.status === 'failed'
                              ? 'destructive'
                              : 'secondary'
                          }
                        >
                          {log.status}
                        </Badge>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Preferences Tab */}
          <TabsContent value="preferences">
            <Card>
              <CardHeader>
                <CardTitle>Preferências de Notificação</CardTitle>
                <CardDescription>
                  Configure como você deseja receber notificações
                </CardDescription>
              </CardHeader>
              <CardContent>
                {!preferences ? (
                  <NoDataYet />
                ) : (
                  <div className="space-y-6">
                    <div className="space-y-4">
                      <h3 className="font-semibold">Canais</h3>
                      <div className="flex items-center justify-between">
                        <Label htmlFor="email">Email</Label>
                        <Switch
                          id="email"
                          checked={preferences.email_enabled}
                          onCheckedChange={(checked) =>
                            handleUpdatePreferences({ email_enabled: checked })
                          }
                        />
                      </div>
                      <div className="flex items-center justify-between">
                        <Label htmlFor="push">Push</Label>
                        <Switch
                          id="push"
                          checked={preferences.push_enabled}
                          onCheckedChange={(checked) =>
                            handleUpdatePreferences({ push_enabled: checked })
                          }
                        />
                      </div>
                    </div>

                    <div className="space-y-4">
                      <h3 className="font-semibold">Categorias</h3>
                      {Object.entries(preferences.categories).map(([key, value]) => (
                        <div key={key} className="flex items-center justify-between">
                          <Label htmlFor={key} className="capitalize">
                            {key}
                          </Label>
                          <Switch
                            id={key}
                            checked={value as boolean}
                            onCheckedChange={(checked) =>
                              handleUpdatePreferences({
                                categories: { ...preferences.categories, [key]: checked },
                              })
                            }
                          />
                        </div>
                      ))}
                    </div>
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
