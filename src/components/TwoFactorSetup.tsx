// =====================================================
// KWANZACONTROL - Two Factor Setup Component
// Configuração de 2FA
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { Shield, Mail, Smartphone, Check } from 'lucide-react';
import { twoFactorService } from '@/services';
import { useAuth } from '@/hooks/useAuth';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';

export function TwoFactorSetup() {
  const { user } = useAuth();
  const [isEnabled, setIsEnabled] = useState(false);
  const [method, setMethod] = useState<'EMAIL' | 'SMS'>('EMAIL');
  const [testCode, setTestCode] = useState('');
  const [sentCode, setSentCode] = useState(false);
  const [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSendTestCode = async () => {
    setLoading(true);
    try {
      await twoFactorService.generateCode(method, user?.email);
      setSentCode(true);
    } catch (error) {
      console.error('Error sending code:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyAndEnable = async () => {
    setLoading(true);
    try {
      const result = await twoFactorService.verifyCode(testCode, user!.id);
      if (result.success) {
        await twoFactorService.enable2FA(user!.id, method);
        setVerified(true);
        setIsEnabled(true);
      }
    } catch (error) {
      console.error('Error verifying code:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleDisable = async () => {
    setLoading(true);
    try {
      await twoFactorService.disable2FA(user!.id);
      setIsEnabled(false);
      setVerified(false);
      setSentCode(false);
      setTestCode('');
    } catch (error) {
      console.error('Error disabling 2FA:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-8 space-y-6 max-w-2xl">
      <div>
        <h1 className="text-3xl font-bold flex items-center gap-2">
          <Shield className="w-8 h-8 text-primary" />
          Autenticação de Dois Fatores (2FA)
        </h1>
        <p className="text-muted-foreground mt-2">
          Adicione uma camada extra de segurança à sua conta
        </p>
      </div>

      <Card className="p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h3 className="font-bold">Ativar 2FA</h3>
            <p className="text-sm text-muted-foreground">
              Requer código de verificação ao fazer login
            </p>
          </div>
          <Switch
            checked={isEnabled}
            onCheckedChange={setIsEnabled}
            disabled={verified}
          />
        </div>

        {isEnabled && !verified && (
          <div className="space-y-4">
            <div>
              <Label>Método de Verificação</Label>
              <RadioGroup value={method} onValueChange={(v) => setMethod(v as any)} className="mt-2">
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="EMAIL" id="email" />
                  <Label htmlFor="email" className="flex items-center gap-2 cursor-pointer">
                    <Mail className="w-4 h-4" />
                    Email ({user?.email})
                  </Label>
                </div>
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="SMS" id="sms" />
                  <Label htmlFor="sms" className="flex items-center gap-2 cursor-pointer">
                    <Smartphone className="w-4 h-4" />
                    SMS (em breve)
                  </Label>
                </div>
              </RadioGroup>
            </div>

            {!sentCode ? (
              <Button onClick={handleSendTestCode} disabled={loading || method === 'SMS'}>
                {loading ? 'Enviando...' : 'Enviar Código de Teste'}
              </Button>
            ) : (
              <div className="space-y-4">
                <div>
                  <Label htmlFor="code">Código de Verificação</Label>
                  <Input
                    id="code"
                    value={testCode}
                    onChange={(e) => setTestCode(e.target.value)}
                    placeholder="000000"
                    maxLength={6}
                    className="text-center text-2xl tracking-widest"
                  />
                </div>
                <Button onClick={handleVerifyAndEnable} disabled={loading || testCode.length !== 6}>
                  {loading ? 'Verificando...' : 'Verificar e Ativar'}
                </Button>
              </div>
            )}
          </div>
        )}

        {verified && (
          <div className="space-y-4">
            <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 p-4 rounded-lg">
              <div className="flex items-center gap-2 text-green-800 dark:text-green-200">
                <Check className="w-5 h-5" />
                <p className="font-medium">2FA Ativado com Sucesso!</p>
              </div>
              <p className="text-sm text-green-700 dark:text-green-300 mt-1">
                Método: {method === 'EMAIL' ? 'Email' : 'SMS'}
              </p>
            </div>
            <Button variant="destructive" onClick={handleDisable} disabled={loading}>
              {loading ? 'Desativando...' : 'Desativar 2FA'}
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

export default TwoFactorSetup;
