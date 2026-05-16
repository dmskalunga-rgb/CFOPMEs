// Categories - Categorias
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { FolderOpen, Package, DollarSign, TrendingUp } from 'lucide-react';

interface Category {
  id: string;
  name: string;
  description: string;
  products_count: number;
  total_sales: number;
  revenue: number;
  status: 'active' | 'inactive';
}

export default function Categories() {
  const [categories] = useState<Category[]>([
    { id: '1', name: 'Eletrônicos', description: 'Produtos eletrônicos e tecnologia', products_count: 45, total_sales: 1250, revenue: 8500000, status: 'active' },
    { id: '2', name: 'Acessórios', description: 'Acessórios diversos', products_count: 32, total_sales: 890, revenue: 3200000, status: 'active' },
    { id: '3', name: 'Premium', description: 'Produtos premium e exclusivos', products_count: 18, total_sales: 420, revenue: 5600000, status: 'active' },
    { id: '4', name: 'Básico', description: 'Produtos básicos e essenciais', products_count: 28, total_sales: 1100, revenue: 2800000, status: 'active' },
    { id: '5', name: 'Serviços', description: 'Serviços e consultoria', products_count: 12, total_sales: 180, revenue: 4200000, status: 'active' },
    { id: '6', name: 'Descontinuado', description: 'Produtos descontinuados', products_count: 8, total_sales: 45, revenue: 320000, status: 'inactive' }
  ]);

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalProducts = categories.reduce((sum, c) => sum + c.products_count, 0);
  const totalRevenue = categories.reduce((sum, c) => sum + c.revenue, 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Categorias</h1>
          <p className="text-muted-foreground">Gestão de categorias de produtos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Categorias</CardTitle>
              <FolderOpen className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{categories.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Produtos</CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{totalProducts}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
              <DollarSign className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalRevenue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Categorias Ativas</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {categories.filter(c => c.status === 'active').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Lista de Categorias</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {categories.map((category) => (
                <div key={category.id} className="border rounded-lg p-4">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="font-semibold text-lg">{category.name}</h3>
                        <Badge variant={category.status === 'active' ? 'default' : 'secondary'}>
                          {category.status === 'active' ? 'Ativa' : 'Inativa'}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground">{category.description}</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-4 mt-3 pt-3 border-t">
                    <div>
                      <p className="text-xs text-muted-foreground">Produtos</p>
                      <p className="text-lg font-bold">{category.products_count}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Vendas</p>
                      <p className="text-lg font-bold">{category.total_sales}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Receita</p>
                      <p className="text-lg font-bold text-green-600">{formatCurrency(category.revenue)}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}
