// =====================================================
// KWANZACONTROL - Login with 2FA
// Página de login com autenticação de dois fatores
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { LogIn, Mail, Lock, AlertCircle, Shield, ArrowLeft } from 'lucide-react';
import { ROUTE_PATHS } from '@/lib/index';
import { fadeInUp, scaleIn, springPresets } from '@/lib/motion';
import { useAuth } from '@/hooks/useAuth';
import { TwoFactorVerify } from '@/components/TwoFactorVerify';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';

type LoginStep = 'credentials' | '2fa';

export default function LoginWith2FA() {
  const navigate = useNavigate();
  const { signIn, loading } = useAuth();
  
  // Form state
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [rememberMe, setRememberMe] = useState(false);
  
  // 2FA state
  const [loginStep, setLoginStep] = useState<LoginStep>('credentials');
  const [userId, setUserId] = useState<string>('');
  const [requires2FA, setRequires2FA] = useState(false);

  const handleCredentialsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email || !password) {
      setError('Por favor, preencha todos os campos');
      return;
    }

    try {
      // Attempt sign in
      await signIn(email, password);
      
      // Check if user has 2FA enabled
      // TODO: Get this from user profile
      const user2FAEnabled = false; // Placeholder
      
      if (user2FAEnabled) {
        setUserId('user-id-placeholder');
        setRequires2FA(true);
        setLoginStep('2fa');
      } else {
        // No 2FA required, proceed to dashboard
        navigate(ROUTE_PATHS.DASHBOARD);
      }
    } catch (err: any) {
      setError(err.message || 'Erro ao fazer login');
    }
  };

  const handle2FASuccess = () => {
    // 2FA verified successfully
    navigate(ROUTE_PATHS.DASHBOARD);
  };

  const handle2FAError = (errorMessage: string) => {
    setError(errorMessage);
  };

  const handleBackToCredentials = () => {
    setLoginStep('credentials');
    setError('');
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 px-4 py-12">
      <motion.div
        variants={scaleIn}
        initial="hidden"
        animate="visible"
        className="w-full max-w-md"
      >
        <AnimatePresence mode="wait">
          {loginStep === 'credentials' ? (
            <motion.div
              key="credentials"
              variants={fadeInUp}
              initial="hidden"
              animate="visible"
              exit="hidden"
              className="bg-card border border-border rounded-2xl shadow-lg p-8 space-y-6"
              style={{
                boxShadow: '0 8px 30px -6px color-mix(in srgb, var(--primary) 15%, transparent)',
              }}
            >
              <div className="text-center space-y-2">
                <motion.div
                  initial={{ scale: 0.8, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={springPresets.bouncy}
                  className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary to-primary/80 mb-4"
                >
                  <LogIn className="w-8 h-8 text-primary-foreground" />
                </motion.div>
                <h1 className="text-3xl font-bold tracking-tight text-foreground">
                  KWANZACONTROL
                </h1>
                <p className="text-lg text-muted-foreground">Bem-vindo de volta</p>
              </div>

              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={springPresets.snappy}
                >
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                </motion.div>
              )}

              <form onSubmit={handleCredentialsSubmit} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="email" className="text-sm font-medium">
                    Email
                  </Label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      id="email"
                      type="email"
                      placeholder="seu@email.com"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="pl-10"
                      disabled={loading}
                      autoComplete="email"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="password" className="text-sm font-medium">
                    Senha
                  </Label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      id="password"
                      type="password"
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="pl-10"
                      disabled={loading}
                      autoComplete="current-password"
                    />
                  </div>
                </div>

                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="remember"
                    checked={rememberMe}
                    onCheckedChange={(checked) => setRememberMe(checked as boolean)}
                  />
                  <Label
                    htmlFor="remember"
                    className="text-sm font-normal cursor-pointer"
                  >
                    Lembrar-me
                  </Label>
                </div>

                <Button
                  type="submit"
                  className="w-full"
                  disabled={loading}
                  size="lg"
                >
                  {loading ? 'Entrando...' : 'Entrar'}
                </Button>
              </form>

              <div className="text-center text-sm text-muted-foreground">
                <p>Esqueceu a senha? <a href="#" className="text-primary hover:underline">Recuperar</a></p>
              </div>
            </motion.div>
          ) : (
            <motion.div
              key="2fa"
              variants={fadeInUp}
              initial="hidden"
              animate="visible"
              exit="hidden"
              className="bg-card border border-border rounded-2xl shadow-lg p-8 space-y-6"
              style={{
                boxShadow: '0 8px 30px -6px color-mix(in srgb, var(--primary) 15%, transparent)',
              }}
            >
              <div className="text-center space-y-2">
                <motion.div
                  initial={{ scale: 0.8, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={springPresets.bouncy}
                  className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary to-primary/80 mb-4"
                >
                  <Shield className="w-8 h-8 text-primary-foreground" />
                </motion.div>
                <h1 className="text-2xl font-bold tracking-tight text-foreground">
                  Verificação em Dois Fatores
                </h1>
                <p className="text-sm text-muted-foreground">
                  Digite o código de 6 dígitos do seu aplicativo autenticador
                </p>
              </div>

              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={springPresets.snappy}
                >
                  <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4" />
                    <AlertDescription>{error}</AlertDescription>
                  </Alert>
                </motion.div>
              )}

              <TwoFactorVerify
                userId={userId}
                email={email}
                onVerified={handle2FASuccess}
              />

              <Button
                variant="ghost"
                onClick={handleBackToCredentials}
                className="w-full"
              >
                <ArrowLeft className="w-4 h-4 mr-2" />
                Voltar ao login
              </Button>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
