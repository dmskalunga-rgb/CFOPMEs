// PAMDashboardPage - Privileged Access Management
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ShieldCheck, Lock, Eye, AlertCircle } from 'lucide-react';

export default function PAMDashboardPage() {
  const stats = [
    { label: 'Contas Privilegiadas', value: 12, icon: ShieldCheck, color: 'text-purple-600' },
    { label: 'Sessões Monitoradas', value: 8, icon: Eye, color: 'text-blue-600' },
    { label: 'Cofres Ativos', value: 5, icon: Lock, color: 'text-green-600' },
    { label: 'Violações', value: 0, icon: AlertCircle, color: 'text-red-600' }
  ];

  const privilegedAccounts = [
    { name: 'root@server-prod', type: 'SSH', lastUsed: '1 hora atrás', status: 'active' },
    { name: 'admin@database', type: 'Database', lastUsed: '3 horas atrás', status: 'active' },
    { name: 'sysadmin@cloud', type: 'Cloud', lastUsed: '1 dia atrás', status: 'inactive' }
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">PAM Dashboard</h1>
          <p className="text-muted-foreground">Privileged Access Management</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          {stats.map((stat, i) => (
            <Card key={i}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">{stat.label}</CardTitle>
                <stat.icon className={`h-4 w-4 ${stat.color}`} />
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${stat.color}`}>{stat.value}</div>
              </CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Contas Privilegiadas</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {privilegedAccounts.map((account, i) => (
                <div key={i} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{account.name}</p>
                    <p className="text-sm text-muted-foreground">Tipo: {account.type}</p>
                  </div>
                  <div className="text-right">
                    <Badge variant={account.status === 'active' ? 'default' : 'secondary'}>
                      {account.status === 'active' ? 'Ativo' : 'Inativo'}
                    </Badge>
                    <p className="text-xs text-muted-foreground mt-1">{account.lastUsed}</p>
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
