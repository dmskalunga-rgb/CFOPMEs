import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Building2, Mail, Lock, User, Eye, EyeOff, Loader2, CheckCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';
import { ROUTE_PATHS } from '@/lib/index';

export default function Register() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [acceptedTerms, setAcceptedTerms] = useState(false);

  // Form data
  const [companyName, setCompanyName] = useState('');
  const [companyNif, setCompanyNif] = useState('');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const handleNextStep = () => {
    if (step === 1) {
      if (!companyName || !companyNif) {
        toast({
          title: '⚠️ Campos obrigatórios',
          description: 'Por favor, preencha todos os campos da empresa',
          variant: 'destructive',
        });
        return;
      }
    }
    setStep(step + 1);
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();

    if (password !== confirmPassword) {
      toast({
        title: '❌ Senhas não coincidem',
        description: 'As senhas devem ser iguais',
        variant: 'destructive',
      });
      return;
    }

    if (password.length < 8) {
      toast({
        title: '❌ Senha fraca',
        description: 'A senha deve ter pelo menos 8 caracteres',
        variant: 'destructive',
      });
      return;
    }

    if (!acceptedTerms) {
      toast({
        title: '⚠️ Termos não aceitos',
        description: 'Você deve aceitar os termos de serviço',
        variant: 'destructive',
      });
      return;
    }

    setLoading(true);

    try {
      // 1. Criar usuário no Supabase Auth
      const { data: authData, error: authError } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            full_name: fullName,
            company_name: companyName,
            company_nif: companyNif,
          },
          emailRedirectTo: `${window.location.origin}${ROUTE_PATHS.ONBOARDING || '/onboarding'}`,
        },
      });

      if (authError) throw authError;

      if (authData.user) {
        // Try to create organization and profile, but don't fail if tables don't exist
        try {
          // 2. Criar organização (optional)
          const { data: orgData, error: orgError } = await supabase
            .from('organizations')
            .insert({
              name: companyName,
              slug: companyName.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
              domain: email.split('@')[1],
              settings: {
                nif: companyNif,
                timezone: 'Africa/Luanda',
                currency: 'AOA',
                language: 'pt-PT',
              },
              status: 'active',
            })
            .select()
            .single();

          if (!orgError && orgData) {
            // 3. Criar perfil de usuário (optional)
            await supabase
              .from('user_profiles_iam')
              .insert({
                id: authData.user.id,
                organization_id: orgData.id,
                email,
                full_name: fullName,
                status: 'active',
                metadata: {
                  company_name: companyName,
                  company_nif: companyNif,
                },
              });
          }
        } catch (profileError) {
          // Log but don't fail - tables might not exist yet
          console.warn('Could not create organization/profile (tables may not exist):', profileError);
        }

        toast({
          title: '✅ Conta criada com sucesso!',
          description: 'Verifique seu email para confirmar sua conta',
        });

        // Redirecionar para onboarding
        navigate(ROUTE_PATHS.ONBOARDING || '/onboarding');
      }
    } catch (error: any) {
      console.error('Erro ao registrar:', error);
      toast({
        title: '❌ Erro ao criar conta',
        description: error.message || 'Tente novamente mais tarde',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-2xl"
      >
        <Card className="border-2">
          <CardHeader className="space-y-1">
            <div className="flex items-center justify-center mb-4">
              <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center">
                <Building2 className="w-8 h-8 text-primary" />
              </div>
            </div>
            <CardTitle className="text-2xl text-center">Criar conta</CardTitle>
            <CardDescription className="text-center">
              Comece seu trial gratuito de 14 dias
            </CardDescription>
          </CardHeader>

          <CardContent>
            {/* Progress Steps */}
            <div className="flex items-center justify-center mb-8">
              <div className="flex items-center space-x-4">
                <div className={`flex items-center ${step >= 1 ? 'text-primary' : 'text-muted-foreground'}`}>
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center border-2 ${step >= 1 ? 'border-primary bg-primary text-primary-foreground' : 'border-muted'}`}>
                    {step > 1 ? <CheckCircle className="w-5 h-5" /> : '1'}
                  </div>
                  <span className="ml-2 text-sm font-medium hidden sm:inline">Empresa</span>
                </div>
                <div className="w-12 h-0.5 bg-border" />
                <div className={`flex items-center ${step >= 2 ? 'text-primary' : 'text-muted-foreground'}`}>
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center border-2 ${step >= 2 ? 'border-primary bg-primary text-primary-foreground' : 'border-muted'}`}>
                    {step > 2 ? <CheckCircle className="w-5 h-5" /> : '2'}
                  </div>
                  <span className="ml-2 text-sm font-medium hidden sm:inline">Conta</span>
                </div>
              </div>
            </div>

            <form onSubmit={handleRegister} className="space-y-4">
              {step === 1 && (
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="space-y-4"
                >
                  <div className="space-y-2">
                    <Label htmlFor="companyName">Nome da Empresa *</Label>
                    <div className="relative">
                      <Building2 className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="companyName"
                        placeholder="Sua Empresa Lda"
                        value={companyName}
                        onChange={(e) => setCompanyName(e.target.value)}
                        className="pl-10"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="companyNif">NIF da Empresa *</Label>
                    <Input
                      id="companyNif"
                      placeholder="123456789"
                      value={companyNif}
                      onChange={(e) => setCompanyNif(e.target.value)}
                      required
                    />
                  </div>

                  <Button type="button" onClick={handleNextStep} className="w-full">
                    Continuar
                  </Button>
                </motion.div>
              )}

              {step === 2 && (
                <motion.div
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="space-y-4"
                >
                  <div className="space-y-2">
                    <Label htmlFor="fullName">Nome Completo *</Label>
                    <div className="relative">
                      <User className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="fullName"
                        placeholder="João Silva"
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                        className="pl-10"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="email">Email *</Label>
                    <div className="relative">
                      <Mail className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="email"
                        type="email"
                        placeholder="joao@empresa.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        className="pl-10"
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="password">Senha *</Label>
                    <div className="relative">
                      <Lock className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="password"
                        type={showPassword ? 'text' : 'password'}
                        placeholder="••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        className="pl-10 pr-10"
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-3 top-3 text-muted-foreground hover:text-foreground"
                      >
                        {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    <p className="text-xs text-muted-foreground">Mínimo 8 caracteres</p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="confirmPassword">Confirmar Senha *</Label>
                    <div className="relative">
                      <Lock className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="confirmPassword"
                        type={showPassword ? 'text' : 'password'}
                        placeholder="••••••••"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        className="pl-10"
                        required
                      />
                    </div>
                  </div>

                  <div className="flex items-start space-x-2">
                    <Checkbox
                      id="terms"
                      checked={acceptedTerms}
                      onCheckedChange={(checked) => setAcceptedTerms(checked as boolean)}
                    />
                    <label
                      htmlFor="terms"
                      className="text-sm text-muted-foreground leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                    >
                      Aceito os{' '}
                      <a href="#" className="text-primary hover:underline">
                        Termos de Serviço
                      </a>{' '}
                      e{' '}
                      <a href="#" className="text-primary hover:underline">
                        Política de Privacidade
                      </a>
                    </label>
                  </div>

                  <div className="flex space-x-4">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setStep(1)}
                      className="w-full"
                    >
                      Voltar
                    </Button>
                    <Button type="submit" className="w-full" disabled={loading}>
                      {loading ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Criando conta...
                        </>
                      ) : (
                        'Criar conta'
                      )}
                    </Button>
                  </div>
                </motion.div>
              )}
            </form>
          </CardContent>

          <CardFooter className="flex flex-col space-y-4">
            <div className="text-sm text-center text-muted-foreground">
              Já tem uma conta?{' '}
              <Link to={ROUTE_PATHS.LOGIN} className="text-primary hover:underline font-medium">
                Fazer login
              </Link>
            </div>
          </CardFooter>
        </Card>
      </motion.div>
    </div>
  );
}
