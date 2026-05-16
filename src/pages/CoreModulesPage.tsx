import { useState, useEffect } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';
import { coreModulesService, Customer, Product, Invoice, Employee, DashboardStats } from '@/services/coreModulesService';
import { Users, Package, FileText, UserCheck, TrendingUp, DollarSign, Loader2, Plus, Edit, Trash2, Search } from 'lucide-react';
import { PageLoader } from '@/components/LoadingStates';
import { motion } from 'framer-motion';

export default function CoreModulesPage() {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [searchTerm, setSearchTerm] = useState('');
  
  // Dialog states
  const [customerDialogOpen, setCustomerDialogOpen] = useState(false);
  const [productDialogOpen, setProductDialogOpen] = useState(false);
  const [invoiceDialogOpen, setInvoiceDialogOpen] = useState(false);
  const [employeeDialogOpen, setEmployeeDialogOpen] = useState(false);
  
  // Form states
  const [customerForm, setCustomerForm] = useState<Partial<Customer>>({});
  const [productForm, setProductForm] = useState<Partial<Product>>({});
  const [invoiceForm, setInvoiceForm] = useState<Partial<Invoice>>({});
  const [employeeForm, setEmployeeForm] = useState<Partial<Employee>>({});
  
  const [submitting, setSubmitting] = useState(false);
  const { toast } = useToast();

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      setLoading(true);
      const [statsData, customersData, productsData, invoicesData, employeesData] = await Promise.all([
        coreModulesService.getDashboardStats(),
        coreModulesService.getCustomers(),
        coreModulesService.getProducts(),
        coreModulesService.getInvoices(),
        coreModulesService.getEmployees(),
      ]);
      
      setStats(statsData);
      setCustomers(customersData);
      setProducts(productsData);
      setInvoices(invoicesData);
      setEmployees(employeesData);
    } catch (error: any) {
      toast({
        title: 'Erro ao carregar dados',
        description: error.message,
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  };

  // CUSTOMER HANDLERS
  const handleCreateCustomer = async () => {
    try {
      setSubmitting(true);
      await coreModulesService.createCustomer(customerForm);
      toast({ title: 'Cliente criado com sucesso!' });
      setCustomerDialogOpen(false);
      setCustomerForm({});
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao criar cliente', description: error.message, variant: 'destructive' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteCustomer = async (id: string) => {
    if (!confirm('Tem certeza que deseja excluir este cliente?')) return;
    try {
      await coreModulesService.deleteCustomer(id);
      toast({ title: 'Cliente excluído com sucesso!' });
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao excluir cliente', description: error.message, variant: 'destructive' });
    }
  };

  // PRODUCT HANDLERS
  const handleCreateProduct = async () => {
    try {
      setSubmitting(true);
      await coreModulesService.createProduct(productForm);
      toast({ title: 'Produto criado com sucesso!' });
      setProductDialogOpen(false);
      setProductForm({});
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao criar produto', description: error.message, variant: 'destructive' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteProduct = async (id: string) => {
    if (!confirm('Tem certeza que deseja excluir este produto?')) return;
    try {
      await coreModulesService.deleteProduct(id);
      toast({ title: 'Produto excluído com sucesso!' });
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao excluir produto', description: error.message, variant: 'destructive' });
    }
  };

  // INVOICE HANDLERS
  const handleCreateInvoice = async () => {
    try {
      setSubmitting(true);
      await coreModulesService.createInvoice(invoiceForm);
      toast({ title: 'Fatura criada com sucesso!' });
      setInvoiceDialogOpen(false);
      setInvoiceForm({});
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao criar fatura', description: error.message, variant: 'destructive' });
    } finally {
      setSubmitting(false);
    }
  };

  // EMPLOYEE HANDLERS
  const handleCreateEmployee = async () => {
    try {
      setSubmitting(true);
      await coreModulesService.createEmployee(employeeForm);
      toast({ title: 'Funcionário criado com sucesso!' });
      setEmployeeDialogOpen(false);
      setEmployeeForm({});
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao criar funcionário', description: error.message, variant: 'destructive' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteEmployee = async (id: string) => {
    if (!confirm('Tem certeza que deseja excluir este funcionário?')) return;
    try {
      await coreModulesService.deleteEmployee(id);
      toast({ title: 'Funcionário excluído com sucesso!' });
      loadData();
    } catch (error: any) {
      toast({ title: 'Erro ao excluir funcionário', description: error.message, variant: 'destructive' });
    }
  };

  // FILTER FUNCTIONS
  const filteredCustomers = customers.filter(c => 
    c.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    c.email?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredProducts = products.filter(p => 
    p.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    p.sku?.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredInvoices = invoices.filter(i => 
    i.invoice_number.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const filteredEmployees = employees.filter(e => 
    `${e.first_name} ${e.last_name}`.toLowerCase().includes(searchTerm.toLowerCase()) ||
    e.employee_number.toLowerCase().includes(searchTerm.toLowerCase())
  );

  if (loading) return <PageLoader />;

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Módulos Principais</h1>
          <p className="text-muted-foreground">Gestão completa de todos os módulos do sistema</p>
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="dashboard">Dashboard</TabsTrigger>
            <TabsTrigger value="customers">Clientes</TabsTrigger>
            <TabsTrigger value="products">Produtos</TabsTrigger>
            <TabsTrigger value="invoices">Faturas</TabsTrigger>
            <TabsTrigger value="employees">Funcionários</TabsTrigger>
          </TabsList>

          {/* DASHBOARD TAB */}
          <TabsContent value="dashboard" className="space-y-6">
            {stats && (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }}>
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                      <CardTitle className="text-sm font-medium">Total Clientes</CardTitle>
                      <Users className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">{stats.total_customers}</div>
                      <p className="text-xs text-muted-foreground">Clientes cadastrados</p>
                    </CardContent>
                  </Card>
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                      <CardTitle className="text-sm font-medium">Total Produtos</CardTitle>
                      <Package className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">{stats.total_products}</div>
                      <p className="text-xs text-muted-foreground">Produtos no catálogo</p>
                    </CardContent>
                  </Card>
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }}>
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                      <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
                      <DollarSign className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">
                        {new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(stats.total_revenue)}
                      </div>
                      <p className="text-xs text-muted-foreground">{stats.total_invoices} faturas</p>
                    </CardContent>
                  </Card>
                </motion.div>

                <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }}>
                  <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                      <CardTitle className="text-sm font-medium">Funcionários</CardTitle>
                      <UserCheck className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                      <div className="text-2xl font-bold">{stats.total_employees}</div>
                      <p className="text-xs text-muted-foreground">Funcionários ativos</p>
                    </CardContent>
                  </Card>
                </motion.div>
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Ações Rápidas</CardTitle>
                  <CardDescription>Acesso rápido às principais funcionalidades</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  <Button className="w-full justify-start" onClick={() => { setActiveTab('customers'); setCustomerDialogOpen(true); }}>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Cliente
                  </Button>
                  <Button className="w-full justify-start" onClick={() => { setActiveTab('products'); setProductDialogOpen(true); }}>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Produto
                  </Button>
                  <Button className="w-full justify-start" onClick={() => { setActiveTab('invoices'); setInvoiceDialogOpen(true); }}>
                    <Plus className="mr-2 h-4 w-4" />
                    Nova Fatura
                  </Button>
                  <Button className="w-full justify-start" onClick={() => { setActiveTab('employees'); setEmployeeDialogOpen(true); }}>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Funcionário
                  </Button>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Resumo Recente</CardTitle>
                  <CardDescription>Últimas atividades do sistema</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm">Faturas Pendentes</span>
                      <Badge variant="outline">{stats?.pending_invoices || 0}</Badge>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-sm">Despesas Mensais</span>
                      <span className="text-sm font-medium">
                        {new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(stats?.monthly_expenses || 0)}
                      </span>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>

          {/* CUSTOMERS TAB */}
          <TabsContent value="customers" className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Buscar clientes..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-10"
                />
              </div>
              <Dialog open={customerDialogOpen} onOpenChange={setCustomerDialogOpen}>
                <DialogTrigger asChild>
                  <Button>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Cliente
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl">
                  <DialogHeader>
                    <DialogTitle>Novo Cliente</DialogTitle>
                    <DialogDescription>Preencha os dados do novo cliente</DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="name">Nome *</Label>
                        <Input
                          id="name"
                          value={customerForm.name || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, name: e.target.value })}
                          placeholder="Nome do cliente"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="email">Email</Label>
                        <Input
                          id="email"
                          type="email"
                          value={customerForm.email || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, email: e.target.value })}
                          placeholder="email@exemplo.com"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="phone">Telefone</Label>
                        <Input
                          id="phone"
                          value={customerForm.phone || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, phone: e.target.value })}
                          placeholder="+244 900 000 000"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="tax_id">NIF</Label>
                        <Input
                          id="tax_id"
                          value={customerForm.tax_id || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, tax_id: e.target.value })}
                          placeholder="000000000"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="address">Endereço</Label>
                      <Input
                        id="address"
                        value={customerForm.address || ''}
                        onChange={(e) => setCustomerForm({ ...customerForm, address: e.target.value })}
                        placeholder="Rua, número, bairro"
                      />
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="city">Cidade</Label>
                        <Input
                          id="city"
                          value={customerForm.city || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, city: e.target.value })}
                          placeholder="Luanda"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="country">País</Label>
                        <Input
                          id="country"
                          value={customerForm.country || ''}
                          onChange={(e) => setCustomerForm({ ...customerForm, country: e.target.value })}
                          placeholder="Angola"
                        />
                      </div>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setCustomerDialogOpen(false)}>
                      Cancelar
                    </Button>
                    <Button onClick={handleCreateCustomer} disabled={submitting || !customerForm.name}>
                      {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Criar Cliente
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {filteredCustomers.map((customer) => (
                <Card key={customer.id}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base">{customer.name}</CardTitle>
                        <CardDescription className="text-xs">{customer.email}</CardDescription>
                      </div>
                      <Badge variant={customer.is_active ? 'default' : 'secondary'}>
                        {customer.is_active ? 'Ativo' : 'Inativo'}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      {customer.phone && <p>📞 {customer.phone}</p>}
                      {customer.tax_id && <p>🆔 NIF: {customer.tax_id}</p>}
                      {customer.city && <p>📍 {customer.city}, {customer.country}</p>}
                    </div>
                    <div className="flex gap-2 mt-4">
                      <Button size="sm" variant="outline" className="flex-1">
                        <Edit className="h-4 w-4 mr-1" />
                        Editar
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDeleteCustomer(customer.id)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          {/* PRODUCTS TAB */}
          <TabsContent value="products" className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Buscar produtos..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-10"
                />
              </div>
              <Dialog open={productDialogOpen} onOpenChange={setProductDialogOpen}>
                <DialogTrigger asChild>
                  <Button>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Produto
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl">
                  <DialogHeader>
                    <DialogTitle>Novo Produto</DialogTitle>
                    <DialogDescription>Preencha os dados do novo produto</DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="product_name">Nome *</Label>
                        <Input
                          id="product_name"
                          value={productForm.name || ''}
                          onChange={(e) => setProductForm({ ...productForm, name: e.target.value })}
                          placeholder="Nome do produto"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="sku">SKU</Label>
                        <Input
                          id="sku"
                          value={productForm.sku || ''}
                          onChange={(e) => setProductForm({ ...productForm, sku: e.target.value })}
                          placeholder="SKU-001"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="description">Descrição</Label>
                      <Textarea
                        id="description"
                        value={productForm.description || ''}
                        onChange={(e) => setProductForm({ ...productForm, description: e.target.value })}
                        placeholder="Descrição do produto"
                      />
                    </div>
                    <div className="grid grid-cols-3 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="price">Preço *</Label>
                        <Input
                          id="price"
                          type="number"
                          step="0.01"
                          value={productForm.price || ''}
                          onChange={(e) => setProductForm({ ...productForm, price: parseFloat(e.target.value) })}
                          placeholder="0.00"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="cost">Custo</Label>
                        <Input
                          id="cost"
                          type="number"
                          step="0.01"
                          value={productForm.cost || ''}
                          onChange={(e) => setProductForm({ ...productForm, cost: parseFloat(e.target.value) })}
                          placeholder="0.00"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="tax_rate">Taxa (%)</Label>
                        <Input
                          id="tax_rate"
                          type="number"
                          step="0.01"
                          value={productForm.tax_rate || ''}
                          onChange={(e) => setProductForm({ ...productForm, tax_rate: parseFloat(e.target.value) })}
                          placeholder="14.00"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="unit">Unidade *</Label>
                        <Select value={productForm.unit || ''} onValueChange={(value) => setProductForm({ ...productForm, unit: value })}>
                          <SelectTrigger>
                            <SelectValue placeholder="Selecione" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="un">Unidade</SelectItem>
                            <SelectItem value="kg">Quilograma</SelectItem>
                            <SelectItem value="l">Litro</SelectItem>
                            <SelectItem value="m">Metro</SelectItem>
                            <SelectItem value="h">Hora</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="stock">Estoque</Label>
                        <Input
                          id="stock"
                          type="number"
                          value={productForm.stock_quantity || ''}
                          onChange={(e) => setProductForm({ ...productForm, stock_quantity: parseInt(e.target.value) })}
                          placeholder="0"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="category">Categoria</Label>
                        <Input
                          id="category"
                          value={productForm.category || ''}
                          onChange={(e) => setProductForm({ ...productForm, category: e.target.value })}
                          placeholder="Categoria"
                        />
                      </div>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setProductDialogOpen(false)}>
                      Cancelar
                    </Button>
                    <Button onClick={handleCreateProduct} disabled={submitting || !productForm.name || !productForm.price || !productForm.unit}>
                      {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Criar Produto
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {filteredProducts.map((product) => (
                <Card key={product.id}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base">{product.name}</CardTitle>
                        <CardDescription className="text-xs">{product.sku}</CardDescription>
                      </div>
                      <Badge variant={product.is_active ? 'default' : 'secondary'}>
                        {product.is_active ? 'Ativo' : 'Inativo'}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      <p className="font-bold text-lg">
                        {new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(product.price)}
                      </p>
                      {product.description && <p className="text-muted-foreground">{product.description}</p>}
                      <div className="flex items-center justify-between pt-2">
                        <span>Estoque: {product.stock_quantity} {product.unit}</span>
                        {product.category && <Badge variant="outline">{product.category}</Badge>}
                      </div>
                    </div>
                    <div className="flex gap-2 mt-4">
                      <Button size="sm" variant="outline" className="flex-1">
                        <Edit className="h-4 w-4 mr-1" />
                        Editar
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDeleteProduct(product.id)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          {/* INVOICES TAB */}
          <TabsContent value="invoices" className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Buscar faturas..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-10"
                />
              </div>
              <Dialog open={invoiceDialogOpen} onOpenChange={setInvoiceDialogOpen}>
                <DialogTrigger asChild>
                  <Button>
                    <Plus className="mr-2 h-4 w-4" />
                    Nova Fatura
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl">
                  <DialogHeader>
                    <DialogTitle>Nova Fatura</DialogTitle>
                    <DialogDescription>Preencha os dados da nova fatura</DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
                    <div className="space-y-2">
                      <Label htmlFor="customer">Cliente *</Label>
                      <Select value={invoiceForm.customer_id || ''} onValueChange={(value) => setInvoiceForm({ ...invoiceForm, customer_id: value })}>
                        <SelectTrigger>
                          <SelectValue placeholder="Selecione um cliente" />
                        </SelectTrigger>
                        <SelectContent>
                          {customers.map((customer) => (
                            <SelectItem key={customer.id} value={customer.id}>
                              {customer.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="issue_date">Data de Emissão *</Label>
                        <Input
                          id="issue_date"
                          type="date"
                          value={invoiceForm.issue_date || ''}
                          onChange={(e) => setInvoiceForm({ ...invoiceForm, issue_date: e.target.value })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="due_date">Data de Vencimento *</Label>
                        <Input
                          id="due_date"
                          type="date"
                          value={invoiceForm.due_date || ''}
                          onChange={(e) => setInvoiceForm({ ...invoiceForm, due_date: e.target.value })}
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="subtotal">Subtotal *</Label>
                        <Input
                          id="subtotal"
                          type="number"
                          step="0.01"
                          value={invoiceForm.subtotal || ''}
                          onChange={(e) => setInvoiceForm({ ...invoiceForm, subtotal: parseFloat(e.target.value) })}
                          placeholder="0.00"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="tax_amount">Imposto</Label>
                        <Input
                          id="tax_amount"
                          type="number"
                          step="0.01"
                          value={invoiceForm.tax_amount || ''}
                          onChange={(e) => setInvoiceForm({ ...invoiceForm, tax_amount: parseFloat(e.target.value) })}
                          placeholder="0.00"
                        />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="notes">Notas</Label>
                      <Textarea
                        id="notes"
                        value={invoiceForm.notes || ''}
                        onChange={(e) => setInvoiceForm({ ...invoiceForm, notes: e.target.value })}
                        placeholder="Observações da fatura"
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setInvoiceDialogOpen(false)}>
                      Cancelar
                    </Button>
                    <Button onClick={handleCreateInvoice} disabled={submitting || !invoiceForm.customer_id || !invoiceForm.issue_date || !invoiceForm.subtotal}>
                      {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Criar Fatura
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>

            <div className="space-y-4">
              {filteredInvoices.map((invoice) => (
                <Card key={invoice.id}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base">{invoice.invoice_number}</CardTitle>
                        <CardDescription className="text-xs">
                          {invoice.customers_2026_04_08?.name || 'Cliente não encontrado'}
                        </CardDescription>
                      </div>
                      <Badge variant={invoice.status === 'paid' ? 'default' : invoice.status === 'pending' ? 'secondary' : 'destructive'}>
                        {invoice.status === 'paid' ? 'Pago' : invoice.status === 'pending' ? 'Pendente' : 'Vencido'}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                      <div>
                        <p className="text-muted-foreground">Emissão</p>
                        <p className="font-medium">{new Date(invoice.issue_date).toLocaleDateString('pt-AO')}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Vencimento</p>
                        <p className="font-medium">{new Date(invoice.due_date).toLocaleDateString('pt-AO')}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Subtotal</p>
                        <p className="font-medium">{new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(invoice.subtotal)}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Total</p>
                        <p className="font-bold text-lg">{new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(invoice.total_amount)}</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>

          {/* EMPLOYEES TAB */}
          <TabsContent value="employees" className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Buscar funcionários..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-10"
                />
              </div>
              <Dialog open={employeeDialogOpen} onOpenChange={setEmployeeDialogOpen}>
                <DialogTrigger asChild>
                  <Button>
                    <Plus className="mr-2 h-4 w-4" />
                    Novo Funcionário
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl">
                  <DialogHeader>
                    <DialogTitle>Novo Funcionário</DialogTitle>
                    <DialogDescription>Preencha os dados do novo funcionário</DialogDescription>
                  </DialogHeader>
                  <div className="grid gap-4 py-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="first_name">Nome *</Label>
                        <Input
                          id="first_name"
                          value={employeeForm.first_name || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, first_name: e.target.value })}
                          placeholder="Nome"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="last_name">Sobrenome *</Label>
                        <Input
                          id="last_name"
                          value={employeeForm.last_name || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, last_name: e.target.value })}
                          placeholder="Sobrenome"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="emp_email">Email</Label>
                        <Input
                          id="emp_email"
                          type="email"
                          value={employeeForm.email || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, email: e.target.value })}
                          placeholder="email@exemplo.com"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="emp_phone">Telefone</Label>
                        <Input
                          id="emp_phone"
                          value={employeeForm.phone || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, phone: e.target.value })}
                          placeholder="+244 900 000 000"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="position">Cargo</Label>
                        <Input
                          id="position"
                          value={employeeForm.position || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, position: e.target.value })}
                          placeholder="Cargo"
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="department">Departamento</Label>
                        <Input
                          id="department"
                          value={employeeForm.department || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, department: e.target.value })}
                          placeholder="Departamento"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <Label htmlFor="hire_date">Data de Contratação *</Label>
                        <Input
                          id="hire_date"
                          type="date"
                          value={employeeForm.hire_date || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, hire_date: e.target.value })}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="salary">Salário *</Label>
                        <Input
                          id="salary"
                          type="number"
                          step="0.01"
                          value={employeeForm.salary || ''}
                          onChange={(e) => setEmployeeForm({ ...employeeForm, salary: parseFloat(e.target.value) })}
                          placeholder="0.00"
                        />
                      </div>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setEmployeeDialogOpen(false)}>
                      Cancelar
                    </Button>
                    <Button onClick={handleCreateEmployee} disabled={submitting || !employeeForm.first_name || !employeeForm.last_name || !employeeForm.hire_date || !employeeForm.salary}>
                      {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Criar Funcionário
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {filteredEmployees.map((employee) => (
                <Card key={employee.id}>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base">{employee.first_name} {employee.last_name}</CardTitle>
                        <CardDescription className="text-xs">{employee.employee_number}</CardDescription>
                      </div>
                      <Badge variant={employee.is_active ? 'default' : 'secondary'}>
                        {employee.is_active ? 'Ativo' : 'Inativo'}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2 text-sm">
                      {employee.position && <p>💼 {employee.position}</p>}
                      {employee.department && <p>🏢 {employee.department}</p>}
                      {employee.email && <p>📧 {employee.email}</p>}
                      <p className="font-bold text-lg pt-2">
                        {new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA' }).format(employee.salary)}
                      </p>
                    </div>
                    <div className="flex gap-2 mt-4">
                      <Button size="sm" variant="outline" className="flex-1">
                        <Edit className="h-4 w-4 mr-1" />
                        Editar
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDeleteEmployee(employee.id)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </Layout>
  );
}
