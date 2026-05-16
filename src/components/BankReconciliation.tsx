import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { FileText, Upload, CheckCircle2, AlertTriangle, RefreshCw } from 'lucide-react';
import { bankReconciliationService, BankStatement, ReconciliationReport } from '@/services/bankReconciliationService';
import { formatCurrency } from '@/lib/index';
import { useToast } from '@/lib/toast-provider';

export function BankReconciliation() {
  const [statements, setStatements] = useState<BankStatement[]>([]);
  const [report, setReport] = useState<ReconciliationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const { success, error: showError } = useToast();

  const handleAutoReconcile = async () => {
    setReconciling(true);
    try {
      const period = {
        start: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
        end: new Date(),
      };
      
      const matches = await bankReconciliationService.autoReconcile('account-001', period);
      success('Sucesso', `${matches.length} transações conciliadas automaticamente`);
      
      // Load report
      const reportData = await bankReconciliationService.generateReconciliationReport('account-001', period);
      setReport(reportData);
    } catch (err) {
      console.error('Erro na conciliação:', err);
      showError('Erro', 'Não foi possível conciliar automaticamente');
    } finally {
      setReconciling(false);
    }
  };

  const reconciliationProgress = report
    ? (report.reconciledItems / (report.reconciledItems + report.unreconciledItems)) * 100
    : 0;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <FileText className="h-6 w-6 text-primary" />
            Conciliação Bancária
          </h2>
          <p className="text-muted-foreground mt-1">
            Concilie extratos bancários com transações do sistema
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" className="gap-2">
            <Upload className="h-4 w-4" />
            Importar Extrato
          </Button>
          <Button className="gap-2" onClick={handleAutoReconcile} disabled={reconciling}>
            <RefreshCw className={`h-4 w-4 ${reconciling ? 'animate-spin' : ''}`} />
            Conciliar Automaticamente
          </Button>
        </div>
      </div>

      {report && (
        <>
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Saldo Inicial</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{formatCurrency(report.openingBalance)}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Saldo Final</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{formatCurrency(report.closingBalance)}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Items Conciliados</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-green-600">{report.reconciledItems}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Items Pendentes</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-yellow-600">{report.unreconciledItems}</div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Progresso da Conciliação</CardTitle>
              <CardDescription>
                {report.reconciledItems} de {report.reconciledItems + report.unreconciledItems} items conciliados
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                <Progress value={reconciliationProgress} className="h-2" />
                <div className="flex items-center justify-between text-sm text-muted-foreground">
                  <span>{reconciliationProgress.toFixed(1)}% conciliado</span>
                  <span>{report.unreconciledItems} pendentes</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {report.discrepancies.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-yellow-500" />
                  Discrepâncias Detectadas
                </CardTitle>
                <CardDescription>
                  {report.discrepancies.length} discrepâncias encontradas
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {report.discrepancies.map((disc) => (
                    <div key={disc.id} className="p-3 border rounded-lg">
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <Badge variant={disc.severity === 'high' ? 'destructive' : 'default'}>
                              {disc.severity}
                            </Badge>
                            <span className="text-sm font-medium">{disc.type}</span>
                          </div>
                          <p className="text-sm text-muted-foreground mb-2">{disc.description}</p>
                          <p className="text-xs text-muted-foreground">
                            <strong>Ação sugerida:</strong> {disc.suggestedAction}
                          </p>
                          {disc.amount && (
                            <p className="text-sm font-semibold mt-2">
                              Valor: {formatCurrency(disc.amount)}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Resumo Financeiro</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Total de Débitos:</span>
                    <span className="font-semibold">{formatCurrency(report.totalDebits)}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Total de Créditos:</span>
                    <span className="font-semibold">{formatCurrency(report.totalCredits)}</span>
                  </div>
                </div>
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Período:</span>
                    <span className="font-medium">
                      {report.period.start.toLocaleDateString()} - {report.period.end.toLocaleDateString()}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">Variação:</span>
                    <span className="font-semibold">
                      {formatCurrency(report.closingBalance - report.openingBalance)}
                    </span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {!report && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <FileText className="h-16 w-16 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">Nenhuma conciliação realizada</h3>
            <p className="text-sm text-muted-foreground mb-4 text-center max-w-md">
              Importe um extrato bancário ou execute a conciliação automática para começar
            </p>
            <div className="flex gap-2">
              <Button variant="outline">
                <Upload className="h-4 w-4 mr-2" />
                Importar Extrato
              </Button>
              <Button onClick={handleAutoReconcile}>
                <RefreshCw className="h-4 w-4 mr-2" />
                Conciliar Automaticamente
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
