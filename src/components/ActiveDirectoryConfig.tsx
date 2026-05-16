// =====================================================
// KWANZACONTROL - Active Directory Configuration
// Componente de configuração do Active Directory
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { activeDirectoryService } from '@/services/activeDirectoryService';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Server, RefreshCw, CheckCircle, XCircle, AlertTriangle, History } from 'lucide-react';
import { useToast } from '@/lib/toast-provider';

export function ActiveDirectoryConfig() {
  const { tenant } = useAuth();
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  const [config, setConfig] = useState({
    server_url: '',
    base_dn: '',
    bind_dn: '',
    bind_password: '',
    sync_enabled: false,
    sync_interval: 3600,
  });

  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Fetch existing config
  const { data: existingConfig, isLoading } = useQuery({
    queryKey: ['ad-config', tenant?.id],
    queryFn: () => activeDirectoryService.getADConfig(tenant!.id),
    enabled: !!tenant,
  });

  // Fetch sync history
  const { data: syncHistory } = useQuery({
    queryKey: ['ad-sync-history', tenant?.id],
    queryFn: () => activeDirectoryService.getSyncHistory(tenant!.id),
    enabled: !!tenant,
  });

  // Test connection mutation
  const testConnection = useMutation({
    mutationFn: () => activeDirectoryService.testADConnection(config),
    onSuccess: (data) => {
      setTestResult(data);
      if (data.success) {
        success('Conexão testada', 'Conexão com AD estabelecida com sucesso!');
      } else {
        showError('Erro de conexão', data.message);
      }
    },
  });

  // Save config mutation
  const saveConfig = useMutation({
    mutationFn: () => {
      if (existingConfig) {
        return activeDirectoryService.updateADConfig(existingConfig.id, {
          ...config,
          tenant_id: tenant!.id,
        });
      } else {
        return activeDirectoryService.configureAD({
          ...config,
          tenant_id: tenant!.id,
        });
      }
    },
    onSuccess: () => {
      success('Configuração salva', 'Configuração do AD salva com sucesso!');
      queryClient.invalidateQueries({ queryKey: ['ad-config', tenant?.id] });
    },
    onError: (error: any) => {
      showError('Erro ao salvar', error.message);
    },
  });

  // Sync users mutation
  const syncUsers = useMutation({
    mutationFn: () => activeDirectoryService.syncADUsers(tenant!.id),
    onSuccess: (data) => {
      if (data.success) {
        success(
          'Sincronização concluída',
          `${data.users_synced} utilizadores sincronizados (${data.users_created} criados, ${data.users_updated} atualizados)`
        );
      } else {
        showError('Erro na sincronização', data.errors.join(', '));
      }
      queryClient.invalidateQueries({ queryKey: ['ad-sync-history', tenant?.id] });
    },
  });

  const handleTestConnection = () => {
    testConnection.mutate();
  };

  const handleSaveConfig = () => {
    saveConfig.mutate();
  };

  const handleSyncUsers = () => {
    syncUsers.mutate();
  };

  if (isLoading) {
    return <div className="p-8">Carregando...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Server className="w-6 h-6 text-primary" />
          Integração com Active Directory
        </h2>
        <p className="text-muted-foreground mt-2">
          Configure a sincronização de utilizadores com o Active Directory
        </p>
      </div>

      {/* Configuration Card */}
      <Card>
        <CardHeader>
          <CardTitle>Configuração do Servidor LDAP</CardTitle>
          <CardDescription>
            Insira as credenciais e configurações do seu servidor Active Directory
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="server_url">URL do Servidor</Label>
              <Input
                id="server_url"
                placeholder="ldap://dc.empresa.com:389"
                value={config.server_url}
                onChange={(e) => setConfig({ ...config, server_url: e.target.value })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="base_dn">Base DN</Label>
              <Input
                id="base_dn"
                placeholder="DC=empresa,DC=com"
                value={config.base_dn}
                onChange={(e) => setConfig({ ...config, base_dn: e.target.value })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="bind_dn">Bind DN (Utilizador)</Label>
              <Input
                id="bind_dn"
                placeholder="CN=admin,DC=empresa,DC=com"
                value={config.bind_dn}
                onChange={(e) => setConfig({ ...config, bind_dn: e.target.value })}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="bind_password">Senha</Label>
              <Input
                id="bind_password"
                type="password"
                placeholder="••••••••"
                value={config.bind_password}
                onChange={(e) => setConfig({ ...config, bind_password: e.target.value })}
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-4 border-t">
            <div className="flex items-center space-x-2">
              <Switch
                id="sync_enabled"
                checked={config.sync_enabled}
                onCheckedChange={(checked) => setConfig({ ...config, sync_enabled: checked })}
              />
              <Label htmlFor="sync_enabled">Sincronização Automática</Label>
            </div>

            {config.sync_enabled && (
              <div className="flex items-center gap-2">
                <Label htmlFor="sync_interval">Intervalo (segundos):</Label>
                <Input
                  id="sync_interval"
                  type="number"
                  className="w-24"
                  value={config.sync_interval}
                  onChange={(e) => setConfig({ ...config, sync_interval: parseInt(e.target.value) })}
                />
              </div>
            )}
          </div>

          {testResult && (
            <Alert variant={testResult.success ? 'default' : 'destructive'}>
              {testResult.success ? (
                <CheckCircle className="h-4 w-4" />
              ) : (
                <XCircle className="h-4 w-4" />
              )}
              <AlertDescription>{testResult.message}</AlertDescription>
            </Alert>
          )}

          <div className="flex gap-2 pt-4">
            <Button onClick={handleTestConnection} variant="outline" disabled={testConnection.isPending}>
              {testConnection.isPending ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  Testando...
                </>
              ) : (
                <>
                  <CheckCircle className="w-4 h-4 mr-2" />
                  Testar Conexão
                </>
              )}
            </Button>

            <Button onClick={handleSaveConfig} disabled={saveConfig.isPending}>
              {saveConfig.isPending ? 'Salvando...' : 'Salvar Configuração'}
            </Button>

            <Button onClick={handleSyncUsers} variant="secondary" disabled={syncUsers.isPending || !existingConfig}>
              {syncUsers.isPending ? (
                <>
                  <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                  Sincronizando...
                </>
              ) : (
                <>
                  <RefreshCw className="w-4 h-4 mr-2" />
                  Sincronizar Agora
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Sync History */}
      {syncHistory && syncHistory.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <History className="w-5 h-5" />
              Histórico de Sincronizações
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {syncHistory.map((sync: any, index: number) => (
                <div key={index} className="flex items-center justify-between p-3 border rounded-lg">
                  <div className="flex items-center gap-3">
                    {sync.success ? (
                      <CheckCircle className="w-5 h-5 text-green-500" />
                    ) : (
                      <AlertTriangle className="w-5 h-5 text-orange-500" />
                    )}
                    <div>
                      <p className="font-medium">
                        {new Date(sync.sync_time).toLocaleString('pt-AO')}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {sync.users_synced} sincronizados • {sync.users_created} criados • {sync.users_updated} atualizados
                      </p>
                    </div>
                  </div>
                  <Badge variant={sync.success ? 'default' : 'destructive'}>
                    {sync.success ? 'Sucesso' : 'Erro'}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
