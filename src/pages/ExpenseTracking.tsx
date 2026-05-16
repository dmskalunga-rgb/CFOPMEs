// ExpenseTracking - Controle de Despesas
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DollarSign, TrendingDown, Receipt, CreditCard } from 'lucide-react';

interface Expense {
  id: string;
  description: string;
  category: string;
  amount: number;
  date: string;
  status: 'pending' | 'approved' | 'rejected';
  user: string;
}

export default function ExpenseTracking() {
  const [expenses] = useState<Expense[]>(
    Array.from({ length: 15 }, (_, i) => ({
      id: `exp-${i + 1}`,
      description: `Despesa ${i + 1}`,
      category: ['Transporte', 'Alimentação', 'Material', 'Viagem', 'Outros'][Math.floor(Math.random() * 5)],
      amount: Math.floor(Math.random() * 50000) + 5000,
      date: new Date(Date.now() - Math.random() * 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
      status: ['pending', 'approved', 'rejected'][Math.floor(Math.random() * 3)] as Expense['status'],
      user: ['João Silva', 'Maria Santos', 'Pedro Costa'][Math.floor(Math.random() * 3)]
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalExpenses = expenses.filter(e => e.status === 'approved').reduce((sum, e) => sum + e.amount, 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Controle de Despesas</h1>
          <p className="text-muted-foreground">Gestão de despesas e reembolsos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total de Despesas</CardTitle>
              <Receipt className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{expenses.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
              <DollarSign className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">{formatCurrency(totalExpenses)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
              <CreditCard className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">
                {expenses.filter(e => e.status === 'pending').length}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Aprovadas</CardTitle>
              <TrendingDown className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {expenses.filter(e => e.status === 'approved').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Despesas Recentes</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {expenses.map((expense) => (
                <div key={expense.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{expense.description}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Badge variant="outline">{expense.category}</Badge>
                      <span>•</span>
                      <span>{expense.user}</span>
                      <span>•</span>
                      <span>{new Date(expense.date).toLocaleDateString('pt-AO')}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(expense.amount)}</p>
                    <Badge variant={expense.status === 'approved' ? 'default' : expense.status === 'pending' ? 'secondary' : 'destructive'}>
                      {expense.status === 'approved' ? 'Aprovada' : expense.status === 'pending' ? 'Pendente' : 'Rejeitada'}
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
