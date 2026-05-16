// Customers Page with Complete UX - Real Supabase Integration
import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
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
import { Plus, Edit, Trash2, Users, TrendingUp, MapPin, Building } from 'lucide-react';
import { toast } from 'sonner';
import { customersService, type Customer } from '@/services/customersServiceReal';
import { useConfirmDialog } from '@/components/ui/confirm-dialog';
import { Pagination, usePagination } from '@/components/ui/pagination';
import { AdvancedFilters, useFilters, type FilterConfig } from '@/components/ui/advanced-filters';
import { SearchInput, useSearch } from '@/components/ui/search-input';
import { EmptyState, ErrorState } from '@/components/ui/states';
import { PageSkeleton } from '@/components/ui/skeletons';
import { ExportButton } from '@/lib/export';
import { CustomPieChart } from '@/components/ui/charts';

export default function CustomersPageReal() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingCustomer, setEditingCustomer] = useState<Customer | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    email: '',
    phone: '',
    company: '',
    address: '',
    city: '',
    country: 'Angola',
    tax_id: '',
    customer_status: 'active' as 'active' | 'inactive',
    total_purchases: '0',
  });
  const [submitting, setSubmitting] = useState(false);

  const { isOpen, setIsOpen, confirm, ConfirmDialog } = useConfirmDialog();

  const loadCustomers = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await customersService.getAll();
      setCustomers(data);
    } catch (err) {
      setError(err as Error);
      toast.error('Erro ao carregar clientes');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCustomers();
  }, []);

  // Search
  const { query, setQuery, searchedData } = useSearch(
    customers,
    (customer, q) =>
      customer.name.toLowerCase().includes(q.toLowerCase()) ||
      customer.email.toLowerCase().includes(q.toLowerCase()) ||
      (customer.company && customer.company.toLowerCase().includes(q.toLowerCase()))
  );

  // Filters
  const filterConfigs: FilterConfig[] = [
    {
      key: 'status',
      label: 'Status',
      type: 'select',
      options: [
        { label: 'Ativo', value: 'active' },
        { label: 'Inativo', value: 'inactive' },
      ],
      placeholder: 'Todos os status',
    },
    {
      key: 'city',
      label: 'Cidade',
      type: 'select',
      options: [
        { label: 'Luanda', value: 'Luanda' },
        { label: 'Benguela', value: 'Benguela' },
        { label: 'Huambo', value: 'Huambo' },
        { label: 'Lubango', value: 'Lubango' },
        { label: 'Cabinda', value: 'Cabinda' },
      ],
      placeholder: 'Todas as cidades',
    },
    {
      key: 'min_purchases',
      label: 'Compras Mínimas',
      type: 'number',
      placeholder: '0',
    },
  ];

  const { filters, setFilters, filteredData } = useFilters(searchedData, (customer, f) => {
    if (f.status && customer.customer_status !== f.status) return false;
    if (f.city && customer.city !== f.city) return false;
    if (f.min_purchases && customer.total_purchases < Number(f.min_purchases)) return false;
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

  // Stats
  const totalRevenue = customers.reduce((sum, c) => sum + c.total_purchases, 0);
  const activeCount = customers.filter((c) => c.customer_status === 'active').length;
  const avgPurchase = customers.length > 0 ? totalRevenue / customers.length : 0;

  // Chart data - Customers by city
  const customersByCity = customers.reduce((acc, customer) => {
    const city = customer.city || 'Outros';
    acc[city] = (acc[city] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const cityChartData = Object.entries(customersByCity).map(([name, value]) => ({
    name,
    value: value as number,
  }));

  // Handlers
  const handleCreate = () => {
    setEditingCustomer(null);
    setFormData({
      name: '',
      email: '',
      phone: '',
      company: '',
      address: '',
      city: '',
      country: 'Angola',
      tax_id: '',
      customer_status: 'active',
      total_purchases: '0',
    });
    setIsDialogOpen(true);
  };

  const handleEdit = (customer: Customer) => {
    setEditingCustomer(customer);
    setFormData({
      name: customer.name,
      email: customer.email,
      phone: customer.phone || '',
      company: customer.company || '',
      address: customer.address || '',
      city: customer.city || '',
      country: customer.country,
      tax_id: customer.tax_id || '',
      customer_status: customer.customer_status,
      total_purchases: customer.total_purchases.toString(),
    });
    setIsDialogOpen(true);
  };

  const handleDelete = (customer: Customer) => {
    confirm(
      'Deletar Cliente',
      `Tem certeza que deseja deletar "${customer.name}"? Esta ação não pode ser desfeita.`,
      async () => {
        try {
          await customersService.delete(customer.id);
          toast.success('Cliente deletado com sucesso!');
          loadCustomers();
        } catch (err) {
          toast.error('Erro ao deletar cliente');
        }
      },
      'destructive'
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);

    try {
      const customerData = {
        name: formData.name,
        email: formData.email,
        phone: formData.phone || undefined,
        company: formData.company || undefined,
        address: formData.address || undefined,
        city: formData.city || undefined,
        country: formData.country,
        tax_id: formData.tax_id || undefined,
        customer_status: formData.customer_status,
        total_purchases: parseFloat(formData.total_purchases),
      };

      if (editingCustomer) {
        await customersService.update(editingCustomer.id, customerData);
        toast.success('Cliente atualizado com sucesso!');
      } else {
        await customersService.create(customerData);
        toast.success('Cliente criado com sucesso!');
      }

      setIsDialogOpen(false);
      loadCustomers();
    } catch (err) {
      toast.error(editingCustomer ? 'Erro ao atualizar cliente' : 'Erro ao criar cliente');
    } finally {
      setSubmitting(false);
    }
  };

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  // Export columns
  const exportColumns = [
    { key: 'name' as keyof Customer, label: 'Nome' },
    { key: 'email' as keyof Customer, label: 'Email' },
    { key: 'phone' as keyof Customer, label: 'Telefone' },
    { key: 'company' as keyof Customer, label: 'Empresa' },
    { key: 'city' as keyof Customer, label: 'Cidade' },
    { key: 'total_purchases' as keyof Customer, label: 'Total de Compras' },
    { key: 'customer_status' as keyof Customer, label: 'Status' },
  ];

  if (loading) return <Layout><PageSkeleton /></Layout>;
  if (error) return <Layout><ErrorState error={error} onRetry={loadCustomers} /></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold">Clientes</h1>
            <p className="text-muted-foreground">Gerencie sua base de clientes</p>
          </div>
          <div className="flex gap-2">
            <ExportButton
              data={filteredData}
              filename="clientes"
              title="Relatório de Clientes"
              columns={exportColumns}
            />
            <Button onClick={handleCreate}>
              <Plus className="h-4 w-4 mr-2" />
              Novo Cliente
            </Button>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Clientes</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{customers.length}</div>
              <p className="text-xs text-muted-foreground">{activeCount} ativos</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalRevenue)}</div>
              <p className="text-xs text-muted-foreground">De todos os clientes</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Ticket Médio</CardTitle>
              <TrendingUp className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(avgPurchase)}</div>
              <p className="text-xs text-muted-foreground">Por cliente</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Cidades</CardTitle>
              <MapPin className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{Object.keys(customersByCity).length}</div>
              <p className="text-xs text-muted-foreground">Diferentes localizações</p>
            </CardContent>
          </Card>
        </div>

        {/* Chart */}
        {cityChartData.length > 0 && (
          <CustomPieChart
            data={cityChartData}
            title="Clientes por Cidade"
            description="Distribuição geográfica dos clientes"
            height={300}
          />
        )}

        {/* Search and Filters */}
        <div className="flex gap-4">
          <SearchInput
            placeholder="Buscar por nome, email ou empresa..."
            onSearch={setQuery}
            className="flex-1"
          />
          <AdvancedFilters
            filters={filterConfigs}
            onFiltersChange={setFilters}
            activeFilters={filters}
          />
        </div>

        {/* Customers Table */}
        <Card>
          <CardHeader>
            <CardTitle>Clientes ({filteredData.length})</CardTitle>
          </CardHeader>
          <CardContent>
            {filteredData.length === 0 ? (
              query || Object.keys(filters).length > 0 ? (
                <EmptyState
                  icon={Users}
                  title="Nenhum cliente encontrado"
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
                  icon={Users}
                  title="Nenhum cliente cadastrado"
                  description="Comece criando seu primeiro cliente."
                  action={{
                    label: 'Criar Cliente',
                    onClick: handleCreate,
                  }}
                />
              )
            ) : (
              <>
                <div className="space-y-3">
                  {paginatedData.map((customer) => (
                    <div
                      key={customer.id}
                      className="flex items-center justify-between border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                    >
                      <div className="flex items-center gap-4 flex-1">
                        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
                          {customer.company ? (
                            <Building className="h-6 w-6 text-primary" />
                          ) : (
                            <Users className="h-6 w-6 text-primary" />
                          )}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <p className="font-medium">{customer.name}</p>
                            {customer.company && (
                              <Badge variant="outline">{customer.company}</Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <span>{customer.email}</span>
                            {customer.city && (
                              <>
                                <span>•</span>
                                <span className="flex items-center gap-1">
                                  <MapPin className="h-3 w-3" />
                                  {customer.city}
                                </span>
                              </>
                            )}
                            <span>•</span>
                            <span>{formatCurrency(customer.total_purchases)}</span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant={customer.customer_status === 'active' ? 'default' : 'secondary'}>
                          {customer.customer_status === 'active' ? 'Ativo' : 'Inativo'}
                        </Badge>
                        <Button size="sm" variant="ghost" onClick={() => handleEdit(customer)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => handleDelete(customer)}>
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
              <DialogTitle>{editingCustomer ? 'Editar Cliente' : 'Novo Cliente'}</DialogTitle>
              <DialogDescription>
                {editingCustomer ? 'Atualize as informações do cliente' : 'Preencha os dados do novo cliente'}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="name">Nome *</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email *</Label>
                  <Input
                    id="email"
                    type="email"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="phone">Telefone</Label>
                  <Input
                    id="phone"
                    value={formData.phone}
                    onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="company">Empresa</Label>
                  <Input
                    id="company"
                    value={formData.company}
                    onChange={(e) => setFormData({ ...formData, company: e.target.value })}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="address">Endereço</Label>
                <Input
                  id="address"
                  value={formData.address}
                  onChange={(e) => setFormData({ ...formData, address: e.target.value })}
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="city">Cidade</Label>
                  <Input
                    id="city"
                    value={formData.city}
                    onChange={(e) => setFormData({ ...formData, city: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="country">País *</Label>
                  <Input
                    id="country"
                    value={formData.country}
                    onChange={(e) => setFormData({ ...formData, country: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="tax_id">NIF</Label>
                  <Input
                    id="tax_id"
                    value={formData.tax_id}
                    onChange={(e) => setFormData({ ...formData, tax_id: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="status">Status *</Label>
                  <Select
                    value={formData.customer_status}
                    onValueChange={(value: 'active' | 'inactive') =>
                      setFormData({ ...formData, customer_status: value })
                    }
                  >
                    <SelectTrigger id="status">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="active">Ativo</SelectItem>
                      <SelectItem value="inactive">Inativo</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
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
                  ) : editingCustomer ? (
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
