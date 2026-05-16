// InventoryManagement - Gestão de Inventário
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Package, AlertTriangle, TrendingDown, TrendingUp } from 'lucide-react';

interface InventoryItem {
  id: string;
  name: string;
  sku: string;
  quantity: number;
  min_stock: number;
  max_stock: number;
  unit_price: number;
  category: string;
}

export default function InventoryManagement() {
  const [inventory] = useState<InventoryItem[]>([
    { id: '1', name: 'Produto A', sku: 'PRD-001', quantity: 150, min_stock: 50, max_stock: 200, unit_price: 5000, category: 'Eletrônicos' },
    { id: '2', name: 'Produto B', sku: 'PRD-002', quantity: 25, min_stock: 30, max_stock: 150, unit_price: 8000, category: 'Eletrônicos' },
    { id: '3', name: 'Produto C', sku: 'PRD-003', quantity: 180, min_stock: 40, max_stock: 200, unit_price: 3500, category: 'Acessórios' },
    { id: '4', name: 'Produto D', sku: 'PRD-004', quantity: 10, min_stock: 20, max_stock: 100, unit_price: 12000, category: 'Premium' },
    { id: '5', name: 'Produto E', sku: 'PRD-005', quantity: 95, min_stock: 30, max_stock: 120, unit_price: 6500, category: 'Acessórios' }
  ]);

  const lowStock = inventory.filter(i => i.quantity < i.min_stock);
  const totalValue = inventory.reduce((sum, i) => sum + (i.quantity * i.unit_price), 0);
  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);

  const getStockStatus = (item: InventoryItem) => {
    if (item.quantity < item.min_stock) return { label: 'Baixo', variant: 'destructive' as const, icon: AlertTriangle };
    if (item.quantity > item.max_stock * 0.8) return { label: 'Alto', variant: 'secondary' as const, icon: TrendingUp };
    return { label: 'Normal', variant: 'default' as const, icon: Package };
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão de Inventário</h1>
          <p className="text-muted-foreground">Controle de estoque e produtos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Produtos</CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{inventory.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalValue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Estoque Baixo</CardTitle>
              <AlertTriangle className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{lowStock.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Itens Totais</CardTitle>
              <Package className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{inventory.reduce((sum, i) => sum + i.quantity, 0)}</div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Produtos em Estoque</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {inventory.map((item) => {
                const status = getStockStatus(item);
                const StatusIcon = status.icon;
                return (
                  <div key={item.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                    <div className="flex-1">
                      <p className="font-medium">{item.name}</p>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <span>SKU: {item.sku}</span>
                        <span>•</span>
                        <Badge variant="outline">{item.category}</Badge>
                        <span>•</span>
                        <Badge variant={status.variant}>
                          <StatusIcon className="h-3 w-3 mr-1" />
                          {status.label}
                        </Badge>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-lg font-bold">{item.quantity} un.</p>
                      <p className="text-xs text-muted-foreground">
                        Min: {item.min_stock} • Max: {item.max_stock}
                      </p>
                      <p className="text-xs font-medium">{formatCurrency(item.unit_price)}/un.</p>
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
