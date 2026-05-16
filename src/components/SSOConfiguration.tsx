// =====================================================
// KWANZACONTROL - SSO Configuration Component
// Componente de configuração de Single Sign-On
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ssoService, SSOProvider } from '@/services/ssoService';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { KeyRound, Chrome, Github, Building2, Shield } from 'lucide-react';
import { useToast } from '@/lib/toast-provider';

const PROVIDER_INFO = {
  google: {
    name: 'Google Workspace',
    icon: Chrome,
    color: 'text-blue-500',
    description: 'Autenticação com contas Google',
  },
  microsoft: {
    name: 'Microsoft 365',
    icon: Building2,
    color: 'text-blue-600',
    description: 'Autenticação com contas Microsoft',
  },
  github: {
    name: 'GitHub',
    icon: Github,
    color: 'text-gray-800',
    description: 'Autenticação com contas GitHub',
  },
  saml: {
    name: 'SAML 2.0',
    icon: Shield,
    color: 'text-purple-500',
    description: 'Autenticação SAML personalizada',
  },
};

export function SSOConfiguration() {
  const { tenant } = useAuth();
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  const [selectedProvider, setSelectedProvider] = useState<SSOProvider>('google');
  const [config, setConfig] = useState({
    client_id: '',
    client_secret: '',
    redirect_uri: `${window.location.origin}/auth/callback`,
    scopes: ['email', 'profile', 'openid'],
  });

  // Fetch SSO configs
  const { data: ssoConfigs, isLoading } = useQuery({
    queryKey: ['sso-configs', tenant?.id],
    queryFn: () => ssoService.getSSOConfigs(tenant!.id),
    enabled: !!tenant,
  });

  // Save config mutation
  const saveConfig = useMutation({
    mutationFn: () =>
      ssoService.configureSSOProvider({
        tenant_id: tenant!.id,
        provider: selectedProvider,
        enabled: true,
        ...config,
      }),
    onSuccess: () => {
      success('Configuração salva', `Provedor ${PROVIDER_INFO[selectedProvider].name} configurado com sucesso!`);
      queryClient.invalidateQueries({ queryKey: ['sso-configs', tenant?.id] });
    },
    onError: (error: any) => {
      showError('Erro ao salvar', error.message);
    },
  });

  // Toggle provider mutation
  const toggleProvider = useMutation({
    mutationFn: ({ configId, enabled }: { configId: string; enabled: boolean }) =>
      ssoService.toggleSSOProvider(configId, enabled),
    onSuccess: () => {
      success('Status atualizado', 'Provedor SSO atualizado com sucesso!');
      queryClient.invalidateQueries({ queryKey: ['sso-configs', tenant?.id] });
    },
  });

  const handleSaveConfig = () => {
    saveConfig.mutate();
  };

  const handleToggleProvider = (configId: string, enabled: boolean) => {
    toggleProvider.mutate({ configId, enabled });
  };

  if (isLoading) {
    return <div className="p-8">Carregando...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <KeyRound className="w-6 h-6 text-primary" />
          Single Sign-On (SSO)
        </h2>
        <p className="text-muted-foreground mt-2">
          Configure provedores de autenticação externa para seus utilizadores
        </p>
      </div>

      {/* Provider Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {Object.entries(PROVIDER_INFO).map(([provider, info]) => {
          const Icon = info.icon;
          const existingConfig = ssoConfigs?.find((c) => c.provider === provider);

          return (
            <Card key={provider} className="relative">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <Icon className={`w-8 h-8 ${info.color}`} />
                  {existingConfig && (
                    <Switch
                      checked={existingConfig.enabled}
                      onCheckedChange={(checked) => handleToggleProvider(existingConfig.id, checked)}
                    />
                  )}
                </div>
                <CardTitle className="text-lg">{info.name}</CardTitle>
                <CardDescription className="text-xs">{info.description}</CardDescription>
              </CardHeader>
              <CardContent>
                {existingConfig ? (
                  <Badge variant={existingConfig.enabled ? 'default' : 'secondary'}>
                    {existingConfig.enabled ? 'Ativo' : 'Inativo'}
                  </Badge>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setSelectedProvider(provider as SSOProvider)}
                  >
                    Configurar
                  </Button>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Configuration Tabs */}
      <Card>
        <CardHeader>
          <CardTitle>Configurar Provedor SSO</CardTitle>
          <CardDescription>
            Insira as credenciais do provedor selecionado
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs value={selectedProvider} onValueChange={(v) => setSelectedProvider(v as SSOProvider)}>
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="google">Google</TabsTrigger>
              <TabsTrigger value="microsoft">Microsoft</TabsTrigger>
              <TabsTrigger value="github">GitHub</TabsTrigger>
              <TabsTrigger value="saml">SAML</TabsTrigger>
            </TabsList>

            {/* Google */}
            <TabsContent value="google" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="google_client_id">Client ID</Label>
                <Input
                  id="google_client_id"
                  placeholder="123456789-abc.apps.googleusercontent.com"
                  value={config.client_id}
                  onChange={(e) => setConfig({ ...config, client_id: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="google_client_secret">Client Secret</Label>
                <Input
                  id="google_client_secret"
                  type="password"
                  placeholder="GOCSPX-..."
                  value={config.client_secret}
                  onChange={(e) => setConfig({ ...config, client_secret: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="google_redirect_uri">Redirect URI</Label>
                <Input
                  id="google_redirect_uri"
                  value={config.redirect_uri}
                  onChange={(e) => setConfig({ ...config, redirect_uri: e.target.value })}
                  readOnly
                />
                <p className="text-xs text-muted-foreground">
                  Configure este URI no Google Cloud Console
                </p>
              </div>
            </TabsContent>

            {/* Microsoft */}
            <TabsContent value="microsoft" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="microsoft_client_id">Application (client) ID</Label>
                <Input
                  id="microsoft_client_id"
                  placeholder="12345678-1234-1234-1234-123456789012"
                  value={config.client_id}
                  onChange={(e) => setConfig({ ...config, client_id: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="microsoft_client_secret">Client Secret</Label>
                <Input
                  id="microsoft_client_secret"
                  type="password"
                  placeholder="..."
                  value={config.client_secret}
                  onChange={(e) => setConfig({ ...config, client_secret: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="microsoft_redirect_uri">Redirect URI</Label>
                <Input
                  id="microsoft_redirect_uri"
                  value={config.redirect_uri}
                  onChange={(e) => setConfig({ ...config, redirect_uri: e.target.value })}
                  readOnly
                />
                <p className="text-xs text-muted-foreground">
                  Configure este URI no Azure Portal
                </p>
              </div>
            </TabsContent>

            {/* GitHub */}
            <TabsContent value="github" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="github_client_id">Client ID</Label>
                <Input
                  id="github_client_id"
                  placeholder="Iv1.1234567890abcdef"
                  value={config.client_id}
                  onChange={(e) => setConfig({ ...config, client_id: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="github_client_secret">Client Secret</Label>
                <Input
                  id="github_client_secret"
                  type="password"
                  placeholder="..."
                  value={config.client_secret}
                  onChange={(e) => setConfig({ ...config, client_secret: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="github_redirect_uri">Authorization callback URL</Label>
                <Input
                  id="github_redirect_uri"
                  value={config.redirect_uri}
                  onChange={(e) => setConfig({ ...config, redirect_uri: e.target.value })}
                  readOnly
                />
                <p className="text-xs text-muted-foreground">
                  Configure este URL nas configurações do OAuth App no GitHub
                </p>
              </div>
            </TabsContent>

            {/* SAML */}
            <TabsContent value="saml" className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="saml_entity_id">Entity ID</Label>
                <Input
                  id="saml_entity_id"
                  placeholder="https://idp.empresa.com/saml"
                  value={config.client_id}
                  onChange={(e) => setConfig({ ...config, client_id: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="saml_sso_url">SSO URL</Label>
                <Input
                  id="saml_sso_url"
                  placeholder="https://idp.empresa.com/sso"
                  value={config.client_secret}
                  onChange={(e) => setConfig({ ...config, client_secret: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="saml_certificate">Certificado X.509</Label>
                <textarea
                  id="saml_certificate"
                  className="w-full min-h-[100px] p-2 border rounded-md"
                  placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                />
              </div>
            </TabsContent>
          </Tabs>

          <div className="flex gap-2 pt-4">
            <Button onClick={handleSaveConfig} disabled={saveConfig.isPending}>
              {saveConfig.isPending ? 'Salvando...' : 'Salvar Configuração'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
