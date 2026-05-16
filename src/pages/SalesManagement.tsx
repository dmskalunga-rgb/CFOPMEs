// SalesManagement - Gestão de Vendas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { TrendingUp, DollarSign, ShoppingCart, Users } from 'lucide-react';

interface Sale {
  id: string;
  customer: string;
  product: string;
  amount: number;
  status: 'completed' | 'pending' | 'cancelled';
  date: string;
}

export default function SalesManagement() {
  const [sales] = useState<Sale[]>(
    Array.from({ length: 15 }, (_, i) => ({
      id: `sale-${i + 1}`,
      customer: `Cliente ${i + 1}`,
      product: `Produto ${String.fromCharCode(65 + (i % 5))}`,
      amount: Math.floor(Math.random() * 200000) + 50000,
      status: Math.random() > 0.2 ? 'completed' : Math.random() > 0.5 ? 'pending' : 'cancelled',
      date: new Date(Date.now() - Math.random() * 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
    }))
  );

  const totalSales = sales.filter(s => s.status === 'completed').reduce((sum, s) => sum + s.amount, 0);
  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão de Vendas</h1>
          <p className="text-muted-foreground">Controle de vendas e performance</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Vendas</CardTitle>
              <ShoppingCart className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{sales.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Receita Total</CardTitle>
              <DollarSign className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalSales)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Ticket Médio</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalSales / sales.filter(s => s.status === 'completed').length)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Clientes</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{new Set(sales.map(s => s.customer)).size}</div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Vendas Recentes</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {sales.slice(0, 10).map((sale) => (
                <div key={sale.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{sale.customer}</p>
                    <p className="text-sm text-muted-foreground">{sale.product} • {new Date(sale.date).toLocaleDateString('pt-AO')}</p>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(sale.amount)}</p>
                    <Badge variant={sale.status === 'completed' ? 'default' : sale.status === 'pending' ? 'secondary' : 'destructive'}>
                      {sale.status === 'completed' ? 'Concluída' : sale.status === 'pending' ? 'Pendente' : 'Cancelada'}
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
