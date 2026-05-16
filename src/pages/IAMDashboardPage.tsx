// IAMDashboardPage - Identity and Access Management
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Shield, Users, Key, AlertTriangle } from 'lucide-react';

export default function IAMDashboardPage() {
  const stats = [
    { label: 'Usuários Ativos', value: 156, icon: Users, color: 'text-blue-600' },
    { label: 'Sessões Ativas', value: 89, icon: Key, color: 'text-green-600' },
    { label: 'Políticas', value: 24, icon: Shield, color: 'text-purple-600' },
    { label: 'Alertas', value: 3, icon: AlertTriangle, color: 'text-red-600' }
  ];

  const recentActivity = [
    { user: 'João Silva', action: 'Login bem-sucedido', time: '2 min atrás', status: 'success' },
    { user: 'Maria Santos', action: 'Falha de autenticação', time: '15 min atrás', status: 'error' },
    { user: 'Pedro Costa', action: 'Permissões atualizadas', time: '1 hora atrás', status: 'info' }
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">IAM Dashboard</h1>
          <p className="text-muted-foreground">Identity and Access Management</p>
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
            <CardTitle>Atividade Recente</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {recentActivity.map((activity, i) => (
                <div key={i} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{activity.user}</p>
                    <p className="text-sm text-muted-foreground">{activity.action}</p>
                  </div>
                  <div className="text-right">
                    <Badge variant={activity.status === 'success' ? 'default' : activity.status === 'error' ? 'destructive' : 'secondary'}>
                      {activity.status}
                    </Badge>
                    <p className="text-xs text-muted-foreground mt-1">{activity.time}</p>
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
