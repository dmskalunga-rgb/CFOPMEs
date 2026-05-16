// ActivityLog - Registro de Atividades (ÚLTIMA PÁGINA!)
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Activity, User, FileText, Settings, Database } from 'lucide-react';

interface ActivityLogEntry {
  id: string;
  user: string;
  action: string;
  resource: string;
  type: 'create' | 'update' | 'delete' | 'view' | 'login';
  timestamp: string;
  ip_address: string;
}

export default function ActivityLog() {
  const [activities] = useState<ActivityLogEntry[]>(
    Array.from({ length: 30 }, (_, i) => ({
      id: `activity-${i + 1}`,
      user: ['João Silva', 'Maria Santos', 'Pedro Costa', 'Ana Oliveira'][Math.floor(Math.random() * 4)],
      action: ['Criou', 'Atualizou', 'Deletou', 'Visualizou', 'Login'][Math.floor(Math.random() * 5)],
      resource: ['Fatura', 'Usuário', 'Relatório', 'Projeto', 'Contrato', 'Documento'][Math.floor(Math.random() * 6)],
      type: ['create', 'update', 'delete', 'view', 'login'][Math.floor(Math.random() * 5)] as ActivityLogEntry['type'],
      timestamp: new Date(Date.now() - Math.random() * 7 * 24 * 60 * 60 * 1000).toISOString(),
      ip_address: `192.168.${Math.floor(Math.random() * 255)}.${Math.floor(Math.random() * 255)}`
    }))
  );

  const todayActivities = activities.filter(a => 
    new Date(a.timestamp).toDateString() === new Date().toDateString()
  );

  const getTypeIcon = (type: ActivityLogEntry['type']) => {
    const icons = {
      create: FileText,
      update: Settings,
      delete: Database,
      view: Activity,
      login: User
    };
    return icons[type];
  };

  const getTypeBadge = (type: ActivityLogEntry['type']) => {
    const variants = {
      create: { label: 'Criação', variant: 'default' as const },
      update: { label: 'Atualização', variant: 'secondary' as const },
      delete: { label: 'Exclusão', variant: 'destructive' as const },
      view: { label: 'Visualização', variant: 'outline' as const },
      login: { label: 'Login', variant: 'secondary' as const }
    };
    return variants[type];
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Registro de Atividades</h1>
          <p className="text-muted-foreground">Histórico de ações no sistema</p>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <Activity className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{activities.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Hoje</CardTitle>
              <Activity className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{todayActivities.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Usuários Ativos</CardTitle>
              <User className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {new Set(activities.map(a => a.user)).size}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Ações Críticas</CardTitle>
              <Database className="h-4 w-4 text-red-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-red-600">
                {activities.filter(a => a.type === 'delete').length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Atividades Recentes</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {activities.map((activity) => {
                const TypeIcon = getTypeIcon(activity.type);
                return (
                  <div key={activity.id} className="flex items-start gap-3 border-b pb-3 last:border-0">
                    <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${activity.type === 'delete' ? 'bg-red-100' : activity.type === 'create' ? 'bg-green-100' : 'bg-blue-100'}`}>
                      <TypeIcon className={`h-5 w-5 ${activity.type === 'delete' ? 'text-red-600' : activity.type === 'create' ? 'text-green-600' : 'text-blue-600'}`} />
                    </div>
                    <div className="flex-1">
                      <p className="font-medium">
                        <span className="text-primary">{activity.user}</span> {activity.action.toLowerCase()} {activity.resource.toLowerCase()}
                      </p>
                      <div className="flex items-center gap-2 mt-1">
                        <Badge variant={getTypeBadge(activity.type).variant}>
                          {getTypeBadge(activity.type).label}
                        </Badge>
                        <span className="text-xs text-muted-foreground">
                          {new Date(activity.timestamp).toLocaleString('pt-AO')}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          • IP: {activity.ip_address}
                        </span>
                      </div>
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
