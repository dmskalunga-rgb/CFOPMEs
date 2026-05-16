// =====================================================
// KWANZACONTROL - RPA Dashboard
// Dashboard de Automação de Processos
// Data: 2026-04-08
// =====================================================

import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { rpaService, type RPAMetrics } from '@/services/newFeaturesService';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/hooks/use-toast';
import {
  Bot,
  Play,
  RefreshCw,
  CheckCircle,
  XCircle,
  Clock,
  TrendingUp,
  Zap,
} from 'lucide-react';

export default function RPADashboard() {
  const { profile } = useAuth();
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<RPAMetrics | null>(null);
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [executions, setExecutions] = useState<any[]>([]);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    if (!profile?.tenant_id) return;

    try {
      setLoading(true);
      const data = await rpaService.getDashboard(profile.tenant_id);
      setMetrics(data.metrics);
      setWorkflows(data.workflows || []);
      setExecutions(data.executions || []);
    } catch (error) {
      console.error('Error loading RPA dashboard:', error);
      toast({
        title: 'Erro',
        description: 'Erro ao carregar dashboard de RPA',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteWorkflow = async (workflowId: string) => {
    if (!profile?.tenant_id) return;

    try {
      setExecuting(workflowId);
      const result = await rpaService.executeWorkflow(profile.tenant_id, workflowId);
      toast({
        title: 'Sucesso',
        description: result.message || 'Workflow executado com sucesso',
      });
      await loadDashboard();
    } catch (error) {
      console.error('Error executing workflow:', error);
      toast({
        title: 'Erro',
        description: 'Erro ao executar workflow',
        variant: 'destructive',
      });
    } finally {
      setExecuting(null);
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 60) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'COMPLETED':
        return 'default';
      case 'FAILED':
        return 'destructive';
      case 'RUNNING':
        return 'secondary';
      default:
        return 'outline';
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <RefreshCw className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">RPA Dashboard</h1>
          <p className="text-muted-foreground">Automação de Processos Robóticos</p>
        </div>
        <Button onClick={loadDashboard}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Atualizar
        </Button>
      </div>

      {/* Metrics Cards */}
      {metrics && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Automation Score</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${getScoreColor(metrics.automationScore)}`}>
                {metrics.automationScore}/100
              </div>
              <p className="text-xs text-muted-foreground">Score de automação</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Workflows Ativos</CardTitle>
              <Bot className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{metrics.activeWorkflows}</div>
              <p className="text-xs text-muted-foreground">
                de {metrics.totalWorkflows} workflows
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Taxa de Sucesso</CardTitle>
              <CheckCircle className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${getScoreColor(metrics.successRate)}`}>
                {metrics.successRate}%
              </div>
              <p className="text-xs text-muted-foreground">
                {metrics.completedExecutions}/{metrics.totalExecutions} execuções
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Tempo Economizado</CardTitle>
              <Zap className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{metrics.timeSavedHours}h</div>
              <p className="text-xs text-muted-foreground">
                {metrics.executionsLast24h} execuções (24h)
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Tabs */}
      <Tabs defaultValue="workflows" className="space-y-4">
        <TabsList>
          <TabsTrigger value="workflows">Workflows</TabsTrigger>
          <TabsTrigger value="executions">Execuções</TabsTrigger>
        </TabsList>

        {/* Workflows Tab */}
        <TabsContent value="workflows" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Workflows de Automação</CardTitle>
              <CardDescription>Processos automatizados configurados</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {workflows.map((workflow) => (
                  <div
                    key={workflow.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      <Bot className="h-5 w-5 text-primary" />
                      <div>
                        <p className="font-medium">{workflow.name}</p>
                        <p className="text-sm text-muted-foreground">{workflow.description}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {workflow.steps.length} passos | {workflow.trigger_type}
                          {workflow.schedule && ` | ${workflow.schedule}`}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={workflow.enabled ? 'default' : 'secondary'}>
                        {workflow.enabled ? 'Ativo' : 'Inativo'}
                      </Badge>
                      <Button
                        size="sm"
                        onClick={() => handleExecuteWorkflow(workflow.id)}
                        disabled={executing === workflow.id || !workflow.enabled}
                      >
                        {executing === workflow.id ? (
                          <RefreshCw className="h-4 w-4 animate-spin" />
                        ) : (
                          <Play className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Executions Tab */}
        <TabsContent value="executions" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Histórico de Execuções</CardTitle>
              <CardDescription>Últimas execuções de workflows</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {executions.map((execution) => (
                  <div
                    key={execution.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      {execution.status === 'COMPLETED' ? (
                        <CheckCircle className="h-5 w-5 text-green-600" />
                      ) : execution.status === 'FAILED' ? (
                        <XCircle className="h-5 w-5 text-red-600" />
                      ) : (
                        <RefreshCw className="h-5 w-5 text-blue-600 animate-spin" />
                      )}
                      <div>
                        <p className="font-medium">{execution.workflow_name}</p>
                        <p className="text-sm text-muted-foreground">
                          {execution.steps_completed}/{execution.steps_total} passos
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <Badge variant={getStatusColor(execution.status)}>
                          {execution.status}
                        </Badge>
                        <p className="text-xs text-muted-foreground mt-1">
                          <Clock className="h-3 w-3 inline mr-1" />
                          {execution.duration_ms}ms
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
