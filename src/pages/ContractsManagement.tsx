// ContractsManagement, DocumentsManagement, CalendarEvents, Notifications, ActivityLog
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { FileText, Clock, CheckCircle, AlertCircle } from 'lucide-react';

interface Contract {
  id: string;
  title: string;
  client: string;
  value: number;
  start_date: string;
  end_date: string;
  status: 'active' | 'pending' | 'expired' | 'cancelled';
}

export default function ContractsManagement() {
  const [contracts] = useState<Contract[]>(
    Array.from({ length: 10 }, (_, i) => ({
      id: `contract-${i + 1}`,
      title: `Contrato ${i + 1}`,
      client: `Cliente ${String.fromCharCode(65 + i)}`,
      value: Math.floor(Math.random() * 5000000) + 500000,
      start_date: new Date(2026, 0, 1 + i * 10).toISOString().split('T')[0],
      end_date: new Date(2026, 11, 31 - i * 5).toISOString().split('T')[0],
      status: ['active', 'pending', 'expired', 'cancelled'][Math.floor(Math.random() * 4)] as Contract['status']
    }))
  );

  const formatCurrency = (value: number) => new Intl.NumberFormat('pt-AO', { style: 'currency', currency: 'AOA', minimumFractionDigits: 0 }).format(value);
  const totalValue = contracts.filter(c => c.status === 'active').reduce((sum, c) => sum + c.value, 0);

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Gestão de Contratos</h1>
          <p className="text-muted-foreground">Controle de contratos e acordos</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{contracts.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Ativos</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {contracts.filter(c => c.status === 'active').length}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Valor Total</CardTitle>
              <FileText className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{formatCurrency(totalValue)}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Pendentes</CardTitle>
              <Clock className="h-4 w-4 text-yellow-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-yellow-600">
                {contracts.filter(c => c.status === 'pending').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Contratos</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {contracts.map((contract) => (
                <div key={contract.id} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{contract.title}</p>
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span>{contract.client}</span>
                      <span>•</span>
                      <span>{new Date(contract.start_date).toLocaleDateString('pt-AO')} - {new Date(contract.end_date).toLocaleDateString('pt-AO')}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-bold">{formatCurrency(contract.value)}</p>
                    <Badge variant={contract.status === 'active' ? 'default' : contract.status === 'pending' ? 'secondary' : 'destructive'}>
                      {contract.status === 'active' ? 'Ativo' : contract.status === 'pending' ? 'Pendente' : contract.status === 'expired' ? 'Expirado' : 'Cancelado'}
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
