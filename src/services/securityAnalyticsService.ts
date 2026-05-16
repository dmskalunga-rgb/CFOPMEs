// Security & Analytics Service - Versão com dados de demonstração
import { supabase } from '@/integrations/supabase/client';

export interface SecurityStats {
  total_sessions: number;
  active_sessions: number;
  failed_logins: number;
  security_events: number;
  last_login?: string;
}

export interface AuditLog {
  id: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  details: any;
  ip_address?: string;
  created_at: string;
}

export interface UserSession {
  id: string;
  device_name?: string;
  device_type?: string;
  browser?: string;
  os?: string;
  ip_address?: string;
  location?: string;
  is_active: boolean;
  last_activity_at: string;
  created_at: string;
}

export interface AnalyticsSummary {
  total_revenue: number;
  total_invoices: number;
  total_customers: number;
  avg_invoice_value: number;
  growth_rate: number;
}

// Dados de demonstração
const mockSessions: UserSession[] = [
  {
    id: '1',
    device_name: 'Desktop - Chrome',
    device_type: 'desktop',
    browser: 'Chrome 120',
    os: 'Windows 11',
    ip_address: '197.149.45.123',
    location: 'Luanda, Angola',
    is_active: true,
    last_activity_at: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    created_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '2',
    device_name: 'iPhone 15',
    device_type: 'mobile',
    browser: 'Safari 17',
    os: 'iOS 17',
    ip_address: '197.149.45.124',
    location: 'Luanda, Angola',
    is_active: true,
    last_activity_at: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString(),
    created_at: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '3',
    device_name: 'MacBook Pro',
    device_type: 'desktop',
    browser: 'Firefox 121',
    os: 'macOS 14',
    ip_address: '197.149.45.125',
    location: 'Benguela, Angola',
    is_active: false,
    last_activity_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    created_at: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString()
  }
];

const mockAuditLogs: AuditLog[] = [
  {
    id: '1',
    action: 'LOGIN',
    resource_type: 'auth',
    details: { method: 'email', success: true },
    ip_address: '197.149.45.123',
    created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString()
  },
  {
    id: '2',
    action: 'CREATE',
    resource_type: 'invoice',
    resource_id: 'INV-2026-00145',
    details: { amount: 450000, customer: 'Empresa ABC' },
    ip_address: '197.149.45.123',
    created_at: new Date(Date.now() - 1 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '3',
    action: 'UPDATE',
    resource_type: 'customer',
    resource_id: 'CLT-789',
    details: { field: 'email', old: 'old@email.com', new: 'new@email.com' },
    ip_address: '197.149.45.123',
    created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '4',
    action: 'DELETE',
    resource_type: 'product',
    resource_id: 'PRD-456',
    details: { name: 'Produto Teste' },
    ip_address: '197.149.45.123',
    created_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '5',
    action: 'EXPORT',
    resource_type: 'report',
    resource_id: 'RPT-001',
    details: { format: 'pdf', type: 'financial' },
    ip_address: '197.149.45.123',
    created_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString()
  },
  {
    id: '6',
    action: 'VIEW',
    resource_type: 'dashboard',
    resource_id: 'DASH-001',
    details: { page: 'analytics' },
    ip_address: '197.149.45.124',
    created_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString()
  }
];

class SecurityAnalyticsService {
  async getSecurityStats(userId: string): Promise<SecurityStats> {
    // Retornar dados de demonstração
    return {
      total_sessions: 3,
      active_sessions: 2,
      failed_logins: 1,
      security_events: 3,
      last_login: new Date(Date.now() - 5 * 60 * 1000).toISOString()
    };
  }

  async getAuditLogs(userId: string, limit = 50): Promise<AuditLog[]> {
    // Retornar dados de demonstração
    return mockAuditLogs.slice(0, limit);
  }

  async getUserSessions(userId: string): Promise<UserSession[]> {
    // Retornar dados de demonstração
    return mockSessions;
  }

  async revokeSession(sessionId: string): Promise<void> {
    // Simular revogação
    const session = mockSessions.find(s => s.id === sessionId);
    if (session) {
      session.is_active = false;
    }
  }

  async getAnalyticsSummary(
    userId: string,
    startDate: string,
    endDate: string
  ): Promise<AnalyticsSummary> {
    // Retornar dados de demonstração
    return {
      total_revenue: 2450000,
      total_invoices: 127,
      total_customers: 45,
      avg_invoice_value: 19291,
      growth_rate: 15.2
    };
  }

  async requestDataExport(userId: string, type: string, format: string): Promise<void> {
    // Simular solicitação de exportação
    console.log(`Export requested: ${type} in ${format} format for user ${userId}`);
    // Em produção, isso criaria um job de exportação
  }
}

export const securityAnalyticsService = new SecurityAnalyticsService();
