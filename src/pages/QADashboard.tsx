// =====================================================
// KWANZACONTROL - QA & Testing Dashboard
// Dashboard de Qualidade e Testes
// Data: 2026-04-08
// =====================================================

import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { qaService, type QAMetrics } from '@/services/newFeaturesService';
import { useAuth } from '@/hooks/useAuth';
import { useToast } from '@/hooks/use-toast';
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Activity,
  TrendingUp,
  Clock,
  Bug,
  Play,
  RefreshCw,
} from 'lucide-react';

export default function QADashboard() {
  const { profile } = useAuth();
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState<QAMetrics | null>(null);
  const [testRuns, setTestRuns] = useState<any[]>([]);
  const [bugs, setBugs] = useState<any[]>([]);
  const [apiTests, setApiTests] = useState<any[]>([]);
  const [uiTests, setUiTests] = useState<any[]>([]);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    if (!profile?.tenant_id) return;

    try {
      setLoading(true);
      const data = await qaService.getDashboard(profile.tenant_id);
      setMetrics(data.metrics);
      setTestRuns(data.testRuns || []);
      setBugs(data.bugs || []);
      setApiTests(data.apiTests || []);
      setUiTests(data.uiTests || []);
    } catch (error) {
      console.error('Error loading QA dashboard:', error);
      toast({
        title: 'Erro',
        description: 'Erro ao carregar dashboard de QA',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-green-600';
    if (score >= 60) return 'text-yellow-600';
    return 'text-red-600';
  };

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'CRITICAL':
        return 'destructive';
      case 'HIGH':
        return 'destructive';
      case 'MEDIUM':
        return 'default';
      case 'LOW':
        return 'secondary';
      default:
        return 'default';
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'PASSED':
      case 'RESOLVED':
        return 'default';
      case 'FAILED':
      case 'OPEN':
        return 'destructive';
      case 'IN_PROGRESS':
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
          <h1 className="text-3xl font-bold">QA & Testing Dashboard</h1>
          <p className="text-muted-foreground">Sistema de Qualidade e Testes</p>
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
              <CardTitle className="text-sm font-medium">Quality Score</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${getScoreColor(metrics.qualityScore)}`}>
                {metrics.qualityScore}/100
              </div>
              <p className="text-xs text-muted-foreground">Score geral de qualidade</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Test Success Rate</CardTitle>
              <CheckCircle className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${getScoreColor(metrics.testSuccessRate)}`}>
                {metrics.testSuccessRate}%
              </div>
              <p className="text-xs text-muted-foreground">
                {metrics.passedTests}/{metrics.totalTests} testes passaram
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Bugs Abertos</CardTitle>
              <Bug className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{metrics.openBugs}</div>
              <p className="text-xs text-muted-foreground">
                {metrics.highSeverityBugs} de alta severidade
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Code Coverage</CardTitle>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold ${getScoreColor(metrics.overallCoverage)}`}>
                {metrics.overallCoverage}%
              </div>
              <p className="text-xs text-muted-foreground">Cobertura de código</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Tabs */}
      <Tabs defaultValue="test-runs" className="space-y-4">
        <TabsList>
          <TabsTrigger value="test-runs">Test Runs</TabsTrigger>
          <TabsTrigger value="bugs">Bugs</TabsTrigger>
          <TabsTrigger value="api-tests">API Tests</TabsTrigger>
          <TabsTrigger value="ui-tests">UI Tests</TabsTrigger>
        </TabsList>

        {/* Test Runs Tab */}
        <TabsContent value="test-runs" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Execuções de Testes</CardTitle>
              <CardDescription>Histórico de execuções de testes</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {testRuns.map((run) => (
                  <div
                    key={run.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      {run.status === 'PASSED' ? (
                        <CheckCircle className="h-5 w-5 text-green-600" />
                      ) : (
                        <XCircle className="h-5 w-5 text-red-600" />
                      )}
                      <div>
                        <p className="font-medium">{run.test_suite}</p>
                        <p className="text-sm text-muted-foreground">
                          {run.passed}/{run.total_tests} testes passaram
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <Badge variant={getStatusColor(run.status)}>{run.status}</Badge>
                        <p className="text-xs text-muted-foreground mt-1">
                          <Clock className="h-3 w-3 inline mr-1" />
                          {run.duration_ms}ms
                        </p>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Bugs Tab */}
        <TabsContent value="bugs" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Bugs Reportados</CardTitle>
              <CardDescription>Lista de bugs e seu status</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {bugs.map((bug) => (
                  <div
                    key={bug.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      <AlertTriangle className="h-5 w-5 text-yellow-600" />
                      <div>
                        <p className="font-medium">{bug.title}</p>
                        <p className="text-sm text-muted-foreground">
                          Atribuído a: {bug.assigned_to || 'Não atribuído'}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={getSeverityColor(bug.severity)}>{bug.severity}</Badge>
                      <Badge variant={getStatusColor(bug.status)}>{bug.status}</Badge>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* API Tests Tab */}
        <TabsContent value="api-tests" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Testes de API</CardTitle>
              <CardDescription>
                Taxa de sucesso: {metrics?.apiSuccessRate}% | Tempo médio:{' '}
                {metrics?.avgApiResponseTime}ms
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {apiTests.map((test) => (
                  <div
                    key={test.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      {test.status === 'PASSED' ? (
                        <CheckCircle className="h-5 w-5 text-green-600" />
                      ) : (
                        <XCircle className="h-5 w-5 text-red-600" />
                      )}
                      <div>
                        <p className="font-medium">
                          {test.method} {test.endpoint}
                        </p>
                        <p className="text-sm text-muted-foreground">
                          Tempo de resposta: {test.response_time_ms}ms
                        </p>
                      </div>
                    </div>
                    <Badge variant={getStatusColor(test.status)}>{test.status}</Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* UI Tests Tab */}
        <TabsContent value="ui-tests" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Testes de UI</CardTitle>
              <CardDescription>
                Taxa de sucesso: {metrics?.uiSuccessRate}%
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {uiTests.map((test) => (
                  <div
                    key={test.id}
                    className="flex items-center justify-between p-4 border rounded-lg"
                  >
                    <div className="flex items-center gap-4">
                      {test.status === 'PASSED' ? (
                        <CheckCircle className="h-5 w-5 text-green-600" />
                      ) : (
                        <XCircle className="h-5 w-5 text-red-600" />
                      )}
                      <div>
                        <p className="font-medium">{test.test_name}</p>
                        <p className="text-sm text-muted-foreground">
                          Duração: {test.duration_ms}ms
                        </p>
                      </div>
                    </div>
                    <Badge variant={getStatusColor(test.status)}>{test.status}</Badge>
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
