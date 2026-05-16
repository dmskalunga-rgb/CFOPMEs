// Products Page with Complete UX - Real Supabase Integration
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
import { Plus, Edit, Trash2, Package, AlertTriangle, TrendingUp, TrendingDown } from 'lucide-react';
import { toast } from 'sonner';
import { productsService, type Product } from '@/services/supabaseServices';
import { useConfirmDialog } from '@/components/ui/confirm-dialog';
import { Pagination, usePagination } from '@/components/ui/pagination';
import { AdvancedFilters, useFilters, type FilterConfig } from '@/components/ui/advanced-filters';
import { SearchInput, useSearch } from '@/components/ui/search-input';
import { EmptyState, ErrorState, LoadingState } from '@/components/ui/states';
import { PageSkeleton, TableSkeleton } from '@/components/ui/skeletons';

export default function ProductsPageReal() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [editingProduct, setEditingProduct] = useState<Product | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    sku: '',
    description: '',
    category: '',
    price: '',
    cost: '',
    stock: '',
    min_stock: '',
    unit: 'un',
    product_status: 'active' as 'active' | 'inactive' | 'discontinued',
  });
  const [submitting, setSubmitting] = useState(false);

  const { isOpen, setIsOpen, confirm, ConfirmDialog } = useConfirmDialog();

  // Load products
  const loadProducts = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await productsService.getAll();
      setProducts(data);
    } catch (err) {
      setError(err as Error);
      toast.error('Erro ao carregar produtos');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProducts();
  }, []);

  // Search
  const { query, setQuery, searchedData } = useSearch(
    products,
    (product, q) =>
      product.name.toLowerCase().includes(q.toLowerCase()) ||
      product.sku.toLowerCase().includes(q.toLowerCase()) ||
      product.category.toLowerCase().includes(q.toLowerCase())
  );

  // Filters
  const filterConfigs: FilterConfig[] = [
    {
      key: 'category',
      label: 'Categoria',
      type: 'select',
      options: [
        { label: 'Eletrônicos', value: 'Eletrônicos' },
        { label: 'Acessórios', value: 'Acessórios' },
        { label: 'Outros', value: 'Outros' },
      ],
      placeholder: 'Todas as categorias',
    },
    {
      key: 'status',
      label: 'Status',
      type: 'select',
      options: [
        { label: 'Ativo', value: 'active' },
        { label: 'Inativo', value: 'inactive' },
        { label: 'Descontinuado', value: 'discontinued' },
      ],
      placeholder: 'Todos os status',
    },
    {
      key: 'min_price',
      label: 'Preço Mínimo',
      type: 'number',
      placeholder: '0',
    },
    {
      key: 'max_price',
      label: 'Preço Máximo',
      type: 'number',
      placeholder: '1000000',
    },
  ];

  const { filters, setFilters, filteredData } = useFilters(searchedData, (product, f) => {
    if (f.category && product.category !== f.category) return false;
    if (f.status && product.product_status !== f.status) return false;
    if (f.min_price && product.price < Number(f.min_price)) return false;
    if (f.max_price && product.price > Number(f.max_price)) return false;
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

  // Calculate stats
  const totalValue = products.reduce((sum, p) => sum + p.price * p.stock, 0);
  const lowStockCount = products.filter((p) => p.stock <= p.min_stock).length;
  const activeCount = products.filter((p) => p.product_status === 'active').length;

  // Handlers
  const handleCreate = () => {
    setEditingProduct(null);
    setFormData({
      name: '',
      sku: '',
      description: '',
      category: '',
      price: '',
      cost: '',
      stock: '',
      min_stock: '',
      unit: 'un',
      product_status: 'active',
    });
    setIsDialogOpen(true);
  };

  const handleEdit = (product: Product) => {
    setEditingProduct(product);
    setFormData({
      name: product.name,
      sku: product.sku,
      description: product.description || '',
      category: product.category,
      price: product.price.toString(),
      cost: product.cost?.toString() || '',
      stock: product.stock.toString(),
      min_stock: product.min_stock.toString(),
      unit: product.unit,
      product_status: product.product_status,
    });
    setIsDialogOpen(true);
  };

  const handleDelete = (product: Product) => {
    confirm(
      'Deletar Produto',
      `Tem certeza que deseja deletar "${product.name}"? Esta ação não pode ser desfeita.`,
      async () => {
        try {
          await productsService.delete(product.id);
          toast.success('Produto deletado com sucesso!');
          loadProducts();
        } catch (err) {
          toast.error('Erro ao deletar produto');
        }
      },
      'destructive'
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);

    try {
      const productData = {
        name: formData.name,
        sku: formData.sku,
        description: formData.description || undefined,
        category: formData.category,
        price: parseFloat(formData.price),
        cost: formData.cost ? parseFloat(formData.cost) : undefined,
        stock: parseInt(formData.stock),
        min_stock: parseInt(formData.min_stock),
        unit: formData.unit,
        product_status: formData.product_status,
      };

      if (editingProduct) {
        await productsService.update(editingProduct.id, productData);
        toast.success('Produto atualizado com sucesso!');
      } else {
        await productsService.create(productData);
        toast.success('Produto criado com sucesso!');
      }

      setIsDialogOpen(false);
      loadProducts();
    } catch (err) {
      toast.error(editingProduct ? 'Erro ao atualizar produto' : 'Erro ao criar produto');
    } finally {
      setSubmitting(false);
    }
  };

  const formatCurrency = (value: number) =>
    new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  if (loading) return <Layout><PageSkeleton /></Layout>;
  if (error) return <Layout><ErrorState error={error} onRetry={loadProducts} /></Layout>;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-3xl font-bold">Produtos</h1>
            <p className="text-muted-foreground">Gerencie seu inventário de produtos</p>
          </div>
          <Button onClick={handleCreate}>
            <Plus className="h-4 w-4 mr-2" />
            Novo Produto
          </Button>
        </div>

        {/* Stats Cards */}
        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Produtos</CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{products.length}</div>
              <p className="text-xs text-muted-foreground">{activeCount} ativos</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalValue)}</div>
              <p className="text-xs text-muted-foreground">Em estoque</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Estoque Baixo</CardTitle>
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">{lowStockCount}</div>
              <p className="text-xs text-muted-foreground">Produtos abaixo do mínimo</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Categorias</CardTitle>
              <TrendingDown className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {new Set(products.map((p) => p.category)).size}
              </div>
              <p className="text-xs text-muted-foreground">Diferentes categorias</p>
            </CardContent>
          </Card>
        </div>

        {/* Search and Filters */}
        <div className="flex gap-4">
          <SearchInput
            placeholder="Buscar por nome, SKU ou categoria..."
            onSearch={setQuery}
            className="flex-1"
          />
          <AdvancedFilters
            filters={filterConfigs}
            onFiltersChange={setFilters}
            activeFilters={filters}
          />
        </div>

        {/* Products Table */}
        <Card>
          <CardHeader>
            <CardTitle>
              Produtos ({filteredData.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {filteredData.length === 0 ? (
              query || Object.keys(filters).length > 0 ? (
                <EmptyState
                  icon={Package}
                  title="Nenhum produto encontrado"
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
                  icon={Package}
                  title="Nenhum produto cadastrado"
                  description="Comece criando seu primeiro produto."
                  action={{
                    label: 'Criar Produto',
                    onClick: handleCreate,
                  }}
                />
              )
            ) : (
              <>
                <div className="space-y-3">
                  {paginatedData.map((product) => (
                    <div
                      key={product.id}
                      className="flex items-center justify-between border rounded-lg p-4 hover:bg-muted/50 transition-colors"
                    >
                      <div className="flex items-center gap-4 flex-1">
                        <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-primary/10">
                          <Package className="h-6 w-6 text-primary" />
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <p className="font-medium">{product.name}</p>
                            <Badge variant="outline">{product.sku}</Badge>
                            {product.stock <= product.min_stock && (
                              <Badge variant="destructive">Estoque Baixo</Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <span>{product.category}</span>
                            <span>•</span>
                            <span>Estoque: {product.stock} {product.unit}</span>
                            <span>•</span>
                            <span>{formatCurrency(product.price)}</span>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge
                          variant={
                            product.product_status === 'active'
                              ? 'default'
                              : product.product_status === 'inactive'
                              ? 'secondary'
                              : 'destructive'
                          }
                        >
                          {product.product_status === 'active'
                            ? 'Ativo'
                            : product.product_status === 'inactive'
                            ? 'Inativo'
                            : 'Descontinuado'}
                        </Badge>
                        <Button size="sm" variant="ghost" onClick={() => handleEdit(product)}>
                          <Edit className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(product)}
                        >
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
                {editingProduct ? 'Editar Produto' : 'Novo Produto'}
              </DialogTitle>
              <DialogDescription>
                {editingProduct
                  ? 'Atualize as informações do produto'
                  : 'Preencha os dados do novo produto'}
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
                  <Label htmlFor="sku">SKU *</Label>
                  <Input
                    id="sku"
                    value={formData.sku}
                    onChange={(e) => setFormData({ ...formData, sku: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Descrição</Label>
                <Input
                  id="description"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="category">Categoria *</Label>
                  <Input
                    id="category"
                    value={formData.category}
                    onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="unit">Unidade *</Label>
                  <Input
                    id="unit"
                    value={formData.unit}
                    onChange={(e) => setFormData({ ...formData, unit: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="price">Preço *</Label>
                  <Input
                    id="price"
                    type="number"
                    step="0.01"
                    value={formData.price}
                    onChange={(e) => setFormData({ ...formData, price: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="cost">Custo</Label>
                  <Input
                    id="cost"
                    type="number"
                    step="0.01"
                    value={formData.cost}
                    onChange={(e) => setFormData({ ...formData, cost: e.target.value })}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="stock">Estoque *</Label>
                  <Input
                    id="stock"
                    type="number"
                    value={formData.stock}
                    onChange={(e) => setFormData({ ...formData, stock: e.target.value })}
                    required
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="min_stock">Estoque Mínimo *</Label>
                  <Input
                    id="min_stock"
                    type="number"
                    value={formData.min_stock}
                    onChange={(e) => setFormData({ ...formData, min_stock: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="status">Status *</Label>
                <Select
                  value={formData.product_status}
                  onValueChange={(value: 'active' | 'inactive' | 'discontinued') =>
                    setFormData({ ...formData, product_status: value })
                  }
                >
                  <SelectTrigger id="status">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="active">Ativo</SelectItem>
                    <SelectItem value="inactive">Inativo</SelectItem>
                    <SelectItem value="discontinued">Descontinuado</SelectItem>
                  </SelectContent>
                </Select>
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
                  ) : editingProduct ? (
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
