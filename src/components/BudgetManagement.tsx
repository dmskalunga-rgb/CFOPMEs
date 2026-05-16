import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Plus, Target, AlertTriangle, TrendingUp, TrendingDown, Edit, Trash2 } from 'lucide-react';
import { budgetService, Budget, BudgetCategory } from '@/services/budgetService';
import { TRANSACTION_CATEGORIES, formatCurrency } from '@/lib/index';
import { useToast } from '@/lib/toast-provider';
import { motion } from 'framer-motion';

export function BudgetManagement() {
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [selectedBudget, setSelectedBudget] = useState<Budget | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const { success, error: showError } = useToast();

  // Form state
  const [formData, setFormData] = useState({
    name: '',
    period: 'monthly' as 'monthly' | 'quarterly' | 'yearly',
    startDate: new Date().toISOString().slice(0, 10),
    endDate: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    totalBudget: 0,
    categories: [] as BudgetCategory[],
  });

  useEffect(() => {
    loadBudgets();
  }, []);

  const loadBudgets = async () => {
    setLoading(true);
    try {
      const data = await budgetService.listBudgets('comp-001');
      setBudgets(data);
      if (data.length > 0 && !selectedBudget) {
        setSelectedBudget(data[0]);
        loadBudgetProgress(data[0].id);
      }
    } catch (err) {
      console.error('Erro ao carregar orçamentos:', err);
      showError('Erro', 'Não foi possível carregar os orçamentos');
    } finally {
      setLoading(false);
    }
  };

  const loadBudgetProgress = async (budgetId: string) => {
    try {
      const progress = await budgetService.calculateBudgetProgress(budgetId);
      if (selectedBudget) {
        setSelectedBudget({ ...selectedBudget, categories: progress });
      }
    } catch (err) {
      console.error('Erro ao calcular progresso:', err);
    }
  };

  const handleCreateBudget = async () => {
    try {
      if (!formData.name || formData.categories.length === 0) {
        showError('Erro', 'Preencha todos os campos obrigatórios');
        return;
      }

      const newBudget = await budgetService.createBudget({
        companyId: 'comp-001',
        name: formData.name,
        period: formData.period,
        startDate: new Date(formData.startDate),
        endDate: new Date(formData.endDate),
        totalBudget: formData.totalBudget,
        categories: formData.categories,
        status: 'active',
      });

      setBudgets([newBudget, ...budgets]);
      setSelectedBudget(newBudget);
      setIsDialogOpen(false);
      success('Sucesso', 'Orçamento criado com sucesso');
      resetForm();
    } catch (err) {
      console.error('Erro ao criar orçamento:', err);
      showError('Erro', 'Não foi possível criar o orçamento');
    }
  };

  const addCategory = () => {
    const newCategory: BudgetCategory = {
      category: '',
      allocatedAmount: 0,
      spentAmount: 0,
      remainingAmount: 0,
      percentage: 0,
      status: 'on_track',
    };
    setFormData({ ...formData, categories: [...formData.categories, newCategory] });
  };

  const updateCategory = (index: number, field: keyof BudgetCategory, value: any) => {
    const updatedCategories = [...formData.categories];
    updatedCategories[index] = { ...updatedCategories[index], [field]: value };
    
    if (field === 'allocatedAmount') {
      updatedCategories[index].remainingAmount = value;
    }
    
    const total = updatedCategories.reduce((sum, cat) => sum + (cat.allocatedAmount || 0), 0);
    setFormData({ ...formData, categories: updatedCategories, totalBudget: total });
  };

  const removeCategory = (index: number) => {
    const updatedCategories = formData.categories.filter((_, i) => i !== index);
    const total = updatedCategories.reduce((sum, cat) => sum + (cat.allocatedAmount || 0), 0);
    setFormData({ ...formData, categories: updatedCategories, totalBudget: total });
  };

  const resetForm = () => {
    setFormData({
      name: '',
      period: 'monthly',
      startDate: new Date().toISOString().slice(0, 10),
      endDate: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
      totalBudget: 0,
      categories: [],
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'on_track':
        return 'bg-green-500';
      case 'warning':
        return 'bg-yellow-500';
      case 'exceeded':
        return 'bg-red-500';
      default:
        return 'bg-gray-500';
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'on_track':
        return <Badge className="bg-green-500">No Prazo</Badge>;
      case 'warning':
        return <Badge className="bg-yellow-500">Atenção</Badge>;
      case 'exceeded':
        return <Badge variant="destructive">Excedido</Badge>;
      default:
        return <Badge variant="secondary">Desconhecido</Badge>;
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <Target className="h-12 w-12 animate-pulse text-primary mx-auto mb-4" />
          <p className="text-muted-foreground">Carregando orçamentos...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold tracking-tight flex items-center gap-2">
            <Target className="h-6 w-6 text-primary" />
            Gestão de Orçamento
          </h2>
          <p className="text-muted-foreground mt-1">
            Planeje e acompanhe seus orçamentos por categoria
          </p>
        </div>
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogTrigger asChild>
            <Button className="gap-2">
              <Plus className="h-4 w-4" />
              Novo Orçamento
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>Criar Novo Orçamento</DialogTitle>
              <DialogDescription>
                Defina o orçamento e aloque valores por categoria
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Nome do Orçamento</Label>
                  <Input
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    placeholder="Ex: Orçamento Q1 2026"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Período</Label>
                  <Select
                    value={formData.period}
                    onValueChange={(value: any) => setFormData({ ...formData, period: value })}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="monthly">Mensal</SelectItem>
                      <SelectItem value="quarterly">Trimestral</SelectItem>
                      <SelectItem value="yearly">Anual</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Data Início</Label>
                  <Input
                    type="date"
                    value={formData.startDate}
                    onChange={(e) => setFormData({ ...formData, startDate: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Data Fim</Label>
                  <Input
                    type="date"
                    value={formData.endDate}
                    onChange={(e) => setFormData({ ...formData, endDate: e.target.value })}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Categorias</Label>
                  <Button type="button" variant="outline" size="sm" onClick={addCategory}>
                    <Plus className="h-4 w-4 mr-2" />
                    Adicionar Categoria
                  </Button>
                </div>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {formData.categories.map((cat, index) => (
                    <div key={index} className="flex gap-2 items-end">
                      <div className="flex-1 space-y-2">
                        <Select
                          value={cat.category}
                          onValueChange={(value) => updateCategory(index, 'category', value)}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="Selecione a categoria" />
                          </SelectTrigger>
                          <SelectContent>
                            {[...TRANSACTION_CATEGORIES.INCOME, ...TRANSACTION_CATEGORIES.EXPENSE].map((c) => (
                              <SelectItem key={c} value={c}>
                                {c}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="flex-1 space-y-2">
                        <Input
                          type="number"
                          placeholder="Valor alocado"
                          value={cat.allocatedAmount || ''}
                          onChange={(e) =>
                            updateCategory(index, 'allocatedAmount', parseFloat(e.target.value) || 0)
                          }
                        />
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={() => removeCategory(index)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>

              <div className="pt-4 border-t">
                <div className="flex items-center justify-between text-lg font-semibold">
                  <span>Total do Orçamento:</span>
                  <span>{formatCurrency(formData.totalBudget)}</span>
                </div>
              </div>

              <div className="flex gap-2 justify-end">
                <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
                  Cancelar
                </Button>
                <Button onClick={handleCreateBudget}>Criar Orçamento</Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      {/* Budget Selection */}
      {budgets.length > 0 && (
        <div className="flex gap-2 overflow-x-auto pb-2">
          {budgets.map((budget) => (
            <Button
              key={budget.id}
              variant={selectedBudget?.id === budget.id ? 'default' : 'outline'}
              onClick={() => {
                setSelectedBudget(budget);
                loadBudgetProgress(budget.id);
              }}
              className="whitespace-nowrap"
            >
              {budget.name}
            </Button>
          ))}
        </div>
      )}

      {/* Budget Details */}
      {selectedBudget && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="space-y-6"
        >
          {/* Summary Cards */}
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Orçamento Total</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{formatCurrency(selectedBudget.totalBudget)}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Gasto Total</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">
                  {formatCurrency(
                    selectedBudget.categories.reduce((sum, cat) => sum + cat.spentAmount, 0)
                  )}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Restante</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-green-600">
                  {formatCurrency(
                    selectedBudget.categories.reduce((sum, cat) => sum + cat.remainingAmount, 0)
                  )}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">Status</CardTitle>
              </CardHeader>
              <CardContent>
                <Badge variant={selectedBudget.status === 'active' ? 'default' : 'secondary'}>
                  {selectedBudget.status === 'active' ? 'Ativo' : selectedBudget.status}
                </Badge>
              </CardContent>
            </Card>
          </div>

          {/* Categories */}
          <Card>
            <CardHeader>
              <CardTitle>Categorias do Orçamento</CardTitle>
              <CardDescription>Acompanhe o progresso de cada categoria</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {selectedBudget.categories.map((cat, index) => (
                  <div key={index} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{cat.category}</span>
                        {getStatusBadge(cat.status)}
                      </div>
                      <div className="text-sm text-muted-foreground">
                        {formatCurrency(cat.spentAmount)} / {formatCurrency(cat.allocatedAmount)}
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Progress value={cat.percentage} className={getStatusColor(cat.status)} />
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>{cat.percentage.toFixed(1)}% utilizado</span>
                        <span>Restante: {formatCurrency(cat.remainingAmount)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      )}

      {budgets.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <Target className="h-16 w-16 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">Nenhum orçamento criado</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Crie seu primeiro orçamento para começar a planejar suas finanças
            </p>
            <Button onClick={() => setIsDialogOpen(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Criar Primeiro Orçamento
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
