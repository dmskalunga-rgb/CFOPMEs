// Products - Produtos
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Package, DollarSign, TrendingUp, Star } from 'lucide-react';

interface Product {
  id: string;
  name: string;
  sku: string;
  category: string;
  price: number;
  stock: number;
  sales: number;
  rating: number;
}

export default function Products() {
  const [products] = useState<Product[]>(
    Array.from({ length: 12 }, (_, i) => ({
      id: `PRD-${i + 1}`,
      name: `Produto ${String.fromCharCode(65 + i)}`,
      sku: `SKU-${String(i + 1).padStart(3, '0')}`,
      category: ['Eletrônicos', 'Acessórios', 'Premium', 'Básico'][Math.floor(Math.random() * 4)],
      price: Math.floor(Math.random() * 50000) + 5000,
      stock: Math.floor(Math.random() * 200) + 10,
      sales: Math.floor(Math.random() * 500) + 50,
      rating: Math.floor(Math.random() * 2) + 3.5
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalValue = products.reduce((sum, p) => sum + (p.price * p.stock), 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Produtos</h1>
          <p className="text-muted-foreground">Catálogo de produtos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Produtos</CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{products.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor em Estoque</CardTitle>
              <DollarSign className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalValue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Vendas Totais</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {products.reduce((sum, p) => sum + p.sales, 0)}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Avaliação Média</CardTitle>
              <Star className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {(products.reduce((sum, p) => sum + p.rating, 0) / products.length).toFixed(1)}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Catálogo de Produtos</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {products.map((product) => (
                <div key={product.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{product.name}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span>SKU: {product.sku}</span>
                      <span>•</span>
                      <Badge variant="outline">{product.category}</Badge>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                        {product.rating.toFixed(1)}
                      </span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(product.price)}</p>
                    <p className="text-xs text-muted-foreground">
                      Estoque: {product.stock} • Vendas: {product.sales}
                    </p>
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
