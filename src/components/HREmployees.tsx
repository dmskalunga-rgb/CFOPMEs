import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { hrService, Employee } from '@/services/hrService';
import { UserPlus, Search, Edit, Trash2, FileText, Award, Calendar, TrendingDown, TrendingUp } from 'lucide-react';
import { canPerformAction } from '@/services/quotaService';
import { formatCurrency, formatDate } from '@/lib/index';

export function HREmployees() {
  const { toast } = useToast();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [selectedEmployee, setSelectedEmployee] = useState<Employee | null>(null);
  const [formData, setFormData] = useState<Partial<Employee>>({
    full_name: '',
    email: '',
    phone: '',
    nif: '',
    bi_number: '',
    inss_number: '',
    position: '',
    department: '',
    gross_salary: 0,
    employment_type: 'FULL_TIME',
    contract_type: 'PERMANENT',
    hire_date: new Date().toISOString().split('T')[0],
    status: 'ACTIVE',
    marital_status: 'SINGLE',
    dependents: 0,
    vacation_days_total: 22,
  });

  useEffect(() => {
    loadEmployees();
  }, []);

  const loadEmployees = async () => {
    try {
      setLoading(true);
      const data = await hrService.getAllEmployees('comp-001'); // Mock tenant
      setEmployees(data);
    } catch (error: any) {
      console.error('Erro ao carregar funcionários:', error);
      // Fallback para dados mock
      setEmployees([
        {
          id: '1',
          tenant_id: 'comp-001',
          employee_number: 'EMP001',
          full_name: 'João Silva',
          email: 'joao.silva@empresa.ao',
          phone: '+244 923 456 789',
          nif: '123456789',
          bi_number: '001234567LA045',
          inss_number: 'INSS123456',
          position: 'Gerente de TI',
          department: 'Tecnologia',
          hire_date: '2024-01-15',
          gross_salary: 500000,
          employment_type: 'FULL_TIME',
          contract_type: 'PERMANENT',
          status: 'ACTIVE',
          performance_score: 4.5,
          risk_score: 0.15,
          vacation_days_total: 22,
          vacation_days_used: 5,
          sick_days_used: 2,
        },
        {
          id: '2',
          tenant_id: 'comp-001',
          employee_number: 'EMP002',
          full_name: 'Maria Santos',
          email: 'maria.santos@empresa.ao',
          phone: '+244 923 456 790',
          nif: '987654321',
          bi_number: '002345678LA046',
          inss_number: 'INSS234567',
          position: 'Contabilista',
          department: 'Financeiro',
          hire_date: '2023-06-01',
          gross_salary: 350000,
          employment_type: 'FULL_TIME',
          contract_type: 'PERMANENT',
          status: 'ACTIVE',
          performance_score: 4.8,
          risk_score: 0.08,
          vacation_days_total: 22,
          vacation_days_used: 10,
          sick_days_used: 1,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (selectedEmployee) {
        await hrService.updateEmployee(selectedEmployee.id, formData);
        toast({ title: 'Sucesso', description: 'Funcionário atualizado com sucesso' });
      } else {
        // Verificar quota antes de criar
        const check = await canPerformAction('create_employee');
        if (!check.allowed) {
          toast({
            title: 'Limite atingido',
            description: check.message,
            variant: 'destructive',
          });
          return;
        }

        await hrService.createEmployee({ ...formData, tenant_id: 'comp-001' });
        toast({ title: 'Sucesso', description: 'Funcionário criado com sucesso' });
      }
      setIsFormOpen(false);
      setSelectedEmployee(null);
      setFormData({});
      loadEmployees();
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const handleEdit = (employee: Employee) => {
    setSelectedEmployee(employee);
    setFormData(employee);
    setIsFormOpen(true);
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Tem certeza que deseja deletar este funcionário?')) return;
    try {
      await hrService.deleteEmployee(id);
      toast({ title: 'Sucesso', description: 'Funcionário deletado com sucesso' });
      loadEmployees();
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const filteredEmployees = employees.filter((emp) => {
    const matchesSearch =
      emp.full_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      emp.email?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      emp.employee_number?.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = filterStatus === 'all' || emp.status === filterStatus;
    return matchesSearch && matchesStatus;
  });

  const stats = {
    total: employees.length,
    active: employees.filter((e) => e.status === 'ACTIVE').length,
    inactive: employees.filter((e) => e.status === 'INACTIVE').length,
    highRisk: employees.filter((e) => (e.risk_score || 0) >= 0.5).length,
    avgSalary: employees.length > 0 ? employees.reduce((sum, e) => sum + e.gross_salary, 0) / employees.length : 0,
  };

  return (
    <div className="space-y-6">
      {/* Stats Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Funcionários</CardTitle>
            <UserPlus className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats.total}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Ativos</CardTitle>
            <TrendingUp className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-green-600">{stats.active}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Inativos</CardTitle>
            <TrendingDown className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-orange-600">{stats.inactive}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Alto Risco</CardTitle>
            <TrendingDown className="h-4 w-4 text-red-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold text-red-600">{stats.highRisk}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Salário Médio</CardTitle>
            <Award className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(stats.avgSalary)}</div>
          </CardContent>
        </Card>
      </div>

      {/* Filters and Actions */}
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
              <CardTitle>Funcionários</CardTitle>
              <CardDescription>Gestão completa de funcionários</CardDescription>
            </div>
            <Dialog open={isFormOpen} onOpenChange={setIsFormOpen}>
              <DialogTrigger asChild>
                <Button onClick={() => { setSelectedEmployee(null); setFormData({}); }}>
                  <UserPlus className="mr-2 h-4 w-4" />
                  Novo Funcionário
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                  <DialogTitle>{selectedEmployee ? 'Editar Funcionário' : 'Novo Funcionário'}</DialogTitle>
                  <DialogDescription>
                    Preencha os dados do funcionário
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="space-y-4">
                  <Tabs defaultValue="personal" className="w-full">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="personal">Dados Pessoais</TabsTrigger>
                      <TabsTrigger value="professional">Dados Profissionais</TabsTrigger>
                      <TabsTrigger value="financial">Dados Financeiros</TabsTrigger>
                    </TabsList>
                    <TabsContent value="personal" className="space-y-4 mt-4">
                      <div className="grid grid-cols-2 gap-4">
                        <div className="col-span-2">
                          <Label htmlFor="full_name">Nome Completo *</Label>
                          <Input
                            id="full_name"
                            value={formData.full_name || ''}
                            onChange={(e) => setFormData({ ...formData, full_name: e.target.value })}
                            required
                          />
                        </div>
                        <div>
                          <Label htmlFor="email">Email</Label>
                          <Input
                            id="email"
                            type="email"
                            value={formData.email || ''}
                            onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="phone">Telefone</Label>
                          <Input
                            id="phone"
                            value={formData.phone || ''}
                            onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="nif">NIF</Label>
                          <Input
                            id="nif"
                            value={formData.nif || ''}
                            onChange={(e) => setFormData({ ...formData, nif: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="bi_number">Bilhete de Identidade</Label>
                          <Input
                            id="bi_number"
                            value={formData.bi_number || ''}
                            onChange={(e) => setFormData({ ...formData, bi_number: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="inss_number">Número INSS</Label>
                          <Input
                            id="inss_number"
                            value={formData.inss_number || ''}
                            onChange={(e) => setFormData({ ...formData, inss_number: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="date_of_birth">Data de Nascimento</Label>
                          <Input
                            id="date_of_birth"
                            type="date"
                            value={formData.date_of_birth || ''}
                            onChange={(e) => setFormData({ ...formData, date_of_birth: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="marital_status">Estado Civil</Label>
                          <Select
                            value={formData.marital_status || 'SINGLE'}
                            onValueChange={(value) => setFormData({ ...formData, marital_status: value })}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="SINGLE">Solteiro(a)</SelectItem>
                              <SelectItem value="MARRIED">Casado(a)</SelectItem>
                              <SelectItem value="DIVORCED">Divorciado(a)</SelectItem>
                              <SelectItem value="WIDOWED">Viúvo(a)</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div>
                          <Label htmlFor="dependents">Dependentes</Label>
                          <Input
                            id="dependents"
                            type="number"
                            value={formData.dependents || 0}
                            onChange={(e) => setFormData({ ...formData, dependents: parseInt(e.target.value) })}
                          />
                        </div>
                      </div>
                    </TabsContent>
                    <TabsContent value="professional" className="space-y-4 mt-4">
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <Label htmlFor="position">Cargo *</Label>
                          <Input
                            id="position"
                            value={formData.position || ''}
                            onChange={(e) => setFormData({ ...formData, position: e.target.value })}
                            required
                          />
                        </div>
                        <div>
                          <Label htmlFor="department">Departamento</Label>
                          <Input
                            id="department"
                            value={formData.department || ''}
                            onChange={(e) => setFormData({ ...formData, department: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="hire_date">Data de Admissão *</Label>
                          <Input
                            id="hire_date"
                            type="date"
                            value={formData.hire_date || ''}
                            onChange={(e) => setFormData({ ...formData, hire_date: e.target.value })}
                            required
                          />
                        </div>
                        <div>
                          <Label htmlFor="employment_type">Tipo de Emprego</Label>
                          <Select
                            value={formData.employment_type || 'FULL_TIME'}
                            onValueChange={(value) => setFormData({ ...formData, employment_type: value })}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="FULL_TIME">Tempo Integral</SelectItem>
                              <SelectItem value="PART_TIME">Meio Período</SelectItem>
                              <SelectItem value="CONTRACT">Contrato</SelectItem>
                              <SelectItem value="INTERN">Estágio</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div>
                          <Label htmlFor="contract_type">Tipo de Contrato</Label>
                          <Select
                            value={formData.contract_type || 'PERMANENT'}
                            onValueChange={(value) => setFormData({ ...formData, contract_type: value })}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="PERMANENT">Permanente</SelectItem>
                              <SelectItem value="FIXED_TERM">Prazo Determinado</SelectItem>
                              <SelectItem value="TEMPORARY">Temporário</SelectItem>
                              <SelectItem value="INTERNSHIP">Estágio</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div>
                          <Label htmlFor="status">Status</Label>
                          <Select
                            value={formData.status || 'ACTIVE'}
                            onValueChange={(value) => setFormData({ ...formData, status: value })}
                          >
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="ACTIVE">Ativo</SelectItem>
                              <SelectItem value="INACTIVE">Inativo</SelectItem>
                              <SelectItem value="TERMINATED">Desligado</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </TabsContent>
                    <TabsContent value="financial" className="space-y-4 mt-4">
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <Label htmlFor="gross_salary">Salário Bruto (Kz) *</Label>
                          <Input
                            id="gross_salary"
                            type="number"
                            value={formData.gross_salary || 0}
                            onChange={(e) => setFormData({ ...formData, gross_salary: parseFloat(e.target.value) })}
                            required
                          />
                        </div>
                        <div>
                          <Label htmlFor="bank_name">Banco</Label>
                          <Input
                            id="bank_name"
                            value={formData.bank_name || ''}
                            onChange={(e) => setFormData({ ...formData, bank_name: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="bank_account">Conta Bancária</Label>
                          <Input
                            id="bank_account"
                            value={formData.bank_account || ''}
                            onChange={(e) => setFormData({ ...formData, bank_account: e.target.value })}
                          />
                        </div>
                        <div>
                          <Label htmlFor="iban">IBAN</Label>
                          <Input
                            id="iban"
                            value={formData.iban || ''}
                            onChange={(e) => setFormData({ ...formData, iban: e.target.value })}
                          />
                        </div>
                      </div>
                    </TabsContent>
                  </Tabs>
                  <div className="flex justify-end gap-2 pt-4">
                    <Button type="button" variant="outline" onClick={() => setIsFormOpen(false)}>
                      Cancelar
                    </Button>
                    <Button type="submit">
                      {selectedEmployee ? 'Atualizar' : 'Criar'}
                    </Button>
                  </div>
                </form>
              </DialogContent>
            </Dialog>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col sm:flex-row gap-4 mb-4">
            <div className="flex-1">
              <div className="relative">
                <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Pesquisar funcionários..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-8"
                />
              </div>
            </div>
            <Select value={filterStatus} onValueChange={setFilterStatus}>
              <SelectTrigger className="w-full sm:w-[180px]">
                <SelectValue placeholder="Filtrar por status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos</SelectItem>
                <SelectItem value="ACTIVE">Ativos</SelectItem>
                <SelectItem value="INACTIVE">Inativos</SelectItem>
                <SelectItem value="TERMINATED">Desligados</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {loading ? (
            <div className="text-center py-8">Carregando...</div>
          ) : (
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nome</TableHead>
                    <TableHead>Cargo</TableHead>
                    <TableHead>Departamento</TableHead>
                    <TableHead>Salário</TableHead>
                    <TableHead>Performance</TableHead>
                    <TableHead>Risco</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Ações</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredEmployees.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                        Nenhum funcionário encontrado
                      </TableCell>
                    </TableRow>
                  ) : (
                    filteredEmployees.map((employee) => (
                      <TableRow key={employee.id}>
                        <TableCell className="font-medium">{employee.full_name}</TableCell>
                        <TableCell>{employee.position}</TableCell>
                        <TableCell>{employee.department || '-'}</TableCell>
                        <TableCell>{formatCurrency(employee.gross_salary)}</TableCell>
                        <TableCell>
                          {employee.performance_score ? (
                            <Badge variant={employee.performance_score >= 4 ? 'default' : 'secondary'}>
                              {employee.performance_score.toFixed(1)}
                            </Badge>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell>
                          {employee.risk_score !== undefined ? (
                            <Badge
                              variant={
                                employee.risk_score >= 0.7
                                  ? 'destructive'
                                  : employee.risk_score >= 0.5
                                  ? 'default'
                                  : 'secondary'
                              }
                            >
                              {(employee.risk_score * 100).toFixed(0)}%
                            </Badge>
                          ) : (
                            '-'
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              employee.status === 'ACTIVE'
                                ? 'default'
                                : employee.status === 'INACTIVE'
                                ? 'secondary'
                                : 'destructive'
                            }
                          >
                            {employee.status === 'ACTIVE'
                              ? 'Ativo'
                              : employee.status === 'INACTIVE'
                              ? 'Inativo'
                              : 'Desligado'}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex justify-end gap-2">
                            <Button variant="ghost" size="sm" onClick={() => handleEdit(employee)}>
                              <Edit className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" size="sm" onClick={() => handleDelete(employee.id)}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
