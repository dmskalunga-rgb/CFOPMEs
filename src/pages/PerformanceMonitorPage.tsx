// Performance Monitor Page - Monitoramento de Performance
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { 
  Activity, 
  Zap, 
  Database, 
  Clock, 
  TrendingUp,
  RefreshCw,
  CheckCircle,
  AlertTriangle,
  XCircle
} from 'lucide-react';
import { getPerformanceMetrics } from '@/lib/performance';
import { cacheService } from '@/services/cacheService';

export default function PerformanceMonitorPage() {
  const [metrics, setMetrics] = useState<any>(null);
  const [cacheStats, setCacheStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const loadMetrics = () => {
    setLoading(true);
    
    // Performance Metrics
    const perfMetrics = getPerformanceMetrics();
    setMetrics(perfMetrics);

    // Cache Stats
    const stats = cacheService.getStats();
    setCacheStats(stats);

    setLoading(false);
  };

  useEffect(() => {
    loadMetrics();
  }, []);

  const getScoreColor = (score: number) => {
    if (score < 1000) return 'text-green-600';
    if (score < 3000) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getScoreBadge = (score: number) => {
    if (score < 1000) return { variant: 'default' as const, label: 'Excelente', icon: CheckCircle };
    if (score < 3000) return { variant: 'secondary' as const, label: 'Bom', icon: AlertTriangle };
    return { variant: 'destructive' as const, label: 'Precisa Melhorar', icon: XCircle };
  };

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center h-96">
          <div className="text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary mx-auto mb-4"></div>
            <p className="text-muted-foreground">Carregando métricas...</p>
          </div>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Monitor de Performance</h1>
            <p className="text-muted-foreground">
              Métricas e otimizações do sistema
            </p>
          </div>
          <Button onClick={loadMetrics}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Atualizar
          </Button>
        </div>

        {/* Overview Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Tempo de Carregamento
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <p className={`text-2xl font-bold ${getScoreColor(metrics?.loadTime || 0)}`}>
                  {metrics?.loadTime ? `${(metrics.loadTime / 1000).toFixed(2)}s` : 'N/A'}
                </p>
                <Clock className="h-8 w-8 text-muted-foreground" />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                First Paint
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <p className={`text-2xl font-bold ${getScoreColor(metrics?.firstPaint || 0)}`}>
                  {metrics?.firstPaint ? `${(metrics.firstPaint / 1000).toFixed(2)}s` : 'N/A'}
                </p>
                <Zap className="h-8 w-8 text-muted-foreground" />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Cache Size
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                <p className="text-2xl font-bold">
                  {cacheStats?.size || 0}
                </p>
                <Database className="h-8 w-8 text-muted-foreground" />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Score Geral
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between">
                {(() => {
                  const score = metrics?.loadTime || 0;
                  const badge = getScoreBadge(score);
                  const Icon = badge.icon;
                  return (
                    <>
                      <Badge variant={badge.variant}>{badge.label}</Badge>
                      <Icon className="h-8 w-8" />
                    </>
                  );
                })()}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="metrics" className="space-y-4">
          <TabsList>
            <TabsTrigger value="metrics">
              <Activity className="h-4 w-4 mr-2" />
              Métricas
            </TabsTrigger>
            <TabsTrigger value="cache">
              <Database className="h-4 w-4 mr-2" />
              Cache
            </TabsTrigger>
            <TabsTrigger value="optimizations">
              <TrendingUp className="h-4 w-4 mr-2" />
              Otimizações
            </TabsTrigger>
          </TabsList>

          {/* Metrics Tab */}
          <TabsContent value="metrics" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Métricas de Performance</CardTitle>
                <CardDescription>
                  Tempos de carregamento e renderização
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <p className="font-medium">Tempo Total de Carregamento</p>
                      <p className="text-sm text-muted-foreground">
                        Tempo desde o início até o load completo
                      </p>
                    </div>
                    <p className={`text-xl font-bold ${getScoreColor(metrics?.loadTime || 0)}`}>
                      {metrics?.loadTime ? `${(metrics.loadTime / 1000).toFixed(2)}s` : 'N/A'}
                    </p>
                  </div>

                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <p className="font-medium">DOM Content Loaded</p>
                      <p className="text-sm text-muted-foreground">
                        Tempo até o DOM estar pronto
                      </p>
                    </div>
                    <p className={`text-xl font-bold ${getScoreColor(metrics?.domContentLoaded || 0)}`}>
                      {metrics?.domContentLoaded ? `${(metrics.domContentLoaded / 1000).toFixed(2)}s` : 'N/A'}
                    </p>
                  </div>

                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <p className="font-medium">First Paint</p>
                      <p className="text-sm text-muted-foreground">
                        Tempo até o primeiro pixel ser renderizado
                      </p>
                    </div>
                    <p className={`text-xl font-bold ${getScoreColor(metrics?.firstPaint || 0)}`}>
                      {metrics?.firstPaint ? `${(metrics.firstPaint / 1000).toFixed(2)}s` : 'N/A'}
                    </p>
                  </div>

                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <p className="font-medium">First Contentful Paint</p>
                      <p className="text-sm text-muted-foreground">
                        Tempo até o primeiro conteúdo ser renderizado
                      </p>
                    </div>
                    <p className={`text-xl font-bold ${getScoreColor(metrics?.firstContentfulPaint || 0)}`}>
                      {metrics?.firstContentfulPaint ? `${(metrics.firstContentfulPaint / 1000).toFixed(2)}s` : 'N/A'}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Cache Tab */}
          <TabsContent value="cache" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Estatísticas de Cache</CardTitle>
                <CardDescription>
                  Informações sobre o cache do sistema
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-4 border rounded-lg">
                    <div>
                      <p className="font-medium">Itens em Cache</p>
                      <p className="text-sm text-muted-foreground">
                        Número total de itens armazenados
                      </p>
                    </div>
                    <p className="text-xl font-bold">{cacheStats?.size || 0}</p>
                  </div>

                  <div className="p-4 border rounded-lg">
                    <p className="font-medium mb-2">Chaves em Cache</p>
                    {cacheStats?.keys && cacheStats.keys.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {cacheStats.keys.map((key: string) => (
                          <Badge key={key} variant="secondary">
                            {key}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">Nenhum item em cache</p>
                    )}
                  </div>

                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        cacheService.clear();
                        loadMetrics();
                      }}
                    >
                      Limpar Cache
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        cacheService.cleanup();
                        loadMetrics();
                      }}
                    >
                      Limpar Expirados
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Optimizations Tab */}
          <TabsContent value="optimizations" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Otimizações Implementadas</CardTitle>
                <CardDescription>
                  Recursos de performance ativos no sistema
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[
                    { name: 'Lazy Loading de Rotas', status: 'Ativo', description: 'Páginas carregadas sob demanda' },
                    { name: 'Code Splitting', status: 'Ativo', description: 'Código dividido em chunks menores' },
                    { name: 'Cache de API', status: 'Ativo', description: 'Respostas de API em cache' },
                    { name: 'Lazy Loading de Imagens', status: 'Ativo', description: 'Imagens carregadas quando visíveis' },
                    { name: 'Debounce em Inputs', status: 'Ativo', description: 'Reduz chamadas de API em buscas' },
                    { name: 'Virtual Scrolling', status: 'Disponível', description: 'Para listas grandes' },
                    { name: 'Memoização', status: 'Ativo', description: 'Cache de resultados de funções' },
                    { name: 'Prefetch de Links', status: 'Disponível', description: 'Pré-carregamento de páginas' },
                  ].map((opt, index) => (
                    <div key={index} className="flex items-center justify-between p-3 border rounded-lg">
                      <div>
                        <p className="font-medium">{opt.name}</p>
                        <p className="text-sm text-muted-foreground">{opt.description}</p>
                      </div>
                      <Badge variant={opt.status === 'Ativo' ? 'default' : 'secondary'}>
                        {opt.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
