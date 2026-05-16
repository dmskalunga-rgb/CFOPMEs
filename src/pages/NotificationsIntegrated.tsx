import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Bell, Check, Trash2, Filter, Search } from 'lucide-react';
import { Layout } from '@/components/Layout';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { formatDate } from '@/lib/index';
import { integratedServices } from '@/services/integratedServices';
import { supabase } from '@/integrations/supabase/client';
import { springPresets, staggerContainer, staggerItem } from '@/lib/motion';

export default function Notifications() {
  const { toast } = useToast();
  const [notifications, setNotifications] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('ALL');
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    loadNotifications();
  }, []);

  const loadNotifications = async () => {
    try {
      setLoading(true);
      const { data: user } = await supabase.auth.getUser();

      if (!user?.user) {
        toast({ title: 'Erro', description: 'Usuário não autenticado', variant: 'destructive' });
        return;
      }

      const { data, error } = await supabase
        .from('notifications')
        .select('*')
        .eq('user_id', user.user.id)
        .order('created_at', { ascending: false });

      if (error) throw error;
      setNotifications(data || []);
    } catch (error: any) {
      console.error('Erro ao carregar notificações:', error);
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  };

  const handleMarkAsRead = async (notificationId: string) => {
    try {
      await integratedServices.notifications.markAsRead(notificationId);
      setNotifications((prev) =>
        prev.map((n) => (n.id === notificationId ? { ...n, is_read: true } : n))
      );
      toast({ title: 'Sucesso', description: 'Notificação marcada como lida' });
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const handleMarkAllAsRead = async () => {
    try {
      const unreadIds = notifications.filter((n) => !n.is_read).map((n) => n.id);
      await Promise.all(unreadIds.map((id) => integratedServices.notifications.markAsRead(id)));
      setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
      toast({ title: 'Sucesso', description: 'Todas as notificações marcadas como lidas' });
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const handleDelete = async (notificationId: string) => {
    try {
      const { error } = await supabase.from('notifications').delete().eq('id', notificationId);
      if (error) throw error;
      setNotifications((prev) => prev.filter((n) => n.id !== notificationId));
      toast({ title: 'Sucesso', description: 'Notificação excluída' });
    } catch (error: any) {
      toast({ title: 'Erro', description: error.message, variant: 'destructive' });
    }
  };

  const filteredNotifications = notifications.filter((n) => {
    if (filter === 'UNREAD' && n.is_read) return false;
    if (filter === 'READ' && !n.is_read) return false;
    if (searchTerm && !n.title.toLowerCase().includes(searchTerm.toLowerCase())) return false;
    return true;
  });

  const unreadCount = notifications.filter((n) => !n.is_read).length;

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'URGENT':
        return 'destructive';
      case 'HIGH':
        return 'destructive';
      case 'MEDIUM':
        return 'default';
      case 'LOW':
        return 'secondary';
      default:
        return 'default';
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'SUCCESS':
        return '✅';
      case 'WARNING':
        return '⚠️';
      case 'ERROR':
        return '❌';
      case 'INFO':
        return 'ℹ️';
      default:
        return '🔔';
    }
  };

  return (
    <Layout>
      <motion.div
        className="space-y-6"
        variants={staggerContainer}
        initial="initial"
        animate="animate"
      >
        {/* Header */}
        <motion.div variants={staggerItem} className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2">
              <Bell className="h-8 w-8" />
              Notificações
              {unreadCount > 0 && (
                <Badge variant="destructive" className="ml-2">
                  {unreadCount}
                </Badge>
              )}
            </h1>
            <p className="text-muted-foreground">Gerencie suas notificações e alertas</p>
          </div>
          <Button onClick={handleMarkAllAsRead} disabled={unreadCount === 0}>
            <Check className="mr-2 h-4 w-4" />
            Marcar Todas como Lidas
          </Button>
        </motion.div>

        {/* Filters */}
        <motion.div variants={staggerItem} className="flex gap-4">
          <div className="flex-1">
            <Input
              placeholder="Buscar notificações..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="max-w-sm"
            />
          </div>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">Todas</SelectItem>
              <SelectItem value="UNREAD">Não Lidas</SelectItem>
              <SelectItem value="READ">Lidas</SelectItem>
            </SelectContent>
          </Select>
        </motion.div>

        {/* Notifications List */}
        <motion.div variants={staggerItem} className="space-y-4">
          {loading ? (
            <Card>
              <CardContent className="py-12 text-center">
                <p className="text-muted-foreground">Carregando notificações...</p>
              </CardContent>
            </Card>
          ) : filteredNotifications.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <Bell className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
                <p className="text-muted-foreground">Nenhuma notificação encontrada</p>
              </CardContent>
            </Card>
          ) : (
            filteredNotifications.map((notification) => (
              <Card
                key={notification.id}
                className={`${!notification.is_read ? 'border-primary' : ''}`}
              >
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-3 flex-1">
                      <span className="text-2xl">{getTypeIcon(notification.type)}</span>
                      <div className="flex-1">
                        <CardTitle className="text-base flex items-center gap-2">
                          {notification.title}
                          {!notification.is_read && (
                            <div className="h-2 w-2 rounded-full bg-primary" />
                          )}
                        </CardTitle>
                        <CardDescription className="mt-1">{notification.message}</CardDescription>
                        <div className="flex items-center gap-2 mt-2">
                          <Badge variant={getPriorityColor(notification.priority)}>
                            {notification.priority}
                          </Badge>
                          <span className="text-xs text-muted-foreground">
                            {formatDate(notification.created_at)}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex gap-2">
                      {!notification.is_read && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleMarkAsRead(notification.id)}
                        >
                          <Check className="h-4 w-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(notification.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </CardHeader>
              </Card>
            ))
          )}
        </motion.div>
      </motion.div>
    </Layout>
  );
}
