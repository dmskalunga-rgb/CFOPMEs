// =====================================================
// KWANZACONTROL - Compliance Dashboard Component
// Dashboard de compliance e relatórios regulatórios
// Data: 2026-04-04
// =====================================================

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { complianceService, ComplianceStandard } from '@/services/complianceService';
import { useAuth } from '@/hooks/useAuth';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Shield, FileText, Download, CheckCircle, AlertTriangle, XCircle, TrendingUp } from 'lucide-react';
import { useToast } from '@/lib/toast-provider';

const STANDARDS_INFO = {
  GDPR: {
    name: 'GDPR',
    fullName: 'General Data Protection Regulation',
    description: 'Regulamento Geral de Proteção de Dados (UE)',
    color: 'text-blue-500',
  },
  LGPD: {
    name: 'LGPD',
    fullName: 'Lei Geral de Proteção de Dados',
    description: 'Lei Geral de Proteção de Dados (Brasil)',
    color: 'text-green-500',
  },
  SOC2: {
    name: 'SOC 2',
    fullName: 'Service Organization Control 2',
    description: 'Controles de Segurança e Privacidade',
    color: 'text-purple-500',
  },
  ISO27001: {
    name: 'ISO 27001',
    fullName: 'ISO/IEC 27001',
    description: 'Gestão de Segurança da Informação',
    color: 'text-orange-500',
  },
  HIPAA: {
    name: 'HIPAA',
    fullName: 'Health Insurance Portability and Accountability Act',
    description: 'Proteção de Dados de Saúde (EUA)',
    color: 'text-red-500',
  },
};

export function ComplianceDashboard() {
  const { tenant } = useAuth();
  const queryClient = useQueryClient();
  const { success, error: showError } = useToast();

  const [selectedStandard, setSelectedStandard] = useState<ComplianceStandard>('GDPR');

  // Fetch compliance dashboard
  const { data: dashboard, isLoading } = useQuery({
    queryKey: ['compliance-dashboard', tenant?.id],
    queryFn: () => complianceService.getComplianceDashboard(tenant!.id),
    enabled: !!tenant,
  });

  // Fetch reports
  const { data: reports } = useQuery({
    queryKey: ['compliance-reports', tenant?.id, selectedStandard],
    queryFn: () => complianceService.getComplianceReports(tenant!.id, selectedStandard),
    enabled: !!tenant,
  });

  // Fetch data subject requests
  const { data: dataSubjectRequests } = useQuery({
    queryKey: ['data-subject-requests', tenant?.id],
    queryFn: () => complianceService.getDataSubjectRequests(tenant!.id),
    enabled: !!tenant,
  });

  // Generate report mutation
  const generateReport = useMutation({
    mutationFn: ({ standard, periodStart, periodEnd }: { standard: ComplianceStandard; periodStart: string; periodEnd: string }) =>
      complianceService.generateComplianceReport(tenant!.id, standard, periodStart, periodEnd),
    onSuccess: () => {
      success('Relatório gerado', 'Relatório de compliance gerado com sucesso!');
      queryClient.invalidateQueries({ queryKey: ['compliance-reports', tenant?.id] });
    },
    onError: (error: any) => {
      showError('Erro ao gerar relatório', error.message);
    },
  });

  const handleGenerateReport = () => {
    const now = new Date();
    const periodEnd = now.toISOString().split('T')[0];
    const periodStart = new Date(now.setMonth(now.getMonth() - 1)).toISOString().split('T')[0];

    generateReport.mutate({
      standard: selectedStandard,
      periodStart,
      periodEnd,
    });
  };

  const handleExportReport = async (reportId: string, format: 'pdf' | 'excel') => {
    try {
      await complianceService.exportComplianceReport(reportId, format);
      success('Relatório exportado', `Relatório exportado em ${format.toUpperCase()} com sucesso!`);
    } catch (error: any) {
      showError('Erro ao exportar', error.message);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'compliant':
        return <CheckCircle className="w-5 h-5 text-green-500" />;
      case 'non-compliant':
        return <XCircle className="w-5 h-5 text-red-500" />;
      case 'partial':
        return <AlertTriangle className="w-5 h-5 text-orange-500" />;
      default:
        return <AlertTriangle className="w-5 h-5 text-gray-500" />;
    }
  };

  const getScoreColor = (score: number) => {
    if (score >= 90) return 'text-green-500';
    if (score >= 70) return 'text-orange-500';
    return 'text-red-500';
  };

  if (isLoading) {
    return <div className="p-8">Carregando...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <Shield className="w-6 h-6 text-primary" />
          Compliance & Relatórios Regulatórios
        </h2>
        <p className="text-muted-foreground mt-2">
          Gerencie conformidade com GDPR, LGPD, SOC 2 e outros padrões
        </p>
      </div>

      {/* Overall Score */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Pontuação Geral de Compliance</span>
            <span className={`text-3xl font-bold ${getScoreColor(dashboard?.overall_score || 0)}`}>
              {dashboard?.overall_score || 0}%
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Progress value={dashboard?.overall_score || 0} className="h-3" />
          <div className="grid gap-4 md:grid-cols-3 mt-6">
            <div className="flex items-center gap-2">
              <FileText className="w-5 h-5 text-muted-foreground" />
              <div>
                <p className="text-sm text-muted-foreground">Relatórios Gerados</p>
                <p className="text-2xl font-bold">{reports?.length || 0}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-muted-foreground" />
              <div>
                <p className="text-sm text-muted-foreground">Solicitações Pendentes</p>
                <p className="text-2xl font-bold">{dashboard?.pending_requests || 0}</p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-muted-foreground" />
              <div>
                <p className="text-sm text-muted-foreground">Registros de Auditoria</p>
                <p className="text-2xl font-bold">{dashboard?.audit_trail_count || 0}</p>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Standards Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        {Object.entries(STANDARDS_INFO).map(([key, info]) => {
          const standardData = dashboard?.standards?.[key as ComplianceStandard];
          return (
            <Card key={key} className="cursor-pointer hover:border-primary" onClick={() => setSelectedStandard(key as ComplianceStandard)}>
              <CardHeader>
                <CardTitle className={`text-lg ${info.color}`}>{info.name}</CardTitle>
                <CardDescription className="text-xs">{info.description}</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{standardData?.score || 0}%</div>
                <Badge variant={standardData?.status === 'compliant' ? 'default' : 'secondary'} className="mt-2">
                  {standardData?.status || 'Não avaliado'}
                </Badge>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Reports Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Relatórios de {STANDARDS_INFO[selectedStandard].name}</CardTitle>
              <CardDescription>{STANDARDS_INFO[selectedStandard].fullName}</CardDescription>
            </div>
            <Button onClick={handleGenerateReport} disabled={generateReport.isPending}>
              {generateReport.isPending ? 'Gerando...' : 'Gerar Relatório'}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {reports && reports.length > 0 ? (
            <div className="space-y-3">
              {reports.map((report) => (
                <div key={report.id} className="flex items-center justify-between p-4 border rounded-lg">
                  <div>
                    <p className="font-medium">
                      Período: {new Date(report.period_start).toLocaleDateString('pt-AO')} - {new Date(report.period_end).toLocaleDateString('pt-AO')}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      Gerado em {new Date(report.generated_at).toLocaleString('pt-AO')}
                    </p>
                    <div className="flex items-center gap-2 mt-2">
                      <span className="text-sm">Pontuação:</span>
                      <span className={`font-bold ${getScoreColor(report.score)}`}>{report.score}%</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={report.status === 'completed' ? 'default' : 'secondary'}>
                      {report.status}
                    </Badge>
                    <Button variant="outline" size="sm" onClick={() => handleExportReport(report.id, 'pdf')}>
                      <Download className="w-4 h-4 mr-2" />
                      PDF
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => handleExportReport(report.id, 'excel')}>
                      <Download className="w-4 h-4 mr-2" />
                      Excel
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <FileText className="w-12 h-12 mx-auto mb-2 opacity-50" />
              <p>Nenhum relatório gerado</p>
              <p className="text-sm">Clique em "Gerar Relatório" para criar um novo relatório</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Data Subject Requests */}
      <Card>
        <CardHeader>
          <CardTitle>Solicitações de Titulares de Dados (GDPR/LGPD)</CardTitle>
          <CardDescription>
            Solicitações de acesso, retificação, exclusão e portabilidade de dados
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dataSubjectRequests && dataSubjectRequests.length > 0 ? (
            <div className="space-y-3">
              {dataSubjectRequests.slice(0, 5).map((request) => (
                <div key={request.id} className="flex items-center justify-between p-3 border rounded-lg">
                  <div>
                    <p className="font-medium">{request.subject_name} ({request.subject_email})</p>
                    <p className="text-sm text-muted-foreground">
                      Tipo: {request.request_type} • Solicitado em {new Date(request.requested_at).toLocaleDateString('pt-AO')}
                    </p>
                  </div>
                  <Badge variant={request.status === 'completed' ? 'default' : request.status === 'pending' ? 'secondary' : 'outline'}>
                    {request.status}
                  </Badge>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <p>Nenhuma solicitação registrada</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
