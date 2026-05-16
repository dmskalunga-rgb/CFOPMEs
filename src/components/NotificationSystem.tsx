import { useState, useEffect } from 'react';
import { Bell, X, Check, AlertTriangle, Info, DollarSign, FileText, Calendar } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { supabase } from '@/integrations/supabase/client';
import { formatCurrency } from '@/lib/index';

export interface Notification {
  id: string;
  type: 'budget' | 'contract' | 'payment' | 'anomaly' | 'info';
  title: string;
  message: string;
  severity: 'low' | 'medium' | 'high';
  read: boolean;
  createdAt: Date;
  actionUrl?: string;
  metadata?: any;
}

interface NotificationPreferences {
  budgetAlerts: boolean;
  contractAlerts: boolean;
  paymentReminders: boolean;
  anomalyDetection: boolean;
  emailNotifications: boolean;
}

export function NotificationSystem() {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [preferences, setPreferences] = useState<NotificationPreferences>({
    budgetAlerts: true,
    contractAlerts: true,
    paymentReminders: true,
    anomalyDetection: true,
    emailNotifications: false,
  });
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    loadNotifications();
    const interval = setInterval(loadNotifications, 60000); // Check every minute
    return () => clearInterval(interval);
  }, []);

  const loadNotifications = async () => {
    try {
      const notifs: Notification[] = [];

      // Check budget alerts
      if (preferences.budgetAlerts) {
        const { data: budgets } = await supabase
          .from('budgets')
          .select('*');

        budgets?.forEach(budget => {
          const usage = (budget.spent_amount / budget.total_amount) * 100;
          
          if (usage >= 100) {
            notifs.push({
              id: `budget-exceeded-${budget.id}`,
              type: 'budget',
              title: 'Orçamento Excedido',
              message: `O orçamento "${budget.name}" excedeu o limite em ${formatCurrency(budget.spent_amount - budget.total_amount)}`,
              severity: 'high',
              read: false,
              createdAt: new Date(),
              metadata: { budgetId: budget.id },
            });
          } else if (usage >= 90) {
            notifs.push({
              id: `budget-warning-${budget.id}`,
              type: 'budget',
              title: 'Orçamento Próximo do Limite',
              message: `O orçamento "${budget.name}" está ${usage.toFixed(0)}% utilizado`,
              severity: 'medium',
              read: false,
              createdAt: new Date(),
              metadata: { budgetId: budget.id },
            });
          }
        });
      }

      // Check contract alerts
      if (preferences.contractAlerts) {
        const { data: contracts } = await supabase
          .from('contracts')
          .select('*')
          .eq('status', 'active');

        const now = new Date();
        contracts?.forEach(contract => {
          const endDate = new Date(contract.end_date);
          const daysUntilExpiry = Math.ceil((endDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));

          if (daysUntilExpiry <= 7 && daysUntilExpiry > 0) {
            notifs.push({
              id: `contract-expiring-${contract.id}`,
              type: 'contract',
              title: 'Contrato Vencendo',
              message: `O contrato "${contract.name}" vence em ${daysUntilExpiry} dias`,
              severity: 'high',
              read: false,
              createdAt: new Date(),
              metadata: { contractId: contract.id },
            });
          } else if (daysUntilExpiry <= 30 && daysUntilExpiry > 7) {
            notifs.push({
              id: `contract-warning-${contract.id}`,
              type: 'contract',
              title: 'Contrato Próximo do Vencimento',
              message: `O contrato "${contract.name}" vence em ${daysUntilExpiry} dias`,
              severity: 'medium',
              read: false,
              createdAt: new Date(),
              metadata: { contractId: contract.id },
            });
          }

          // Payment reminders
          if (preferences.paymentReminders && contract.next_payment_date) {
            const paymentDate = new Date(contract.next_payment_date);
            const daysUntilPayment = Math.ceil((paymentDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));

            if (daysUntilPayment <= 3 && daysUntilPayment >= 0) {
              notifs.push({
                id: `payment-due-${contract.id}`,
                type: 'payment',
                title: 'Pagamento Próximo',
                message: `Pagamento de ${formatCurrency(contract.value)} vence em ${daysUntilPayment} dias`,
                severity: 'high',
                read: false,
                createdAt: new Date(),
                metadata: { contractId: contract.id },
              });
            }
          }
        });
      }

      // Check for anomalies
      if (preferences.anomalyDetection) {
        const { data: transactions } = await supabase
          .from('transactions')
          .select('*')
          .gte('date', new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString())
          .order('amount', { ascending: false })
          .limit(5);

        // Simple anomaly detection: transactions > 2x average
        const amounts = transactions?.map(t => t.amount) || [];
        const avg = amounts.reduce((sum, a) => sum + a, 0) / amounts.length;

        transactions?.forEach(tx => {
          if (tx.amount > avg * 2) {
            notifs.push({
              id: `anomaly-${tx.id}`,
              type: 'anomaly',
              title: 'Transação Incomum Detectada',
              message: `Transação de ${formatCurrency(tx.amount)} é ${((tx.amount / avg) * 100).toFixed(0)}% acima da média`,
              severity: 'medium',
              read: false,
              createdAt: new Date(tx.date),
              metadata: { transactionId: tx.id },
            });
          }
        });
      }

      setNotifications(notifs);
    } catch (err) {
      console.error('Erro ao carregar notificações:', err);
    }
  };

  const markAsRead = (id: string) => {
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
  };

  const markAllAsRead = () => {
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
  };

  const deleteNotification = (id: string) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  };

  const unreadCount = notifications.filter(n => !n.read).length;

  const getIcon = (type: Notification['type']) => {
    switch (type) {
      case 'budget':
        return <DollarSign className="h-4 w-4" />;
      case 'contract':
        return <FileText className="h-4 w-4" />;
      case 'payment':
        return <Calendar className="h-4 w-4" />;
      case 'anomaly':
        return <AlertTriangle className="h-4 w-4" />;
      default:
        return <Info className="h-4 w-4" />;
    }
  };

  const getSeverityColor = (severity: Notification['severity']) => {
    switch (severity) {
      case 'high':
        return 'text-red-600 bg-red-50';
      case 'medium':
        return 'text-yellow-600 bg-yellow-50';
      default:
        return 'text-blue-600 bg-blue-50';
    }
  };

  const filterByType = (type: string) => {
    if (type === 'all') return notifications;
    return notifications.filter(n => n.type === type);
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="h-5 w-5" />
          {unreadCount > 0 && (
            <Badge
              variant="destructive"
              className="absolute -top-1 -right-1 h-5 w-5 flex items-center justify-center p-0 text-xs"
            >
              {unreadCount > 9 ? '9+' : unreadCount}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-96 p-0" align="end">
        <Tabs defaultValue="all" className="w-full">
          <div className="p-4 border-b">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold">Notificações</h3>
              {unreadCount > 0 && (
                <Button variant="ghost" size="sm" onClick={markAllAsRead}>
                  <Check className="h-4 w-4 mr-1" />
                  Marcar todas como lidas
                </Button>
              )}
            </div>
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="all">Todas</TabsTrigger>
              <TabsTrigger value="budget">
                <DollarSign className="h-4 w-4" />
              </TabsTrigger>
              <TabsTrigger value="contract">
                <FileText className="h-4 w-4" />
              </TabsTrigger>
              <TabsTrigger value="payment">
                <Calendar className="h-4 w-4" />
              </TabsTrigger>
              <TabsTrigger value="anomaly">
                <AlertTriangle className="h-4 w-4" />
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {['all', 'budget', 'contract', 'payment', 'anomaly'].map(type => (
              <TabsContent key={type} value={type} className="m-0">
                {filterByType(type).length === 0 ? (
                  <div className="p-8 text-center text-muted-foreground">
                    <Bell className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    <p>Nenhuma notificação</p>
                  </div>
                ) : (
                  <div className="divide-y">
                    {filterByType(type).map(notification => (
                      <div
                        key={notification.id}
                        className={`p-4 hover:bg-muted/50 transition-colors ${!notification.read ? 'bg-muted/30' : ''}`}
                      >
                        <div className="flex items-start gap-3">
                          <div className={`p-2 rounded-full ${getSeverityColor(notification.severity)}`}>
                            {getIcon(notification.type)}
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-start justify-between gap-2">
                              <h4 className="font-medium text-sm">{notification.title}</h4>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 flex-shrink-0"
                                onClick={() => deleteNotification(notification.id)}
                              >
                                <X className="h-3 w-3" />
                              </Button>
                            </div>
                            <p className="text-sm text-muted-foreground mt-1">{notification.message}</p>
                            <div className="flex items-center gap-2 mt-2">
                              <span className="text-xs text-muted-foreground">
                                {notification.createdAt.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}
                              </span>
                              {!notification.read && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-6 text-xs"
                                  onClick={() => markAsRead(notification.id)}
                                >
                                  Marcar como lida
                                </Button>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </TabsContent>
            ))}
          </div>

          <div className="p-4 border-t">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm">Preferências de Notificação</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between">
                  <Label htmlFor="budget-alerts" className="text-sm">Alertas de Orçamento</Label>
                  <Switch
                    id="budget-alerts"
                    checked={preferences.budgetAlerts}
                    onCheckedChange={(checked) => setPreferences({ ...preferences, budgetAlerts: checked })}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="contract-alerts" className="text-sm">Alertas de Contratos</Label>
                  <Switch
                    id="contract-alerts"
                    checked={preferences.contractAlerts}
                    onCheckedChange={(checked) => setPreferences({ ...preferences, contractAlerts: checked })}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="payment-reminders" className="text-sm">Lembretes de Pagamento</Label>
                  <Switch
                    id="payment-reminders"
                    checked={preferences.paymentReminders}
                    onCheckedChange={(checked) => setPreferences({ ...preferences, paymentReminders: checked })}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label htmlFor="anomaly-detection" className="text-sm">Detecção de Anomalias</Label>
                  <Switch
                    id="anomaly-detection"
                    checked={preferences.anomalyDetection}
                    onCheckedChange={(checked) => setPreferences({ ...preferences, anomalyDetection: checked })}
                  />
                </div>
              </CardContent>
            </Card>
          </div>
        </Tabs>
      </PopoverContent>
    </Popover>
  );
}
