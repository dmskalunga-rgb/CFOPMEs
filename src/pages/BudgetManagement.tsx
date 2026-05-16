// BudgetManagement, ContractsManagement, DocumentsManagement - Últimas páginas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { PieChart, TrendingUp, AlertTriangle } from 'lucide-react';

interface Budget {
  id: string;
  category: string;
  allocated: number;
  spent: number;
  remaining: number;
  period: string;
}

export default function BudgetManagement() {
  const [budgets] = useState<Budget[]>(
    ['Marketing', 'Operações', 'RH', 'TI', 'Vendas', 'Administrativo'].map((cat, i) => {
      const allocated = Math.floor(Math.random() * 2000000) + 500000;
      const spent = Math.floor(allocated * (Math.random() * 0.9));
      return {
        id: `budget-${i + 1}`,
        category: cat,
        allocated,
        spent,
        remaining: allocated - spent,
        period: '2026 Q2'
      };
    })
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalAllocated = budgets.reduce((sum, b) => sum + b.allocated, 0);
  const totalSpent = budgets.reduce((sum, b) => sum + b.spent, 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão de Orçamentos</h1>
          <p className="text-muted-foreground">Controle de orçamentos por categoria</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Orçamento Total</CardTitle>
              <PieChart className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalAllocated)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Gasto Total</CardTitle>
              <TrendingUp className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{formatCurrency(totalSpent)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Disponível</CardTitle>
              <TrendingUp className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">{formatCurrency(totalAllocated - totalSpent)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Utilização</CardTitle>
              <AlertTriangle className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{Math.round(totalSpent / totalAllocated * 100)}%</div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Orçamentos por Categoria</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {budgets.map((budget) => {
                const percentage = Math.round(budget.spent / budget.allocated * 100);
                return (
                  <div key={budget.id} className="space-y-2">
                    <div className="flex justify-between">
                      <div>
                        <p className="font-medium">{budget.category}</p>
                        <p className="text-sm text-muted-foreground">{budget.period}</p>
                      </div>
                      <div className="text-right">
                        <p className="font-bold">{formatCurrency(budget.spent)} / {formatCurrency(budget.allocated)}</p>
                        <Badge variant={percentage > 90 ? 'destructive' : percentage > 75 ? 'default' : 'secondary'}>
                          {percentage}% utilizado
                        </Badge>
                      </div>
                    </div>
                    <div className="h-2 bg-muted rounded-full overflow-hidden">
                      <div 
                        className={`h-full ${percentage > 90 ? 'bg-red-600' : percentage > 75 ? 'bg-yellow-600' : 'bg-green-600'}`}
                        style={{ width: `${percentage}%` }}
                      />
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
