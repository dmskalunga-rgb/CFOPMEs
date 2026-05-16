// UX/UI Showcase Page - Demonstração de melhorias UX/UI
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { 
  Sparkles, 
  Loader2, 
  Inbox, 
  Keyboard,
  GraduationCap,
  Zap
} from 'lucide-react';

// Import UX components
import {
  Skeleton,
  CardSkeleton,
  TableSkeleton,
  ListSkeleton,
  Spinner,
  PageLoader,
  InlineLoader,
  ProgressBar,
  DotsLoader,
} from '@/components/LoadingStates';

import {
  EmptyState,
  NoResultsFound,
  NoDataYet,
  NoUsersFound,
  ErrorState,
  SuccessState,
  ComingSoonState,
} from '@/components/EmptyStates';

import { KeyboardShortcutsHelp, useKeyboardShortcuts } from '@/components/KeyboardShortcuts';
import { useOnboarding } from '@/components/OnboardingTour';

export default function UXShowcasePage() {
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(45);
  const [showShortcuts, setShowShortcuts] = useState(false);

  // Keyboard shortcuts
  useKeyboardShortcuts();

  // Onboarding tour
  const { TourComponent, resetTour } = useOnboarding('ux-showcase', [
    {
      target: '[data-tour="loading"]',
      title: 'Estados de Carregamento',
      description: 'Veja diferentes tipos de loading states para melhor UX',
      position: 'right',
    },
    {
      target: '[data-tour="empty"]',
      title: 'Estados Vazios',
      description: 'Estados vazios com ilustrações e ações sugeridas',
      position: 'right',
    },
    {
      target: '[data-tour="shortcuts"]',
      title: 'Atalhos de Teclado',
      description: 'Navegue rapidamente usando atalhos de teclado',
      position: 'right',
    },
  ]);

  const simulateLoading = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 2000);
  };

  const simulateProgress = () => {
    setProgress(0);
    const interval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(interval);
          return 100;
        }
        return prev + 10;
      });
    }, 200);
  };

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Sparkles className="h-8 w-8 text-primary" />
              Melhorias UX/UI
            </h1>
            <p className="text-muted-foreground">
              Componentes e padrões para uma experiência premium
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={resetTour}>
              <GraduationCap className="h-4 w-4 mr-2" />
              Iniciar Tour
            </Button>
            <Button variant="outline" onClick={() => setShowShortcuts(true)}>
              <Keyboard className="h-4 w-4 mr-2" />
              Atalhos (?)
            </Button>
          </div>
        </div>

        {/* Stats */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Componentes
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">20+</div>
              <p className="text-xs text-muted-foreground">Novos componentes UX</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Loading States
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">8</div>
              <p className="text-xs text-muted-foreground">Tipos diferentes</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Empty States
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">7</div>
              <p className="text-xs text-muted-foreground">Estados vazios</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Atalhos
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">8+</div>
              <p className="text-xs text-muted-foreground">Atalhos de teclado</p>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="loading" className="space-y-4">
          <TabsList>
            <TabsTrigger value="loading" data-tour="loading">
              <Loader2 className="h-4 w-4 mr-2" />
              Loading States
            </TabsTrigger>
            <TabsTrigger value="empty" data-tour="empty">
              <Inbox className="h-4 w-4 mr-2" />
              Empty States
            </TabsTrigger>
            <TabsTrigger value="shortcuts" data-tour="shortcuts">
              <Keyboard className="h-4 w-4 mr-2" />
              Atalhos
            </TabsTrigger>
            <TabsTrigger value="animations">
              <Zap className="h-4 w-4 mr-2" />
              Animações
            </TabsTrigger>
          </TabsList>

          {/* Loading States Tab */}
          <TabsContent value="loading" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Estados de Carregamento</CardTitle>
                <CardDescription>
                  Diferentes tipos de loading states para melhor feedback visual
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Skeleton */}
                <div>
                  <h3 className="font-semibold mb-3 flex items-center gap-2">
                    Skeleton Loaders
                    <Badge variant="secondary">Recomendado</Badge>
                  </h3>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <p className="text-sm text-muted-foreground mb-2">Card Skeleton</p>
                      <CardSkeleton />
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground mb-2">List Skeleton</p>
                      <ListSkeleton items={2} />
                    </div>
                  </div>
                </div>

                {/* Spinners */}
                <div>
                  <h3 className="font-semibold mb-3">Spinners</h3>
                  <div className="flex items-center gap-8">
                    <div className="text-center">
                      <Spinner size="sm" />
                      <p className="text-xs text-muted-foreground mt-2">Small</p>
                    </div>
                    <div className="text-center">
                      <Spinner size="md" />
                      <p className="text-xs text-muted-foreground mt-2">Medium</p>
                    </div>
                    <div className="text-center">
                      <Spinner size="lg" />
                      <p className="text-xs text-muted-foreground mt-2">Large</p>
                    </div>
                    <div className="text-center">
                      <DotsLoader />
                      <p className="text-xs text-muted-foreground mt-2">Dots</p>
                    </div>
                  </div>
                </div>

                {/* Progress Bar */}
                <div>
                  <h3 className="font-semibold mb-3">Progress Bar</h3>
                  <div className="space-y-4">
                    <div>
                      <div className="flex justify-between text-sm mb-2">
                        <span>Upload Progress</span>
                        <span className="text-muted-foreground">{progress}%</span>
                      </div>
                      <ProgressBar progress={progress} />
                    </div>
                    <Button onClick={simulateProgress} size="sm">
                      Simular Progresso
                    </Button>
                  </div>
                </div>

                {/* Inline Loader */}
                <div>
                  <h3 className="font-semibold mb-3">Inline Loader</h3>
                  <div className="border rounded-lg">
                    <InlineLoader message="Carregando dados..." />
                  </div>
                </div>

                {/* Demo Button */}
                <div className="pt-4 border-t">
                  <Button onClick={simulateLoading} disabled={loading}>
                    {loading ? (
                      <>
                        <Spinner size="sm" className="mr-2" />
                        Carregando...
                      </>
                    ) : (
                      'Testar Loading'
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Empty States Tab */}
          <TabsContent value="empty" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Estados Vazios</CardTitle>
                <CardDescription>
                  Estados vazios com ilustrações e ações sugeridas
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-6 md:grid-cols-2">
                  <div className="border rounded-lg p-4">
                    <NoResultsFound onReset={() => alert('Filtros limpos!')} />
                  </div>
                  <div className="border rounded-lg p-4">
                    <NoDataYet onCreate={() => alert('Criar item!')} />
                  </div>
                  <div className="border rounded-lg p-4">
                    <NoUsersFound onInvite={() => alert('Convidar usuários!')} />
                  </div>
                  <div className="border rounded-lg p-4">
                    <ErrorState onRetry={() => alert('Tentando novamente!')} />
                  </div>
                  <div className="border rounded-lg p-4">
                    <SuccessState 
                      message="Operação concluída com sucesso!" 
                      onContinue={() => alert('Continuar!')} 
                    />
                  </div>
                  <div className="border rounded-lg p-4">
                    <ComingSoonState />
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Shortcuts Tab */}
          <TabsContent value="shortcuts" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Atalhos de Teclado</CardTitle>
                <CardDescription>
                  Navegue rapidamente usando atalhos de teclado
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="p-4 bg-muted rounded-lg">
                    <p className="text-sm mb-2">
                      Pressione <Badge variant="secondary" className="font-mono">?</Badge> ou{' '}
                      <Badge variant="secondary" className="font-mono">Shift + ?</Badge> para ver todos os atalhos
                    </p>
                  </div>

                  <div className="space-y-3">
                    {[
                      { keys: ['Ctrl', 'D'], description: 'Ir para Dashboard' },
                      { keys: ['Ctrl', 'F'], description: 'Ir para Faturação' },
                      { keys: ['Ctrl', 'U'], description: 'Ir para Utilizadores' },
                      { keys: ['Ctrl', 'S'], description: 'Ir para Configurações' },
                      { keys: ['Ctrl', 'P'], description: 'Ir para Performance' },
                      { keys: ['Ctrl', 'K'], description: 'Abrir busca rápida' },
                      { keys: ['/'], description: 'Focar na busca' },
                      { keys: ['?'], description: 'Mostrar atalhos' },
                    ].map((shortcut, index) => (
                      <div key={index} className="flex items-center justify-between py-2 border-b">
                        <span className="text-sm">{shortcut.description}</span>
                        <div className="flex gap-1">
                          {shortcut.keys.map((key, i) => (
                            <Badge key={i} variant="secondary" className="font-mono">
                              {key}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>

                  <Button onClick={() => setShowShortcuts(true)}>
                    Ver Todos os Atalhos
                  </Button>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Animations Tab */}
          <TabsContent value="animations" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Animações e Transições</CardTitle>
                <CardDescription>
                  Micro-interações para melhor feedback visual
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-6">
                  <div>
                    <h3 className="font-semibold mb-3">Hover Effects</h3>
                    <div className="flex gap-4">
                      <Button className="transition-transform hover:scale-105">
                        Scale on Hover
                      </Button>
                      <Button className="transition-all hover:shadow-lg">
                        Shadow on Hover
                      </Button>
                      <Button className="transition-colors hover:bg-primary/90">
                        Color Transition
                      </Button>
                    </div>
                  </div>

                  <div>
                    <h3 className="font-semibold mb-3">Fade In Animation</h3>
                    <div className="animate-in fade-in duration-500">
                      <Card>
                        <CardContent className="p-6">
                          <p>Este card aparece com fade in</p>
                        </CardContent>
                      </Card>
                    </div>
                  </div>

                  <div>
                    <h3 className="font-semibold mb-3">Slide In Animation</h3>
                    <div className="animate-in slide-in-from-left duration-500">
                      <Card>
                        <CardContent className="p-6">
                          <p>Este card desliza da esquerda</p>
                        </CardContent>
                      </Card>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>

      {/* Keyboard Shortcuts Modal */}
      {showShortcuts && <KeyboardShortcutsHelp />}

      {/* Onboarding Tour */}
      {TourComponent}
    </Layout>
  );
}
