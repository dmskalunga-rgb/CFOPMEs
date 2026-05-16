// Notifications - Central de Notificações
import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Bell, CheckCircle, AlertTriangle, Info, Mail } from 'lucide-react';
import { toast } from 'sonner';

interface Notification {
  id: string;
  title: string;
  message: string;
  type: 'info' | 'success' | 'warning' | 'error';
  read: boolean;
  timestamp: string;
}

export default function Notifications() {
  const [notifications, setNotifications] = useState<Notification[]>(
    Array.from({ length: 20 }, (_, i) => ({
      id: `notif-${i + 1}`,
      title: `Notificação ${i + 1}`,
      message: `Mensagem da notificação ${i + 1}`,
      type: ['info', 'success', 'warning', 'error'][Math.floor(Math.random() * 4)] as Notification['type'],
      read: Math.random() > 0.5,
      timestamp: new Date(Date.now() - Math.random() * 7 * 24 * 60 * 60 * 1000).toISOString()
    }))
  );

  const unreadCount = notifications.filter(n => !n.read).length;

  const handleMarkAsRead = (id: string) => {
    setNotifications(notifications.map(n => n.id === id ? { ...n, read: true } : n));
    toast.success('Marcada como lida!');
  };

  const handleMarkAllAsRead = () => {
    setNotifications(notifications.map(n => ({ ...n, read: true })));
    toast.success('Todas marcadas como lidas!');
  };

  const getTypeIcon = (type: Notification['type']) => {
    const icons = {
      info: Info,
      success: CheckCircle,
      warning: AlertTriangle,
      error: AlertTriangle
    };
    return icons[type];
  };

  const getTypeBadge = (type: Notification['type']) => {
    const variants = {
      info: { label: 'Info', variant: 'secondary' as const },
      success: { label: 'Sucesso', variant: 'default' as const },
      warning: { label: 'Aviso', variant: 'default' as const },
      error: { label: 'Erro', variant: 'destructive' as const }
    };
    return variants[type];
  };

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold">Notificações</h1>
            <p className="text-muted-foreground">Central de notificações</p>
          </div>
          <Button onClick={handleMarkAllAsRead} disabled={unreadCount === 0}>
            Marcar todas como lidas
          </Button>
        </div>

        <div className="grid gap-4 md:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total</CardTitle>
              <Bell className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{notifications.length}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Não Lidas</CardTitle>
              <Mail className="h-4 w-4 text-blue-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-blue-600">{unreadCount}</div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Lidas</CardTitle>
              <CheckCircle className="h-4 w-4 text-green-600" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold text-green-600">
                {notifications.length - unreadCount}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Hoje</CardTitle>
              <Bell className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {notifications.filter(n => new Date(n.timestamp).toDateString() === new Date().toDateString()).length}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Todas as Notificações</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {notifications.map((notification) => {
                const TypeIcon = getTypeIcon(notification.type);
                return (
                  <div 
                    key={notification.id} 
                    className={`flex items-start justify-between border-b pb-3 last:border-0 ${!notification.read ? 'bg-primary/5 -mx-4 px-4 py-3' : ''}`}
                  >
                    <div className="flex gap-3 flex-1">
                      <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${notification.type === 'error' ? 'bg-red-100' : notification.type === 'warning' ? 'bg-yellow-100' : notification.type === 'success' ? 'bg-green-100' : 'bg-blue-100'}`}>
                        <TypeIcon className={`h-5 w-5 ${notification.type === 'error' ? 'text-red-600' : notification.type === 'warning' ? 'text-yellow-600' : notification.type === 'success' ? 'text-green-600' : 'text-blue-600'}`} />
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p className="font-medium">{notification.title}</p>
                          {!notification.read && <Badge variant="default">Nova</Badge>}
                        </div>
                        <p className="text-sm text-muted-foreground mt-1">{notification.message}</p>
                        <div className="flex items-center gap-2 mt-2">
                          <Badge variant={getTypeBadge(notification.type).variant}>
                            {getTypeBadge(notification.type).label}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {new Date(notification.timestamp).toLocaleString('pt-AO')}
                          </span>
                        </div>
                      </div>
                    </div>
                    {!notification.read && (
                      <Button size="sm" variant="ghost" onClick={() => handleMarkAsRead(notification.id)}>
                        Marcar como lida
                      </Button>
                    )}
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
