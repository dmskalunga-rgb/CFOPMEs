import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { useToast } from '@/hooks/use-toast';
import { hrService } from '@/services/hrService';
import { DollarSign, Calculator, Download, FileText, TrendingUp, Users, AlertCircle } from 'lucide-react';
import { formatCurrency, formatDate } from '@/lib/index';

interface Employee {
  id: string;
  full_name: string;
  employee_number?: string;
  position: string;
  department?: string;
  gross_salary: number;
  allowances?: any;
}

export function HRPayrollAdvanced() {
  const { toast } = useToast();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedEmployees, setSelectedEmployees] = useState<string[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [payrollMonth, setPayrollMonth] = useState(new Date().toISOString().substring(0, 7));
  const [results, setResults] = useState<any[]>([]);
  const [showResults, setShowResults] = useState(false);

  // Opções de payroll
  const [options, setOptions] = useState({
    paymentDate: new Date().toISOString().split('T')[0],
    foodAllowance: 15000,
    transportAllowance: 10000,
    enableAI: true,
    createTransactions: true,
  });

  // Horas extras, bónus, comissões por funcionário
  const [overtime, setOvertime] = useState<Record<string, number>>({});
  const [bonuses, setBonuses] = useState<Record<string, number>>({});
  const [commissions, setCommissions] = useState<Record<string, number>>({});
  const [advances, setAdvances] = useState<Record<string, number>>({});

  useEffect(() => {
    loadEmployees();
  }, []);

  const loadEmployees = async () => {
    try {
      setLoading(true);
      const data = await hrService.getActiveEmployees('comp-001');
      setEmployees(data);
    } catch (error: any) {
      console.error('Erro ao carregar funcionários:', error);
      // Fallback para dados mock
      setEmployees([
        {
          id: '1',
          full_name: 'João Silva',
          employee_number: 'EMP001',
          position: 'Gerente de TI',
          department: 'Tecnologia',
          gross_salary: 500000,
          allowances: { food: 15000, transport: 10000 },
        },
        {
          id: '2',
          full_name: 'Maria Santos',
          employee_number: 'EMP002',
          position: 'Contabilista',
          department: 'Financeiro',
          gross_salary: 350000,
          allowances: { food: 15000, transport: 10000 },
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleEmployee = (employeeId: string) => {
    setSelectedEmployees((prev) =>
      prev.includes(employeeId) ? prev.filter((id) => id !== employeeId) : [...prev, employeeId]
    );
  };

  const handleSelectAll = () => {
    if (selectedEmployees.length === employees.length) {
      setSelectedEmployees([]);
    } else {
      setSelectedEmployees(employees.map((emp) => emp.id));
    }
  };

  const handleProcessPayroll = async () => {
    if (selectedEmployees.length === 0) {
      toast({ title: 'Erro', description: 'Selecione pelo menos um funcionário', variant: 'destructive' });
      return;
    }

    try {
      setIsProcessing(true);
      const result = await hrService.processPayroll('comp-001', payrollMonth, selectedEmployees, {
        ...options,
        overtime,
        bonuses,
        commissions,
        advances,
      });

      setResults(result.calculations || []);
      setShowResults(true);
      toast({
        title: 'Sucesso',
        description: `Payroll processado para ${result.calculations?.length || 0} funcionário(s)`,
      });
    } catch (error: any) {
      console.error('Erro ao processar payroll:', error);
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleDownloadPDF = () => {
    toast({ title: 'Info', description: 'Geração de PDF em desenvolvimento', variant: 'default' });
  };

  const totalGross = results.reduce((sum, r) => sum + r.totalEarnings, 0);
  const totalNet = results.reduce((sum, r) => sum + r.netSalary, 0);
  const totalDeductions = results.reduce((sum, r) => sum + r.totalDeductions, 0);

  return (
    <div className="space-y-6">
      {/* Stats Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Funcionários Selecionados</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{selectedEmployees.length}</div>
            <p className="text-xs text-muted-foreground">de {employees.length} ativos</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Bruto</CardTitle>
            <TrendingUp className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(totalGross)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Deduções</CardTitle>
            <AlertCircle className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-600">{formatCurrency(totalDeductions)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Líquido</CardTitle>
            <DollarSign className="h-4 w-4 text-primary" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-primary">{formatCurrency(totalNet)}</div>
          </CardContent>
        </Card>
      </div>

      {/* Configuration */}
      <Card>
        <CardHeader>
          <CardTitle>Configuração de Payroll</CardTitle>
          <CardDescription>Configure o mês e opções de processamento</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="payrollMonth">Mês de Payroll</Label>
              <Input
                id="payrollMonth"
                type="month"
                value={payrollMonth}
                onChange={(e) => setPayrollMonth(e.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="paymentDate">Data de Pagamento</Label>
              <Input
                id="paymentDate"
                type="date"
                value={options.paymentDate}
                onChange={(e) => setOptions({ ...options, paymentDate: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor="foodAllowance">Subsídio Alimentação (Kz)</Label>
              <Input
                id="foodAllowance"
                type="number"
                value={options.foodAllowance}
                onChange={(e) => setOptions({ ...options, foodAllowance: parseFloat(e.target.value) })}
              />
            </div>
            <div>
              <Label htmlFor="transportAllowance">Subsídio Transporte (Kz)</Label>
              <Input
                id="transportAllowance"
                type="number"
                value={options.transportAllowance}
                onChange={(e) => setOptions({ ...options, transportAllowance: parseFloat(e.target.value) })}
              />
            </div>
            <div className="flex items-center space-x-2 pt-6">
              <Checkbox
                id="enableAI"
                checked={options.enableAI}
                onCheckedChange={(checked) => setOptions({ ...options, enableAI: checked as boolean })}
              />
              <Label htmlFor="enableAI">Ativar IA (Previsão de Turnover)</Label>
            </div>
            <div className="flex items-center space-x-2 pt-6">
              <Checkbox
                id="createTransactions"
                checked={options.createTransactions}
                onCheckedChange={(checked) => setOptions({ ...options, createTransactions: checked as boolean })}
              />
              <Label htmlFor="createTransactions">Criar Transações Financeiras</Label>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Employee Selection */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Selecionar Funcionários</CardTitle>
              <CardDescription>Escolha os funcionários para processar payroll</CardDescription>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleSelectAll}>
                {selectedEmployees.length === employees.length ? 'Desmarcar Todos' : 'Selecionar Todos'}
              </Button>
              <Button onClick={handleProcessPayroll} disabled={isProcessing || selectedEmployees.length === 0}>
                <Calculator className="mr-2 h-4 w-4" />
                {isProcessing ? 'Processando...' : 'Processar Payroll'}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-8">Carregando...</div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12">
                      <Checkbox
                        checked={selectedEmployees.length === employees.length}
                        onCheckedChange={handleSelectAll}
                      />
                    </TableHead>
                    <TableHead>Nome</TableHead>
                    <TableHead>Cargo</TableHead>
                    <TableHead>Salário Base</TableHead>
                    <TableHead>Horas Extras</TableHead>
                    <TableHead>Bónus</TableHead>
                    <TableHead>Comissões</TableHead>
                    <TableHead>Adiantamentos</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {employees.map((employee) => (
                    <TableRow key={employee.id}>
                      <TableCell>
                        <Checkbox
                          checked={selectedEmployees.includes(employee.id)}
                          onCheckedChange={() => handleToggleEmployee(employee.id)}
                        />
                      </TableCell>
                      <TableCell className="font-medium">{employee.full_name}</TableCell>
                      <TableCell>{employee.position}</TableCell>
                      <TableCell>{formatCurrency(employee.gross_salary)}</TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          placeholder="0"
                          className="w-24"
                          value={overtime[employee.id] || ''}
                          onChange={(e) => setOvertime({ ...overtime, [employee.id]: parseFloat(e.target.value) || 0 })}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          placeholder="0"
                          className="w-24"
                          value={bonuses[employee.id] || ''}
                          onChange={(e) => setBonuses({ ...bonuses, [employee.id]: parseFloat(e.target.value) || 0 })}
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          placeholder="0"
                          className="w-24"
                          value={commissions[employee.id] || ''}
                          onChange={(e) =>
                            setCommissions({ ...commissions, [employee.id]: parseFloat(e.target.value) || 0 })
                          }
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          placeholder="0"
                          className="w-24"
                          value={advances[employee.id] || ''}
                          onChange={(e) => setAdvances({ ...advances, [employee.id]: parseFloat(e.target.value) || 0 })}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Results */}
      {showResults && results.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Resultados do Payroll</CardTitle>
                <CardDescription>Recibos gerados para {results.length} funcionário(s)</CardDescription>
              </div>
              <Button onClick={handleDownloadPDF}>
                <Download className="mr-2 h-4 w-4" />
                Baixar PDF
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nome</TableHead>
                    <TableHead>Salário Bruto</TableHead>
                    <TableHead>Subsídios</TableHead>
                    <TableHead>Total Ganhos</TableHead>
                    <TableHead>INSS</TableHead>
                    <TableHead>IRT</TableHead>
                    <TableHead>Deduções</TableHead>
                    <TableHead>Salário Líquido</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {results.map((result) => (
                    <TableRow key={result.employeeId}>
                      <TableCell className="font-medium">{result.employeeName}</TableCell>
                      <TableCell>{formatCurrency(result.grossSalary)}</TableCell>
                      <TableCell>
                        {formatCurrency(
                          result.foodAllowance +
                            result.transportAllowance +
                            result.vacationAllowance +
                            result.christmasAllowance
                        )}
                      </TableCell>
                      <TableCell className="font-semibold">{formatCurrency(result.totalEarnings)}</TableCell>
                      <TableCell className="text-orange-600">{formatCurrency(result.inssEmployee)}</TableCell>
                      <TableCell className="text-orange-600">
                        {formatCurrency(result.irt)}
                        <Badge variant="outline" className="ml-2">
                          Escalão {result.irtBracket}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-red-600">{formatCurrency(result.totalDeductions)}</TableCell>
                      <TableCell className="font-bold text-green-600">{formatCurrency(result.netSalary)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
