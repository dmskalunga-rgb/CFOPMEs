// Finance Page with Complete UX - Real Supabase Integration
import { useState, useEffect, useMemo } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Plus,
  Edit,
  Trash2,
  TrendingUp,
  TrendingDown,
  DollarSign,
  ArrowUpCircle,
  ArrowDownCircle,
  Wallet,
} from 'lucide-react';
import { toast } from 'sonner';
import { transactionsService, type Transaction } from '@/services/transactionsServiceReal';
import { useConfirmDialog } from '@/components/ui/confirm-dialog';
import { Pagination, usePagination } from '@/components/ui/pagination';
import { AdvancedFilters, useFilters, type FilterConfig } from '@/components/ui/advanced-filters';
import { SearchInput, useSearch } from '@/components/ui/search-input';
import { EmptyState, ErrorState } from '@/components/ui/states';
import { PageSkeleton } from '@/components/ui/skeletons';
import { ExportButton } from '@/lib/export';
import {
  CustomLineChart,
  CustomPieChart,
  CustomAreaChart,
  CustomBarChart,
  StatCard,
} from '@/components/ui/charts';

export default function FinancePageReal() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingTransaction, setEditingTransaction] = useState<Transaction | null>(null);
  const [formData, setFormData] = useState({
    type: 'income' as 'income' | 'expense',
    category: '',
    description: '',
    amount: '',
    transaction_date: new Date().toISOString().split('T')[0],
    payment_method: 'cash',
    reference: '',
    status: 'completed' as 'pending' | 'completed' | 'cancelled',
    notes: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const { isOpen, setIsOpen, confirm, ConfirmDialog } = useConfirmDialog();

  const loadTransactions = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await transactionsService.getAll();
      setTransactions(data);
    } catch (err) {
      setError(err as Error);
      toast.error('Erro ao carregar transações');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTransactions();
  }, []);

  // Search
  const { query, setQuery, searchedData } = useSearch(
    transactions,
    (transaction, q) =>
      transaction.description.toLowerCase().includes(q.toLowerCase()) ||
      transaction.category.toLowerCase().includes(q.toLowerCase()) ||
      (transaction.reference && transaction.reference.toLowerCase().includes(q.toLowerCase()))
  );

  // Filters
  const filterConfigs: FilterConfig[] = [
    {
      key: 'type',
      label: 'Tipo',
      type: 'select',
      options: [
        { label: 'Receita', value: 'income' },
        { label: 'Despesa', value: 'expense' },
      ],
      placeholder: 'Todos os tipos',
    },
    {
      key: 'category',
      label: 'Categoria',
      type: 'select',
      options: [
        { label: 'Vendas', value: 'Vendas' },
        { label: 'Serviços', value: 'Serviços' },
        { label: 'Salários', value: 'Salários' },
        { label: 'Aluguel', value: 'Aluguel' },
        { label: 'Fornecedores', value: 'Fornecedores' },
        { label: 'Marketing', value: 'Marketing' },
        { label: 'Outros', value: 'Outros' },
      ],
      placeholder: 'Todas as categorias',
    },
    {
      key: 'status',
      label: 'Status',
      type: 'select',
      options: [
        { label: 'Concluída', value: 'completed' },
        { label: 'Pendente', value: 'pending' },
        { label: 'Cancelada', value: 'cancelled' },
      ],
      placeholder: 'Todos os status',
    },
    {
      key: 'payment_method',
      label: 'Método de Pagamento',
      type: 'select',
      options: [
        { label: 'Dinheiro', value: 'cash' },
        { label: 'Transferência', value: 'transfer' },
        { label: 'Cartão', value: 'card' },
        { label: 'Cheque', value: 'check' },
      ],
      placeholder: 'Todos os métodos',
    },
    {
      key: 'start_date',
      label: 'Data Inicial',
      type: 'date',
    },
    {
      key: 'end_date',
      label: 'Data Final',
      type: 'date',
    },
  ];

  const { filters, setFilters, filteredData } = useFilters(searchedData, (transaction, f) => {
    if (f.type && transaction.type !== f.type) return false;
    if (f.category && transaction.category !== f.category) return false;
    if (f.status && transaction.status !== f.status) return false;
    if (f.payment_method && transaction.payment_method !== f.payment_method) return false;
    if (f.start_date && transaction.transaction_date < f.start_date) return false;
    if (f.end_date && transaction.transaction_date > f.end_date) return false;
    return true;
  });

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    paginatedData,
    handlePageChange,
    handlePageSizeChange,
  } = usePagination(filteredData, 10);

  // Stats - Only completed transactions
  const completedTransactions = transactions.filter((t) => t.status === 'completed');
  const totalIncome = completedTransactions
    .filter((t) => t.type === 'income')
    .reduce((sum, t) => sum + t.amount, 0);
  const totalExpense = completedTransactions
    .filter((t) => t.type === 'expense')
    .reduce((sum, t) => sum + t.amount, 0);
  const balance = totalIncome - totalExpense;
  const pendingCount = transactions.filter((t) => t.status === 'pending').length;

  // Chart data - Monthly income vs expense
  const monthlyData = useMemo(() => {
    const months: Record<string, { income: number; expense: number }> = {};

    completedTransactions.forEach((transaction) => {
      const date = new Date(transaction.transaction_date);
      const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;

      if (!months[monthKey]) {
        months[monthKey] = { income: 0, expense: 0 };
      }

      if (transaction.type === 'income') {
        months[monthKey].income += transaction.amount;
      } else {
        months[monthKey].expense += transaction.amount;
      }
    });

    return Object.entries(months)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-6) // Last 6 months
      .map(([month, data]) => ({
        name: new Date(month + '-01').toLocaleDateString('pt-AO', { month: 'short', year: '2-digit' }),
        receita: data.income,
        despesa: data.expense,
        saldo: data.income - data.expense,
      }));
  }, [completedTransactions]);

  // Chart data - Transactions by category
  const categoryData = useMemo(() => {
    const categories: Record<string, number> = {};

    completedTransactions.forEach((transaction) => {
      categories[transaction.category] = (categories[transaction.category] || 0) + transaction.amount;
    });

    return Object.entries(categories)
      .map(([name, value]) => ({ name, value: value as number }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6); // Top 6 categories
  }, [completedTransactions]);

  // Chart data - Income vs Expense by type
  const typeData = [
    { name: 'Receitas', valor: totalIncome },
    { name: 'Despesas', valor: totalExpense },
  ];

  // Handlers
  const handleCreate = () => {
    setEditingTransaction(null);
    setFormData({
      type: 'income',
      category: '',
      description: '',
      amount: '',
      transaction_date: new Date().toISOString().split('T')[0],
      payment_method: 'cash',
      reference: '',
      status: 'completed',
      notes: '',
    });
    setIsDialogOpen(true);
  };

  const handleEdit = (transaction: Transaction) => {
    setEditingTransaction(transaction);
    setFormData({
      type: transaction.type,
      category: transaction.category,
      description: transaction.description,
      amount: transaction.amount.toString(),
      transaction_date: transaction.transaction_date,
      payment_method: transaction.payment_method,
      reference: transaction.reference || '',
      status: transaction.status,
      notes: transaction.notes || '',
    });
    setIsDialogOpen(true);
  };

  const handleDelete = (transaction: Transaction) => {
    confirm(
      'Deletar Transação',
      `Tem certeza que deseja deletar esta transação de ${formatCurrency(transaction.amount)}? Esta ação não pode ser desfeita.`,
      async () => {
        try {
          await transactionsService.delete(transaction.id);
          toast.success('Transação deletada com sucesso!');
          loadTransactions();
        } catch (err) {
          toast.error('Erro ao deletar transação');
        }
      },
      'destructive'
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);

    try {
      const transactionData = {
        type: formData.type,
        category: formData.category,
        description: formData.description,
        amount: parseFloat(formData.amount),
        transaction_date: formData.transaction_date,
        payment_method: formData.payment_method,
        reference: formData.reference || undefined,
        status: formData.status,
        notes: formData.notes || undefined,
      };

      if (editingTransaction) {
        await transactionsService.update(editingTransaction.id, transactionData);
        toast.success('Transação atualizada com sucesso!');
      } else {
        await transactionsService.create(transactionData);
        toast.success('Transação criada com sucesso!');
      }

      setIsDialogOpen(false);
      loadTransactions();
    } catch (err) {
      toast.error(editingTransaction ? 'Erro ao atualizar transação' : 'Erro ao criar transação');
    } finally {
      setSubmitting(false);
    }
  };

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  // Export columns
  const exportColumns = [
    { key: 'transaction_date' as keyof Transaction, label: 'Data' },
    { key: 'type' as keyof Transaction, label: 'Tipo' },
    { key: 'category' as keyof Transaction, label: 'Categoria' },
    { key: 'description' as keyof Transaction, label: 'Descrição' },
    { key: 'amount' as keyof Transaction, label: 'Valor' },
    { key: 'payment_method' as keyof Transaction, label: 'Método' },
    { key: 'status' as keyof Transaction, label: 'Status' },
  ];

  if (loading) return <Layout><PageSkeleton /></Layout>;
  if (error) return <Layout><ErrorState error={error} onRetry={loadTransactions} /></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold">Finanças</h1>
            <p className="text-muted-foreground">Gerencie suas transações financeiras</p>
          </div>
          <div className="flex gap-2">
            <ExportButton
              data={filteredData}
              filename="transacoes"
              title="Relatório de Transações"
              columns={exportColumns}
            />
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-2" />
              Nova Transação
            </Button>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <StatCard
            title="Receitas"
            value={formatCurrency(totalIncome)}
            icon={<ArrowUpCircle className="h-4 w-4 text-green-600" />}
            description="Total de entradas"
          />

          <StatCard
            title="Despesas"
            value={formatCurrency(totalExpense)}
            icon={<ArrowDownCircle className="h-4 w-4 text-red-600" />}
            description="Total de saídas"
          />

          <StatCard
            title="Saldo"
            value={formatCurrency(balance)}
            icon={balance >= 0 ? <TrendingUp className="h-4 w-4 text-green-600" /> : <TrendingDown className="h-4 w-4 text-red-600" />}
            description={balance >= 0 ? 'Positivo' : 'Negativo'}
          />

          <StatCard
            title="Pendentes"
            value={pendingCount}
            icon={<Wallet className="h-4 w-4 text-yellow-600" />}
            description="Transações pendentes"
          />
        </div>

        {/* Charts */}
        <div className="grid gap-4 md:grid-cols-2">
          <CustomLineChart
            data={monthlyData}
            lines={[
              { dataKey: 'receita', stroke: '#10b981', name: 'Receita' },
              { dataKey: 'despesa', stroke: '#ef4444', name: 'Despesa' },
              { dataKey: 'saldo', stroke: '#2563eb', name: 'Saldo' },
            ]}
            title="Fluxo de Caixa Mensal"
            description="Últimos 6 meses"
            height={300}
          />

          <CustomPieChart
            data={categoryData}
            title="Transações por Categoria"
            description="Top 6 categorias"
            height={300}
          />
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <CustomBarChart
            data={typeData}
            bars={[
              { dataKey: 'valor', fill: '#2563eb', name: 'Valor' },
            ]}
            title="Receitas vs Despesas"
            description="Comparação total"
            height={300}
          />

          <CustomAreaChart
            data={monthlyData}
            areas={[
              { dataKey: 'saldo', fill: '#2563eb', stroke: '#1d4ed8', name: 'Saldo' },
            ]}
            title="Evolução do Saldo"
            description="Últimos 6 meses"
            height={300}
          />
        </div>

        {/* Search and Filters */}
        <div className="flex gap-4">
          <SearchInput
            placeholder="Buscar por descrição, categoria ou referência..."
            onSearch={setQuery}
            className="flex-1"
          />
          <AdvancedFilters
            filters={filterConfigs}
            onFiltersChange={setFilters}
            activeFilters={filters}
          />
        </div>

        {/* Transactions Table */}
        <Card>
          <CardHeader>
            <CardTitle>Transações ({filteredData.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {filteredData.length === 0 ? (
              query || Object.keys(filters).length > 0 ? (
                <EmptyState
                  icon={DollarSign}
                  title="Nenhuma transação encontrada"
                  description="Tente ajustar os filtros ou buscar por outros termos."
                  action={{
                    label: 'Limpar Filtros',
                    onClick: () => {
                      setQuery('');
                      setFilters({});
                    },
                  }}
                />
              ) : (
                <EmptyState
                  icon={DollarSign}
                  title="Nenhuma transação cadastrada"
                  description="Comece criando sua primeira transação."
                  action={{
                    label: 'Criar Transação',
                    onClick: handleCreate,
                  }}
                />
              )
            ) : (
              <>
                <div className="space-y-3">
                  {paginatedData.map((transaction) => (
                    <div
                      key={transaction.id}
                      className="flex items-center justify-between border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                    >
                      <div className="flex items-center gap-4 flex-1">
                        <div
                          className={`flex h-12 w-12 items-center justify-center rounded-lg ${
                            transaction.type === 'income' ? 'bg-green-100' : 'bg-red-100'
                          }`}
                        >
                          {transaction.type === 'income' ? (
                            <ArrowUpCircle className="h-6 w-6 text-green-600" />
                          ) : (
                            <ArrowDownCircle className="h-6 w-6 text-red-600" />
                          )}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <p className="font-medium">{transaction.description}</p>
                            <Badge variant="outline">{transaction.category}</Badge>
                          </div>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <span>
                              {new Date(transaction.transaction_date).toLocaleDateString('pt-AO')}
                            </span>
                            <span>•</span>
                            <span>{transaction.payment_method}</span>
                            {transaction.reference && (
                              <>
                                <span>•</span>
                                <span>Ref: {transaction.reference}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <div className="text-right">
                          <p
                            className={`text-lg font-bold ${
                              transaction.type === 'income' ? 'text-green-600' : 'text-red-600'
                            }`}
                          >
                            {transaction.type === 'income' ? '+' : '-'}
                            {formatCurrency(transaction.amount)}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 ml-4">
                        <Badge
                          variant={
                            transaction.status === 'completed'
                              ? 'default'
                              : transaction.status === 'pending'
                              ? 'secondary'
                              : 'destructive'
                          }
                        >
                          {transaction.status === 'completed'
                            ? 'Concluída'
                            : transaction.status === 'pending'
                            ? 'Pendente'
                            : 'Cancelada'}
                        </Badge>
                        <Button size="sm" variant="ghost" onClick={() => handleEdit(transaction)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(transaction)}>
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>

                <Pagination
                  currentPage={currentPage}
                  totalPages={totalPages}
                  pageSize={pageSize}
                  totalItems={filteredData.length}
                  onPageChange={handlePageChange}
                  onPageSizeChange={handlePageSizeChange}
                />
              </>
            )}
          </CardContent>
        </Card>

        {/* Create/Edit Dialog */}
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
            <DialogHeader>
              <DialogTitle>
                {editingTransaction ? 'Editar Transação' : 'Nova Transação'}
              </DialogTitle>
              <DialogDescription>
                {editingTransaction
                  ? 'Atualize as informações da transação'
                  : 'Preencha os dados da nova transação'}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="type">Tipo *</Label>
                  <Select
                    value={formData.type}
                    onValueChange={(value: 'income' | 'expense') =>
                      setFormData({ ...formData, type: value })
                    }
                  >
                    <SelectTrigger id="type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="income">Receita</SelectItem>
                      <SelectItem value="expense">Despesa</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="category">Categoria *</Label>
                  <Input
                    id="category"
                    value={formData.category}
                    onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Descrição *</Label>
                <Input
                  id="description"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="amount">Valor *</Label>
                  <Input
                    id="amount"
                    type="number"
                    step="0.01"
                    value={formData.amount}
                    onChange={(e) => setFormData({ ...formData, amount: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="transaction_date">Data *</Label>
                  <Input
                    id="transaction_date"
                    type="date"
                    value={formData.transaction_date}
                    onChange={(e) =>
                      setFormData({ ...formData, transaction_date: e.target.value })
                    }
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="payment_method">Método de Pagamento *</Label>
                  <Select
                    value={formData.payment_method}
                    onValueChange={(value) =>
                      setFormData({ ...formData, payment_method: value })
                    }
                  >
                    <SelectTrigger id="payment_method">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cash">Dinheiro</SelectItem>
                      <SelectItem value="transfer">Transferência</SelectItem>
                      <SelectItem value="card">Cartão</SelectItem>
                      <SelectItem value="check">Cheque</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="status">Status *</Label>
                  <Select
                    value={formData.status}
                    onValueChange={(value: 'pending' | 'completed' | 'cancelled') =>
                      setFormData({ ...formData, status: value })
                    }
                  >
                    <SelectTrigger id="status">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="completed">Concluída</SelectItem>
                      <SelectItem value="pending">Pendente</SelectItem>
                      <SelectItem value="cancelled">Cancelada</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="reference">Referência</Label>
                <Input
                  id="reference"
                  value={formData.reference}
                  onChange={(e) => setFormData({ ...formData, reference: e.target.value })}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="notes">Observações</Label>
                <Textarea
                  id="notes"
                  value={formData.notes}
                  onChange={(e) => setFormData({ ...formData, notes: e.target.value })}
                  rows={3}
                />
              </div>

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsDialogOpen(false)}
                  disabled={submitting}
                >
                  Cancelar
                </Button>
                <Button type="submit" disabled={submitting}>
                  {submitting ? (
                    <>
                      <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2" />
                      Salvando...
                    </>
                  ) : editingTransaction ? (
                    'Atualizar'
                  ) : (
                    'Criar'
                  )}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        <ConfirmDialog />
      </div>
    </Layout>
  );
}
