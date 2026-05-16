// RBACDashboardPage - Role-Based Access Control
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Shield, Users, Lock, CheckCircle } from 'lucide-react';

export default function RBACDashboardPage() {
  const stats = [
    { label: 'Funções Ativas', value: 8, icon: Shield, color: 'text-blue-600' },
    { label: 'Usuários Atribuídos', value: 156, icon: Users, color: 'text-green-600' },
    { label: 'Permissões', value: 42, icon: Lock, color: 'text-purple-600' },
    { label: 'Políticas', value: 15, icon: CheckCircle, color: 'text-orange-600' }
  ];

  const roles = [
    { name: 'Administrador', users: 3, permissions: 42, level: 'high' },
    { name: 'Gerente', users: 12, permissions: 28, level: 'medium' },
    { name: 'Usuário', users: 125, permissions: 15, level: 'low' },
    { name: 'Visualizador', users: 16, permissions: 8, level: 'low' }
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">RBAC Dashboard</h1>
          <p className="text-muted-foreground">Role-Based Access Control</p>
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
            <CardTitle>Funções e Permissões</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {roles.map((role, i) => (
                <div key={i} className="flex items-center justify-between border-b pb-3 last:border-0">
                  <div>
                    <p className="font-medium">{role.name}</p>
                    <p className="text-sm text-muted-foreground">{role.users} usuários • {role.permissions} permissões</p>
                  </div>
                  <Badge variant={role.level === 'high' ? 'destructive' : role.level === 'medium' ? 'default' : 'secondary'}>
                    {role.level === 'high' ? 'Alto' : role.level === 'medium' ? 'Médio' : 'Baixo'}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}
