// =====================================================
// KWANZACONTROL - Intelligent Notification System
// Sistema de notificações automáticas e inteligentes
// =====================================================

import { supabase } from '@/integrations/supabase/client';

export interface Notification {
  id: string;
  user_id: string;
  organization_id: string;
  type: 'info' | 'warning' | 'error' | 'success';
  category: 'invoice' | 'payment' | 'budget' | 'quota' | 'anomaly' | 'approval' | 'system';
  title: string;
  message: string;
  action_url?: string;
  action_label?: string;
  metadata?: Record<string, any>;
  read: boolean;
  created_at: string;
}

export interface NotificationRule {
  id: string;
  organization_id: string;
  name: string;
  type: 'invoice_due' | 'payment_overdue' | 'budget_exceeded' | 'quota_limit' | 'anomaly_detected' | 'approval_pending';
  enabled: boolean;
  conditions: Record<string, any>;
  actions: Array<{
    type: 'notification' | 'email' | 'webhook';
    config: Record<string, any>;
  }>;
}

class NotificationService {
  // Criar notificação
  async createNotification(notification: Omit<Notification, 'id' | 'created_at' | 'read'>): Promise<Notification> {
    const { data, error } = await supabase
      .from('notifications')
      .insert({
        ...notification,
        read: false,
      })
      .select()
      .single();

    if (error) throw error;
    return data;
  }

  // Listar notificações do usuário
  async getUserNotifications(userId: string, limit = 50): Promise<Notification[]> {
    const { data, error } = await supabase
      .from('notifications')
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false })
      .limit(limit);

    if (error) throw error;
    return data || [];
  }

  // Marcar como lida
  async markAsRead(notificationId: string): Promise<void> {
    const { error } = await supabase
      .from('notifications')
      .update({ read: true })
      .eq('id', notificationId);

    if (error) throw error;
  }

  // Marcar todas como lidas
  async markAllAsRead(userId: string): Promise<void> {
    const { error } = await supabase
      .from('notifications')
      .update({ read: true })
      .eq('user_id', userId)
      .eq('read', false);

    if (error) throw error;
  }

  // Contar não lidas
  async getUnreadCount(userId: string): Promise<number> {
    const { count, error } = await supabase
      .from('notifications')
      .select('*', { count: 'exact', head: true })
      .eq('user_id', userId)
      .eq('read', false);

    if (error) throw error;
    return count || 0;
  }

  // Deletar notificação
  async deleteNotification(notificationId: string): Promise<void> {
    const { error } = await supabase
      .from('notifications')
      .delete()
      .eq('id', notificationId);

    if (error) throw error;
  }

  // AUTOMAÇÃO: Verificar faturas vencendo
  async checkInvoicesDueSoon(organizationId: string): Promise<void> {
    const threeDaysFromNow = new Date();
    threeDaysFromNow.setDate(threeDaysFromNow.getDate() + 3);

    const { data: invoices, error } = await supabase
      .from('invoices')
      .select('*')
      .eq('organization_id', organizationId)
      .eq('status', 'SENT_AGT')
      .lte('due_date', threeDaysFromNow.toISOString())
      .gte('due_date', new Date().toISOString());

    if (error) throw error;

    for (const invoice of invoices || []) {
      const daysUntilDue = Math.ceil(
        (new Date(invoice.due_date).getTime() - new Date().getTime()) / (1000 * 60 * 60 * 24)
      );

      await this.createNotification({
        user_id: invoice.created_by || '',
        organization_id: organizationId,
        type: 'warning',
        category: 'invoice',
        title: 'Fatura Vencendo em Breve',
        message: `A fatura ${invoice.invoice_number} vence em ${daysUntilDue} dias.`,
        action_url: `/invoicing?id=${invoice.id}`,
        action_label: 'Ver Fatura',
        metadata: { invoice_id: invoice.id, days_until_due: daysUntilDue },
      });
    }
  }

  // AUTOMAÇÃO: Verificar faturas vencidas
  async checkOverdueInvoices(organizationId: string): Promise<void> {
    const { data: invoices, error } = await supabase
      .from('invoices')
      .select('*')
      .eq('organization_id', organizationId)
      .in('status', ['SENT_AGT', 'VALIDATED'])
      .lt('due_date', new Date().toISOString());

    if (error) throw error;

    for (const invoice of invoices || []) {
      const daysOverdue = Math.ceil(
        (new Date().getTime() - new Date(invoice.due_date).getTime()) / (1000 * 60 * 60 * 24)
      );

      await this.createNotification({
        user_id: invoice.created_by || '',
        organization_id: organizationId,
        type: 'error',
        category: 'payment',
        title: 'Fatura Vencida',
        message: `A fatura ${invoice.invoice_number} está vencida há ${daysOverdue} dias.`,
        action_url: `/invoicing?id=${invoice.id}`,
        action_label: 'Ver Fatura',
        metadata: { invoice_id: invoice.id, days_overdue: daysOverdue },
      });
    }
  }

  // AUTOMAÇÃO: Verificar orçamento excedido
  async checkBudgetExceeded(organizationId: string): Promise<void> {
    const { data: costCenters, error } = await supabase
      .from('cost_centers')
      .select('*')
      .eq('organization_id', organizationId)
      .eq('active', true);

    if (error) throw error;

    for (const cc of costCenters || []) {
      // Buscar despesas do centro de custo
      const { data: transactions } = await supabase
        .from('transactions')
        .select('amount')
        .eq('cost_center_id', cc.id)
        .eq('type', 'EXPENSE')
        .gte('created_at', new Date(new Date().setMonth(new Date().getMonth() - 1)).toISOString());

      const totalExpenses = transactions?.reduce((sum, t) => sum + (t.amount || 0), 0) || 0;
      const budgetPercentage = (totalExpenses / cc.budget) * 100;

      if (budgetPercentage >= 90) {
        await this.createNotification({
          user_id: cc.created_by || '',
          organization_id: organizationId,
          type: 'error',
          category: 'budget',
          title: 'Orçamento Crítico',
          message: `O centro de custo "${cc.name}" atingiu ${budgetPercentage.toFixed(0)}% do orçamento.`,
          action_url: `/cost-centers?id=${cc.id}`,
          action_label: 'Ver Análise',
          metadata: { cost_center_id: cc.id, budget_percentage: budgetPercentage },
        });
      } else if (budgetPercentage >= 80) {
        await this.createNotification({
          user_id: cc.created_by || '',
          organization_id: organizationId,
          type: 'warning',
          category: 'budget',
          title: 'Orçamento em Atenção',
          message: `O centro de custo "${cc.name}" atingiu ${budgetPercentage.toFixed(0)}% do orçamento.`,
          action_url: `/cost-centers?id=${cc.id}`,
          action_label: 'Ver Análise',
          metadata: { cost_center_id: cc.id, budget_percentage: budgetPercentage },
        });
      }
    }
  }

  // AUTOMAÇÃO: Verificar quotas próximas do limite
  async checkQuotaLimits(organizationId: string): Promise<void> {
    // Buscar assinatura
    const { data: subscription } = await supabase
      .from('subscriptions')
      .select('*, subscription_plans(*)')
      .eq('organization_id', organizationId)
      .eq('status', 'active')
      .single();

    if (!subscription?.subscription_plans) return;

    const limits = subscription.subscription_plans.limits as any;

    // Verificar faturas
    const startOfMonth = new Date();
    startOfMonth.setDate(1);
    startOfMonth.setHours(0, 0, 0, 0);

    const { count: invoiceCount } = await supabase
      .from('invoices')
      .select('*', { count: 'exact', head: true })
      .eq('organization_id', organizationId)
      .gte('created_at', startOfMonth.toISOString());

    const invoicePercentage = (invoiceCount || 0) / limits.invoices_per_month * 100;

    if (invoicePercentage >= 90) {
      await this.createNotification({
        user_id: subscription.user_id || '',
        organization_id: organizationId,
        type: 'warning',
        category: 'quota',
        title: 'Limite de Faturas Próximo',
        message: `Você usou ${invoiceCount} de ${limits.invoices_per_month} faturas este mês (${invoicePercentage.toFixed(0)}%).`,
        action_url: '/billing',
        action_label: 'Fazer Upgrade',
        metadata: { resource: 'invoices', used: invoiceCount, limit: limits.invoices_per_month },
      });
    }
  }

  // AUTOMAÇÃO: Executar todas as verificações
  async runAutomatedChecks(organizationId: string): Promise<void> {
    try {
      await Promise.all([
        this.checkInvoicesDueSoon(organizationId),
        this.checkOverdueInvoices(organizationId),
        this.checkBudgetExceeded(organizationId),
        this.checkQuotaLimits(organizationId),
      ]);
    } catch (error) {
      console.error('Error running automated checks:', error);
    }
  }
}

export const notificationService = new NotificationService();
