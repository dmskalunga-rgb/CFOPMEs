// AGT Integration Service
import { supabase } from '@/integrations/supabase/client';

export interface AGTInvoice {
  id: string;
  invoice_number: string;
  customer_name: string;
  customer_nif?: string;
  amount: number;
  tax_amount?: number;
  total_amount: number;
  issue_date: string;
  agt_status: 'pending' | 'sent' | 'validated' | 'rejected' | 'cancelled';
  agt_reference?: string;
  agt_validation_code?: string;
  sent_at?: string;
  validated_at?: string;
  rejected_at?: string;
  rejection_reason?: string;
  retry_count: number;
}

export interface AGTLog {
  id: string;
  invoice_id?: string;
  action: string;
  status: string;
  message: string;
  duration_ms?: number;
  created_at: string;
}

export interface AGTStats {
  total_invoices: number;
  sent_to_agt: number;
  validated: number;
  rejected: number;
  pending: number;
  success_rate: number;
}

export interface AGTConnectionStatus {
  is_configured: boolean;
  is_active: boolean;
  is_test_mode: boolean;
  company_nif?: string;
  company_name?: string;
  last_sync_at?: string;
  last_activity?: string;
  connection_status: 'online' | 'idle' | 'offline';
}

class AGTService {
  async getConnectionStatus(): Promise<AGTConnectionStatus> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    const { data, error } = await supabase.rpc('get_agt_connection_status_2026_04_09', {
      user_uuid: user.id
    });

    if (error) throw error;
    return data;
  }

  async getStatistics(): Promise<AGTStats> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    const { data, error } = await supabase.rpc('get_agt_statistics_2026_04_09', {
      user_uuid: user.id
    });

    if (error) throw error;
    return data;
  }

  async listInvoices(filters?: { status?: string; limit?: number }): Promise<AGTInvoice[]> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    let query = supabase
      .from('agt_invoices_2026_04_09')
      .select('*')
      .eq('user_id', user.id)
      .order('issue_date', { ascending: false });

    if (filters?.status) {
      query = query.eq('agt_status', filters.status);
    }

    if (filters?.limit) {
      query = query.limit(filters.limit);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  }

  async listLogs(limit: number = 50): Promise<AGTLog[]> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    const { data, error } = await supabase
      .from('agt_integration_logs_2026_04_09')
      .select('*')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false })
      .limit(limit);

    if (error) throw error;
    return data || [];
  }

  async sendToAGT(invoiceId: string): Promise<void> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    // Simular envio para AGT
    const { error } = await supabase
      .from('agt_invoices_2026_04_09')
      .update({
        agt_status: 'sent',
        sent_at: new Date().toISOString(),
        agt_reference: `AGT-${new Date().toISOString().split('T')[0]}-${Math.random().toString(36).substr(2, 9)}`
      })
      .eq('id', invoiceId)
      .eq('user_id', user.id);

    if (error) throw error;

    // Criar log
    await supabase.from('agt_integration_logs_2026_04_09').insert({
      invoice_id: invoiceId,
      action: 'SENT',
      status: 'success',
      message: 'Fatura enviada para AGT',
      duration_ms: 180,
      user_id: user.id
    });
  }

  async retryInvoice(invoiceId: string): Promise<void> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    // Primeiro, buscar o retry_count atual
    const { data: invoice } = await supabase
      .from('agt_invoices_2026_04_09')
      .select('retry_count')
      .eq('id', invoiceId)
      .eq('user_id', user.id)
      .single();

    const { error } = await supabase
      .from('agt_invoices_2026_04_09')
      .update({
        agt_status: 'sent',
        retry_count: (invoice?.retry_count || 0) + 1,
        last_retry_at: new Date().toISOString()
      })
      .eq('id', invoiceId)
      .eq('user_id', user.id);

    if (error) throw error;

    await supabase.from('agt_integration_logs_2026_04_09').insert({
      invoice_id: invoiceId,
      action: 'RETRY',
      status: 'success',
      message: 'Tentativa de reenvio para AGT',
      user_id: user.id
    });
  }

  formatCurrency(value: number): string {
    return new Intl.NumberFormat('pt-AO', {
      style: 'currency',
      currency: 'AOA'
    }).format(value);
  }

  getStatusLabel(status: string): string {
    const labels: Record<string, string> = {
      pending: 'Pendente',
      sent: 'Enviada',
      validated: 'Validada',
      rejected: 'Rejeitada',
      cancelled: 'Cancelada'
    };
    return labels[status] || status;
  }

  getStatusColor(status: string): string {
    const colors: Record<string, string> = {
      pending: 'bg-yellow-100 text-yellow-700 border-yellow-200',
      sent: 'bg-blue-100 text-blue-700 border-blue-200',
      validated: 'bg-green-100 text-green-700 border-green-200',
      rejected: 'bg-red-100 text-red-700 border-red-200',
      cancelled: 'bg-gray-100 text-gray-700 border-gray-200'
    };
    return colors[status] || 'bg-gray-100 text-gray-700 border-gray-200';
  }

  getLogStatusColor(status: string): string {
    const colors: Record<string, string> = {
      success: 'text-green-600',
      error: 'text-red-600',
      pending: 'text-yellow-600',
      warning: 'text-orange-600'
    };
    return colors[status] || 'text-gray-600';
  }
}

export const agtService = new AGTService();
