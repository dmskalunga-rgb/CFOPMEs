// Customers, Products, Categories - Últimas 3 páginas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Users, DollarSign, ShoppingCart, TrendingUp } from 'lucide-react';

interface Customer {
  id: string;
  name: string;
  email: string;
  total_purchases: number;
  orders: number;
  status: 'active' | 'inactive';
}

export default function Customers() {
  const [customers] = useState<Customer[]>(
    Array.from({ length: 15 }, (_, i) => ({
      id: `CUST-${i + 1}`,
      name: `Cliente ${i + 1}`,
      email: `cliente${i + 1}@email.ao`,
      total_purchases: Math.floor(Math.random() * 1000000) + 100000,
      orders: Math.floor(Math.random() * 20) + 1,
      status: Math.random() > 0.2 ? 'active' : 'inactive'
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalRevenue = customers.reduce((sum, c) => sum + c.total_purchases, 0);
  const avgOrderValue = totalRevenue / customers.reduce((sum, c) => sum + c.orders, 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Clientes</h1>
          <p className="text-muted-foreground">Gestão de clientes</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Clientes</CardTitle>
              <Users className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{customers.length}</div>
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
              <CardTitle className="text-sm font-medium">Ticket Médio</CardTitle>
              <TrendingUp className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(avgOrderValue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Clientes Ativos</CardTitle>
              <Users className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {customers.filter(c => c.status === 'active').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Lista de Clientes</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {customers.map((customer) => (
                <div key={customer.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{customer.name}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span>{customer.email}</span>
                      <span>•</span>
                      <span>{customer.orders} pedidos</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(customer.total_purchases)}</p>
                    <Badge variant={customer.status === 'active' ? 'default' : 'secondary'}>
                      {customer.status === 'active' ? 'Ativo' : 'Inativo'}
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
