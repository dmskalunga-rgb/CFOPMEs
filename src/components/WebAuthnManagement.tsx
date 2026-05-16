// =====================================================
// KWANZACONTROL - WebAuthn Management Component
// Componente de gerenciamento de autenticação biométrica
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { webAuthnService } from '@/services/webAuthnService';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Fingerprint, Smartphone, Key, Trash2, Edit2, CheckCircle, XCircle, AlertTriangle } from 'lucide-react';
import { useToast } from '@/lib/toast-provider';

export function WebAuthnManagement() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  const [isSupported, setIsSupported] = useState(webAuthnService.isSupported());
  const [isPlatformAvailable, setIsPlatformAvailable] = useState(false);
  const [deviceName, setDeviceName] = useState('');
  const [isRegistering, setIsRegistering] = useState(false);

  // Check platform authenticator availability
  useState(() => {
    webAuthnService.isPlatformAuthenticatorAvailable().then(setIsPlatformAvailable);
  });

  // Fetch user credentials
  const { data: credentials, isLoading } = useQuery({
    queryKey: ['webauthn-credentials', user?.id],
    queryFn: () => webAuthnService.getUserCredentials(user!.id),
    enabled: !!user && isSupported,
  });

  // Register credential mutation
  const registerCredential = useMutation({
    mutationFn: async () => {
      setIsRegistering(true);
      try {
        return await webAuthnService.registerCredential(user!.id, deviceName || 'Dispositivo sem nome');
      } finally {
        setIsRegistering(false);
      }
    },
    onSuccess: () => {
      success('Credencial registrada', 'Dispositivo biométrico registrado com sucesso!');
      setDeviceName('');
      queryClient.invalidateQueries({ queryKey: ['webauthn-credentials', user?.id] });
    },
    onError: (error: any) => {
      showError('Erro ao registrar', error.message || 'Falha ao registrar dispositivo biométrico');
    },
  });

  // Remove credential mutation
  const removeCredential = useMutation({
    mutationFn: (credentialId: string) => webAuthnService.removeCredential(credentialId),
    onSuccess: () => {
      success('Credencial removida', 'Dispositivo biométrico removido com sucesso!');
      queryClient.invalidateQueries({ queryKey: ['webauthn-credentials', user?.id] });
    },
    onError: (error: any) => {
      showError('Erro ao remover', error.message);
    },
  });

  const handleRegister = () => {
    registerCredential.mutate();
  };

  const handleRemove = (credentialId: string) => {
    if (confirm('Tem certeza que deseja remover este dispositivo?')) {
      removeCredential.mutate(credentialId);
    }
  };

  const getDeviceIcon = (deviceType: string) => {
    switch (deviceType) {
      case 'platform':
        return <Fingerprint className="w-5 h-5 text-blue-500" />;
      case 'cross-platform':
        return <Key className="w-5 h-5 text-green-500" />;
      default:
        return <Smartphone className="w-5 h-5 text-gray-500" />;
    }
  };

  if (!isSupported) {
    return (
      <Alert variant="destructive">
        <XCircle className="h-4 w-4" />
        <AlertDescription>
          Seu navegador não suporta autenticação biométrica (WebAuthn/FIDO2).
          Por favor, use um navegador moderno como Chrome, Firefox, Safari ou Edge.
        </AlertDescription>
      </Alert>
    );
  }

  if (isLoading) {
    return <div className="p-8">Carregando...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Fingerprint className="w-6 h-6 text-primary" />
          Autenticação Biométrica
        </h2>
        <p className="text-muted-foreground mt-2">
          Gerencie dispositivos de autenticação biométrica (impressão digital, Face ID, chaves de segurança)
        </p>
      </div>

      {/* Support Status */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Suporte do Navegador</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              <CheckCircle className="w-5 h-5 text-green-500" />
              <span>WebAuthn suportado</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Autenticador de Plataforma</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              {isPlatformAvailable ? (
                <>
                  <CheckCircle className="w-5 h-5 text-green-500" />
                  <span>Disponível (Touch ID, Face ID, Windows Hello)</span>
                </>
              ) : (
                <>
                  <AlertTriangle className="w-5 h-5 text-orange-500" />
                  <span>Não disponível neste dispositivo</span>
                </>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Register New Device */}
      <Card>
        <CardHeader>
          <CardTitle>Registrar Novo Dispositivo</CardTitle>
          <CardDescription>
            Adicione um novo dispositivo biométrico ou chave de segurança
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="device_name">Nome do Dispositivo</Label>
            <Input
              id="device_name"
              placeholder="Ex: iPhone 15, YubiKey, Windows Hello"
              value={deviceName}
              onChange={(e) => setDeviceName(e.target.value)}
            />
          </div>

          <Button onClick={handleRegister} disabled={isRegistering}>
            {isRegistering ? (
              'Aguardando autenticação...'
            ) : (
              <>
                <Fingerprint className="w-4 h-4 mr-2" />
                Registrar Dispositivo
              </>
            )}
          </Button>

          <Alert>
            <AlertDescription className="text-sm">
              <strong>Como funciona:</strong> Ao clicar em "Registrar Dispositivo", você será solicitado a
              usar sua impressão digital, Face ID, ou inserir sua chave de segurança. Este dispositivo
              poderá ser usado para fazer login sem senha.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>

      {/* Registered Devices */}
      <Card>
        <CardHeader>
          <CardTitle>Dispositivos Registrados</CardTitle>
          <CardDescription>
            {credentials?.length || 0} dispositivo(s) registrado(s)
          </CardDescription>
        </CardHeader>
        <CardContent>
          {credentials && credentials.length > 0 ? (
            <div className="space-y-3">
              {credentials.map((credential) => (
                <div
                  key={credential.id}
                  className="flex items-center justify-between p-4 border rounded-lg"
                >
                  <div className="flex items-center gap-3">
                    {getDeviceIcon(credential.device_type)}
                    <div>
                      <p className="font-medium">{credential.device_name}</p>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span>Registrado em {new Date(credential.created_at).toLocaleDateString('pt-AO')}</span>
                        {credential.last_used && (
                          <>
                            <span>•</span>
                            <span>Último uso: {new Date(credential.last_used).toLocaleDateString('pt-AO')}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={credential.device_type === 'platform' ? 'default' : 'secondary'}>
                      {credential.device_type === 'platform' ? 'Plataforma' : 'Portátil'}
                    </Badge>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => handleRemove(credential.id)}
                    >
                      <Trash2 className="w-4 h-4 text-destructive" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <Fingerprint className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>Nenhum dispositivo registrado</p>
              <p className="text-sm">Registre um dispositivo para começar a usar autenticação biométrica</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Security Info */}
      <Alert>
        <AlertDescription>
          <strong>Segurança:</strong> Suas credenciais biométricas nunca saem do seu dispositivo.
          Apenas uma chave criptográfica é armazenada em nossos servidores, garantindo máxima segurança.
        </AlertDescription>
      </Alert>
    </div>
  );
}
