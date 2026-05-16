import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { CheckCircle, ArrowRight, Sparkles, Database, Rocket } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import { supabase } from '@/integrations/supabase/client';
import { ROUTE_PATHS } from '@/lib/index';

export default function Onboarding() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);

  const handleCreateDemoData = async () => {
    setLoading(true);
    try {
      // Chamar Edge Function para criar dados de demo
      const { data, error } = await supabase.functions.invoke('create_demo_data_2026_04_06', {
        body: {},
      });

      if (error) throw error;

      toast({
        title: '✅ Dados de demonstração criados',
        description: 'Seu sistema está pronto para uso!',
      });

      setStep(4);
    } catch (error: any) {
      console.error('Erro ao criar dados de demo:', error);
      toast({
        title: '⚠️ Aviso',
        description: 'Você pode criar dados manualmente',
        variant: 'default',
      });
      setStep(4);
    } finally {
      setLoading(false);
    }
  };

  const handleFinish = () => {
    navigate(ROUTE_PATHS.DASHBOARD);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-background via-background to-primary/5 p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-4xl"
      >
        <Card className="p-8">
          {/* Progress */}
          <div className="flex items-center justify-center mb-8">
            {[1, 2, 3, 4].map((s) => (
              <div key={s} className="flex items-center">
                <div
                  className={`w-10 h-10 rounded-full flex items-center justify-center ${
                    step >= s ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'
                  }`}
                >
                  {step > s ? <CheckCircle className="w-5 h-5" /> : s}
                </div>
                {s < 4 && <div className="w-16 h-0.5 bg-border mx-2" />}
              </div>
            ))}
          </div>

          {/* Step 1: Bem-vindo */}
          {step === 1 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center space-y-6">
              <Sparkles className="w-16 h-16 text-primary mx-auto" />
              <h1 className="text-3xl font-bold">Bem-vindo ao KWANZACONTROL!</h1>
              <p className="text-muted-foreground max-w-2xl mx-auto">
                Vamos configurar sua conta em 4 passos simples. Leva apenas 2 minutos.
              </p>
              <Button onClick={() => setStep(2)} size="lg">
                Começar <ArrowRight className="ml-2 w-4 h-4" />
              </Button>
            </motion.div>
          )}

          {/* Step 2: Configurações */}
          {step === 2 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
              <h2 className="text-2xl font-bold text-center">Configurações Básicas</h2>
              <p className="text-center text-muted-foreground">
                Suas configurações padrão foram aplicadas. Você pode alterá-las depois em Configurações.
              </p>
              <div className="grid grid-cols-3 gap-4 max-w-2xl mx-auto">
                <Card className="p-4 text-center">
                  <p className="text-sm text-muted-foreground">Moeda</p>
                  <p className="font-semibold">AOA (Kwanza)</p>
                </Card>
                <Card className="p-4 text-center">
                  <p className="text-sm text-muted-foreground">Timezone</p>
                  <p className="font-semibold">Africa/Luanda</p>
                </Card>
                <Card className="p-4 text-center">
                  <p className="text-sm text-muted-foreground">Idioma</p>
                  <p className="font-semibold">Português</p>
                </Card>
              </div>
              <div className="flex justify-center space-x-4">
                <Button variant="outline" onClick={() => setStep(1)}>
                  Voltar
                </Button>
                <Button onClick={() => setStep(3)}>
                  Continuar <ArrowRight className="ml-2 w-4 h-4" />
                </Button>
              </div>
            </motion.div>
          )}

          {/* Step 3: Dados de Demo */}
          {step === 3 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center space-y-6">
              <Database className="w-16 h-16 text-primary mx-auto" />
              <h2 className="text-2xl font-bold">Criar Dados de Demonstração?</h2>
              <p className="text-muted-foreground max-w-2xl mx-auto">
                Podemos criar dados de exemplo (clientes, faturas, funcionários) para você explorar o sistema.
              </p>
              <div className="flex justify-center space-x-4">
                <Button variant="outline" onClick={() => setStep(4)}>
                  Pular
                </Button>
                <Button onClick={handleCreateDemoData} disabled={loading}>
                  {loading ? 'Criando...' : 'Criar Dados de Demo'}
                </Button>
              </div>
            </motion.div>
          )}

          {/* Step 4: Concluído */}
          {step === 4 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-center space-y-6">
              <Rocket className="w-16 h-16 text-primary mx-auto" />
              <h2 className="text-2xl font-bold">Tudo Pronto!</h2>
              <p className="text-muted-foreground max-w-2xl mx-auto">
                Sua conta está configurada. Vamos começar a usar o KWANZACONTROL!
              </p>
              <Button onClick={handleFinish} size="lg">
                Ir para o Dashboard <ArrowRight className="ml-2 w-4 h-4" />
              </Button>
            </motion.div>
          )}
        </Card>
      </motion.div>
    </div>
  );
}
