// Audit Service - Serviço para sistema de auditoria
import { supabase } from '@/integrations/supabase/client';

export interface AuditLog {
  id: string;
  user_name?: string;
  user_email?: string;
  action: string;
  resource?: string;
  status: string;
  ip_address?: string;
  details?: string;
  created_at: string;
}

export interface CriticalEvent {
  id: string;
  event_type: string;
  severity: string;
  title: string;
  description?: string;
  user_email?: string;
  resource?: string;
  status: string;
  resolved_at?: string;
  created_at: string;
}

export interface UserActivity {
  user_id?: string;
  user_name?: string;
  user_email?: string;
  actions_count: number;
  last_active: string;
}

export interface AuditStats {
  total_logs: number;
  logs_today: number;
  success_rate: number;
  failed_actions: number;
  active_users: number;
  critical_events: number;
}

class AuditService {
  async getStatistics(): Promise<AuditStats> {
    const { data, error } = await supabase.rpc('get_audit_statistics_2026_04_09');
    if (error) throw error;
    return data;
  }

  async listLogs(filters?: { action?: string; status?: string; limit?: number }): Promise<AuditLog[]> {
    let query = supabase
      .from('audit_logs_2026_04_09')
      .select('*')
      .order('created_at', { ascending: false });

    if (filters?.action) {
      query = query.eq('action', filters.action);
    }

    if (filters?.status) {
      query = query.eq('status', filters.status);
    }

    if (filters?.limit) {
      query = query.limit(filters.limit);
    } else {
      query = query.limit(100);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  }

  async searchLogs(searchTerm: string): Promise<AuditLog[]> {
    const { data, error } = await supabase
      .from('audit_logs_2026_04_09')
      .select('*')
      .or(`user_name.ilike.%${searchTerm}%,user_email.ilike.%${searchTerm}%,resource.ilike.%${searchTerm}%,details.ilike.%${searchTerm}%`)
      .order('created_at', { ascending: false })
      .limit(50);

    if (error) throw error;
    return data || [];
  }

  async getUserActivity(daysLimit: number = 7): Promise<UserActivity[]> {
    const { data, error } = await supabase.rpc('get_user_activity_2026_04_09', {
      days_limit: daysLimit
    });

    if (error) throw error;
    return data || [];
  }

  async listCriticalEvents(status?: string): Promise<CriticalEvent[]> {
    let query = supabase
      .from('audit_critical_events_2026_04_09')
      .select('*')
      .order('created_at', { ascending: false });

    if (status) {
      query = query.eq('status', status);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  }

  async resolveCriticalEvent(eventId: string): Promise<void> {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user) throw new Error('User not authenticated');

    const { error } = await supabase
      .from('audit_critical_events_2026_04_09')
      .update({
        status: 'resolved',
        resolved_at: new Date().toISOString(),
        resolved_by: user.id
      })
      .eq('id', eventId);

    if (error) throw error;
  }

  async exportLogs(logs: AuditLog[]): Promise<void> {
    // Criar CSV
    const headers = ['Data/Hora', 'Usuário', 'Ação', 'Recurso', 'Status', 'IP', 'Detalhes'];
    const rows = logs.map(log => [
      new Date(log.created_at).toLocaleString('pt-AO'),
      log.user_name || log.user_email || 'N/A',
      log.action,
      log.resource || 'N/A',
      log.status,
      log.ip_address || 'N/A',
      log.details || 'N/A'
    ]);

    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    // Download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `audit_logs_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  getActionLabel(action: string): string {
    const labels: Record<string, string> = {
      LOGIN: 'Login',
      LOGOUT: 'Logout',
      CREATE: 'Criar',
      UPDATE: 'Atualizar',
      DELETE: 'Excluir',
      VIEW: 'Visualizar',
      EXPORT: 'Exportar',
      IMPORT: 'Importar',
      BACKUP: 'Backup',
      SYNC: 'Sincronizar'
    };
    return labels[action] || action;
  }

  getStatusColor(status: string): string {
    const colors: Record<string, string> = {
      success: 'bg-green-100 text-green-700 border-green-200',
      failed: 'bg-red-100 text-red-700 border-red-200',
      pending: 'bg-yellow-100 text-yellow-700 border-yellow-200',
      warning: 'bg-orange-100 text-orange-700 border-orange-200'
    };
    return colors[status] || 'bg-gray-100 text-gray-700 border-gray-200';
  }

  getSeverityColor(severity: string): string {
    const colors: Record<string, string> = {
      critical: 'bg-red-100 text-red-700 border-red-200',
      high: 'bg-orange-100 text-orange-700 border-orange-200',
      medium: 'bg-yellow-100 text-yellow-700 border-yellow-200',
      low: 'bg-blue-100 text-blue-700 border-blue-200'
    };
    return colors[severity] || 'bg-gray-100 text-gray-700 border-gray-200';
  }

  getSeverityLabel(severity: string): string {
    const labels: Record<string, string> = {
      critical: 'Crítico',
      high: 'Alto',
      medium: 'Médio',
      low: 'Baixo'
    };
    return labels[severity] || severity;
  }
}

export const auditService = new AuditService();
