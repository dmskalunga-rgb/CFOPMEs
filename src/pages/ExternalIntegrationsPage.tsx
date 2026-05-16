// External Integrations Page - Gerenciamento de Integrações Externas
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { 
  Key, 
  Webhook as WebhookIcon, 
  Plus,
  Trash2,
  Copy,
  CheckCircle,
  XCircle,
  RefreshCw,
  BarChart3,
  Loader2,
  Eye,
  EyeOff
} from 'lucide-react';
import { externalIntegrationsService, APIKey, Webhook, IntegrationStats } from '@/services/externalIntegrationsService';
import { useAuth } from '@/hooks/useAuth';
import { toast } from 'sonner';
import { PageLoader } from '@/components/LoadingStates';
import { NoDataYet } from '@/components/EmptyStates';

export default function ExternalIntegrationsPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<IntegrationStats | null>(null);
  const [apiKeys, setApiKeys] = useState<APIKey[]>([]);
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [showNewKey, setShowNewKey] = useState(false);
  const [newKeyData, setNewKeyData] = useState<APIKey | null>(null);
  const [showWebhookDialog, setShowWebhookDialog] = useState(false);

  useEffect(() => {
    if (user) loadData();
  }, [user]);

  const loadData = async () => {
    if (!user) return;
    try {
      setLoading(true);
      const [statsData, keysData, webhooksData] = await Promise.all([
        externalIntegrationsService.getStats(user.id),
        externalIntegrationsService.listAPIKeys(user.id),
        externalIntegrationsService.listWebhooks(user.id),
      ]);
      setStats(statsData);
      setApiKeys(keysData);
      setWebhooks(webhooksData);
    } catch (error: any) {
      toast.error('Erro ao carregar dados: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateAPIKey = async (name: string) => {
    if (!user) return;
    try {
      const newKey = await externalIntegrationsService.createAPIKey(user.id, name);
      setNewKeyData(newKey);
      setShowNewKey(true);
      loadData();
      toast.success('API Key criada com sucesso!');
    } catch (error: any) {
      toast.error('Erro ao criar API Key: ' + error.message);
    }
  };

  const handleDeleteAPIKey = async (keyId: string) => {
    if (!user || !confirm('Tem certeza que deseja deletar esta API Key?')) return;
    try {
      await externalIntegrationsService.deleteAPIKey(keyId, user.id);
      loadData();
      toast.success('API Key deletada!');
    } catch (error: any) {
      toast.error('Erro ao deletar: ' + error.message);
    }
  };

  const handleCreateWebhook = async (name: string, url: string, events: string[]) => {
    if (!user) return;
    try {
      await externalIntegrationsService.createWebhook(user.id, name, url, events);
      setShowWebhookDialog(false);
      loadData();
      toast.success('Webhook criado com sucesso!');
    } catch (error: any) {
      toast.error('Erro ao criar webhook: ' + error.message);
    }
  };

  const handleTestWebhook = async (webhookId: string) => {
    if (!user) return;
    try {
      const result = await externalIntegrationsService.testWebhook(webhookId, user.id);
      if (result.success) {
        toast.success('Webhook testado com sucesso!');
      } else {
        toast.error('Falha no teste do webhook');
      }
      loadData();
    } catch (error: any) {
      toast.error('Erro ao testar webhook: ' + error.message);
    }
  };

  const handleDeleteWebhook = async (webhookId: string) => {
    if (!user || !confirm('Tem certeza que deseja deletar este webhook?')) return;
    try {
      await externalIntegrationsService.deleteWebhook(webhookId, user.id);
      loadData();
      toast.success('Webhook deletado!');
    } catch (error: any) {
      toast.error('Erro ao deletar: ' + error.message);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('Copiado para área de transferência!');
  };

  if (loading) {
    return (
      <Layout>
        <PageLoader message="Carregando integrações..." />
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
              <Key className="h-8 w-8 text-primary" />
              Integrações Externas
            </h1>
            <p className="text-muted-foreground">
              API Keys, Webhooks e integrações
            </p>
          </div>
          <Button variant="outline" onClick={loadData}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Atualizar
          </Button>
        </div>

        {/* Stats */}
        {stats && (
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  API Keys Ativas
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.active_api_keys}</div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Webhooks Ativos
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.active_webhooks}</div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Requisições (30d)
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{stats.total_requests}</div>
                <p className="text-xs text-muted-foreground">
                  {stats.successful_requests} sucesso
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">
                  Tempo Médio
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {Math.round(stats.avg_response_time)}ms
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Tabs */}
        <Tabs defaultValue="api-keys" className="space-y-4">
          <TabsList>
            <TabsTrigger value="api-keys">
              <Key className="h-4 w-4 mr-2" />
              API Keys
            </TabsTrigger>
            <TabsTrigger value="webhooks">
              <WebhookIcon className="h-4 w-4 mr-2" />
              Webhooks
            </TabsTrigger>
            <TabsTrigger value="docs">
              <BarChart3 className="h-4 w-4 mr-2" />
              Documentação
            </TabsTrigger>
          </TabsList>

          {/* API Keys Tab */}
          <TabsContent value="api-keys">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>API Keys</CardTitle>
                    <CardDescription>
                      Gerencie suas chaves de API para acesso externo
                    </CardDescription>
                  </div>
                  <Dialog>
                    <DialogTrigger asChild>
                      <Button>
                        <Plus className="h-4 w-4 mr-2" />
                        Nova API Key
                      </Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Criar Nova API Key</DialogTitle>
                        <DialogDescription>
                          Dê um nome descritivo para sua API Key
                        </DialogDescription>
                      </DialogHeader>
                      <div className="space-y-4">
                        <div>
                          <Label htmlFor="keyName">Nome</Label>
                          <Input
                            id="keyName"
                            placeholder="Minha Integração"
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                handleCreateAPIKey(e.currentTarget.value);
                              }
                            }}
                          />
                        </div>
                        <Button
                          onClick={() => {
                            const input = document.getElementById('keyName') as HTMLInputElement;
                            if (input?.value) handleCreateAPIKey(input.value);
                          }}
                        >
                          Criar API Key
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                </div>
              </CardHeader>
              <CardContent>
                {apiKeys.length === 0 ? (
                  <NoDataYet onCreate={() => {}} />
                ) : (
                  <div className="space-y-3">
                    {apiKeys.map((key) => (
                      <div key={key.id} className="flex items-center justify-between p-4 border rounded-lg">
                        <div className="flex-1">
                          <p className="font-medium">{key.name}</p>
                          <p className="text-sm text-muted-foreground font-mono">
                            {key.key_prefix}...
                          </p>
                          <div className="flex gap-2 mt-2">
                            <Badge variant={key.is_active ? 'default' : 'secondary'}>
                              {key.is_active ? 'Ativa' : 'Inativa'}
                            </Badge>
                            <Badge variant="outline">
                              {key.rate_limit} req/h
                            </Badge>
                          </div>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteAPIKey(key.id)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Webhooks Tab */}
          <TabsContent value="webhooks">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Webhooks</CardTitle>
                    <CardDescription>
                      Configure webhooks para receber eventos
                    </CardDescription>
                  </div>
                  <Dialog open={showWebhookDialog} onOpenChange={setShowWebhookDialog}>
                    <DialogTrigger asChild>
                      <Button>
                        <Plus className="h-4 w-4 mr-2" />
                        Novo Webhook
                      </Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Criar Novo Webhook</DialogTitle>
                      </DialogHeader>
                      <div className="space-y-4">
                        <div>
                          <Label>Nome</Label>
                          <Input id="webhookName" placeholder="Meu Webhook" />
                        </div>
                        <div>
                          <Label>URL</Label>
                          <Input id="webhookUrl" placeholder="https://api.example.com/webhook" />
                        </div>
                        <Button
                          onClick={() => {
                            const name = (document.getElementById('webhookName') as HTMLInputElement)?.value;
                            const url = (document.getElementById('webhookUrl') as HTMLInputElement)?.value;
                            if (name && url) handleCreateWebhook(name, url, []);
                          }}
                        >
                          Criar Webhook
                        </Button>
                      </div>
                    </DialogContent>
                  </Dialog>
                </div>
              </CardHeader>
              <CardContent>
                {webhooks.length === 0 ? (
                  <NoDataYet onCreate={() => setShowWebhookDialog(true)} />
                ) : (
                  <div className="space-y-3">
                    {webhooks.map((webhook) => (
                      <div key={webhook.id} className="flex items-center justify-between p-4 border rounded-lg">
                        <div className="flex-1">
                          <p className="font-medium">{webhook.name}</p>
                          <p className="text-sm text-muted-foreground">{webhook.url}</p>
                          <div className="flex gap-2 mt-2">
                            <Badge variant={webhook.is_active ? 'default' : 'secondary'}>
                              {webhook.is_active ? 'Ativo' : 'Inativo'}
                            </Badge>
                            {webhook.success_count > 0 && (
                              <Badge variant="outline" className="text-xs">
                                ✓ {webhook.success_count} ok
                              </Badge>
                            )}
                            {webhook.failure_count > 0 && (
                              <Badge variant="destructive" className="text-xs">
                                ✗ {webhook.failure_count}
                              </Badge>
                            )}
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleTestWebhook(webhook.id)}
                          >
                            Testar
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleDeleteWebhook(webhook.id)}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Documentation Tab */}
          <TabsContent value="docs">
            <Card>
              <CardHeader>
                <CardTitle>Documentação da API</CardTitle>
                <CardDescription>
                  Como usar a API REST do KWANZACONTROL
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <h3 className="font-semibold mb-2">Autenticação</h3>
                  <p className="text-sm text-muted-foreground mb-2">
                    Inclua sua API Key no header de todas as requisições:
                  </p>
                  <pre className="bg-muted p-3 rounded text-sm">
                    X-API-Key: kc_your_api_key_here
                  </pre>
                </div>

                <div>
                  <h3 className="font-semibold mb-2">Endpoints Disponíveis</h3>
                  <div className="space-y-2">
                    <div className="border-l-4 border-primary pl-3">
                      <p className="font-mono text-sm">GET /api/invoices</p>
                      <p className="text-xs text-muted-foreground">Listar faturas</p>
                    </div>
                    <div className="border-l-4 border-primary pl-3">
                      <p className="font-mono text-sm">POST /api/invoices</p>
                      <p className="text-xs text-muted-foreground">Criar fatura</p>
                    </div>
                    <div className="border-l-4 border-primary pl-3">
                      <p className="font-mono text-sm">GET /api/customers</p>
                      <p className="text-xs text-muted-foreground">Listar clientes</p>
                    </div>
                  </div>
                </div>

                <div>
                  <h3 className="font-semibold mb-2">Webhooks</h3>
                  <p className="text-sm text-muted-foreground">
                    Configure webhooks para receber notificações de eventos:
                  </p>
                  <ul className="list-disc list-inside text-sm text-muted-foreground mt-2 space-y-1">
                    <li>invoice.created - Nova fatura criada</li>
                    <li>invoice.paid - Fatura paga</li>
                    <li>customer.created - Novo cliente</li>
                    <li>payment.received - Pagamento recebido</li>
                  </ul>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

        {/* New Key Dialog */}
        {showNewKey && newKeyData && (
          <Dialog open={showNewKey} onOpenChange={setShowNewKey}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>API Key Criada!</DialogTitle>
                <DialogDescription>
                  Copie sua API Key agora. Ela não será mostrada novamente.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                <div className="p-4 bg-muted rounded">
                  <p className="text-sm font-mono break-all">{newKeyData.key}</p>
                </div>
                <Button onClick={() => copyToClipboard(newKeyData.key!)}>
                  <Copy className="h-4 w-4 mr-2" />
                  Copiar API Key
                </Button>
              </div>
            </DialogContent>
          </Dialog>
        )}
      </div>
    </Layout>
  );
}
