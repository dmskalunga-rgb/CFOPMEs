// PurchaseOrders, Suppliers, Customers, Products - Versões Concisas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ShoppingBag, Clock, CheckCircle, XCircle } from 'lucide-react';

interface PurchaseOrder {
  id: string;
  supplier: string;
  items: number;
  total: number;
  status: 'pending' | 'approved' | 'received' | 'cancelled';
  date: string;
}

export default function PurchaseOrders() {
  const [orders] = useState<PurchaseOrder[]>(
    Array.from({ length: 12 }, (_, i) => ({
      id: `PO-${String(i + 1).padStart(4, '0')}`,
      supplier: `Fornecedor ${String.fromCharCode(65 + (i % 5))}`,
      items: Math.floor(Math.random() * 10) + 1,
      total: Math.floor(Math.random() * 500000) + 100000,
      status: ['pending', 'approved', 'received', 'cancelled'][Math.floor(Math.random() * 4)] as PurchaseOrder['status'],
      date: new Date(Date.now() - Math.random() * 60 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalValue = orders.filter(o => o.status !== 'cancelled').reduce((sum, o) => sum + o.total, 0);

  const getStatusBadge = (status: PurchaseOrder['status']) => {
    const variants = {
      pending: { label: 'Pendente', variant: 'secondary' as const, icon: Clock },
      approved: { label: 'Aprovado', variant: 'default' as const, icon: CheckCircle },
      received: { label: 'Recebido', variant: 'default' as const, icon: CheckCircle },
      cancelled: { label: 'Cancelado', variant: 'destructive' as const, icon: XCircle }
    };
    return variants[status];
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Ordens de Compra</h1>
          <p className="text-muted-foreground">Gestão de compras e fornecedores</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Ordens</CardTitle>
              <ShoppingBag className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{orders.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
              <ShoppingBag className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalValue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
              <Clock className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">
                {orders.filter(o => o.status === 'pending').length}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Recebidas</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {orders.filter(o => o.status === 'received').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Ordens de Compra</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {orders.map((order) => {
                const status = getStatusBadge(order.status);
                const StatusIcon = status.icon;
                return (
                  <div key={order.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                    <div>
                      <p className="font-medium">{order.id}</p>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span>{order.supplier}</span>
                        <span>•</span>
                        <span>{order.items} itens</span>
                        <span>•</span>
                        <span>{new Date(order.date).toLocaleDateString('pt-AO')}</span>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="font-bold">{formatCurrency(order.total)}</p>
                      <Badge variant={status.variant}>
                        <StatusIcon className="h-3 w-3 mr-1" />
                        {status.label}
                      </Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}
