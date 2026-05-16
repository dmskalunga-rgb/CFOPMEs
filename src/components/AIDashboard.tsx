import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Progress } from '@/components/ui/progress';
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Lightbulb,
  Target,
  DollarSign,
  Activity,
  Brain,
  Sparkles,
  ArrowRight,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { aiFinanceService, CashFlowPrediction, FinancialAnomaly, AIRecommendation } from '@/services/aiFinanceService';
import { formatCurrency } from '@/lib/index';
import { motion } from 'framer-motion';

export function AIDashboard() {
  const [predictions, setPredictions] = useState<CashFlowPrediction[]>([]);
  const [anomalies, setAnomalies] = useState<FinancialAnomaly[]>([]);
  const [recommendations, setRecommendations] = useState<AIRecommendation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadAIData();
  }, []);

  const loadAIData = async () => {
    setLoading(true);
    try {
      const [pred, anom, rec] = await Promise.all([
        aiFinanceService.predictCashFlow(6),
        aiFinanceService.detectAnomalies(),
        aiFinanceService.generateRecommendations(),
      ]);

      setPredictions(pred);
      setAnomalies(anom);
      setRecommendations(rec);
    } catch (error) {
      console.error('Erro ao carregar dados de IA:', error);
    } finally {
      setLoading(false);
    }
  };

  const predictionChartData = predictions.map((p) => ({
    month: new Date(p.month).toLocaleDateString('pt-AO', { month: 'short' }),
    receita: p.predictedIncome,
    despesa: p.predictedExpense,
    saldo: p.predictedBalance,
  }));

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'critical':
        return 'destructive';
      case 'high':
        return 'destructive';
      case 'medium':
        return 'default';
      default:
        return 'secondary';
    }
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'high':
        return 'destructive';
      case 'medium':
        return 'default';
      default:
        return 'secondary';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <Brain className="h-12 w-12 animate-pulse text-primary mx-auto mb-4" />
          <p className="text-muted-foreground">Analisando dados financeiros...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between"
      >
        <div>
          <h2 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <Brain className="h-8 w-8 text-primary" />
            Inteligência Financeira
          </h2>
          <p className="text-muted-foreground mt-2">
            Análises preditivas e recomendações inteligentes baseadas em IA
          </p>
        </div>
        <Button onClick={loadAIData} variant="outline" className="gap-2">
          <Sparkles className="h-4 w-4" />
          Atualizar Análise
        </Button>
      </motion.div>

      <Tabs defaultValue="predictions" className="space-y-6">
        <TabsList className="grid w-full max-w-2xl grid-cols-3">
          <TabsTrigger value="predictions">Previsões</TabsTrigger>
          <TabsTrigger value="anomalies">Anomalias</TabsTrigger>
          <TabsTrigger value="recommendations">Recomendações</TabsTrigger>
        </TabsList>

        {/* Previsões */}
        <TabsContent value="predictions" className="space-y-6">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="h-5 w-5 text-primary" />
                  Previsão de Fluxo de Caixa (6 meses)
                </CardTitle>
                <CardDescription>
                  Projeções baseadas em histórico e tendências identificadas
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ResponsiveContainer width="100%" height={400}>
                  <LineChart data={predictionChartData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="month" />
                    <YAxis />
                    <Tooltip
                      formatter={(value: number) => formatCurrency(value)}
                      contentStyle={{ backgroundColor: 'hsl(var(--card))', border: '1px solid hsl(var(--border))' }}
                    />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="receita"
                      stroke="hsl(var(--chart-1))"
                      strokeWidth={2}
                      name="Receita Prevista"
                    />
                    <Line
                      type="monotone"
                      dataKey="despesa"
                      stroke="hsl(var(--chart-2))"
                      strokeWidth={2}
                      name="Despesa Prevista"
                    />
                    <Line
                      type="monotone"
                      dataKey="saldo"
                      stroke="hsl(var(--chart-3))"
                      strokeWidth={2}
                      name="Saldo Previsto"
                    />
                  </LineChart>
                </ResponsiveContainer>

                <div className="grid gap-4 md:grid-cols-3 mt-6">
                  {predictions.slice(0, 3).map((pred, index) => (
                    <Card key={index}>
                      <CardHeader className="pb-3">
                        <CardTitle className="text-sm font-medium">
                          {new Date(pred.month).toLocaleDateString('pt-AO', { month: 'long', year: 'numeric' })}
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Saldo Previsto:</span>
                          <span className="font-semibold">{formatCurrency(pred.predictedBalance)}</span>
                        </div>
                        <div className="flex items-center justify-between text-sm">
                          <span className="text-muted-foreground">Confiança:</span>
                          <Badge variant="secondary">{(pred.confidence * 100).toFixed(0)}%</Badge>
                        </div>
                        <div className="flex items-center gap-2">
                          {pred.trend === 'up' ? (
                            <TrendingUp className="h-4 w-4 text-green-500" />
                          ) : pred.trend === 'down' ? (
                            <TrendingDown className="h-4 w-4 text-red-500" />
                          ) : (
                            <Activity className="h-4 w-4 text-blue-500" />
                          )}
                          <span className="text-xs text-muted-foreground">
                            Tendência {pred.trend === 'up' ? 'positiva' : pred.trend === 'down' ? 'negativa' : 'estável'}
                          </span>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </TabsContent>

        {/* Anomalias */}
        <TabsContent value="anomalies" className="space-y-6">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-destructive" />
                  Anomalias Detectadas
                </CardTitle>
                <CardDescription>
                  Transações e padrões incomuns identificados pela IA
                </CardDescription>
              </CardHeader>
              <CardContent>
                {anomalies.length === 0 ? (
                  <div className="text-center py-12">
                    <CheckCircle2 className="h-12 w-12 text-green-500 mx-auto mb-4" />
                    <p className="text-lg font-medium">Nenhuma anomalia detectada</p>
                    <p className="text-sm text-muted-foreground mt-2">
                      Suas finanças estão dentro dos padrões esperados
                    </p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    {anomalies.map((anomaly) => (
                      <Card key={anomaly.id} className="border-l-4 border-l-destructive">
                        <CardContent className="pt-6">
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-2">
                                <Badge variant={getSeverityColor(anomaly.severity)}>
                                  {anomaly.severity.toUpperCase()}
                                </Badge>
                                <Badge variant="outline">{anomaly.category}</Badge>
                              </div>
                              <h4 className="font-semibold mb-2">{anomaly.description}</h4>
                              <p className="text-sm text-muted-foreground mb-3">
                                {anomaly.recommendation}
                              </p>
                              <div className="flex items-center gap-4 text-sm">
                                <span className="text-muted-foreground">
                                  Valor: <span className="font-semibold">{formatCurrency(anomaly.amount)}</span>
                                </span>
                                <span className="text-muted-foreground">
                                  Data: {anomaly.date.toLocaleDateString('pt-AO')}
                                </span>
                              </div>
                            </div>
                            <AlertTriangle className="h-6 w-6 text-destructive flex-shrink-0" />
                          </div>
                        </CardContent>
                      </Card>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>
        </TabsContent>

        {/* Recomendações */}
        <TabsContent value="recommendations" className="space-y-6">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Lightbulb className="h-5 w-5 text-primary" />
                  Recomendações Inteligentes
                </CardTitle>
                <CardDescription>
                  Sugestões personalizadas para otimizar suas finanças
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {recommendations.map((rec) => (
                    <Card key={rec.id} className="border-l-4 border-l-primary">
                      <CardContent className="pt-6">
                        <div className="flex items-start justify-between mb-4">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-2">
                              <Badge variant={getPriorityColor(rec.priority)}>
                                {rec.priority.toUpperCase()}
                              </Badge>
                              <Badge variant="outline">{rec.type.replace('_', ' ')}</Badge>
                            </div>
                            <h4 className="font-semibold text-lg mb-2">{rec.title}</h4>
                            <p className="text-sm text-muted-foreground mb-4">{rec.description}</p>
                          </div>
                          <Lightbulb className="h-6 w-6 text-primary flex-shrink-0" />
                        </div>

                        <div className="grid gap-4 md:grid-cols-3 mb-4">
                          <div className="space-y-1">
                            <p className="text-xs text-muted-foreground">Economia Potencial</p>
                            <p className="text-lg font-semibold text-green-600">
                              {formatCurrency(rec.potentialSavings)}
                            </p>
                          </div>
                          <div className="space-y-1">
                            <p className="text-xs text-muted-foreground">Dificuldade</p>
                            <Badge variant="secondary">{rec.implementationDifficulty}</Badge>
                          </div>
                          <div className="space-y-1">
                            <p className="text-xs text-muted-foreground">Impacto Estimado</p>
                            <p className="text-sm font-medium">{rec.estimatedImpact}</p>
                          </div>
                        </div>

                        <div className="space-y-2">
                          <p className="text-sm font-medium">Ações Recomendadas:</p>
                          <ul className="space-y-2">
                            {rec.actionItems.map((action, index) => (
                              <li key={index} className="flex items-start gap-2 text-sm">
                                <ArrowRight className="h-4 w-4 text-primary mt-0.5 flex-shrink-0" />
                                <span>{action}</span>
                              </li>
                            ))}
                          </ul>
                        </div>

                        <Button className="w-full mt-4" variant="outline">
                          Implementar Recomendação
                        </Button>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
