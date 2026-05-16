// =====================================================
// KWANZACONTROL - Transaction Form with AI
// Formulário de transação com sugestão de IA
// Data: 2026-04-04
// =====================================================

import { useState, useEffect } from 'react';
import { TransactionForm } from '@/components/Forms';
import { supabase } from '@/integrations/supabase/client';
import { Sparkles, Loader2 } from 'lucide-react';

interface TransactionFormWithAIProps {
  onSubmit: (data: any) => void;
}

export function TransactionFormWithAI({ onSubmit }: TransactionFormWithAIProps) {
  const [aiSuggestion, setAiSuggestion] = useState<string>('');
  const [isLoadingAI, setIsLoadingAI] = useState(false);
  const [formData, setFormData] = useState<any>(null);

  // Debounce para chamar IA após usuário parar de digitar
  useEffect(() => {
    if (!formData?.description || formData.description.length < 5) {
      setAiSuggestion('');
      return;
    }

    const timer = setTimeout(() => {
      fetchAISuggestion();
    }, 1000); // 1 segundo após parar de digitar

    return () => clearTimeout(timer);
  }, [formData?.description, formData?.amount, formData?.type]);

  const fetchAISuggestion = async () => {
    if (!formData?.description || !formData?.type) return;

    setIsLoadingAI(true);
    try {
      const { data, error } = await supabase.functions.invoke('transaction_ai_suggestion_2026_04_04', {
        body: {
          description: formData.description,
          amount: formData.amount || 0,
          type: formData.type,
          tenant_id: 'YOUR_TENANT_ID', // TODO: Obter do contexto
        },
      });

      if (error) {
        console.error('Erro ao buscar sugestão de IA:', error);
        setAiSuggestion('Não foi possível obter sugestão de IA no momento.');
      } else if (data?.suggestion) {
        setAiSuggestion(data.suggestion);
      }
    } catch (err) {
      console.error('Erro:', err);
      setAiSuggestion('');
    } finally {
      setIsLoadingAI(false);
    }
  };

  const handleFormChange = (data: any) => {
    setFormData(data);
  };

  return (
    <div className="space-y-4">
      <TransactionForm onSubmit={onSubmit} onChange={handleFormChange} />
      
      {/* Sugestão de IA */}
      {(aiSuggestion || isLoadingAI) && (
        <div className="p-4 bg-accent/10 rounded-lg border border-accent/20">
          <div className="flex items-start gap-3">
            {isLoadingAI ? (
              <Loader2 className="h-5 w-5 text-accent mt-0.5 animate-spin" />
            ) : (
              <Sparkles className="h-5 w-5 text-accent mt-0.5" />
            )}
            <div>
              <p className="text-sm font-medium">Sugestão de IA</p>
              <p className="text-sm text-muted-foreground mt-1">
                {isLoadingAI ? 'Analisando transação...' : aiSuggestion}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
