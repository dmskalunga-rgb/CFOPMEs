import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { MessageSquare, X, Send, ThumbsUp, ThumbsDown, Meh } from 'lucide-react';
import { useToast } from '@/lib/toast-provider';
import { supabase } from '@/integrations/supabase/client';

type FeedbackType = 'bug' | 'feature' | 'improvement' | 'other';
type SatisfactionLevel = 'satisfied' | 'neutral' | 'unsatisfied';

export function FeedbackWidget() {
  const [isOpen, setIsOpen] = useState(false);
  const [type, setType] = useState<FeedbackType>('improvement');
  const [satisfaction, setSatisfaction] = useState<SatisfactionLevel>('neutral');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const { success, error } = useToast();

  const handleSubmit = async () => {
    if (!message.trim()) {
      error('Erro', 'Por favor, escreva sua mensagem');
      return;
    }

    setLoading(true);
    try {
      const { error: submitError } = await supabase
        .from('user_feedback')
        .insert({
          type,
          satisfaction,
          message: message.trim(),
          page: window.location.pathname,
          user_agent: navigator.userAgent,
        });

      if (submitError) throw submitError;

      success('Sucesso', 'Feedback enviado! Obrigado pela sua contribuição.');
      setMessage('');
      setIsOpen(false);
    } catch (err) {
      console.error('Erro ao enviar feedback:', err);
      error('Erro', 'Não foi possível enviar o feedback. Tente novamente.');
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) {
    return (
      <Button
        onClick={() => setIsOpen(true)}
        className="fixed bottom-6 right-6 rounded-full h-14 w-14 shadow-lg z-50"
        size="icon"
      >
        <MessageSquare className="h-6 w-6" />
      </Button>
    );
  }

  return (
    <Card className="fixed bottom-6 right-6 w-96 shadow-2xl z-50 animate-in slide-in-from-bottom-5">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg">Feedback</CardTitle>
            <CardDescription>Ajude-nos a melhorar o KWANZACONTROL</CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setIsOpen(false)}
            className="h-8 w-8"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Tipo de Feedback */}
        <div className="space-y-2">
          <Label>Tipo de Feedback</Label>
          <RadioGroup value={type} onValueChange={(v) => setType(v as FeedbackType)}>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="bug" id="bug" />
              <Label htmlFor="bug" className="font-normal cursor-pointer">
                🐛 Bug / Erro
              </Label>
            </div>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="feature" id="feature" />
              <Label htmlFor="feature" className="font-normal cursor-pointer">
                ✨ Nova Funcionalidade
              </Label>
            </div>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="improvement" id="improvement" />
              <Label htmlFor="improvement" className="font-normal cursor-pointer">
                🚀 Melhoria
              </Label>
            </div>
            <div className="flex items-center space-x-2">
              <RadioGroupItem value="other" id="other" />
              <Label htmlFor="other" className="font-normal cursor-pointer">
                💬 Outro
              </Label>
            </div>
          </RadioGroup>
        </div>

        {/* Nível de Satisfação */}
        <div className="space-y-2">
          <Label>Como você avalia sua experiência?</Label>
          <div className="flex gap-2">
            <Button
              type="button"
              variant={satisfaction === 'satisfied' ? 'default' : 'outline'}
              className="flex-1"
              onClick={() => setSatisfaction('satisfied')}
            >
              <ThumbsUp className="h-4 w-4 mr-2" />
              Satisfeito
            </Button>
            <Button
              type="button"
              variant={satisfaction === 'neutral' ? 'default' : 'outline'}
              className="flex-1"
              onClick={() => setSatisfaction('neutral')}
            >
              <Meh className="h-4 w-4 mr-2" />
              Neutro
            </Button>
            <Button
              type="button"
              variant={satisfaction === 'unsatisfied' ? 'default' : 'outline'}
              className="flex-1"
              onClick={() => setSatisfaction('unsatisfied')}
            >
              <ThumbsDown className="h-4 w-4 mr-2" />
              Insatisfeito
            </Button>
          </div>
        </div>

        {/* Mensagem */}
        <div className="space-y-2">
          <Label htmlFor="message">Sua Mensagem</Label>
          <Textarea
            id="message"
            placeholder="Descreva seu feedback em detalhes..."
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            className="resize-none"
          />
          <p className="text-xs text-muted-foreground">
            Página atual: {window.location.pathname}
          </p>
        </div>

        {/* Botão Enviar */}
        <Button
          onClick={handleSubmit}
          disabled={loading}
          className="w-full"
        >
          {loading ? (
            <>Enviando...</>
          ) : (
            <>
              <Send className="h-4 w-4 mr-2" />
              Enviar Feedback
            </>
          )}
        </Button>
      </CardContent>
    </Card>
  );
}
