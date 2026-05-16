// =====================================================
// KWANZACONTROL - Two Factor Verify Component
// Verificação de 2FA no Login
// Data: 2026-04-04
// =====================================================

import { useState, useEffect } from 'react';
import { Shield, RefreshCw } from 'lucide-react';
import { twoFactorService } from '@/services';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface TwoFactorVerifyProps {
  userId: string;
  email: string;
  onVerified: () => void;
}

export function TwoFactorVerify({ userId, email, onVerified }: TwoFactorVerifyProps) {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [timeRemaining, setTimeRemaining] = useState(600); // 10 minutos

  useEffect(() => {
    const timer = setInterval(() => {
      setTimeRemaining((prev) => {
        if (prev <= 0) {
          clearInterval(timer);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const handleVerify = async () => {
    if (code.length !== 6) return;

    setLoading(true);
    setError('');

    try {
      const result = await twoFactorService.verifyCode(code, userId);
      if (result.success) {
        onVerified();
      } else {
        setError('Código inválido ou expirado');
      }
    } catch (err) {
      setError('Erro ao verificar código');
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    setLoading(true);
    setError('');

    try {
      await twoFactorService.generateCode('EMAIL', email);
      setTimeRemaining(600);
      setCode('');
    } catch (err) {
      setError('Erro ao reenviar código');
    } finally {
      setLoading(false);
    }
  };

  const minutes = Math.floor(timeRemaining / 60);
  const seconds = timeRemaining % 60;

  return (
    <Card className="p-8 max-w-md mx-auto">
      <div className="text-center mb-6">
        <Shield className="w-16 h-16 mx-auto text-primary mb-4" />
        <h2 className="text-2xl font-bold">Verificação de Dois Fatores</h2>
        <p className="text-muted-foreground mt-2">
          Digite o código de 6 dígitos enviado para {email}
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <Label htmlFor="code">Código de Verificação</Label>
          <Input
            id="code"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="000000"
            maxLength={6}
            className="text-center text-3xl tracking-widest font-bold"
            autoFocus
          />
        </div>

        {error && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-3 rounded text-sm text-red-800 dark:text-red-200">
            {error}
          </div>
        )}

        <div className="text-center text-sm text-muted-foreground">
          Código expira em: {minutes}:{seconds.toString().padStart(2, '0')}
        </div>

        <Button
          onClick={handleVerify}
          disabled={loading || code.length !== 6 || timeRemaining === 0}
          className="w-full"
        >
          {loading ? 'Verificando...' : 'Verificar'}
        </Button>

        <Button
          variant="outline"
          onClick={handleResend}
          disabled={loading}
          className="w-full"
        >
          <RefreshCw className="w-4 h-4 mr-2" />
          Reenviar Código
        </Button>
      </div>
    </Card>
  );
}

export default TwoFactorVerify;
