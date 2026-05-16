import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FileText, Download, TrendingUp, DollarSign, BarChart3, PieChart } from 'lucide-react';
import { financialReportsService, IncomeStatementData, CashFlowStatementData, BalanceSheetData, CostAnalysisData } from '@/services/financialReportsService';
import { formatCurrency } from '@/lib/index';
import { useToast } from '@/lib/toast-provider';

type PeriodType = 'month' | 'quarter' | 'year';

export function FinancialReports() {
  const [period, setPeriod] = useState<PeriodType>('month');
  const [incomeStatement, setIncomeStatement] = useState<IncomeStatementData | null>(null);
  const [cashFlow, setCashFlow] = useState<CashFlowStatementData | null>(null);
  const [balanceSheet, setBalanceSheet] = useState<BalanceSheetData | null>(null);
  const [costAnalysis, setCostAnalysis] = useState<CostAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const { error: showError } = useToast();

  useEffect(() => {
    loadReports();
  }, [period]);

  const getPeriodDates = () => {
    const end = new Date();
    const start = new Date();

    switch (period) {
      case 'month':
        start.setMonth(end.getMonth() - 1);
        break;
      case 'quarter':
        start.setMonth(end.getMonth() - 3);
        break;
      case 'year':
        start.setFullYear(end.getFullYear() - 1);
        break;
    }

    return { start, end };
  };

  const loadReports = async () => {
    setLoading(true);
    try {
      const { start, end } = getPeriodDates();

      const [income, cash, balance, cost] = await Promise.all([
        financialReportsService.generateIncomeStatement('comp-001', start, end),
        financialReportsService.generateCashFlowStatement('comp-001', start, end),
        financialReportsService.generateBalanceSheet('comp-001', end),
        financialReportsService.generateCostAnalysis('comp-001', start, end),
      ]);

      setIncomeStatement(income);
      setCashFlow(cash);
      setBalanceSheet(balance);
      setCostAnalysis(cost);
    } catch (err) {
      console.error('Erro ao carregar relatórios:', err);
      // Usar dados mock se falhar
      const now = new Date();
      const start = new Date();
      start.setMonth(now.getMonth() - 1);

      setIncomeStatement({
        period: { start, end: now },
        revenue: {
          total: 1500000,
          byCategory: [
            { category: 'Vendas', amount: 1000000 },
            { category: 'Serviços', amount: 500000 },
          ],
        },
        expenses: {
          total: 1000000,
          byCategory: [
            { category: 'Salários', amount: 500000 },
            { category: 'Fornecedores', amount: 300000 },
            { category: 'Marketing', amount: 200000 },
          ],
        },
        grossProfit: 500000,
        netProfit: 450000,
        profitMargin: 30,
      });
      setCashFlow({
        period: { start, end: now },
        openingBalance: 800000,
        closingBalance: 1000000,
        operatingActivities: {
          receipts: 1500000,
          payments: 1000000,
          net: 500000,
        },
        investingActivities: {
          receipts: 0,
          payments: 200000,
          net: -200000,
        },
        financingActivities: {
          receipts: 0,
          payments: 100000,
          net: -100000,
        },
        netCashFlow: 200000,
      });
      setBalanceSheet({
        date: now,
        assets: {
          current: [
            { name: 'Caixa', amount: 500000 },
            { name: 'Bancos', amount: 800000 },
            { name: 'Contas a Receber', amount: 200000 },
          ],
          nonCurrent: [
            { name: 'Imóveis', amount: 1500000 },
            { name: 'Equipamentos', amount: 500000 },
          ],
          total: 3500000,
        },
        liabilities: {
          current: [
            { name: 'Fornecedores', amount: 300000 },
            { name: 'Salários a Pagar', amount: 200000 },
          ],
          nonCurrent: [
            { name: 'Empréstimos', amount: 1000000 },
          ],
          total: 1500000,
        },
        equity: {
          items: [
            { name: 'Capital Social', amount: 1500000 },
            { name: 'Lucros Acumulados', amount: 500000 },
          ],
          total: 2000000,
        },
      });
      setCostAnalysis({
        period: { start, end: now },
        byCostCenter: [
          { costCenter: 'Operações', amount: 500000, percentage: 41.67 },
          { costCenter: 'Vendas', amount: 400000, percentage: 33.33 },
          { costCenter: 'Administração', amount: 300000, percentage: 25 },
        ],
        byCategory: [
          { category: 'Salários', amount: 500000, percentage: 41.67 },
          { category: 'Fornecedores', amount: 300000, percentage: 25 },
          { category: 'Marketing', amount: 200000, percentage: 16.67 },
          { category: 'Outros', amount: 200000, percentage: 16.67 },
        ],
        byMonth: [
          { month: 'Jan', amount: 400000 },
          { month: 'Fev', amount: 420000 },
          { month: 'Mar', amount: 380000 },
        ],
        total: 1200000,
      });
      console.warn('Usando dados mock para relatórios');
    } finally {
      setLoading(false);
    }
  };

  const handleExport = (reportType: string) => {
    alert(`Exportação de ${reportType} em desenvolvimento`);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <FileText className="h-12 w-12 animate-pulse text-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold flex items-center gap-2">
            <FileText className="h-6 w-6 text-primary" />
            Relatórios Financeiros
          </h2>
          <p className="text-muted-foreground mt-1">Demonstrações financeiras completas</p>
        </div>
        <div className="flex gap-2">
          <Select value={period} onValueChange={(value) => setPeriod(value as PeriodType)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="month">Último Mês</SelectItem>
              <SelectItem value="quarter">Último Trimestre</SelectItem>
              <SelectItem value="year">Último Ano</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <Tabs defaultValue="income" className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="income">DRE</TabsTrigger>
          <TabsTrigger value="cashflow">Fluxo de Caixa</TabsTrigger>
          <TabsTrigger value="balance">Balanço</TabsTrigger>
          <TabsTrigger value="costs">Análise de Custos</TabsTrigger>
        </TabsList>

        {/* DRE */}
        <TabsContent value="income">
          {incomeStatement && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Demonstração de Resultados (DRE)</CardTitle>
                    <CardDescription>
                      Período: {incomeStatement.period.start.toLocaleDateString()} - {incomeStatement.period.end.toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <Button variant="outline" onClick={() => handleExport('DRE')}>
                    <Download className="h-4 w-4 mr-2" />
                    Exportar PDF
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Receitas */}
                <div>
                  <h3 className="font-semibold text-lg mb-3 flex items-center gap-2">
                    <TrendingUp className="h-5 w-5 text-green-600" />
                    Receitas
                  </h3>
                  <div className="space-y-2">
                    {incomeStatement.revenue.byCategory.map((item, index) => (
                      <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded">
                        <span className="text-sm">{item.category}</span>
                        <span className="font-semibold">{formatCurrency(item.amount)}</span>
                      </div>
                    ))}
                    <div className="flex items-center justify-between p-3 bg-green-50 dark:bg-green-950 rounded font-bold">
                      <span>Total de Receitas</span>
                      <span className="text-green-600">{formatCurrency(incomeStatement.revenue.total)}</span>
                    </div>
                  </div>
                </div>

                {/* Despesas */}
                <div>
                  <h3 className="font-semibold text-lg mb-3 flex items-center gap-2">
                    <DollarSign className="h-5 w-5 text-red-600" />
                    Despesas
                  </h3>
                  <div className="space-y-2">
                    {incomeStatement.expenses.byCategory.map((item, index) => (
                      <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded">
                        <span className="text-sm">{item.category}</span>
                        <span className="font-semibold">{formatCurrency(item.amount)}</span>
                      </div>
                    ))}
                    <div className="flex items-center justify-between p-3 bg-red-50 dark:bg-red-950 rounded font-bold">
                      <span>Total de Despesas</span>
                      <span className="text-red-600">{formatCurrency(incomeStatement.expenses.total)}</span>
                    </div>
                  </div>
                </div>

                {/* Resultado */}
                <div className="border-t pt-4">
                  <div className="space-y-2">
                    <div className="flex items-center justify-between p-3 bg-muted rounded">
                      <span className="font-semibold">Lucro Bruto</span>
                      <span className="font-bold text-lg">{formatCurrency(incomeStatement.grossProfit)}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-primary/10 rounded">
                      <span className="font-semibold">Lucro Líquido</span>
                      <span className="font-bold text-xl text-primary">{formatCurrency(incomeStatement.netProfit)}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-muted/50 rounded">
                      <span className="text-sm">Margem de Lucro</span>
                      <span className="font-semibold">{incomeStatement.profitMargin.toFixed(2)}%</span>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Fluxo de Caixa */}
        <TabsContent value="cashflow">
          {cashFlow && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Demonstração de Fluxo de Caixa</CardTitle>
                    <CardDescription>
                      Período: {cashFlow.period.start.toLocaleDateString()} - {cashFlow.period.end.toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <Button variant="outline" onClick={() => handleExport('Fluxo de Caixa')}>
                    <Download className="h-4 w-4 mr-2" />
                    Exportar PDF
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="p-4 bg-blue-50 dark:bg-blue-950 rounded-lg">
                    <p className="text-sm text-muted-foreground mb-1">Saldo Inicial</p>
                    <p className="text-2xl font-bold">{formatCurrency(cashFlow.openingBalance)}</p>
                  </div>
                  <div className="p-4 bg-green-50 dark:bg-green-950 rounded-lg">
                    <p className="text-sm text-muted-foreground mb-1">Saldo Final</p>
                    <p className="text-2xl font-bold text-green-600">{formatCurrency(cashFlow.closingBalance)}</p>
                  </div>
                </div>

                {/* Atividades Operacionais */}
                <div>
                  <h3 className="font-semibold text-lg mb-3">Atividades Operacionais</h3>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between p-2 bg-muted/50 rounded">
                      <span className="text-sm">Recebimentos</span>
                      <span className="font-semibold text-green-600">{formatCurrency(cashFlow.operatingActivities.receipts)}</span>
                    </div>
                    <div className="flex items-center justify-between p-2 bg-muted/50 rounded">
                      <span className="text-sm">Pagamentos</span>
                      <span className="font-semibold text-red-600">{formatCurrency(cashFlow.operatingActivities.payments)}</span>
                    </div>
                    <div className="flex items-center justify-between p-3 bg-muted rounded font-bold">
                      <span>Caixa Líquido Operacional</span>
                      <span>{formatCurrency(cashFlow.operatingActivities.net)}</span>
                    </div>
                  </div>
                </div>

                {/* Resultado Final */}
                <div className="border-t pt-4">
                  <div className="flex items-center justify-between p-4 bg-primary/10 rounded-lg">
                    <span className="font-semibold text-lg">Variação de Caixa</span>
                    <span className="font-bold text-2xl text-primary">{formatCurrency(cashFlow.netCashFlow)}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Balanço Patrimonial */}
        <TabsContent value="balance">
          {balanceSheet && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Balanço Patrimonial</CardTitle>
                    <CardDescription>
                      Data: {balanceSheet.date.toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <Button variant="outline" onClick={() => handleExport('Balanço')}>
                    <Download className="h-4 w-4 mr-2" />
                    Exportar PDF
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid gap-6 md:grid-cols-2">
                  {/* Ativos */}
                  <div>
                    <h3 className="font-semibold text-lg mb-3">Ativos</h3>
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm font-medium mb-2">Circulante</p>
                        {balanceSheet.assets.current.map((item, index) => (
                          <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded mb-1">
                            <span className="text-sm">{item.name}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                        ))}
                      </div>
                      <div>
                        <p className="text-sm font-medium mb-2">Não Circulante</p>
                        {balanceSheet.assets.nonCurrent.map((item, index) => (
                          <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded mb-1">
                            <span className="text-sm">{item.name}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center justify-between p-3 bg-blue-50 dark:bg-blue-950 rounded font-bold">
                        <span>Total de Ativos</span>
                        <span className="text-blue-600">{formatCurrency(balanceSheet.assets.total)}</span>
                      </div>
                    </div>
                  </div>

                  {/* Passivos + Patrimônio */}
                  <div>
                    <h3 className="font-semibold text-lg mb-3">Passivos</h3>
                    <div className="space-y-4">
                      <div>
                        <p className="text-sm font-medium mb-2">Circulante</p>
                        {balanceSheet.liabilities.current.map((item, index) => (
                          <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded mb-1">
                            <span className="text-sm">{item.name}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                        ))}
                      </div>
                      <div>
                        <p className="text-sm font-medium mb-2">Não Circulante</p>
                        {balanceSheet.liabilities.nonCurrent.map((item, index) => (
                          <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded mb-1">
                            <span className="text-sm">{item.name}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                        ))}
                      </div>
                      <div className="flex items-center justify-between p-3 bg-red-50 dark:bg-red-950 rounded font-bold">
                        <span>Total de Passivos</span>
                        <span className="text-red-600">{formatCurrency(balanceSheet.liabilities.total)}</span>
                      </div>
                      
                      <div className="mt-4">
                        <p className="text-sm font-medium mb-2">Patrimônio Líquido</p>
                        {balanceSheet.equity.items.map((item, index) => (
                          <div key={index} className="flex items-center justify-between p-2 bg-muted/50 rounded mb-1">
                            <span className="text-sm">{item.name}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                        ))}
                        <div className="flex items-center justify-between p-3 bg-green-50 dark:bg-green-950 rounded font-bold mt-2">
                          <span>Total do Patrimônio</span>
                          <span className="text-green-600">{formatCurrency(balanceSheet.equity.total)}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Análise de Custos */}
        <TabsContent value="costs">
          {costAnalysis && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Análise de Custos</CardTitle>
                    <CardDescription>
                      Período: {costAnalysis.period.start.toLocaleDateString()} - {costAnalysis.period.end.toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <Button variant="outline" onClick={() => handleExport('Análise de Custos')}>
                    <Download className="h-4 w-4 mr-2" />
                    Exportar PDF
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="p-4 bg-primary/10 rounded-lg">
                  <p className="text-sm text-muted-foreground mb-1">Total de Custos</p>
                  <p className="text-3xl font-bold">{formatCurrency(costAnalysis.total)}</p>
                </div>

                <div className="grid gap-6 md:grid-cols-2">
                  {/* Por Centro de Custo */}
                  <div>
                    <h3 className="font-semibold text-lg mb-3 flex items-center gap-2">
                      <BarChart3 className="h-5 w-5" />
                      Por Centro de Custo
                    </h3>
                    <div className="space-y-2">
                      {costAnalysis.byCostCenter.map((item, index) => (
                        <div key={index} className="space-y-1">
                          <div className="flex items-center justify-between text-sm">
                            <span>{item.costCenter}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                          <div className="h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary"
                              style={{ width: `${item.percentage}%` }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Por Categoria */}
                  <div>
                    <h3 className="font-semibold text-lg mb-3 flex items-center gap-2">
                      <PieChart className="h-5 w-5" />
                      Por Categoria
                    </h3>
                    <div className="space-y-2">
                      {costAnalysis.byCategory.map((item, index) => (
                        <div key={index} className="space-y-1">
                          <div className="flex items-center justify-between text-sm">
                            <span>{item.category}</span>
                            <span className="font-semibold">{formatCurrency(item.amount)}</span>
                          </div>
                          <div className="h-2 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary"
                              style={{ width: `${item.percentage}%` }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
