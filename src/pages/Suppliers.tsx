// Suppliers - Fornecedores
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Truck, Star, DollarSign, Package } from 'lucide-react';

interface Supplier {
  id: string;
  name: string;
  category: string;
  rating: number;
  total_purchases: number;
  status: 'active' | 'inactive';
}

export default function Suppliers() {
  const [suppliers] = useState<Supplier[]>(
    Array.from({ length: 10 }, (_, i) => ({
      id: `SUP-${i + 1}`,
      name: `Fornecedor ${String.fromCharCode(65 + i)}`,
      category: ['Eletrônicos', 'Acessórios', 'Matéria-Prima', 'Serviços'][Math.floor(Math.random() * 4)],
      rating: Math.floor(Math.random() * 2) + 3.5,
      total_purchases: Math.floor(Math.random() * 2000000) + 500000,
      status: Math.random() > 0.2 ? 'active' : 'inactive'
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Fornecedores</h1>
          <p className="text-muted-foreground">Gestão de fornecedores</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <Truck className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{suppliers.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Ativos</CardTitle>
              <Truck className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {suppliers.filter(s => s.status === 'active').length}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Compras Totais</CardTitle>
              <DollarSign className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatCurrency(suppliers.reduce((sum, s) => sum + s.total_purchases, 0))}
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
                {(suppliers.reduce((sum, s) => sum + s.rating, 0) / suppliers.length).toFixed(1)}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Lista de Fornecedores</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {suppliers.map((supplier) => (
                <div key={supplier.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{supplier.name}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Badge variant="outline">{supplier.category}</Badge>
                      <span>•</span>
                      <span className="flex items-center gap-1">
                        <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                        {supplier.rating.toFixed(1)}
                      </span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(supplier.total_purchases)}</p>
                    <Badge variant={supplier.status === 'active' ? 'default' : 'secondary'}>
                      {supplier.status === 'active' ? 'Ativo' : 'Inativo'}
                    </Badge>
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
